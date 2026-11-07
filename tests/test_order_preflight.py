"""Regression tests for the order-size pre-flight (Bitunix 20003).

The bug: the engine sized the order and sent it without ever comparing the
required margin against the real available balance, so the exchange answered
20003 Insufficient balance instead of the engine refusing locally.
"""
from __future__ import annotations

import pytest

from jrock.trader.risk import (PairRules, Tier, margin_required,
                               notional_from_qty, pick_tier,
                               position_size_by_risk)

AKE = PairRules(
    symbol="AKEUSDT", min_trade_volume=1.0, max_market_order_volume=100000000.0,
    max_limit_order_volume=1500000000.0, base_precision=0, quote_precision=6,
    min_leverage=1, max_leverage=50, default_leverage=10,
    price_protect_scope=0.05, symbol_status="OPEN", api_supported=True,
)

PRICE = 0.005136
STOP = 0.004887


# --- sizing math -------------------------------------------------------------

def test_sizing_produces_the_expected_margin():
    """equity 100, risk 1%, 10x -> ~2.06 USDT margin (the live AKEUSDT case)."""
    qty = position_size_by_risk(100.0, 1.0, PRICE, STOP, 10, AKE)
    assert qty == 4016
    notional = notional_from_qty(qty, PRICE)
    assert notional == pytest.approx(20.6262, abs=1e-3)
    assert margin_required(notional, 10) == pytest.approx(2.0626, abs=1e-3)


def test_sizing_scales_with_equity():
    small = position_size_by_risk(10.0, 1.0, PRICE, STOP, 10, AKE)
    big = position_size_by_risk(100.0, 1.0, PRICE, STOP, 10, AKE)
    assert big == pytest.approx(small * 10, rel=0.01)


def test_sizing_returns_zero_below_min_trade_volume():
    """A tiny account cannot meet minTradeVolume=1, so qty is 0, not a bad order."""
    assert position_size_by_risk(0.0001, 1.0, PRICE, STOP, 10, AKE) == 0.0


def test_margin_required_is_notional_over_leverage():
    assert margin_required(100.0, 10) == pytest.approx(10.0)
    assert margin_required(100.0, 50) == pytest.approx(2.0)
    assert margin_required(100.0, 0) == pytest.approx(100.0)  # never divide by 0


# --- the pre-flight rule -----------------------------------------------------

def _preflight(equity, qty, price, leverage, *, min_margin=0.0,
               max_margin=0.0, max_notional=0.0):
    """Mirror of TraderEngine.try_enter's pre-flight, returns the reason or None."""
    notional = notional_from_qty(qty, price)
    margin = margin_required(notional, leverage)
    if margin > equity:
        return (f"required margin {margin:.4f} USDT exceeds available "
                f"balance {equity:.4f} USDT")
    if min_margin and margin < min_margin:
        return f"below MIN_MARGIN_USDT={min_margin}"
    if max_margin and margin > max_margin:
        return f"exceeds MAX_MARGIN_USDT={max_margin}"
    if max_notional and notional > max_notional:
        return f"exceeds MAX_NOTIONAL_USDT={max_notional}"
    return None


def test_preflight_refuses_when_margin_exceeds_balance():
    # 4016 AKE at 10x needs 2.06 USDT; a 1 USDT account cannot cover it.
    reason = _preflight(1.0, 4016, PRICE, 10)
    assert reason is not None
    assert "required margin" in reason
    assert "available balance" in reason


def test_preflight_passes_when_it_fits():
    assert _preflight(100.0, 4016, PRICE, 10) is None


def test_preflight_message_names_both_numbers():
    reason = _preflight(1.0, 4016, PRICE, 10)
    assert "2.0626" in reason   # required margin
    assert "1.0000" in reason   # available balance


def test_min_margin_bound_is_respected():
    assert _preflight(100.0, 4016, PRICE, 10, min_margin=5.0) is not None
    assert _preflight(100.0, 4016, PRICE, 10, min_margin=1.0) is None


def test_max_margin_bound_is_respected():
    assert _preflight(100.0, 4016, PRICE, 10, max_margin=1.0) is not None
    assert _preflight(100.0, 4016, PRICE, 10, max_margin=5.0) is None


def test_max_notional_bound_is_respected():
    assert _preflight(100.0, 4016, PRICE, 10, max_notional=10.0) is not None
    assert _preflight(100.0, 4016, PRICE, 10, max_notional=50.0) is None


def test_bounds_default_to_disabled():
    """0 means 'no bound', so today's behaviour is unchanged by default."""
    assert _preflight(100.0, 4016, PRICE, 10, min_margin=0.0,
                      max_margin=0.0, max_notional=0.0) is None


# --- exchange rules are enforced ---------------------------------------------

def test_exchange_min_trade_volume_is_enforced():
    from jrock.trader.order_builder import OrderValidationError, build_order_body
    with pytest.raises(OrderValidationError) as e:
        build_order_body(symbol="AKEUSDT", qty=0, side="BUY", tradeSide="OPEN",
                         orderType="MARKET", rules=AKE)
    assert "minTradeVolume" in str(e.value)


def test_exchange_max_market_order_volume_is_enforced():
    from jrock.trader.order_builder import OrderValidationError, build_order_body
    with pytest.raises(OrderValidationError) as e:
        build_order_body(symbol="AKEUSDT", qty=200000000, side="BUY",
                         tradeSide="OPEN", orderType="MARKET", rules=AKE)
    assert "maxMarketOrderVolume" in str(e.value)


def test_config_exposes_the_margin_bounds():
    from jrock.config import Settings
    s = Settings()
    assert s.min_margin_usdt == 0.0
    assert s.max_margin_usdt == 0.0
    assert s.max_notional_usdt == 0.0
    assert s.tpsl_min_stop_ticks == 3


# --- tier leverage cap (a second, real 20003 cause) --------------------------

# Real AKEUSDT tiers from the live get_position_tiers endpoint.
AKE_TIERS = [
    Tier(1, 0.0, 10000.0, 50, 0.015),
    Tier(2, 10000.0, 20000.0, 25, 0.02),
    Tier(3, 20000.0, 40000.0, 20, 0.025),
    Tier(4, 40000.0, 80000.0, 10, 0.05),
    Tier(5, 80000.0, 150000.0, 5, 0.1),
]


def test_tier_caps_leverage_for_the_notional_band():
    assert pick_tier(AKE_TIERS, 5000).leverage == 50
    assert pick_tier(AKE_TIERS, 15000).leverage == 25
    assert pick_tier(AKE_TIERS, 30000).leverage == 20
    assert pick_tier(AKE_TIERS, 60000).leverage == 10
    assert pick_tier(AKE_TIERS, 100000).leverage == 5


def test_engine_leverage_above_the_tier_max_is_refused():
    """50x on a 15k notional is tier 2 (max 25x) -> the exchange wants more
    margin than we computed, which surfaces as 20003."""
    t = pick_tier(AKE_TIERS, 15000.0)
    assert 50 > t.leverage
    # the margin the exchange actually requires is 2x what we assumed
    assert margin_required(15000.0, t.leverage) == pytest.approx(600.0)
    assert margin_required(15000.0, 50) == pytest.approx(300.0)


def test_engine_leverage_within_the_tier_max_is_allowed():
    t = pick_tier(AKE_TIERS, 15000.0)
    assert 25 <= t.leverage
    assert 10 <= t.leverage
