"""The dashboard must be a 1:1 view of the engine, not a subset of it.

These tests cover the features the dashboard used to be missing:

  * a structured state snapshot (engine state, equity, positions, PnL, open
    orders) with a last-updated stamp, refreshed every 30 seconds;
  * the autotrade toggle, including the engine's own safety confirmation;
  * every engine setting that used to be CLI/chat-only, written through the
    engine's command router so validation and persistence are identical;
  * the read/query commands (including the bare ``margin amount`` read)
    surfaced as dashboard readouts.

The point of most of these is the "don't drift" property: the dashboard must
not re-implement engine logic. Where a test can, it asserts that the dashboard
and the engine agree, rather than asserting a hardcoded copy of the value.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from jrock.agent import JRockAgent
from jrock.config import Settings
from jrock.web import (READOUT_COMMANDS, REPORT_INTERVAL_SEC, SETTING_COMMANDS,
                       SYMBOL_SETTING_COMMANDS, TOGGLE_COMMANDS, build_state,
                       make_app)

TOKEN = "test-token"

#: A realistic get_single_account row (every numeric field is a STRING on the
#: wire - see jrock/account.py).
ACCOUNT_ROW = {
    "marginCoin": "USDT", "available": "1000.5", "frozen": "0",
    "margin": "10", "transfer": "1000.5", "positionMode": "ONE_WAY",
    "crossUnrealizedPNL": "0", "isolationUnrealizedPNL": "2.5", "bonus": "0",
}

POSITION_ROW = {
    "symbol": "BTCUSDT", "side": "LONG", "qty": "0.01",
    "avgOpenPrice": "50000", "markPrice": "51000", "liqPrice": "45000",
    "leverage": "10", "margin": "50", "unrealizedPNL": "10",
    "realizedPNL": "0", "marginMode": "ISOLATION",
}

ORDER_ROW = {
    "symbol": "BTCUSDT", "side": "BUY", "orderType": "LIMIT", "qty": "0.01",
    "price": "49000", "status": "NEW", "orderId": "abc123",
}


def _agent(tmp_path, *, keys: bool = False) -> JRockAgent:
    s = Settings(symbols=["BTCUSDT"], market_data_source="rest")
    if keys:
        s.bitunix_api_key = "k"
        s.bitunix_api_secret = "s"
    a = JRockAgent(s)
    a.engine.data_dir = tmp_path
    return a


def _stub_client(agent: JRockAgent, *, account=None, positions=None,
                 orders=None, ok=True) -> None:
    """Replace the network calls with canned rows.

    No test here touches the network: the dashboard's job is to render what the
    client returns, and that is what is under test.
    """
    async def _account(*a, **k):
        return account if account is not None else []

    async def _positions(*a, **k):
        return positions if positions is not None else []

    async def _orders(*a, **k):
        return orders if orders is not None else []

    async def _verify():
        return (ok, "OK - account reachable (1 margin coin(s))")

    agent.engine.client.get_single_account = _account
    agent.engine.client.get_pending_positions = _positions
    agent.engine.client.get_pending_orders = _orders
    agent.engine.client.verify_credentials = _verify


def _run(coro):
    return asyncio.run(coro)


async def _with_client(tmp_path, fn, *, keys: bool = False, **stub):
    agent = _agent(tmp_path, keys=keys)
    _stub_client(agent, **stub)
    app = make_app(agent, TOKEN)
    # Skip the startup hook: it calls the network (model probe, Bitunix auth).
    app.on_startup.clear()
    app.on_cleanup.clear()
    try:
        async with TestClient(TestServer(app)) as client:
            return await fn(client, agent)
    finally:
        # autotrade ON starts the engine loops; never leak them into the suite.
        await agent.engine.stop()


# ============================================================ the 30s report
def test_report_interval_is_30_seconds():
    """The dashboard's refresh cadence is the documented 30 seconds."""
    assert REPORT_INTERVAL_SEC == 30


def test_report_interval_matches_the_engine_default():
    """The page and the engine must agree on what a report interval is.

    If the engine's default report interval ever moves, the dashboard's
    hardcoded 30 would silently disagree with it - this test is the tripwire.
    """
    from jrock.trader.engine import EngineState

    assert EngineState().report_interval == REPORT_INTERVAL_SEC


def test_index_embeds_the_refresh_loop_and_last_updated_indicator(tmp_path):
    """The page really polls: the interval, the timer and the stamp are served."""
    async def go(client, agent):
        r = await client.get(f"/?token={TOKEN}")
        assert r.status == 200
        html = await r.text()
        assert f"const REPORT_INTERVAL_SEC = {REPORT_INTERVAL_SEC};" in html
        assert "setInterval(refresh, REPORT_INTERVAL_SEC * 1000)" in html
        assert 'id="updated"' in html
        assert "last updated" in html
        return html
    _run(_with_client(tmp_path, go))


