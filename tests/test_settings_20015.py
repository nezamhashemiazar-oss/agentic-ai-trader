"""Regression tests for Bitunix error 20015 on the `settings trade` command.

Observed:
    j+rock> settings trade
    === account trading settings ===
    position mode: HEDGE
    trading settings: error 20015: this Futures is not allowed to trade.

Root cause: the bare word `settings` maps to `/settings`, so `trade` arrives
as a symbol token. `cmd_settings` sent `symbols=TRADE` to the private
trading_settings endpoint; `TRADE` is not a tradable futures pair, and Bitunix
returns 20015 "This futures is not supported". The position-mode call (no
params) succeeded, which is why HEDGE read back fine.

Fix: validate every requested symbol against the live trading-pairs list
before calling the private endpoint; drop invalid tokens with a clear warning
and fall back to "all configured" (symbols omitted) when nothing valid remains.
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
def test_settings_trade_no_longer_sends_trade_as_symbol(tmp_path):
    """`settings trade` must not send symbols=TRADE to the private endpoint."""
    client = FakeClient()
    a = _agent(tmp_path, client)
    out = _run(a.handle("settings trade"))
    # the private endpoint is called with NO symbols (falls back to all)
    assert client.trading_settings_calls == [None]
    assert "20015" not in out
    assert "position mode: HEDGE" in out
    assert "trading settings:" in out


def test_settings_trade_skips_unknown_symbol_with_warning(tmp_path):
    """An invalid token is dropped with a warning, not sent to the API."""
    client = FakeClient()
    a = _agent(tmp_path, client)
    out = _run(a.handle("settings trade"))
    assert "skipping unknown symbol(s) TRADE" in out
    assert "20015" not in out


def test_settings_with_valid_symbol_still_queries_that_symbol(tmp_path):
    """A genuine symbol is still passed through to the private endpoint."""
    client = FakeClient()
    a = _agent(tmp_path, client)
    out = _run(a.handle("settings BTCUSDT"))
    assert client.trading_settings_calls == ["BTCUSDT"]
    assert "20015" not in out
    assert "BTCUSDT" in out


def test_settings_mixed_valid_and_invalid_filters_to_valid(tmp_path):
    """`settings BTCUSDT TRADE` keeps BTCUSDT and drops TRADE."""
    client = FakeClient()
    a = _agent(tmp_path, client)
    out = _run(a.handle("settings BTCUSDT TRADE"))
    assert client.trading_settings_calls == ["BTCUSDT"]
    assert "skipping unknown symbol(s) TRADE" in out
    assert "20015" not in out


def test_settings_bare_still_works(tmp_path):
    """`settings` with no symbol still returns all configured settings."""
    client = FakeClient()
    a = _agent(tmp_path, client)
    out = _run(a.handle("settings"))
    assert client.trading_settings_calls == [None]
    assert "20015" not in out
    assert "position mode: HEDGE" in out


# ------------------------------------------------------- genuine bad symbol
def test_settings_genuinely_bad_symbol_is_still_rejected(tmp_path):
    """A real unknown symbol (not the `trade` keyword) is still surfaced."""
    client = FakeClient()
    a = _agent(tmp_path, client)
    out = _run(a.handle("settings NOTACOIN"))
    # NOTACOIN is not in the pairs list, so it is dropped with a warning
    assert "skipping unknown symbol(s) NOTACOIN" in out
    assert "20015" not in out
    assert client.trading_settings_calls == [None]


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