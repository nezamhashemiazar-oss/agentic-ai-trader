"""Regression tests for Bitunix error 20015 on the `settings trade` command.

Observed (before the fix):

    j+rock> settings trade
    === account trading settings ===
    position mode: HEDGE
    trading settings: error 20015: this Futures is not allowed to trade.

Root cause: the bare word `settings` maps to `/settings`, so `trade` arrives
as the `rest` argument of `cmd_settings`. The old code treated every token as
a symbol and sent `symbols=TRADE` to the private trading_settings endpoint.
`TRADE` is not a tradable futures pair (verified against the live public API:
794 pairs, TRADE absent), so Bitunix returned 20015 "This futures is not
supported". The position-mode call takes no parameters, which is why HEDGE
read back fine.

Fix: `settings trade` (and bare `settings`) print the ACCOUNT-LEVEL trading
settings - position mode, margin mode, leverage and the engine's trading
config - and never send a symbol to the exchange, so they cannot raise 20015.
A token that is not a tradable pair is dropped with a warning instead of being
forwarded. Only a genuine symbol triggers the per-symbol lookup.
"""
from __future__ import annotations

import asyncio

import pytest

from jrock.agent import JRockAgent
from jrock.config import Settings
from jrock.trader.errors import BitunixAPIError

VALID_PAIRS = [
    {"symbol": "BTCUSDT", "symbolStatus": "TRADING"},
    {"symbol": "ETHUSDT", "symbolStatus": "TRADING"},
    {"symbol": "AKEUSDT", "symbolStatus": "TRADING"},
]


class FakeClient:
    """Mimics the live Bitunix client for the settings path."""

    def __init__(self) -> None:
        self.trading_settings_calls: list[str | None] = []
        self.trading_pairs_calls = 0
        self.pairs = VALID_PAIRS
        self.settings_rows = [
            {"symbol": "BTCUSDT", "marginMode": "CROSS", "leverage": 20},
        ]

    async def get_position_mode(self):
        return {"positionMode": "HEDGE"}

    async def get_trading_pairs(self, symbols: str | None = None):
        self.trading_pairs_calls += 1
        return self.pairs

    async def get_trading_settings(self, symbols: str | None = None):
        self.trading_settings_calls.append(symbols)
        if symbols:
            for s in symbols.split(","):
                if s.strip().upper() not in {p["symbol"] for p in self.pairs}:
                    raise BitunixAPIError(
                        20015, "this Futures is not allowed to trade.")
        return self.settings_rows

    async def close(self):
        pass


def _agent(tmp_path, client=None) -> JRockAgent:
    s = Settings(bitunix_api_key="k", bitunix_api_secret="s")
    s.data_dir  # noqa: B018
    a = JRockAgent(s)
    a.engine.data_dir = tmp_path
    if client is not None:
        a.engine.client = client
    return a


def _run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------- the bug itself
def test_settings_trade_never_sends_trade_as_symbol(tmp_path):
    """`settings trade` must not send symbols=TRADE to the private endpoint."""
    client = FakeClient()
    a = _agent(tmp_path, client)
    out = _run(a.handle("settings trade"))
    # the private per-symbol endpoint is never called for the keyword form
    assert client.trading_settings_calls == []
    assert "20015" not in out
    assert "position mode: HEDGE" in out


def test_settings_trade_prints_account_config_not_symbols(tmp_path):
    """The output is the account trading config, not a per-symbol listing."""
    client = FakeClient()
    a = _agent(tmp_path, client)
    out = _run(a.handle("settings trade"))
    assert "=== account trading settings ===" in out
    # account-level config lines the user asked for
    assert "margin mode:" in out
    assert "leverage:" in out
    assert "max open positions:" in out
    assert "risk per trade:" in out
    # and NOT a wall of symbols
    assert "  BTCUSDT: leverage=" not in out