def test_state_carries_a_last_updated_stamp(tmp_path):
    async def go(client, agent):
        r = await client.get("/api/state", headers={"X-Auth-Token": TOKEN})
        assert r.status == 200
        body = await r.json()
        assert body["updated_at"]
        assert isinstance(body["updated_ts"], (int, float))
        assert body["report_interval"] == REPORT_INTERVAL_SEC
        return body
    _run(_with_client(tmp_path, go))


def test_state_stamp_advances_between_calls(tmp_path):
    """A second read is a fresh read, not a cached constant."""
    async def go(client, agent):
        first = await (await client.get("/api/state",
                                        headers={"X-Auth-Token": TOKEN})).json()
        await asyncio.sleep(1.1)
        second = await (await client.get("/api/state",
                                         headers={"X-Auth-Token": TOKEN})).json()
        assert second["updated_ts"] > first["updated_ts"]
        return second
    _run(_with_client(tmp_path, go))


# ================================================================== the state
def test_state_reports_engine_state(tmp_path):
    async def go(client, agent):
        body = await (await client.get("/api/state",
                                       headers={"X-Auth-Token": TOKEN})).json()
        e = body["engine"]
        s = agent.engine.state
        # The dashboard must mirror the engine, not a copy of its defaults.
        assert e["engine_on"] == s.engine_on
        assert e["autotrade"] == s.autotrade
        assert e["scan_on"] == s.scan_on
        assert e["report_on"] == s.report_on
        assert e["mid_management"] == s.mid_management
        assert e["symbols"] == list(s.symbols)
        assert e["leverage"] == s.leverage
        assert e["tpsl_method"] == s.tpsl_method
        assert e["errors"] == len(s.errors)
        return body
    _run(_with_client(tmp_path, go))


def test_state_reports_equity_positions_pnl_and_orders(tmp_path):
    async def go(client, agent):
        body = await (await client.get("/api/state",
                                       headers={"X-Auth-Token": TOKEN})).json()
        a = body["account"]
        # equity = available + frozen + margin + unrealized (jrock/account.py)
        assert a["equity"] == pytest.approx(1000.5 + 0 + 10 + 2.5)
        assert a["available"] == pytest.approx(1000.5)
        assert a["margin"] == pytest.approx(10)
        assert a["unrealized"] == pytest.approx(2.5)
        assert a["open_positions"] == 1
        assert a["open_orders"] == 1
        assert a["api_ok"] is True

        p = body["positions"][0]
        assert p["symbol"] == "BTCUSDT"
        assert p["side"] == "LONG"
        assert p["leverage"] == 10
        # 50000 -> 51000 at 10x = +20% ROI, computed by the engine's own roi_pct
        assert p["roi"] == pytest.approx(20.0)
        assert p["unrealized"] == pytest.approx(10.0)

        o = body["orders"][0]
        assert o["symbol"] == "BTCUSDT"
        assert o["type"] == "LIMIT"
        assert o["id"] == "abc123"
        return body
    _run(_with_client(tmp_path, go, keys=True, account=[ACCOUNT_ROW],
                       positions=[POSITION_ROW], orders=[ORDER_ROW]))


def test_state_roi_matches_the_engine_helper(tmp_path):
    """The dashboard's ROI is the engine's ROI, not a second formula."""
    from jrock.trader.risk import roi_pct

    async def go(client, agent):
        body = await (await client.get("/api/state",
                                       headers={"X-Auth-Token": TOKEN})).json()
        expected = roi_pct(50000.0, 51000.0, "LONG", 10)
        assert body["positions"][0]["roi"] == pytest.approx(round(expected, 2))
        return body
    _run(_with_client(tmp_path, go, keys=True, positions=[POSITION_ROW]))


def test_state_is_honest_without_api_keys(tmp_path):
    """No keys must say so, not invent a zero balance."""
    async def go(client, agent):
        body = await (await client.get("/api/state",
                                       headers={"X-Auth-Token": TOKEN})).json()
        assert body["account"]["api_ok"] is False
        assert "not set" in body["account"]["api_msg"]
        assert body["positions"] == []
        assert body["orders"] == []
        return body
    _run(_with_client(tmp_path, go))


