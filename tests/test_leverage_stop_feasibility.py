"""Regression tests for "no valid stop-loss exists" at high leverage.

The bug (seen live on JCTUSDT):

    JCTUSDT LONG: no valid stop-loss exists. The stop must sit between the
    liquidation price 0.015754 and 0.015625 (entry=0.015833, mark=0.015625),
    keeping 0.000003 clear of both. Lower the leverage or widen the tick.

The engine planned the stop from the ENTRY (the last price, 0.015833) while the
exchange validates it against the MARK (0.015625). At 100x with a 0.5%
maintenance margin the liquidation price is only ~0.5% below the entry:

    liq = 0.015833 * (1 - 1/100 + 0.005) = 0.015754

which is ABOVE the mark 0.015625. A LONG stop must be below the mark AND above
the liquidation price, so the interval (0.015754, 0.015625) is empty - no stop
can exist. The engine refused the symbol on every scan, forever, even though
the same signal was perfectly tradable at 55x or below.

The fix: before planning, compute the highest leverage at which a stop still
fits between the liquidation price and the mark, and clamp to it (or refuse
with that number when auto-reduce is off).
"""
from __future__ import annotations

import asyncio

import pytest

from jrock.config import Settings
from jrock.trader.engine import TraderEngine
from jrock.trader.risk import (PairRules, liquidation_price,
                               max_leverage_for_stop, min_stop_distance)

# --- the live JCTUSDT numbers from the screenshot ---------------------------
JCT_ENTRY = 0.015833   # last price the engine traded on
JCT_MARK = 0.015625    # mark price the exchange validates against
JCT_MMR = 0.005        # maintenance margin rate at the 100x tier

JCT = PairRules(
    symbol="JCTUSDT", min_trade_volume=1.0, max_market_order_volume=1e9,
    max_limit_order_volume=1e9, base_precision=0, quote_precision=6,
    min_leverage=1, max_leverage=125, default_leverage=20,
    price_protect_scope=0.05, symbol_status="OPEN", api_supported=True,
)


# --- the helper -------------------------------------------------------------

def test_liquidation_price_reproduces_the_screenshot():
    """The reported liq price must come out of the documented formula."""
    liq = liquidation_price(JCT_ENTRY, 100, "LONG", JCT_MMR)
    assert liq == pytest.approx(0.015754, abs=1e-6)


def test_100x_has_no_room_for_a_stop():
    """liq (0.015754) is ABOVE the mark (0.015625): the interval is empty."""
    liq = liquidation_price(JCT_ENTRY, 100, "LONG", JCT_MMR)
    assert liq > JCT_MARK, "the screenshot's premise: liq sits above the mark"


def test_max_leverage_for_stop_is_below_100x():
    lev = max_leverage_for_stop(entry=JCT_ENTRY, mark=JCT_MARK, side="LONG",
                                maintenance_margin_rate=JCT_MMR, rules=JCT,
                                min_ticks=3)
    assert 0 < lev < 100
    # the analytic bound for these numbers is ~55x; the 3-tick clearance the
    # stop must keep from the mark costs one more step, so 54x is the answer.
    assert lev == 54


def test_the_clamped_leverage_actually_leaves_room():
    """At the returned leverage a stop really does fit - that is the point."""
    lev = max_leverage_for_stop(entry=JCT_ENTRY, mark=JCT_MARK, side="LONG",
                                maintenance_margin_rate=JCT_MMR, rules=JCT,
                                min_ticks=3)
    liq = liquidation_price(JCT_ENTRY, lev, "LONG", JCT_MMR)
    d = min_stop_distance(JCT, 3)
    assert liq < JCT_MARK - d, (
        f"at {lev}x liq={liq} must stay below mark-d={JCT_MARK - d}")


def test_one_step_above_the_bound_does_not_fit():
    """The bound is tight: lev+1 must break the interval."""
    lev = max_leverage_for_stop(entry=JCT_ENTRY, mark=JCT_MARK, side="LONG",
                                maintenance_margin_rate=JCT_MMR, rules=JCT,
                                min_ticks=3)
    liq = liquidation_price(JCT_ENTRY, lev + 1, "LONG", JCT_MMR)
    assert liq >= JCT_MARK - min_stop_distance(JCT, 3)


def test_short_side_mirror():
    """A SHORT with the mark ABOVE the entry has the same problem, mirrored."""
    lev = max_leverage_for_stop(entry=0.015625, mark=0.015833, side="SHORT",
                                maintenance_margin_rate=JCT_MMR, rules=JCT,
                                min_ticks=3)
    assert 0 < lev < 100
    liq = liquidation_price(0.015625, lev, "SHORT", JCT_MMR)
    assert liq > 0.015833 + min_stop_distance(JCT, 3)


def test_no_leverage_works_only_when_the_mark_is_absurdly_far():
    """For a LONG, lowering the leverage always widens the room (at 1x the
    liquidation price is ~99.5% below the entry), so the only way NO leverage
    works is a mark almost at zero - a 99.9% collapse."""
    lev = max_leverage_for_stop(entry=1.0, mark=0.001, side="LONG",
                                maintenance_margin_rate=0.005, rules=JCT,
                                min_ticks=3)
    assert lev == 0


def test_low_leverage_always_has_room():
    """The whole point of clamping: there is always a leverage that fits."""
    for mark in (0.99, 0.95, 0.9, 0.5):
        lev = max_leverage_for_stop(entry=1.0, mark=mark, side="LONG",
                                    maintenance_margin_rate=0.005, rules=JCT,
                                    min_ticks=3)
        assert lev >= 1, f"mark={mark} must still admit some leverage"


