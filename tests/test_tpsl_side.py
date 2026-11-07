"""Regression tests for TP/SL side validation (Bitunix 30020-30033).

The bug: the engine computed the stop-loss from the ENTRY (last) price only and
never fetched the mark price. Bitunix validates the trigger against the mark
price, so when the mark sat below the last price the stop landed on the wrong
side and the exchange rejected the whole order with 30029.
"""
from __future__ import annotations

import pytest

from jrock.trader.order_builder import OrderValidationError, build_order_body
from jrock.trader.risk import (PairRules, TpSlSideError, min_stop_distance,
                               tick_size, validate_tpsl_side)
from jrock.trader.tpsl import plan_tpsl

# Real AKEUSDT rules from the live trading_pairs endpoint.
AKE = PairRules(
    symbol="AKEUSDT", min_trade_volume=1.0, max_market_order_volume=100000000.0,
    max_limit_order_volume=1500000000.0, base_precision=0, quote_precision=6,
    min_leverage=1, max_leverage=50, default_leverage=10,
    price_protect_scope=0.05, symbol_status="OPEN", api_supported=True,
)

LAST = 0.005136
MARK = 0.005126


class _Tier:
    level = 1
    start_value = 0.0
    end_value = 1e18
    leverage = 50
    maintenance_margin_rate = 0.003


TIERS = [_Tier()]


def _plan(side, *, entry=LAST, mark=MARK, leverage=10, ticks=3):
    return plan_tpsl(side=side, entry=entry, leverage=leverage, notional=20.0,
                     strength="MEDIUM", confidence=70.0, tiers=TIERS, rules=AKE,
                     mark=mark, min_stop_ticks=ticks)


# --- the direction rules -----------------------------------------------------

def test_long_sl_is_below_mark_and_entry():
    p = _plan("LONG")
    assert p.sl < MARK
    assert p.sl < LAST


def test_short_sl_is_above_mark_and_entry():
    p = _plan("SHORT")
    assert p.sl > MARK
    assert p.sl > LAST


def test_long_tp_is_above_mark_and_entry():
    p = _plan("LONG")
    assert p.tp > MARK
    assert p.tp > LAST


def test_short_tp_is_below_mark_and_entry():
    p = _plan("SHORT")
    assert p.tp < MARK
    assert p.tp < LAST


@pytest.mark.parametrize("leverage", [5, 10, 20, 50])
@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_every_leverage_produces_a_valid_plan(side, leverage):
    p = _plan(side, leverage=leverage)
    assert validate_tpsl_side(symbol="AKEUSDT", side=side, entry=LAST, mark=MARK,
                              sl=p.sl, tp=p.tp, rules=AKE, liq=p.liq) == []


# --- the exact 30029 case ----------------------------------------------------

def test_the_reported_30029_case_is_now_rejected_locally():
    """SL 0.0051 with mark 0.005090 is the frame the exchange rejected."""
    v = validate_tpsl_side(symbol="AKEUSDT", side="LONG", entry=LAST,
                           mark=0.005090, sl=0.0051, tp=0.00522, rules=AKE)
    assert v, "an SL above the mark must be reported as a violation"
    assert "must be BELOW" in v[0]
    assert "0.005100" in v[0]
    assert "0.005090" in v[0]


def test_mark_below_last_is_what_inverts_the_side():
    """The old code used the last price, so the stop passed locally."""
    # mark == last: the stop is fine
    assert validate_tpsl_side(symbol="AKEUSDT", side="LONG", entry=LAST,
                              mark=LAST, sl=0.005092, tp=0.00522, rules=AKE) == []
    # mark below the stop: the exchange rejects it
    assert validate_tpsl_side(symbol="AKEUSDT", side="LONG", entry=LAST,
                              mark=0.005090, sl=0.005092, tp=0.00522, rules=AKE)


# --- minimum stop distance ---------------------------------------------------