def test_state_requires_a_token(tmp_path):
    async def go(client, agent):
        assert (await client.get("/api/state")).status == 401
        assert (await client.get("/api/state",
                                 headers={"X-Auth-Token": "nope"})).status == 401
    _run(_with_client(tmp_path, go))


def test_build_state_is_reusable_without_http(tmp_path):
    """build_state is the single source of the snapshot (used by the tests too)."""
    async def go():
        agent = _agent(tmp_path, keys=True)
        _stub_client(agent, account=[ACCOUNT_ROW])
        try:
            body = await build_state(agent)
            assert body["account"]["equity"] == pytest.approx(1013.0)
            assert "engine" in body and "settings" in body and "readouts" in body
        finally:
            await agent.engine.stop()
    _run(go())


# ============================================================== margin plan
def test_margin_plan_shows_the_percentage_as_usdt(tmp_path):
    """The dashboard must show what MARGIN_AMOUNT_PCT means in money.

    The user's complaint was that the margin on a real position did not match
    the percentage they set. A bare "10" in a settings box cannot be compared
    against a position's margin; "10% = 100.05 USDT" can.
    """
    async def go():
        agent = _agent(tmp_path, keys=True)
        _stub_client(agent, account=[ACCOUNT_ROW])
        try:
            agent.engine.state.margin_amount_pct = 10.0
            agent.engine.state.leverage = 20
            body = await build_state(agent)
            mp = body["margin_plan"]
            assert mp["pct"] == 10.0
            assert mp["enabled"] is True
            # sized from AVAILABLE (spendable), not equity
            assert mp["available"] == pytest.approx(1000.5)
            assert mp["target_usdt"] == pytest.approx(100.05)
            # leverage changes the notional, never the margin
            assert mp["target_notional"] == pytest.approx(100.05 * 20)
            assert "100.05" in mp["note"]
        finally:
            await agent.engine.stop()
    _run(go())


def test_margin_plan_reports_disabled_at_zero(tmp_path):
    """0 disables the knob, and the dashboard must say so rather than show 0."""
    async def go():
        agent = _agent(tmp_path, keys=True)
        _stub_client(agent, account=[ACCOUNT_ROW])
        try:
            agent.engine.state.margin_amount_pct = 0.0
            mp = (await build_state(agent))["margin_plan"]
            assert mp["enabled"] is False
            assert mp["target_usdt"] == 0.0
            assert "risk_pct" in mp["note"]
        finally:
            await agent.engine.stop()
    _run(go())


def test_margin_plan_tracks_the_setting_written_through_the_engine(tmp_path):
    """Writing the setting must move the derived USDT amount with it.

    This is the anti-drift guard: the dashboard must not compute the plan from
    its own copy of the value.
    """
    async def go():
        agent = _agent(tmp_path, keys=True)
        _stub_client(agent, account=[ACCOUNT_ROW])
        try:
            await agent.handle("margin amount 25")
            mp = (await build_state(agent))["margin_plan"]
            assert mp["pct"] == pytest.approx(25.0)
            assert mp["target_usdt"] == pytest.approx(1000.5 * 0.25)
        finally:
            await agent.engine.stop()
    _run(go())


# ============================================================== autotrade
def test_autotrade_on_requires_confirmation(tmp_path):
    """Turning autotrade ON places real orders - it must not be a bare POST."""
    async def go(client, agent):
        r = await client.post("/api/autotrade", json={"on": True},
                              headers={"X-Auth-Token": TOKEN})
        assert r.status == 409
        body = await r.json()
        assert body["error"] == "confirmation required"
        assert agent.engine.state.autotrade is False
        return body
    _run(_with_client(tmp_path, go))


def test_autotrade_on_with_confirmation_starts_trading(tmp_path):
    async def go(client, agent):
        r = await client.post("/api/autotrade", json={"on": True, "confirm": True},
                              headers={"X-Auth-Token": TOKEN})
        assert r.status == 200
        body = await r.json()
        assert body["state"]["autotrade"] is True
        assert agent.engine.state.autotrade is True
        # The engine's own command is what ran, so its wording is what comes back.
        assert "Autotrade ON" in body["output"]
        return body
    _run(_with_client(tmp_path, go))


def test_autotrade_off_needs_no_confirmation(tmp_path):
    async def go(client, agent):
        await client.post("/api/autotrade", json={"on": True, "confirm": True},
                          headers={"X-Auth-Token": TOKEN})
        r = await client.post("/api/autotrade", json={"on": False},
                              headers={"X-Auth-Token": TOKEN})
        assert r.status == 200
        body = await r.json()
        assert body["state"]["autotrade"] is False
        assert agent.engine.state.autotrade is False
        return body
    _run(_with_client(tmp_path, go))


