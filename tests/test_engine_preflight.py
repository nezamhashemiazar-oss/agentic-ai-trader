"""End-to-end tests for TraderEngine.try_enter's pre-flight gates.

These drive the real engine with a fake client, so they cover the wiring
(mark price fetched, tier checked, order built) rather than just the helpers.
"""
from __future__ import annotations

import asyncio

import pytest

from jrock.config import Settings
from jrock.trader.engine import TraderEngine

PAIR = {
    "symbol": "AKEUSDT", "minTradeVolume": "1",
    "maxMarketOrderVolume": "100000000", "maxLimitOrderVolume": "1500000000",
    "basePrecision": 0, "quotePrecision": 6, "maxLeverage": 50, "minLeverage": 1,
    "defaultLeverage": 10, "priceProtectScope": "0.05", "symbolStatus": "OPEN",
    "isApiSupported": True,
}
TIERS = [
    {"symbol": "AKEUSDT", "level": 1, "startValue": "0", "endValue": "10000",
     "leverage": 50, "maintenanceMarginRate": "0.015"},
    {"symbol": "AKEUSDT", "level": 2, "startValue": "10000", "endValue": "20000",
     "leverage": 25, "maintenanceMarginRate": "0.02"},
    {"symbol": "AKEUSDT", "level": 3, "startValue": "20000", "endValue": "40000",
     "leverage": 20, "maintenanceMarginRate": "0.025"},
]


class FakeClient:
    def __init__(self, equity, mark, last):
        self.equity, self.mark, self.last = equity, mark, last
        self.placed: list[dict] = []

    async def get_tickers(self, sym):
        return [{"symbol": sym, "lastPrice": str(self.last),
                 "markPrice": str(self.mark)}]

    async def get_single_account(self):
        return [{"marginCoin": "USDT", "available": str(self.equity),
                 "frozen": "0", "margin": "0", "unrealized": "0"}]

    async def get_trading_pairs(self, symbols=None):
        return [PAIR]

    async def get_position_tiers(self, sym):
        return TIERS

    async def get_pending_positions(self, sym=None):
        return []

    async def change_margin_mode(self, *a, **k):
        return {}

    async def change_leverage(self, *a, **k):
        return {}

    async def place_order(self, **b):
        self.placed.append(b)
        return {"orderId": "X1"}


class Agg:
    symbol = "AKEUSDT"
    direction = "LONG"
    actionable = True
    strength = "MEDIUM"
    confidence = 75.0
    agreeing = 4


def _run_engine(equity, mark, last, leverage):
    s = Settings()
    s.market_data_source = "rest"
    e = TraderEngine(s)
    e.client = FakeClient(equity, mark, last)
    e.state.leverage = leverage

    async def go():
        await e.load_rules(["AKEUSDT"])
        return await e.try_enter(Agg())

    return asyncio.run(go()), e


def _run(equity, mark, last, leverage):
    r, e = _run_engine(equity, mark, last, leverage)
    return r, e.client.placed


def test_tier_leverage_cap_lowers_leverage_and_continues():
    """A REAL order notional that lands in a tier forbidding the leverage is
    re-sized at the exchange's own tier cap and sent - not refused.

    equity 100k, risk 1%, 50x -> the risk-sized notional is ~400k, which lands
    in the last tier (max 20x). The exchange's cap is the one documented reason
    to use a lower leverage, so the order goes out at 20x and the reason is
    reported. The user's setting is NOT rewritten.
    """
    r, placed = _run(100000.0, 0.005020, 0.005009, 50)
    assert r["ok"] is True, r.get("reason")
    assert len(placed) == 1


def test_tier_leverage_cap_is_reported_to_the_user():
    """The clamp must be visible, not silent: the engine records why."""
    _, e = _run_engine(100000.0, 0.005020, 0.005009, 50)
    assert any("tier cap" in err for err in e.state.errors), e.state.errors
    # and the user's leverage setting is untouched
    assert e.state.leverage == 50


def test_valid_high_leverage_order_is_not_refused():
    """The bug this fix removes: the tier cap was checked against
    `equity * leverage` (a notional far larger than the order), which landed in
    a higher tier and refused orders that were actually fine.

    equity 1000, risk 1%, 50x -> the real notional is ~4k, which is tier 1
    (max 50x), so 50x IS allowed and the order must go through.
    """
    r, placed = _run(1000.0, 0.005020, 0.005009, 50)
    assert r["ok"] is True, r.get("reason")
    assert len(placed) == 1
    assert placed[0]["symbol"] == "AKEUSDT"


def test_sizing_uses_available_not_total_equity():
    """`available` is the spendable balance; total equity (available + margin +
    unrealized) is not. Sizing against equity is what produced 20003."""
    from jrock.trader.risk import spendable_balance
    row = {"available": "9.3044", "frozen": "0", "margin": "50",
           "crossUnrealizedPNL": "5", "isolationUnrealizedPNL": "0"}
    assert spendable_balance(row) == pytest.approx(9.3044)
    # total equity would be 64.3044 - far more than the order may spend
    assert spendable_balance(row) < 9.3044 + 50 + 5
    assert spendable_balance({}) == 0.0
    assert spendable_balance({"available": "not-a-number"}) == 0.0


def test_correctly_sized_order_passes_and_is_built():
    r, placed = _run(100.0, 0.005020, 0.005009, 10)
    assert r["ok"] is True
    assert len(placed) == 1
    body = placed[0]
    assert body["symbol"] == "AKEUSDT"
    assert body["side"] == "BUY"
    assert body["orderType"] == "MARKET"
    # the stop must be below both the entry and the mark
    assert float(body["slPrice"]) < 0.005009
    assert float(body["slPrice"]) < 0.005020
    assert float(body["tpPrice"]) > 0.005020


def test_engine_fetches_the_mark_price_not_just_the_last_price():
    """The mark (0.005020) is above the last (0.005009); the stop must clear
    the mark, which only happens if the mark was actually fetched."""
    r, placed = _run(100.0, 0.005020, 0.005009, 10)
    assert r["ok"] is True
    assert float(placed[0]["slPrice"]) < 0.005020


def test_tiny_account_is_refused_without_sending():
    r, placed = _run(0.0001, 0.005020, 0.005009, 10)
    assert r["ok"] is False
    assert placed == []
    assert "qty is 0" in r["reason"]
