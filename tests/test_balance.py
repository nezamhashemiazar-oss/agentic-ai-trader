"""Regression tests for the `balance` command and the bare-word routing table.

Bug A: `balance` was not a command, so it fell through to the model, which
returned nothing. Every word a trader would naturally type must land on a real
handler.
"""
from __future__ import annotations

import asyncio

import pytest

from jrock.account import (BalanceReport, MarginAccount, is_demo_url,
                           missing_keys_message, parse_accounts, render_balance)
from jrock.agent import AGENT_BARE, FREE_TEXT_WORDS, HELP, JRockAgent
from jrock.config import Settings
from jrock.trader.errors import BitunixAPIError

MOCK_ACCOUNT = [{
    "marginCoin": "USDT", "available": "1000", "frozen": "0", "margin": "10",
    "transfer": "1000", "positionMode": "HEDGE", "crossUnrealizedPNL": "2",
    "isolationUnrealizedPNL": "0", "bonus": "0",
}]


def _agent(tmp_path, **kw) -> JRockAgent:
    s = Settings(**kw)
    s.data_dir  # noqa: B018
    a = JRockAgent(s)
    a.engine.data_dir = tmp_path
    return a


def _run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------ account parsing
def test_parse_accounts_reads_documented_fields():
    accts = parse_accounts(MOCK_ACCOUNT)
    assert len(accts) == 1
    a = accts[0]
    assert a.margin_coin == "USDT"
    assert a.available == 1000.0
    assert a.margin == 10.0
    assert a.unrealized == 2.0
    assert a.equity == 1012.0          # 1000 + 0 + 10 + 2
    assert a.position_mode == "HEDGE"


def test_parse_accounts_tolerates_single_dict():
    accts = parse_accounts(MOCK_ACCOUNT[0])
    assert len(accts) == 1


def test_parse_accounts_handles_garbage_without_raising():
    accts = parse_accounts([{"marginCoin": "USDT", "available": "not-a-number",
                             "margin": None}])
    assert accts[0].available == 0.0
    assert accts[0].margin == 0.0


def test_parse_accounts_empty():
    assert parse_accounts(None) == []
    assert parse_accounts([]) == []


def test_equity_sums_multiple_coins():
    r = BalanceReport(accounts=parse_accounts([
        {"marginCoin": "USDT", "available": "100", "margin": "0",
         "crossUnrealizedPNL": "1"},
        {"marginCoin": "USDC", "available": "50", "margin": "0",
         "crossUnrealizedPNL": "0"},
    ]))
    assert r.total_equity == 151.0
    assert r.total_unrealized == 1.0


def test_is_demo_url():
    assert is_demo_url("https://demo-fapi.bitunix.com") is True
    assert is_demo_url("https://fapi.bitunix.com") is False


# --------------------------------------------------------------- rendering
def test_render_balance_has_all_columns():
    out = render_balance(BalanceReport(accounts=parse_accounts(MOCK_ACCOUNT),
                                       is_demo=False,
                                       base_url="https://fapi.bitunix.com"))
    for col in ("asset", "available", "frozen", "margin", "unrealized", "equity"):
        assert col in out
    assert "USDT" in out
    assert "1012.0000" in out
    assert "LIVE" in out


def test_render_balance_marks_demo():
    out = render_balance(BalanceReport(accounts=parse_accounts(MOCK_ACCOUNT),
                                       is_demo=True,
                                       base_url="https://demo-fapi.bitunix.com"))
    assert "DEMO" in out


def test_render_balance_empty_is_explicit():
    out = render_balance(BalanceReport(accounts=[]))
    assert "No account data" in out
    assert out.strip() != ""


# ------------------------------------------------------- balance command
def test_balance_without_keys_names_the_env_vars(tmp_path):
    a = _agent(tmp_path, bitunix_api_key="", bitunix_api_secret="")
    out = _run(a.handle("balance"))
    assert "BITUNIX_API_KEY" in out
    assert "BITUNIX_API_SECRET" in out
    # must NOT have gone to the model
    assert "You said" not in out
    assert "model" not in out.lower()


def test_slash_balance_without_keys(tmp_path):
    a = _agent(tmp_path, bitunix_api_key="", bitunix_api_secret="")
    out = _run(a.handle("/balance"))
    assert "BITUNIX_API_KEY" in out