def test_autotrade_state_is_visible_in_the_snapshot(tmp_path):
    """The toggle's current state must be shown, not just settable."""
    async def go(client, agent):
        before = await (await client.get("/api/state",
                                         headers={"X-Auth-Token": TOKEN})).json()
        assert before["engine"]["autotrade"] is False
        await client.post("/api/autotrade", json={"on": True, "confirm": True},
                          headers={"X-Auth-Token": TOKEN})
        after = await (await client.get("/api/state",
                                        headers={"X-Auth-Token": TOKEN})).json()
        assert after["engine"]["autotrade"] is True
        return after
    _run(_with_client(tmp_path, go))


def test_autotrade_rejects_a_non_boolean(tmp_path):
    async def go(client, agent):
        r = await client.post("/api/autotrade", json={"on": "yes"},
                              headers={"X-Auth-Token": TOKEN})
        assert r.status == 400
        assert agent.engine.state.autotrade is False
    _run(_with_client(tmp_path, go))


def test_autotrade_requires_a_token(tmp_path):
    async def go(client, agent):
        r = await client.post("/api/autotrade", json={"on": True, "confirm": True})
        assert r.status == 401
        assert agent.engine.state.autotrade is False
    _run(_with_client(tmp_path, go))


def test_autotrade_restart_warning_is_exposed(tmp_path):
    """The engine refuses to auto-resume autotrade; the dashboard must say so."""
    async def go(client, agent):
        agent.engine.autotrade_was_on = True
        body = await (await client.get("/api/state",
                                       headers={"X-Auth-Token": TOKEN})).json()
        assert body["engine"]["autotrade_was_on"] is True
        # ...and it is still OFF: the flag is a warning, not a resume.
        assert body["engine"]["autotrade"] is False
        return body
    _run(_with_client(tmp_path, go))


# =============================================================== settings
def test_every_setting_command_is_accepted_by_the_engine(tmp_path):
    """The dashboard's setting table must not name a command the engine rejects.

    This is the anti-drift test: it feeds each template's command straight to
    ``engine.command()`` and fails if the engine answers "Unknown engine
    command" - which is exactly how a dashboard control would silently do
    nothing.
    """
    async def go():
        agent = _agent(tmp_path)
        try:
            for key, template in SETTING_COMMANDS.items():
                cmd = template.format(v="1")
                out = await agent.engine.command(cmd)
                assert "Unknown engine command" not in out, (key, cmd, out)
        finally:
            await agent.engine.stop()
    _run(go())


def test_settings_endpoint_writes_through_the_engine(tmp_path):
    async def go(client, agent):
        r = await client.post("/api/settings",
                              json={"key": "partial_at_roi_pct", "value": 20},
                              headers={"X-Auth-Token": TOKEN})
        assert r.status == 200
        body = await r.json()
        assert body["command"] == "partial at roi 20"
        assert agent.engine.state.partial_at_roi_pct == 20.0
        return body
    _run(_with_client(tmp_path, go))


def test_settings_endpoint_covers_the_cli_only_settings(tmp_path):
    """The settings that used to be reachable only from the CLI/chat."""
    async def go(client, agent):
        cases = {
            "margin_amount_pct": ("margin amount 15", "margin_amount_pct", 15.0),
            "leverage": ("leverage 5", "leverage", 5),
            "risk_pct": ("risk 2", "risk_pct", 2.0),
            "partial_close_pct": ("partial close pct 30", "partial_close_pct", 30.0),
            "partial_at_roi_pct": ("partial at roi 25", "partial_at_roi_pct", 25.0),
            "max_open_positions": ("max open positions 5", "max_open_positions", 5),
            "min_agreeing": ("min agreeing strategies 4", "min_agreeing", 4),
            "min_confidence": ("min confidence 80", "min_confidence", 80.0),
            "breakeven_pct": ("breakeven threshold pct 1", "breakeven_pct", 1.0),
            "trailing_trigger_roi_pct": ("trailing trigger stop ROI pct 30",
                                         "trailing_trigger_roi_pct", 30.0),
            "liq_sl_distance_pct": ("liq sl distance pct 40", "liq_sl_distance_pct", 40.0),
            "account_tp_usdt": ("account tp usdt 100", "account_tp_usdt", 100.0),
            "account_sl_usdt": ("account sl usdt 50", "account_sl_usdt", 50.0),
            "account_tp_pct": ("account tp pct 5", "account_tp_pct", 5.0),
            "account_sl_pct": ("account sl pct 3", "account_sl_pct", 3.0),
            "scan_interval": ("scans interval sec 60", "scan_interval", 60),
            "report_interval": ("report interval sec 45", "report_interval", 45),
            "reversal_confidence": ("reversal_confidence=80", "reversal_confidence", 80.0),
        }
        for key, (expected_cmd, attr, expected) in cases.items():
            r = await client.post("/api/settings", json={"key": key, "value": expected},
                                  headers={"X-Auth-Token": TOKEN})
            assert r.status == 200, (key, await r.text())
            body = await r.json()
            assert body["command"] == expected_cmd, key
            assert getattr(agent.engine.state, attr) == expected, key
        return cases
    _run(_with_client(tmp_path, go))