def test_min_stop_distance_is_ticks_times_tick_size():
    assert tick_size(AKE) == pytest.approx(1e-6)
    assert min_stop_distance(AKE, 3) == pytest.approx(3e-6)
    assert min_stop_distance(AKE, 1) == pytest.approx(1e-6)


def test_rounding_cannot_push_the_sl_across_the_mark():
    """A stop exactly at the mark must be refused, not rounded onto it."""
    v = validate_tpsl_side(symbol="AKEUSDT", side="LONG", entry=MARK, mark=MARK,
                           sl=MARK, tp=0.0053, rules=AKE)
    assert v and "must be BELOW" in v[0]


def test_plan_keeps_the_minimum_gap_from_the_mark():
    p = _plan("LONG", entry=MARK, mark=MARK, leverage=50)
    assert MARK - p.sl >= min_stop_distance(AKE, 3) - 1e-12


def test_coarse_tick_that_cannot_fit_raises_a_named_error():
    """At 50x with a coarse tick there is no room between liq and mark."""
    coarse = PairRules(symbol="AKEUSDT", min_trade_volume=1.0, base_precision=0,
                       quote_precision=4, max_leverage=50)
    with pytest.raises(TpSlSideError) as e:
        plan_tpsl(side="LONG", entry=0.005126, leverage=50, notional=20.0,
                  strength="MEDIUM", confidence=70.0, tiers=TIERS, rules=coarse,
                  mark=0.005126, min_stop_ticks=3)
    assert "no valid stop-loss exists" in str(e.value)


# --- order-builder pre-flight ------------------------------------------------

def _body(**kw):
    base = dict(symbol="AKEUSDT", qty=4016, side="BUY", tradeSide="OPEN",
                orderType="MARKET", tpStopType="MARK_PRICE",
                tpOrderType="MARKET", slStopType="MARK_PRICE",
                slOrderType="MARKET", rules=AKE, mark_price=MARK,
                entry_price=LAST)
    base.update(kw)
    return build_order_body(**base)


def test_preflight_refuses_an_inverted_long_sl():
    # mark 0.005090 sits BELOW the stop 0.0051 -> the exchange answers 30029.
    with pytest.raises(OrderValidationError) as e:
        _body(slPrice=0.0051, tpPrice=0.00522, mark_price=0.005090)
    msg = str(e.value)
    assert "wrong side" in msg
    assert "AKEUSDT LONG" in msg
    assert "0.005100" in msg and "0.005090" in msg


def test_preflight_refuses_an_inverted_short_sl():
    with pytest.raises(OrderValidationError) as e:
        _body(side="SELL", slPrice=0.0050, tpPrice=0.004661)
    assert "AKEUSDT SHORT" in str(e.value)


def test_preflight_passes_a_correct_long():
    b = _body(slPrice=0.004887, tpPrice=0.005611)
    assert b["slPrice"] == "0.004887"
    assert b["tpPrice"] == "0.005611"


def test_preflight_passes_a_correct_short():
    b = _body(side="SELL", slPrice=0.005385, tpPrice=0.004661)
    assert b["slPrice"] == "0.005385"
    assert b["tpPrice"] == "0.004661"


def test_preflight_refuses_an_inverted_tp():
    with pytest.raises(OrderValidationError) as e:
        _body(slPrice=0.004887, tpPrice=0.0050)
    assert "TP" in str(e.value) and "must be ABOVE" in str(e.value)


def test_preflight_requires_a_reference_price():
    with pytest.raises(OrderValidationError) as e:
        build_order_body(symbol="AKEUSDT", qty=4016, side="BUY",
                         tradeSide="OPEN", orderType="MARKET",
                         slPrice=0.004887, rules=AKE)
    assert "mark_price" in str(e.value)


def test_error_message_names_symbol_direction_sl_and_mark():
    with pytest.raises(OrderValidationError) as e:
        _body(slPrice=0.0051, tpPrice=0.00522, mark_price=0.005090)
    msg = str(e.value)
    for token in ("AKEUSDT", "LONG", "0.005100", "0.005090"):
        assert token in msg, f"{token!r} missing from the error message"