def test_balance_with_mocked_response_renders_table(tmp_path, monkeypatch):
    a = _agent(tmp_path, bitunix_api_key="k", bitunix_api_secret="s")

    async def fake_account(margin_coin="USDT"):
        return MOCK_ACCOUNT

    monkeypatch.setattr(a.engine.client, "get_single_account", fake_account)
    out = _run(a.handle("balance"))
    assert "=== account balance (LIVE) ===" in out
    assert "USDT" in out
    assert "1000.0000" in out
    assert "1012.0000" in out


def test_balance_api_error_shows_code_and_message(tmp_path, monkeypatch):
    a = _agent(tmp_path, bitunix_api_key="k", bitunix_api_secret="s")

    async def boom(margin_coin="USDT"):
        raise BitunixAPIError(10003, "Token invalid")

    monkeypatch.setattr(a.engine.client, "get_single_account", boom)
    out = _run(a.handle("balance"))
    assert "10003" in out
    assert "Token invalid" in out
    assert out.strip() != ""


def test_balance_never_returns_empty_string(tmp_path, monkeypatch):
    a = _agent(tmp_path, bitunix_api_key="k", bitunix_api_secret="s")

    async def boom(margin_coin="USDT"):
        raise RuntimeError("network down")

    monkeypatch.setattr(a.engine.client, "get_single_account", boom)
    out = _run(a.handle("balance"))
    assert out.strip()
    assert "network down" in out


# ------------------------------------------------------------- routing table
BALANCE_ALIASES = ["balance", "bal", "wallet", "equity", "funds"]


@pytest.mark.parametrize("word", BALANCE_ALIASES)
def test_balance_aliases_route_to_balance(word):
    assert AGENT_BARE[word] == "/balance"


@pytest.mark.parametrize("word,expected", [
    ("positions", "/positions"),
    ("orders", "/orders"),
    ("price", "/price"),
    ("funding", "/funding"),
    ("leverage", "/leverage"),
    ("margin", "/margin"),
    ("pnl", "/pnl"),
    ("status", "/status"),
    ("symbols", "/symbols"),
    ("pairs", "/symbols"),
])
def test_trader_words_route_to_real_handlers(word, expected):
    assert AGENT_BARE[word] == expected


@pytest.mark.parametrize("word", [
    "balance", "bal", "wallet", "equity", "funds", "positions", "orders",
    "price", "funding", "leverage", "margin", "pnl", "status", "symbols",
    "pairs", "markets",
])
def test_trader_words_never_reach_the_model(tmp_path, word):
    """The exact bug: these used to fall through to chat()."""
    a = _agent(tmp_path, bitunix_api_key="", bitunix_api_secret="")
    out = _run(a.handle(word))
    assert "You said" not in out, f"{word!r} reached the model"
    assert "I could not send that to a model" not in out


def test_balance_does_not_reach_model_even_with_api_key(tmp_path):
    a = _agent(tmp_path, api_key="k", bitunix_api_key="", bitunix_api_secret="")
    out = _run(a.handle("balance"))
    assert "BITUNIX_API_KEY" in out
    assert "You said" not in out


def test_free_text_words_are_not_commands():
    for w in FREE_TEXT_WORDS:
        assert w not in AGENT_BARE


def test_help_lists_balance():
    assert "/balance" in HELP
    assert "bal" in HELP
    assert "/positions" in HELP
    assert "/price" in HELP


# ------------------------------------------- query vs setter disambiguation
def test_bare_leverage_reads(tmp_path):
    a = _agent(tmp_path, bitunix_api_key="", bitunix_api_secret="")
    out = _run(a.handle("leverage"))
    # a read: either the key message or a leverage table, never a setter reply
    assert "Leverage = " not in out


def test_leverage_with_argument_sets(tmp_path):
    a = _agent(tmp_path)
    out = _run(a.handle("leverage 20"))
    assert "Leverage = 20x" in out
    assert a.engine.state.leverage == 20


def test_symbols_with_argument_sets(tmp_path):
    a = _agent(tmp_path)
    out = _run(a.handle("symbols BTCUSDT,ETHUSDT"))
    assert "Symbols = BTCUSDT,ETHUSDT" in out
    assert a.engine.state.symbols == ["BTCUSDT", "ETHUSDT"]


def test_report_on_still_reaches_engine(tmp_path):
    a = _agent(tmp_path)
    out = _run(a.handle("report on"))
    assert "Report ON" in out
    assert a.engine.state.report_on is True


def test_missing_keys_message_is_actionable():
    msg = missing_keys_message()
    assert "BITUNIX_API_KEY" in msg
    assert "BITUNIX_API_SECRET" in msg
    assert ".env" in msg