def test_settings_endpoint_sets_the_tpsl_method(tmp_path):
    async def go(client, agent):
        r = await client.post("/api/settings",
                              json={"key": "tpsl_method", "value": "PARTIAL"},
                              headers={"X-Auth-Token": TOKEN})
        assert r.status == 200
        assert agent.engine.state.tpsl_method == "PARTIAL"
        return await r.json()
    _run(_with_client(tmp_path, go))


def test_settings_endpoint_sets_the_tp_ladder(tmp_path):
    async def go(client, agent):
        r = await client.post("/api/settings",
                              json={"key": "tp_ladder", "value": "1R:25,2R:25,3R:25"},
                              headers={"X-Auth-Token": TOKEN})
        assert r.status == 200
        assert agent.engine.state.tp_ladder == "1R:25,2R:25,3R:25"
        # The engine's own parser must accept it, not just the string be stored.
        lad = agent.engine.ladder()
        assert len(lad.levels) == 3
        return await r.json()
    _run(_with_client(tmp_path, go))


def test_settings_endpoint_rejects_an_invalid_ladder(tmp_path):
    """A bad ladder must leave the previous one in place (engine behaviour)."""
    async def go(client, agent):
        await client.post("/api/settings",
                          json={"key": "tp_ladder", "value": "1R:25,2R:25"},
                          headers={"X-Auth-Token": TOKEN})
        before = agent.engine.state.tp_ladder
        r = await client.post("/api/settings",
                              json={"key": "tp_ladder", "value": "not-a-ladder"},
                              headers={"X-Auth-Token": TOKEN})
        assert r.status == 200
        body = await r.json()
        assert "Invalid ladder" in body["output"]
        assert agent.engine.state.tp_ladder == before
        return body
    _run(_with_client(tmp_path, go))


def test_settings_endpoint_rejects_an_unknown_key(tmp_path):
    async def go(client, agent):
        r = await client.post("/api/settings", json={"key": "nope", "value": 1},
                              headers={"X-Auth-Token": TOKEN})
        assert r.status == 400
        body = await r.json()
        assert "unknown setting" in body["error"]
        assert "allowed" in body
    _run(_with_client(tmp_path, go))


def test_settings_endpoint_rejects_a_missing_value(tmp_path):
    async def go(client, agent):
        r = await client.post("/api/settings", json={"key": "leverage"},
                              headers={"X-Auth-Token": TOKEN})
        assert r.status == 400
    _run(_with_client(tmp_path, go))


def test_settings_endpoint_requires_a_token(tmp_path):
    async def go(client, agent):
        r = await client.post("/api/settings", json={"key": "leverage", "value": 5})
        assert r.status == 401
        assert agent.engine.state.leverage == 10
    _run(_with_client(tmp_path, go))


def test_settings_are_persisted_like_the_cli(tmp_path):
    """A dashboard change must survive a restart, exactly as a CLI change does."""
    async def go(client, agent):
        await client.post("/api/settings", json={"key": "leverage", "value": 7},
                          headers={"X-Auth-Token": TOKEN})
        assert agent.store.section("engine").get("leverage") == 7
        return agent.store.section("engine")
    _run(_with_client(tmp_path, go))


def test_settings_snapshot_lists_every_writable_setting(tmp_path):
    """The UI is generated from the snapshot, so it must list them all."""
    async def go(client, agent):
        body = await (await client.get("/api/state",
                                       headers={"X-Auth-Token": TOKEN})).json()
        keys = {f["key"] for f in body["settings"]}
        # Every numeric/string setting the dashboard can write is offered...
        for key in ("margin_amount_pct", "leverage", "partial_at_roi_pct",
                    "risk_pct", "max_open_positions", "tp_ladder",
                    "tp_ladder_breakeven_after", "symbols", "timeframes"):
            assert key in keys, key
        # ...and each carries its current value, not a placeholder.
        by_key = {f["key"]: f for f in body["settings"]}
        assert by_key["leverage"]["value"] == agent.engine.state.leverage
        assert by_key["margin_amount_pct"]["value"] == agent.engine.state.margin_amount_pct
        return body
    _run(_with_client(tmp_path, go))


