"""MARGIN_AMOUNT_PCT is a TARGET, and it is independent of leverage.

The user's report was "margin amount pct of balance does not work when opening
a position": the margin actually locked did not match the percentage they set.

Two independent defects produced that, and both are pinned here.

1. **The percentage was only a cap.** `position_size_by_risk` took it as an
   upper bound, and the risk-based size was almost always the smaller of the
   two, so the cap never bound. A 10% setting produced ~2% of the balance. A
   cap that never binds is indistinguishable from a setting that is ignored.
   The engine now sizes FROM the percentage (`position_size_by_margin`).

2. **Leverage was rewritten to "fix" margin.** The pre-flight assigned
   `self.state.leverage = max_lev_for_stop` in place, so one awkward symbol
   silently changed the leverage for every other symbol and every later scan.
   Leverage and margin are independent knobs: margin decides how much of the
   balance one position locks, leverage only decides how much notional that
   margin controls. The ONLY reason to use a different leverage for one order
   is the exchange's own cap (tier leverage, or no room for a stop) - and even
   then the user's setting is left alone.

Tolerance: the margin lands slightly BELOW the target because the taker fee is
reserved out of the same budget and qty is floored to `basePrecision` (0 for
AKEUSDT, so up to one whole coin is dropped). Both are deliberate - over-sizing
is what produced 20003 - so the assertion is a band, not equality.
"""
from __future__ import annotations

import asyncio

import pytest

from jrock.config import Settings
from jrock.trader.engine import TraderEngine
from jrock.trader.risk import (PairRules, margin_budget, margin_required,
                               position_size_by_margin)

PAIR = {
    "symbol": "AKEUSDT", "minTradeVolume": "1",
    "maxMarketOrderVolume": "100000000", "maxLimitOrderVolume": "1500000000",
    "basePrecision": 0, "quotePrecision": 6, "maxLeverage": 50, "minLeverage": 1,
    "defaultLeverage": 10, "priceProtectScope": "0.05", "symbolStatus": "OPEN",
    "isApiSupported": True,
}
#: One wide tier, so the tier cap never interferes with the margin assertion.
#: The tier-cap path has its own coverage in test_engine_preflight.py.
TIERS = [
    {"symbol": "AKEUSDT", "level": 1, "startValue": "0", "endValue": "100000",
     "leverage": 50, "maintenanceMarginRate": "0.015"},
]

RULES = PairRules(
    symbol="AKEUSDT", min_trade_volume=1.0, max_market_order_volume=1e8,
    max_limit_order_volume=1.5e9, base_precision=0, quote_precision=6,
    min_leverage=1, max_leverage=50, default_leverage=10,
    price_protect_scope=0.05, symbol_status="OPEN", api_supported=True,
)

EQUITY = 1000.0
PRICE = 0.005009
MARK = 0.005020


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


def _run_engine(margin_pct, leverage, equity=EQUITY):
    s = Settings()
    s.market_data_source = "rest"
    s.margin_amount_pct = margin_pct
    e = TraderEngine(s)
    e.client = FakeClient(equity, MARK, PRICE)
    e.state.leverage = leverage

    async def go():
        await e.load_rules(["AKEUSDT"])
        return await e.try_enter(Agg())

    return asyncio.run(go()), e


def _locked_margin(e, leverage):
    """The margin the placed order actually locks, from the wire qty."""
    qty = float(e.client.placed[0]["qty"])
    return margin_required(qty * PRICE, leverage)


# --- 1. the percentage is the target, not a cap ------------------------------

@pytest.mark.parametrize("pct", [5.0, 10.0, 20.0, 50.0])
@pytest.mark.parametrize("leverage", [5, 10, 20])
def test_locked_margin_matches_the_configured_percentage(pct, leverage):
    """The whole bug: the margin locked must be the share the user asked for."""
    r, e = _run_engine(pct, leverage)
    assert r["ok"] is True, r.get("reason")
    assert len(e.client.placed) == 1

    locked = _locked_margin(e, leverage)
    target = EQUITY * pct / 100.0
    # Never over the target (over-sizing is what produced 20003), and close
    # enough that the user sees the number they set. The shortfall is the
    # reserved taker fee plus integer-qty flooring.
    assert locked <= target + 1e-9, f"{locked} exceeds the {target} target"
    assert locked >= target * 0.95, (
        f"margin {locked:.4f} is far below the {target:.2f} target "
        f"({locked / target:.3f}x) - the percentage is not being applied")


