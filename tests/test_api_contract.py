"""Regression tests for the API contract and the checker that guards it.

These are the tests that stop the class of bug that produced the 30029 SL-side
error, the 404 endpoint paths and the wrong parameter names: the engine was
built from partial knowledge of the docs. The contract is now fetched from the
docs and the checker fails the suite on any drift.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT = os.path.join(ROOT, "docs", "bitunix_api_contract.json")
CLIENT = os.path.join(ROOT, "jrock", "trader", "client.py")
CHECKER = os.path.join(ROOT, "scripts", "audit_api_contract.py")

sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import audit_api_contract as checker  # noqa: E402


# ------------------------------------------------------------------ contract

def test_contract_file_exists_and_parses():
    with open(CONTRACT, encoding="utf-8") as fh:
        c = json.load(fh)
    assert c["endpoint_count"] == len(c["endpoints"])
    assert c["endpoint_count"] >= 50, "the full nav should be covered"
    assert c["source"].startswith("https://www.bitunix.com/api-docs/futures")


def test_contract_covers_every_endpoint_page():
    with open(CONTRACT, encoding="utf-8") as fh:
        c = json.load(fh)
    rest = [e for e in c["endpoints"] if e["kind"] == "rest"]
    ws = [e for e in c["endpoints"] if e["kind"] == "websocket"]
    guides = [e for e in c["endpoints"] if e["kind"] == "guide"]
    assert len(rest) == 39
    assert len(ws) == 10
    assert len(guides) == 5
    assert len(rest) + len(ws) + len(guides) == c["endpoint_count"]


def test_every_rest_entry_has_method_path_and_source():
    with open(CONTRACT, encoding="utf-8") as fh:
        c = json.load(fh)
    for e in c["endpoints"]:
        if e["kind"] != "rest":
            continue
        assert e["method"] in ("GET", "POST", "PUT", "DELETE", "PATCH"), e["id"]
        assert e["path"].startswith("/api/v1/"), e["id"]
        assert e["source_url"].startswith("https://www.bitunix.com/"), e["id"]
        assert e["fetched_at"], e["id"]


def test_every_websocket_entry_has_a_channel():
    with open(CONTRACT, encoding="utf-8") as fh:
        c = json.load(fh)
    for e in c["endpoints"]:
        if e["kind"] != "websocket":
            continue
        assert e["channel"], f"{e['id']} has no channel name"


def test_guide_pages_carry_no_method_or_path():
    """Guide pages mention paths in prose; they must not be mined for one."""
    with open(CONTRACT, encoding="utf-8") as fh:
        c = json.load(fh)
    for e in c["endpoints"]:
        if e["kind"] != "guide":
            continue
        assert e["method"] is None, f"{e['id']} picked up a method"
        assert e["path"] is None, f"{e['id']} picked up a path"


def test_no_literal_none_parameter_rows():
    """The docs use a literal 'None' row to mean 'no parameters'."""
    with open(CONTRACT, encoding="utf-8") as fh:
        c = json.load(fh)
    for e in c["endpoints"]:
        for p in e["request_params"]:
            assert p["name"].lower() not in ("none", "-", "n/a", ""), e["id"]


def test_contract_records_required_and_optional_params():
    with open(CONTRACT, encoding="utf-8") as fh:
        c = json.load(fh)
    by_path = {e["path"]: e for e in c["endpoints"] if e["kind"] == "rest"}
    # the endpoint whose missing `symbol` caused a real failure
    cancel = by_path["/api/v1/futures/tpsl/cancel_order"]
    req = {p["name"] for p in cancel["request_params"] if p["required"]}
    assert req == {"symbol", "orderId"}
    # the endpoint whose missing tpQty/slQty made partial exits impossible
    place = by_path["/api/v1/futures/tpsl/place_order"]
    opt = {p["name"] for p in place["request_params"] if not p["required"]}
    assert {"tpQty", "slQty"} <= opt


# ------------------------------------------------------------------- checker

def test_checker_reports_no_missing_and_no_mismatch():
    rep = checker.audit()
    assert rep["missing"] == 0, [r["path"] for r in rep["results"]
                                 if r["status"] == "MISSING"]
    assert rep["mismatched"] == 0, [r for r in rep["results"]
                                    if r["status"] in ("MISSING-REQUIRED", "EXTRA-PARAM")]


def test_checker_implements_every_in_scope_endpoint():
    rep = checker.audit()
    assert rep["implemented"] == rep["contract_endpoints"] - rep["out_of_scope"]


def test_checker_exit_code_is_zero_when_clean():
    out = subprocess.run([sys.executable, CHECKER, "--strict"],
                         capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0, out.stdout + out.stderr


def test_checker_fails_on_a_deliberately_broken_parameter(tmp_path):
    """The guard must actually fire - a checker that never fails is useless."""
    src = open(CLIENT, encoding="utf-8").read()
    broken = src.replace(
        'body={"symbol": symbol, "orderId": str(order_id)}',
        'body={"symbal": symbol, "orderId": str(order_id)}')
    assert broken != src, "the anchor text moved; update this test"

    # point the checker at a copy of the tree with the broken client
    fake_root = tmp_path / "repo"
    (fake_root / "jrock" / "trader").mkdir(parents=True)
    (fake_root / "docs").mkdir()
    (fake_root / "jrock" / "trader" / "client.py").write_text(broken)
    (fake_root / "docs" / "bitunix_api_contract.json").write_text(
        open(CONTRACT, encoding="utf-8").read())

    script = open(CHECKER, encoding="utf-8").read()
    script = script.replace(
        'ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))',
        f'ROOT = {str(fake_root)!r}')
    path = tmp_path / "checker.py"
    path.write_text(script)

    out = subprocess.run([sys.executable, str(path), "--strict"],
                         capture_output=True, text=True)
    assert out.returncode == 1, "the checker did not fail on a broken param"
    assert "MISSING-REQUIRED" in out.stdout
    assert "symbol" in out.stdout


def test_checker_parses_the_client_without_network():
    calls = checker.parse_client_calls()
    assert calls, "no request() calls found"
    paths = {c["path"] for c in calls}
    assert "/api/v1/futures/trade/place_order" in paths
    assert "/api/v1/futures/tpsl/place_order" in paths


# --------------------------------------------------- the fixed endpoints

def _client_source() -> str:
    return open(CLIENT, encoding="utf-8").read()


def test_get_leverage_and_margin_mode_uses_the_documented_path():
    src = _client_source()
    assert '"/api/v1/futures/account/get_leverage_margin_mode"' in src
    assert '"/api/v1/futures/account/leverage_margin_mode"' not in src


def test_position_mode_endpoint_is_implemented():
    assert '"/api/v1/futures/account/position_mode"' in _client_source()


def test_trading_settings_endpoint_is_implemented():
    assert '"/api/v1/futures/account/trading_settings"' in _client_source()


def test_change_position_mode_sends_only_position_mode():
    """The docs list `positionMode` as the only parameter."""
    src = _client_source()
    tree = ast.parse(src)
    for fn in ast.walk(tree):
        if isinstance(fn, ast.AsyncFunctionDef) and fn.name == "change_position_mode":
            # inspect the body dict only - the docstring mentions marginCoin
            # precisely because it was removed
            keys: set[str] = set()
            for sub in ast.walk(fn):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                    if sub.func.attr == "request":
                        for kw in sub.keywords:
                            if kw.arg == "body" and isinstance(kw.value, ast.Dict):
                                for k in kw.value.keys:
                                    if isinstance(k, ast.Constant):
                                        keys.add(k.value)
            assert keys == {"positionMode"}, f"unexpected body keys: {keys}"
            return
    pytest.fail("change_position_mode not found")


def test_cancel_orders_requires_symbol():
    src = _client_source()
    tree = ast.parse(src)
    for fn in ast.walk(tree):
        if isinstance(fn, ast.AsyncFunctionDef) and fn.name == "cancel_orders":
            args = [a.arg for a in fn.args.args]
            assert "symbol" in args, "cancel_orders must take symbol (official: required)"
            return
    pytest.fail("cancel_orders not found")


def test_paging_uses_skip_and_limit_not_page():
    """The live server ignores page/pageSize; the docs say skip/limit."""
    src = _client_source()
    for fn_name in ("get_pending_orders", "get_history_orders",
                    "get_history_trades", "get_history_positions"):
        tree = ast.parse(src)
        found = False
        for fn in ast.walk(tree):
            if isinstance(fn, ast.AsyncFunctionDef) and fn.name == fn_name:
                found = True
                args = [a.arg for a in fn.args.args]
                assert "skip" in args and "limit" in args, f"{fn_name} must use skip/limit"
                assert "page" not in args, f"{fn_name} still uses page"
                assert "page_size" not in args, f"{fn_name} still uses page_size"
        assert found, f"{fn_name} not found"


def test_place_order_declares_the_official_required_set():
    from jrock.trader.client import BitunixClient
    assert set(BitunixClient.PLACE_ORDER_REQUIRED) == {
        "symbol", "qty", "side", "tradeSide", "orderType"}


def test_modify_order_declares_the_official_required_set():
    from jrock.trader.client import BitunixClient
    assert set(BitunixClient.MODIFY_ORDER_REQUIRED) == {"qty", "price"}


# ------------------------------------------------- reachability from commands

def test_new_endpoints_are_reachable_from_the_command_set():
    agent_src = open(os.path.join(ROOT, "jrock", "agent.py"), encoding="utf-8").read()
    assert '"/settings"' in agent_src, "/settings command not routed"
    assert "cmd_settings" in agent_src
    assert "get_trading_settings" in agent_src
    assert "get_position_mode" in agent_src


def test_settings_command_is_in_the_help_text():
    agent_src = open(os.path.join(ROOT, "jrock", "agent.py"), encoding="utf-8").read()
    assert "/settings" in agent_src
    # the help block lists it
    assert "account trading settings" in agent_src


def test_settings_alias_resolves():
    agent_src = open(os.path.join(ROOT, "jrock", "agent.py"), encoding="utf-8").read()
    assert '"settings": "/settings"' in agent_src
    assert '"trading_settings": "/settings"' in agent_src