# ================================================================ toggles
def test_toggle_flips_scan_and_report(tmp_path):
    async def go(client, agent):
        r = await client.post("/api/toggle", json={"key": "scan_on"},
                              headers={"X-Auth-Token": TOKEN})
        assert r.status == 200
        assert agent.engine.state.scan_on is False
        r = await client.post("/api/toggle", json={"key": "scan_on"},
                              headers={"X-Auth-Token": TOKEN})
        assert agent.engine.state.scan_on is True

        r = await client.post("/api/toggle", json={"key": "report_on"},
                              headers={"X-Auth-Token": TOKEN})
        assert agent.engine.state.report_on is True
        return await r.json()
    _run(_with_client(tmp_path, go))


def test_toggle_flips_reversal(tmp_path):
    async def go(client, agent):
        await client.post("/api/toggle", json={"key": "reversal"},
                          headers={"X-Auth-Token": TOKEN})
        assert agent.engine.state.reversal is True
        await client.post("/api/toggle", json={"key": "reversal"},
                          headers={"X-Auth-Token": TOKEN})
        assert agent.engine.state.reversal is False
    _run(_with_client(tmp_path, go))


def test_toggle_rejects_an_unknown_key(tmp_path):
    async def go(client, agent):
        r = await client.post("/api/toggle", json={"key": "nope"},
                              headers={"X-Auth-Token": TOKEN})
        assert r.status == 400
    _run(_with_client(tmp_path, go))


def test_toggle_requires_a_token(tmp_path):
    async def go(client, agent):
        r = await client.post("/api/toggle", json={"key": "scan_on"})
        assert r.status == 401
        assert agent.engine.state.scan_on is True
    _run(_with_client(tmp_path, go))


def test_toggle_table_names_real_engine_commands(tmp_path):
    """Every toggle's commands must be ones the engine actually understands."""
    async def go():
        agent = _agent(tmp_path)
        try:
            for key, (on_cmd, off_cmd) in TOGGLE_COMMANDS.items():
                for cmd in (on_cmd, off_cmd):
                    out = await agent.engine.command(cmd)
                    assert "Unknown engine command" not in out, (key, cmd, out)
        finally:
            await agent.engine.stop()
    _run(go())


# =============================================================== readouts
def test_readouts_include_the_bare_margin_amount_read(tmp_path):
    """`margin amount` with no argument is a READ, and the dashboard shows it."""
    async def go(client, agent):
        body = await (await client.get("/api/state",
                                       headers={"X-Auth-Token": TOKEN})).json()
        text = body["readouts"]["margin_amount"]
        assert "Margin per trade" in text
        assert f"{agent.engine.state.margin_amount_pct:g}%" in text
        return body
    _run(_with_client(tmp_path, go))


def test_readouts_include_status_settings_ladder_and_tpsl(tmp_path):
    async def go(client, agent):
        body = await (await client.get("/api/state",
                                       headers={"X-Auth-Token": TOKEN})).json()
        ro = body["readouts"]
        assert "=== status ===" in ro["status"]
        assert "=== TP ladder ===" in ro["ladder"]
        assert "TP/SL method" in ro["tpsl"]
        # `settings trade` is the engine's documented "show trading settings".
        assert ro["settings"]
        return body
    _run(_with_client(tmp_path, go))


def test_readouts_match_what_the_cli_prints(tmp_path):
    """The readout is the CLI's own output, not a re-rendering of it."""
    async def go(client, agent):
        body = await (await client.get("/api/state",
                                       headers={"X-Auth-Token": TOKEN})).json()
        for name, cmd in READOUT_COMMANDS.items():
            expected = await agent.handle(cmd)
            assert body["readouts"][name] == expected, name
        return body
    _run(_with_client(tmp_path, go))


def test_readouts_cover_every_declared_command(tmp_path):
    async def go(client, agent):
        body = await (await client.get("/api/state",
                                       headers={"X-Auth-Token": TOKEN})).json()
        assert set(body["readouts"]) == set(READOUT_COMMANDS)
        return body
    _run(_with_client(tmp_path, go))


