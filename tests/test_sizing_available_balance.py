"""Regression tests for the position-size / 20003 root cause.

The reported symptom: the engine kept answering "insufficient balance" while
the account clearly had funds. Three distinct defects produced it, and each is
pinned here:

1. **Total equity used instead of spendable balance.** `get_single_account`
   returns `available` (what a new order may spend) alongside `margin` (locked
   by open positions) and `crossUnrealizedPNL`. Sizing against the SUM of those
   makes the engine believe it can afford an order the exchange then refuses
   with 20003. `spendable_balance()` is the single accessor for the right field.

2. **The tier leverage cap checked against the wrong notional.** The cap was
   evaluated at `available * leverage` - a notional far larger than the order -
   which lands in a higher tier and refuses orders that are actually fine. It
   must be evaluated at the REAL order notional.

3. **The provisional plan built at an unaffordable notional.** `plan_tpsl` was
   called with `available * leverage`, which at high leverage lands in a tier
   whose maintenance-margin rate makes the liquidation formula degenerate
   (liq on the wrong side of entry) and raises TpSlSideError for a valid order.

The end-to-end behaviour is covered in test_engine_preflight.py; this file
pins the helpers and the arithmetic.
"""
from __future__ import annotations

import pytest

from jrock.trader.risk import (PairRules, Tier, margin_required,
                               max_notional_for_balance, notional_from_qty,
                               position_size_by_risk, required_cost,
                               spendable_balance, tier_for_leverage,
                               tier_leverage_violation)

AKE = PairRules(
    symbol="AKEUSDT", min_trade_volume=1.0, max_market_order_volume=100000000.0,
    max_limit_order_volume=1500000000.0, base_precision=0, quote_precision=6,
    min_leverage=1, max_leverage=50, default_leverage=10,
    price_protect_scope=0.05, symbol_status="OPEN", api_supported=True,
)

# Real AKEUSDT tiers from the live get_position_tiers endpoint.
AKE_TIERS = [
    Tier(1, 0.0, 10000.0, 50, 0.015),
    Tier(2, 10000.0, 20000.0, 25, 0.02),
    Tier(3, 20000.0, 40000.0, 20, 0.025),
    Tier(4, 40000.0, 80000.0, 10, 0.05),
    Tier(5, 80000.0, 150000.0, 5, 0.1),
]


# --- 1. spendable balance, not total equity ---------------------------------

def test_spendable_balance_is_available_only():
    row = {"available": "9.3044", "frozen": "0", "margin": "50",
           "crossUnrealizedPNL": "5", "isolationUnrealizedPNL": "0"}
    assert spendable_balance(row) == pytest.approx(9.3044)


def test_spendable_balance_ignores_locked_margin_and_unrealized():
    """The exact confusion that produced 20003: equity = 9.3044 + 50 + 5."""
    row = {"available": "9.3044", "margin": "50", "crossUnrealizedPNL": "5"}
    assert spendable_balance(row) == pytest.approx(9.3044)
    assert spendable_balance(row) != pytest.approx(64.3044)


def test_spendable_balance_is_defensive():
    assert spendable_balance({}) == 0.0
    assert spendable_balance(None) == 0.0
    assert spendable_balance({"available": None}) == 0.0
    assert spendable_balance({"available": "not-a-number"}) == 0.0
    assert spendable_balance({"available": "-5"}) == 0.0


def test_sizing_against_available_never_exceeds_it():
    """The whole point: the sized order's cost fits the spendable balance."""
    available = 9.3044
    qty = position_size_by_risk(available, 1.0, 0.005943, 0.005900, 10, AKE,
                                safety_pct=2.0)
    notional = notional_from_qty(qty, 0.005943)
    assert required_cost(notional, 10) <= available


# --- 2. the tier cap is checked against the REAL notional -------------------

def test_tier_cap_allows_a_small_order_at_high_leverage():
    """A ~4k notional is tier 1 (max 50x), so 50x is allowed for it."""
    assert tier_leverage_violation(AKE_TIERS, 4000.0, 50) == ""


def test_tier_cap_refuses_a_large_order_at_high_leverage():
    """A ~385k notional is tier 5 (max 5x), so 50x is genuinely not allowed."""
    msg = tier_leverage_violation(AKE_TIERS, 385307.69, 50)
    assert msg
    assert "exceeds the maximum" in msg
    assert "5x" in msg


def test_tier_cap_checked_at_equity_times_leverage_would_be_wrong():
    """Proof of the bug: the OLD check used available*leverage as the notional.

    available 1000, leverage 50 -> 50k notional -> tier 4 (max 10x) -> the old
    check refused 50x. But the REAL order notional is ~4k (tier 1, max 50x), so
    50x is fine and the refusal was bogus.
    """
    old_notional = 1000.0 * 50          # what the old code checked
    real_notional = 4000.0              # what the order actually is
    assert tier_leverage_violation(AKE_TIERS, old_notional, 50) != ""   # refused
    assert tier_leverage_violation(AKE_TIERS, real_notional, 50) == ""  # allowed


def test_tier_for_leverage_picks_a_band_where_the_leverage_is_valid():
    t = tier_for_leverage(AKE_TIERS, 50)
    assert t is not None
    assert t.leverage >= 50
    assert t.end_value == 10000.0       # the largest band allowing 50x


def test_tier_for_leverage_returns_none_when_no_tier_allows_it():
    assert tier_for_leverage(AKE_TIERS, 200) is None


def test_tier_for_leverage_handles_no_tiers():
    t = tier_for_leverage([], 10)
    assert t is not None and t.leverage >= 10


# --- 3. the provisional plan is built at an affordable notional -------------

def test_provisional_notional_stays_inside_the_leverage_valid_tier():
    """The provisional notional must not exceed the band that allows the
    leverage, or plan_tpsl degenerates and raises for a valid order."""
    available, leverage = 1000.0, 50
    max_notional = max_notional_for_balance(available, leverage)
    t = tier_for_leverage(AKE_TIERS, leverage)
    assert t is not None
    prov = min(max_notional, t.end_value * 0.9999)
    assert prov <= t.end_value
    # and the liquidation formula is sane at that notional
    from jrock.trader.risk import liquidation_price, pick_tier
    mmr = pick_tier(AKE_TIERS, prov).maintenance_margin_rate
    liq = liquidation_price(0.005020, leverage, "LONG", mmr)
    assert 0 < liq < 0.005020


# --- the fee is still reserved (the earlier fix must not regress) -----------

def test_max_notional_still_reserves_the_fee():
    cap = max_notional_for_balance(9.3044, 10)
    assert required_cost(cap, 10) == pytest.approx(9.3044, rel=1e-9)
    assert cap < 9.3044 * 10


def test_margin_alone_would_have_passed_but_cost_does_not():
    """The original 20003: margin == balance passes a margin-only check."""
    notional = notional_from_qty(15656, 0.005943)
    assert margin_required(notional, 10) <= 9.3044
    assert required_cost(notional, 10) > 9.3044