def test_settings_trade_does_not_validate_symbols(tmp_path):
    """The keyword form must not even hit the trading-pairs endpoint."""
    client = FakeClient()
    a = _agent(tmp_path, client)
    _run(a.handle("settings trade"))
    assert client.trading_pairs_calls == 0


def test_settings_bare_still_works(tmp_path):
    """`settings` with no argument prints the account config."""
    client = FakeClient()
    a = _agent(tmp_path, client)
    out = _run(a.handle("settings"))
    assert client.trading_settings_calls == []
    assert "20015" not in out
    assert "position mode: HEDGE" in out
    assert "margin mode:" in out


@pytest.mark.parametrize("word", ["trade", "TRADE", "trading", "account", "config"])
def test_settings_keywords_are_not_treated_as_symbols(tmp_path, word):
    """Every documented keyword form prints config and sends no symbol."""
    client = FakeClient()
    a = _agent(tmp_path, client)
    out = _run(a.handle(f"settings {word}"))
    assert client.trading_settings_calls == []
    assert "20015" not in out
    assert "margin mode:" in out


# ------------------------------------------------------- genuine symbol path
def test_settings_with_valid_symbol_queries_that_symbol(tmp_path):
    """A genuine symbol is still passed through to the private endpoint."""
    client = FakeClient()
    a = _agent(tmp_path, client)
    out = _run(a.handle("settings BTCUSDT"))
    assert client.trading_settings_calls == ["BTCUSDT"]
    assert "20015" not in out
    assert "BTCUSDT: leverage=20x marginMode=CROSS" in out


def test_settings_mixed_valid_and_invalid_filters_to_valid(tmp_path):
    """`settings BTCUSDT NOTACOIN` keeps BTCUSDT and drops NOTACOIN."""
    client = FakeClient()
    a = _agent(tmp_path, client)
    out = _run(a.handle("settings BTCUSDT NOTACOIN"))
    assert client.trading_settings_calls == ["BTCUSDT"]
    assert "skipping unknown symbol(s) NOTACOIN" in out
    assert "20015" not in out


def test_settings_genuinely_bad_symbol_is_dropped_not_sent(tmp_path):
    """A real unknown symbol is dropped with a warning, never sent."""
    client = FakeClient()
    a = _agent(tmp_path, client)
    out = _run(a.handle("settings NOTACOIN"))
    assert "skipping unknown symbol(s) NOTACOIN" in out
    assert "20015" not in out
    assert client.trading_settings_calls == []
    # the account config is still shown
    assert "margin mode:" in out


def test_settings_20015_from_api_is_actionable(tmp_path):
    """If the API still returns 20015, the message is actionable, not raw."""
    client = FakeClient()

    async def boom(symbols=None):
        raise BitunixAPIError(20015, "this Futures is not allowed to trade.")

    client.get_trading_settings = boom
    a = _agent(tmp_path, client)
    out = _run(a.handle("settings BTCUSDT"))
    assert "20015" in out
    assert "not a tradable futures pair" in out
    assert "/symbols" in out


def test_settings_pairs_lookup_failure_skips_symbol_section(tmp_path):
    """If the pairs lookup fails, skip the per-symbol section, don't 20015."""
    client = FakeClient()

    async def boom(symbols=None):
        raise RuntimeError("network down")

    client.get_trading_pairs = boom
    a = _agent(tmp_path, client)
    out = _run(a.handle("settings BTCUSDT"))
    assert "could not validate symbols" in out
    assert client.trading_settings_calls == []
    assert "20015" not in out
    assert "margin mode:" in out


# ------------------------------------------------------------- routing guard
def test_settings_trade_routes_to_agent_not_engine(tmp_path):
    """`settings trade` must reach cmd_settings, not the engine settings report."""
    client = FakeClient()
    a = _agent(tmp_path, client)
    out = _run(a.handle("settings trade"))
    # the agent path prints the account trading settings header
    assert "=== account trading settings ===" in out
    # the engine path prints this header instead - must NOT appear
    assert "=== engine settings ===" not in out