def test_every_readout_command_is_a_real_command(tmp_path):
    """A readout must not name a command the agent does not have.

    A typo here would show "Unknown command /foo" in a dashboard panel, which
    looks like a broken engine rather than a broken dashboard.
    """
    async def go():
        agent = _agent(tmp_path)
        try:
            for name, cmd in READOUT_COMMANDS.items():
                out = await agent.handle(cmd)
                assert "Unknown command" not in out, (name, cmd, out)
                assert "Unknown engine command" not in out, (name, cmd, out)
        finally:
            await agent.engine.stop()
    _run(go())


def test_readouts_never_raise_on_a_failing_client(tmp_path):
    """A broken exchange call must degrade to an error string, not a 500."""
    async def go(client, agent):
        async def boom(*a, **k):
            raise RuntimeError("exchange down")
        agent.engine.client.get_single_account = boom
        agent.engine.client.get_pending_positions = boom
        agent.engine.client.get_pending_orders = boom
        agent.engine.client.verify_credentials = boom
        r = await client.get("/api/state", headers={"X-Auth-Token": TOKEN})
        assert r.status == 200
        body = await r.json()
        assert body["account"]["api_ok"] is False
        assert "exchange down" in body["account"]["api_msg"]
        return body
    _run(_with_client(tmp_path, go, keys=True))


# ============================================================ margin adjust
def test_margin_add_and_reduce_are_real_engine_commands(tmp_path):
    """HELP documents `margin add`/`margin reduce`; the router must accept them.

    They were documented and the client implemented `adjust_position_margin`,
    but the engine never wired the command - so the documented form answered
    "Unknown engine command". This is the regression guard.
    """
    async def go():
        agent = _agent(tmp_path)
        try:
            for key, template in SYMBOL_SETTING_COMMANDS.items():
                cmd = template.format(s="BTCUSDT", v="100")
                out = await agent.engine.command(cmd)
                assert "Unknown engine command" not in out, (key, cmd, out)
        finally:
            await agent.engine.stop()
    _run(go())


def test_margin_add_requires_a_symbol_and_an_amount(tmp_path):
    async def go():
        agent = _agent(tmp_path)
        try:
            out = await agent.engine.command("margin add")
            assert "Usage:" in out
            out = await agent.engine.command("margin add BTCUSDT")
            assert "Usage:" in out
        finally:
            await agent.engine.stop()
    _run(go())


def test_margin_add_refuses_a_cross_position(tmp_path):
    """The endpoint is isolated-only; refuse locally with a reason, not an API error."""
    async def go():
        agent = _agent(tmp_path)
        try:
            agent.engine.state.margin_mode = "CROSS"
            out = await agent.engine.command("margin add BTCUSDT 100")
            assert "ISOLATED" in out
            assert "margin mode ISOLATION" in out
        finally:
            await agent.engine.stop()
    _run(go())


def test_margin_add_reports_no_open_position(tmp_path):
    async def go():
        agent = _agent(tmp_path)
        try:
            agent.engine.state.margin_mode = "ISOLATION"
            out = await agent.engine.command("margin add BTCUSDT 100")
            assert "No open BTCUSDT position" in out
        finally:
            await agent.engine.stop()
    _run(go())


def test_margin_add_calls_the_client_with_the_real_position_id(tmp_path):
    """The call must carry the position's own id/side, not a guess."""
    async def go():
        agent = _agent(tmp_path)
        calls = []

        async def _positions(*a, **k):
            return [{"symbol": "BTCUSDT", "side": "LONG", "positionId": "pid-9",
                     "qty": "0.01"}]

        async def _adjust(**kw):
            calls.append(kw)
            return {}

        agent.engine.client.get_pending_positions = _positions
        agent.engine.client.adjust_position_margin = _adjust
        try:
            agent.engine.state.margin_mode = "ISOLATION"
            out = await agent.engine.command("margin add BTCUSDT 100")
            assert "Added 100 USDT" in out
            assert calls == [{"symbol": "BTCUSDT", "amount": "100.0",
                              "position_id": "pid-9", "side": "LONG"}]

            out = await agent.engine.command("margin reduce BTCUSDT 50")
            assert "Reduced 50 USDT" in out
            # reduce is a NEGATIVE amount on the same endpoint
            assert calls[-1]["amount"] == "-50.0"
        finally:
            await agent.engine.stop()
    _run(go())


