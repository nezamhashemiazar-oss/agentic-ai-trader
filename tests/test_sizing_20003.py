"""Regression tests for 20003 Insufficient balance on the entry path.

The bug: `position_size_by_risk` capped the size at `equity * leverage`, so the
initial margin came out EQUAL to the whole available balance. The pre-flight
compared only the margin (`if margin_real > equity`), so `margin == equity`
passed locally - and the exchange then added the 0.06% taker fee and answered
20003 Insufficient balance.

Live numbers from the failed AKEUSDT order:
    price 0.005943, qty 15656, notional 93.0436, leverage 10x
    margin 9.3044 == the account's available balance
"""
from __future__ import annotations

import pytest

from jrock.trader.risk import (TAKER_FEE_RATE, PairRules, fee_for_notional,
                               margin_required, max_notional_for_balance,
                               notional_from_qty, position_size_by_risk,
                               required_cost)

AKE = PairRules(
    symbol="AKEUSDT", min_trade_volume=1.0, max_market_order_volume=100000000.0,
    max_limit_order_volume=1500000000.0, base_precision=0, quote_precision=6,
    min_leverage=1, max_leverage=50, default_leverage=10,
    price_protect_scope=0.05, symbol_status="OPEN", api_supported=True,
)

# The exact numbers from the live failure.
PRICE = 0.005943
EQUITY = 9.3044
LEVERAGE = 10
FAILED_QTY = 15656


# --- the fee is real and is what tipped it over ------------------------------

def test_taker_fee_rate_is_bitunix_vip0():
    assert TAKER_FEE_RATE == 0.0006


def test_fee_on_the_failed_notional_exceeds_the_shortfall():
    """margin == equity, so ANY fee makes the order unaffordable."""
    notional = notional_from_qty(FAILED_QTY, PRICE)
    margin = margin_required(notional, LEVERAGE)
    fee = fee_for_notional(notional)
    # the margin alone consumed the entire balance
    assert margin == pytest.approx(EQUITY, abs=1e-3)
    # and the fee is a real, positive amount on top
    assert fee == pytest.approx(0.0558, abs=1e-3)
    assert margin + fee > EQUITY


def test_required_cost_is_margin_plus_fee():
    assert required_cost(100.0, 10) == pytest.approx(10.0 + 0.06)


# --- the sizing cap now leaves room for the fee ------------------------------

def test_max_notional_accounts_for_the_fee():
    cap = max_notional_for_balance(EQUITY, LEVERAGE)
    # strictly less than the old equity*leverage cap
    assert cap < EQUITY * LEVERAGE
    # and the cost of that notional fits the balance
    assert required_cost(cap, LEVERAGE) == pytest.approx(EQUITY, rel=1e-9)


def test_sizing_no_longer_asks_for_more_than_the_balance():
    """The regression: the old cap produced qty=15656 with margin == equity."""
    qty = position_size_by_risk(EQUITY, 1.0, PRICE, PRICE - 0.000001, LEVERAGE, AKE)
    notional = notional_from_qty(qty, PRICE)
    assert qty < FAILED_QTY
    assert required_cost(notional, LEVERAGE) <= EQUITY


def test_sizing_respects_the_safety_buffer():
    plain = position_size_by_risk(EQUITY, 1.0, PRICE, PRICE - 0.000001, LEVERAGE, AKE)
    buffered = position_size_by_risk(EQUITY, 1.0, PRICE, PRICE - 0.000001, LEVERAGE,
                                     AKE, safety_pct=2.0)
    assert buffered <= plain
    notional = notional_from_qty(buffered, PRICE)
    assert required_cost(notional, LEVERAGE) <= EQUITY * 0.98 + 1e-9


def test_sizing_still_returns_zero_below_min_volume():
    assert position_size_by_risk(0.0001, 1.0, PRICE, PRICE - 0.000001, LEVERAGE, AKE) == 0.0


def test_zero_fee_rate_reproduces_the_old_cap():
    """With the fee disabled the cap is the historical equity*leverage."""
    cap = max_notional_for_balance(EQUITY, LEVERAGE, fee_rate=0.0)
    assert cap == pytest.approx(EQUITY * LEVERAGE)


# --- the pre-flight message is actionable ------------------------------------

def _preflight(equity, qty, price, leverage, *, fee_rate=TAKER_FEE_RATE,
               safety_pct=0.0):
    """Mirror of TraderEngine.try_enter's fee-aware pre-flight."""
    notional = notional_from_qty(qty, price)
    margin = margin_required(notional, leverage)
    fee = fee_for_notional(notional, fee_rate)
    required = margin + fee
    usable = equity * (1.0 - max(0.0, min(0.9, safety_pct / 100.0)))
    if required > usable:
        return (f"insufficient balance for AKEUSDT: required {required:.4f} USDT "
                f"(margin {margin:.4f} + fee {fee:.4f}) vs available "
                f"{equity:.4f} USDT")
    return None


def test_preflight_rejects_the_exact_failed_order():
    """qty=15656 at 9.3044 equity must now be refused LOCALLY, not by the exchange."""
    reason = _preflight(EQUITY, FAILED_QTY, PRICE, LEVERAGE)
    assert reason is not None
    assert "insufficient balance" in reason


def test_preflight_message_names_margin_fee_and_available():
    reason = _preflight(EQUITY, FAILED_QTY, PRICE, LEVERAGE)
    assert "margin 9.3044" in reason
    assert "fee 0.0558" in reason
    assert "available 9.3044" in reason


def test_preflight_passes_for_a_fee_aware_size():
    qty = position_size_by_risk(EQUITY, 1.0, PRICE, PRICE - 0.000001, LEVERAGE, AKE)
    assert _preflight(EQUITY, qty, PRICE, LEVERAGE) is None


def test_preflight_old_margin_only_check_would_have_passed():
    """Proof of the bug: comparing margin alone lets the failed order through."""
    notional = notional_from_qty(FAILED_QTY, PRICE)
    assert margin_required(notional, LEVERAGE) <= EQUITY  # old check: passes
    assert required_cost(notional, LEVERAGE) > EQUITY     # reality: fails


# --- config surface ----------------------------------------------------------

def test_config_exposes_the_fee_settings():
    from jrock.config import Settings
    s = Settings()
    assert s.taker_fee_rate == 0.0006
    assert s.margin_safety_pct == 2.0