@pytest.mark.parametrize("pct", [5.0, 10.0, 20.0, 50.0])
def test_margin_scales_with_the_percentage(pct):
    """Doubling the percentage must roughly double the margin locked."""
    r1, e1 = _run_engine(pct, 10)
    r2, e2 = _run_engine(pct * 2, 10)
    assert r1["ok"] and r2["ok"]
    m1 = _locked_margin(e1, 10)
    m2 = _locked_margin(e2, 10)
    assert m2 > m1
    assert m2 / m1 == pytest.approx(2.0, rel=0.05)


# --- 2. leverage is never touched -------------------------------------------

@pytest.mark.parametrize("pct", [5.0, 10.0, 20.0, 50.0])
@pytest.mark.parametrize("leverage", [5, 10, 20])
def test_leverage_setting_is_unchanged_by_margin_sizing(pct, leverage):
    """A margin setting must never rewrite the leverage.

    This is the regression guard for the old `self.state.leverage =
    max_lev_for_stop` assignment: sizing a position is not a reason to change
    the user's leverage.
    """
    r, e = _run_engine(pct, leverage)
    assert r["ok"] is True, r.get("reason")
    assert e.state.leverage == leverage, (
        f"leverage was rewritten from {leverage}x to {e.state.leverage}x "
        f"while sizing a {pct:g}% margin order")


@pytest.mark.parametrize("leverage", [5, 10, 20])
def test_margin_target_is_independent_of_leverage(leverage):
    """Same percentage, different leverage -> same margin locked.

    Margin is a share of the balance; leverage only changes how much notional
    that margin controls. If the locked margin moved with the leverage, the two
    knobs would be coupled - which is exactly the confusion this fixes.
    """
    r, e = _run_engine(10.0, leverage)
    assert r["ok"] is True, r.get("reason")
    locked = _locked_margin(e, leverage)
    target = EQUITY * 0.10
    assert locked == pytest.approx(target, rel=0.05)


@pytest.mark.parametrize("leverage", [5, 10, 20])
def test_notional_grows_with_leverage_at_a_fixed_margin(leverage):
    """The other half of independence: fixed margin, more leverage = more size."""
    r, e = _run_engine(10.0, leverage)
    assert r["ok"] is True, r.get("reason")
    qty = float(e.client.placed[0]["qty"])
    notional = qty * PRICE
    # notional ~= margin * leverage, minus the fee reserve
    assert notional == pytest.approx(EQUITY * 0.10 * leverage, rel=0.10)


# --- 3. the helper itself ----------------------------------------------------

def test_position_size_by_margin_targets_the_budget():
    qty = position_size_by_margin(EQUITY, 10.0, PRICE, 10, RULES)
    assert qty > 0
    notional = qty * PRICE
    budget = margin_budget(EQUITY, 10.0)
    assert margin_required(notional, 10) <= budget
    assert margin_required(notional, 10) >= budget * 0.95


def test_position_size_by_margin_is_defensive():
    assert position_size_by_margin(0.0, 10.0, PRICE, 10, RULES) == 0.0
    assert position_size_by_margin(EQUITY, 0.0, PRICE, 10, RULES) == 0.0
    assert position_size_by_margin(EQUITY, 10.0, 0.0, 10, RULES) == 0.0
    # a budget too small for the symbol's minimum volume yields no order
    assert position_size_by_margin(EQUITY, 0.00001, PRICE, 10, RULES) == 0.0


def test_zero_percentage_falls_back_to_risk_sizing():
    """0 disables the knob, so the pre-existing risk path still applies."""
    r, e = _run_engine(0.0, 10)
    assert r["ok"] is True, r.get("reason")
    assert len(e.client.placed) == 1
    assert e.state.leverage == 10