def test_margin_endpoint_adds_and_reduces(tmp_path):
    async def go(client, agent):
        calls = []

        async def _positions(*a, **k):
            return [{"symbol": "BTCUSDT", "side": "LONG", "positionId": "pid-9"}]

        async def _adjust(**kw):
            calls.append(kw)
            return {}

        agent.engine.client.get_pending_positions = _positions
        agent.engine.client.adjust_position_margin = _adjust
        agent.engine.state.margin_mode = "ISOLATION"

        r = await client.post("/api/margin",
                              json={"action": "add", "symbol": "btcusdt", "amount": 100},
                              headers={"X-Auth-Token": TOKEN})
        assert r.status == 200
        body = await r.json()
        assert body["command"] == "margin add BTCUSDT 100"
        assert calls[-1]["amount"] == "100.0"

        r = await client.post("/api/margin",
                              json={"action": "reduce", "symbol": "BTCUSDT", "amount": 50},
                              headers={"X-Auth-Token": TOKEN})
        assert r.status == 200
        assert calls[-1]["amount"] == "-50.0"
        return body
    _run(_with_client(tmp_path, go))


def test_margin_endpoint_validates_its_input(tmp_path):
    async def go(client, agent):
        for payload in ({"action": "nope", "symbol": "BTCUSDT", "amount": 1},
                        {"action": "add", "amount": 1},
                        {"action": "add", "symbol": "BTCUSDT"},
                        {"action": "add", "symbol": "  ", "amount": 1}):
            r = await client.post("/api/margin", json=payload,
                                  headers={"X-Auth-Token": TOKEN})
            assert r.status == 400, payload
    _run(_with_client(tmp_path, go))


def test_margin_endpoint_requires_a_token(tmp_path):
    async def go(client, agent):
        r = await client.post("/api/margin",
                              json={"action": "add", "symbol": "BTCUSDT", "amount": 1})
        assert r.status == 401
    _run(_with_client(tmp_path, go))


def test_state_reports_strategies_and_permissions(tmp_path):
    """The engine's strategy registry and the agent's permission flags."""
    from jrock.trader.strategies import registry

    async def go(client, agent):
        body = await (await client.get("/api/state",
                                       headers={"X-Auth-Token": TOKEN})).json()
        sg = body["strategies"]
        assert sg["count"] == len(registry())
        assert sum(sg["by_category"].values()) == sg["count"]
        assert sg["names"] == sorted(registry())

        pm = body["permissions"]
        assert pm["auto_accept_edit"] == agent.perms.auto_accept_edit
        assert pm["full_device_access"] == agent.perms.full_device_access
        assert pm["block_destructive"] == agent.perms.block_destructive
        return body
    _run(_with_client(tmp_path, go))


def test_index_renders_the_strategies_panel(tmp_path):
    async def go(client, agent):
        html = await (await client.get(f"/?token={TOKEN}")).text()
        assert 'id="strategies"' in html
        assert "Strategies &amp; permissions" in html
    _run(_with_client(tmp_path, go))


def test_index_script_is_syntactically_valid(tmp_path):
    """The page's inline JS must parse - a broken script is a dead dashboard.

    The dashboard is generated by string substitution, so a stray edit can
    silently produce invalid JavaScript that no Python test would notice.
    """
    import re
    import shutil
    import subprocess
    import tempfile

    async def go(client, agent):
        html = await (await client.get(f"/?token={TOKEN}")).text()
        m = re.search(r"<script>(.*?)</script>", html, re.S)
        assert m, "no inline script found"
        script = m.group(1)
        node = shutil.which("node")
        if not node:
            pytest.skip("node not available to syntax-check the dashboard script")
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(script)
            path = fh.name
        p = subprocess.run([node, "--check", path], capture_output=True, text=True)
        assert p.returncode == 0, p.stderr
        return script
    _run(_with_client(tmp_path, go))


# ======================================================= existing behaviour
def test_existing_command_endpoint_still_works(tmp_path):
    """The original endpoint must not regress."""
    async def go(client, agent):
        r = await client.post("/api/command", json={"line": "/help"},
                              headers={"X-Auth-Token": TOKEN})
        assert r.status == 200
        assert "J+Rock commands" in (await r.json())["output"]
    _run(_with_client(tmp_path, go))


def test_existing_index_still_serves_the_dashboard(tmp_path):
    async def go(client, agent):
        r = await client.get(f"/?token={TOKEN}")
        assert r.status == 200
        assert "J+Rock dashboard" in await r.text()
    _run(_with_client(tmp_path, go))


def test_state_snapshot_is_json_serialisable(tmp_path):
    """The snapshot goes over the wire; it must not carry a non-JSON value."""
    async def go(client, agent):
        body = await (await client.get("/api/state",
                                       headers={"X-Auth-Token": TOKEN})).json()
        json.dumps(body)  # would raise on a set/datetime/Decimal
        return body
    _run(_with_client(tmp_path, go, keys=True, account=[ACCOUNT_ROW],
                       positions=[POSITION_ROW], orders=[ORDER_ROW]))