def test_a_moderate_gap_still_has_a_low_leverage_that_fits():
    """A 5% gap is not hopeless: 18x still leaves room, so the engine must
    clamp rather than refuse."""
    lev = max_leverage_for_stop(entry=1.0, mark=0.95, side="LONG",
                                maintenance_margin_rate=0.005, rules=JCT,
                                min_ticks=3)
    assert lev == 18


def test_low_leverage_is_untouched():
    """10x has plenty of room; the bound must not clamp it."""
    lev = max_leverage_for_stop(entry=JCT_ENTRY, mark=JCT_MARK, side="LONG",
                                maintenance_margin_rate=JCT_MMR, rules=JCT,
                                min_ticks=3)
    assert lev > 10


# --- the engine wiring ------------------------------------------------------

PAIR = {
    "symbol": "JCTUSDT", "minTradeVolume": "1",
    "maxMarketOrderVolume": "100000000", "maxLimitOrderVolume": "1500000000",
    "basePrecision": 0, "quotePrecision": 6, "maxLeverage": 125,
    "minLeverage": 1, "defaultLeverage": 20, "priceProtectScope": "0.05",
    "symbolStatus": "OPEN", "isApiSupported": True,
}
TIERS = [
    {"symbol": "JCTUSDT", "level": 1, "startValue": "0", "endValue": "5000",
     "leverage": 125, "maintenanceMarginRate": "0.004"},
    {"symbol": "JCTUSDT", "level": 2, "startValue": "5000", "endValue": "20000",
     "leverage": 100, "maintenanceMarginRate": "0.005"},
    {"symbol": "JCTUSDT", "level": 3, "startValue": "20000", "endValue": "50000",
     "leverage": 50, "maintenanceMarginRate": "0.01"},
]


class FakeClient:
    def __init__(self, equity, mark, last):
        self.equity, self.mark, self.last = equity, mark, last
        self.placed: list[dict] = []
        self.leverage_calls: list[int] = []

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

    async def change_leverage(self, sym, lev):
        self.leverage_calls.append(lev)
        return {}

    async def place_order(self, **b):
        self.placed.append(b)
        return {"orderId": "X1"}


class Agg:
    symbol = "JCTUSDT"
    direction = "LONG"
    actionable = True
    strength = "MEDIUM"
    confidence = 85.4
    agreeing = 5


def _run(equity, mark, last, leverage, auto_reduce=True):
    s = Settings()
    s.market_data_source = "rest"
    s.leverage_auto_reduce = auto_reduce
    e = TraderEngine(s)
    e.client = FakeClient(equity, mark, last)
    e.state.leverage = leverage

    async def go():
        await e.load_rules(["JCTUSDT"])
        return await e.try_enter(Agg())

    return asyncio.run(go()), e.client


def test_engine_does_not_refuse_the_screenshot_case():
    """The exact live numbers: 100x must no longer produce
    'no valid stop-loss exists' - the order goes through at a clamped leverage."""
    r, client = _run(100.0, JCT_MARK, JCT_ENTRY, 100)
    assert r["ok"] is True, r.get("reason")
    assert "no valid stop-loss exists" not in str(r.get("reason", ""))
    assert len(client.placed) == 1
    # the leverage actually sent to the exchange is the clamped one
    assert client.leverage_calls and client.leverage_calls[-1] < 100


def test_engine_reports_the_clamp_instead_of_silently_changing_it():
    r, _ = _run(100.0, JCT_MARK, JCT_ENTRY, 100)
    assert r["ok"] is True
    assert any("leaves no room for a stop-loss" in e for e in
               _errors_of(r, JCT_MARK, JCT_ENTRY))


def _errors_of(r, mark, entry):
    """The engine records the clamp as an error line; read it back."""
    # try_enter returns the plan; the clamp message lives on the engine state,
    # so re-run and inspect it.
    s = Settings()
    s.market_data_source = "rest"
    e = TraderEngine(s)
    e.client = FakeClient(100.0, mark, entry)
    e.state.leverage = 100

    async def go():
        await e.load_rules(["JCTUSDT"])
        await e.try_enter(Agg())
        return list(e.state.errors)

    return asyncio.run(go())


def test_auto_reduce_off_refuses_with_the_max_leverage_named():
    r, client = _run(100.0, JCT_MARK, JCT_ENTRY, 100, auto_reduce=False)
    assert r["ok"] is False
    assert client.placed == []
    assert "54x" in r["reason"]
    assert "LEVERAGE_AUTO_REDUCE" in r["reason"]


def test_auto_reduce_off_does_not_claim_insufficient_balance():
    """This is a leverage/stop problem, not a balance problem - the hint must
    not send the user chasing 20003."""
    r, _ = _run(100.0, JCT_MARK, JCT_ENTRY, 100, auto_reduce=False)
    assert r.get("hint") != "20003 Insufficient balance"


def test_sane_leverage_is_not_clamped():
    r, client = _run(100.0, JCT_MARK, JCT_ENTRY, 10)
    assert r["ok"] is True
    assert client.leverage_calls[-1] == 10


def test_hopeless_mark_is_refused_with_a_clear_reason():
    """A mark 99.9% below the entry: no leverage works, and the message says so
    rather than blaming the balance."""
    r, client = _run(100.0, JCT_ENTRY * 0.001, JCT_ENTRY, 20)
    assert r["ok"] is False
    assert client.placed == []
    assert "no stop-loss can fit at any leverage" in r["reason"]
    assert r.get("hint") != "20003 Insufficient balance"
