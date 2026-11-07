"""Regression tests for the 4 TP/SL variants, the 3 order units, and the
configurable MARK_PRICE / LAST_PRICE trigger type.

The point of these tests is the 30029 class of bug: the local pre-flight
validation and the order body must use the SAME trigger price. A drift between
them is what let a stop pass locally and be rejected by the exchange.
"""
from __future__ import annotations

import itertools

import pytest

from jrock.config import Settings, load_settings
from jrock.trader.order_builder import (ORDER_PRICE_TYPES, ORDER_TYPES,
                                        STOP_TYPES, OrderValidationError,
                                        build_order_body)
from jrock.trader.risk import (ORDER_UNITS, PairRules, qty_from_unit,
                               unit_from_qty, validate_tpsl_side)

SYM = "AKEUSDT"
PRICE = 0.005719
MARK = 0.005709

PAIR = {
    "symbol": SYM, "minTradeVolume": "1",
    "maxMarketOrderVolume": "100000000", "maxLimitOrderVolume": "1500000000",
    "basePrecision": 0, "quotePrecision": 6, "maxLeverage": 50, "minLeverage": 1,
    "defaultLeverage": 10, "priceProtectScope": "0.05", "symbolStatus": "OPEN",
    "isApiSupported": True,
}


@pytest.fixture
def rules() -> PairRules:
    return PairRules.from_api(PAIR)


# --------------------------------------------------------------- 4 variants
def test_four_tpsl_variants_exist():
    """The two enums are independent, so there are exactly 4 combinations."""
    assert ORDER_PRICE_TYPES == ("LIMIT", "MARKET")
    assert STOP_TYPES == ("MARK_PRICE", "LAST_PRICE")
    assert len(list(itertools.product(ORDER_PRICE_TYPES, STOP_TYPES))) == 4


@pytest.mark.parametrize("order_type,stop_type",
                         list(itertools.product(ORDER_PRICE_TYPES, STOP_TYPES)))
@pytest.mark.parametrize("side,sl,tp", [
    ("BUY", PRICE * 0.90, PRICE * 1.10),    # LONG
    ("SELL", PRICE * 1.10, PRICE * 0.90),   # SHORT
])
def test_each_tpsl_variant_produces_correct_body(rules, order_type, stop_type,
                                                 side, sl, tp):
    """Every one of the 4 variants x 2 directions lands in the body verbatim."""
    body = build_order_body(
        symbol=SYM, qty=1000, side=side, tradeSide="OPEN",
        orderType="MARKET", reduceOnly=False,
        tpPrice=tp, tpStopType=stop_type, tpOrderType=order_type,
        slPrice=sl, slStopType=stop_type, slOrderType=order_type,
        rules=rules, known_symbols={SYM},
        mark_price=MARK, entry_price=PRICE,
    )
    assert body["tpOrderType"] == order_type
    assert body["slOrderType"] == order_type
    assert body["tpStopType"] == stop_type
    assert body["slStopType"] == stop_type
    assert body["tpPrice"] == f"{tp:.6f}"
    assert body["slPrice"] == f"{sl:.6f}"


def test_tpsl_variant_rejects_unknown_order_type(rules):
    with pytest.raises(OrderValidationError) as e:
        build_order_body(symbol=SYM, qty=1000, side="BUY", tradeSide="OPEN",
                         orderType="MARKET", tpPrice=PRICE * 1.1,
                         tpOrderType="STOP_MARKET", rules=rules,
                         mark_price=MARK, entry_price=PRICE)
    assert "tpOrderType" in str(e.value)


def test_tpsl_variant_rejects_unknown_stop_type(rules):
    with pytest.raises(OrderValidationError) as e:
        build_order_body(symbol=SYM, qty=1000, side="BUY", tradeSide="OPEN",
                         orderType="MARKET", slPrice=PRICE * 0.9,
                         slStopType="INDEX_PRICE", rules=rules,
                         mark_price=MARK, entry_price=PRICE)
    assert "slStopType" in str(e.value)


# ------------------------------------------------------------- 3 order units
def test_three_order_units_exist():
    assert ORDER_UNITS == ("QUANTITY", "NOTIONAL", "COST")


def test_quantity_unit_is_identity(rules):
    assert qty_from_unit(100.0, "QUANTITY", PRICE, 10, rules) == 100.0


def test_notional_unit_divides_by_price(rules):
    q = qty_from_unit(100.0, "NOTIONAL", PRICE, 10, rules)
    assert q == pytest.approx(100.0 / PRICE, rel=1e-3)


def test_cost_unit_multiplies_by_leverage(rules):
    q = qty_from_unit(100.0, "COST", PRICE, 10, rules)
    assert q == pytest.approx(100.0 * 10 / PRICE, rel=1e-3)


def test_cost_unit_is_leverage_times_notional_unit(rules):
    n = qty_from_unit(100.0, "NOTIONAL", PRICE, 10, rules)
    c = qty_from_unit(100.0, "COST", PRICE, 10, rules)
    assert c == pytest.approx(n * 10, rel=1e-3)


@pytest.mark.parametrize("unit", ORDER_UNITS)
def test_order_unit_round_trips(rules, unit):
    q = qty_from_unit(100.0, unit, PRICE, 10, rules)
    back = unit_from_qty(q, unit, PRICE, 10)
    assert back == pytest.approx(100.0, rel=1e-2)


def test_unknown_order_unit_raises(rules):
    with pytest.raises(ValueError) as e:
        qty_from_unit(100.0, "CONTRACTS", PRICE, 10, rules)
    assert "CONTRACTS" in str(e.value)


def test_order_unit_never_changes_the_wire_field(rules):
    """The wire format is always base-coin qty, whatever the unit."""
    for unit in ORDER_UNITS:
        q = qty_from_unit(100.0, unit, PRICE, 10, rules)
        body = build_order_body(symbol=SYM, qty=q, side="BUY", tradeSide="OPEN",
                                orderType="MARKET", reduceOnly=False,
                                rules=rules, known_symbols={SYM})
        assert set(body) >= {"symbol", "qty", "side", "tradeSide", "orderType"}
        assert "notional" not in body and "cost" not in body


# ------------------------------------------------------- trigger type config
def test_trigger_type_defaults_to_mark_price():
    assert Settings().tp_sl_trigger_type == "MARK_PRICE"


def test_order_unit_defaults_to_quantity():
    assert Settings().order_unit == "QUANTITY"


def test_trigger_type_reads_env(monkeypatch):
    monkeypatch.setenv("TP_SL_TRIGGER_TYPE", "LAST_PRICE")
    assert load_settings().tp_sl_trigger_type == "LAST_PRICE"


def test_trigger_type_is_case_insensitive(monkeypatch):
    monkeypatch.setenv("TP_SL_TRIGGER_TYPE", "last_price")
    assert load_settings().tp_sl_trigger_type == "LAST_PRICE"


def test_trigger_type_falls_back_on_garbage(monkeypatch):
    monkeypatch.setenv("TP_SL_TRIGGER_TYPE", "INDEX_PRICE")
    assert load_settings().tp_sl_trigger_type == "MARK_PRICE"


def test_order_unit_reads_env(monkeypatch):
    monkeypatch.setenv("ORDER_UNIT", "COST")
    assert load_settings().order_unit == "COST"


def test_order_unit_falls_back_on_garbage(monkeypatch):
    monkeypatch.setenv("ORDER_UNIT", "CONTRACTS")
    assert load_settings().order_unit == "QUANTITY"


@pytest.mark.parametrize("stop_type", STOP_TYPES)
def test_trigger_type_reaches_the_order_body(rules, stop_type):
    body = build_order_body(
        symbol=SYM, qty=1000, side="BUY", tradeSide="OPEN",
        orderType="MARKET", reduceOnly=False,
        tpPrice=PRICE * 1.10, tpStopType=stop_type, tpOrderType="MARKET",
        slPrice=PRICE * 0.90, slStopType=stop_type, slOrderType="MARKET",
        rules=rules, known_symbols={SYM},
        mark_price=MARK, entry_price=PRICE,
    )
    assert body["tpStopType"] == stop_type
    assert body["slStopType"] == stop_type


# ------------------------------------- pre-flight and body must not disagree
def test_preflight_and_body_use_the_same_trigger_price(rules):
    """The 30029 regression: validate against the SAME price the body implies.

    A stop that is valid against the mark but invalid against the last price
    (or vice versa) must be caught by the pre-flight, not by the exchange.
    """
    # A stop that is fine against the mark but on the wrong side of the last
    # price: mark 0.005709, last 0.005719, stop 0.005715.
    sl = 0.005715
    assert sl < PRICE and sl > MARK          # below last, above mark

    # Validated against the MARK price -> violation (stop is above the mark).
    v_mark = validate_tpsl_side(symbol=SYM, side="LONG", entry=PRICE, mark=MARK,
                                sl=sl, tp=None, rules=rules, min_ticks=3)
    assert v_mark, "a stop above the mark must be flagged for a LONG"

    # Validated against the LAST price -> no violation.
    v_last = validate_tpsl_side(symbol=SYM, side="LONG", entry=PRICE, mark=PRICE,
                                sl=sl, tp=None, rules=rules, min_ticks=3)
    assert not v_last

    # The order body must apply the SAME rule as the pre-flight. With the mark
    # as the reference it must refuse; with the last price it must pass.
    with pytest.raises(OrderValidationError):
        build_order_body(symbol=SYM, qty=1000, side="BUY", tradeSide="OPEN",
                         orderType="MARKET", slPrice=sl, slStopType="MARK_PRICE",
                         slOrderType="MARKET", rules=rules,
                         known_symbols={SYM}, mark_price=MARK, entry_price=PRICE)
    body = build_order_body(symbol=SYM, qty=1000, side="BUY", tradeSide="OPEN",
                            orderType="MARKET", slPrice=sl, slStopType="LAST_PRICE",
                            slOrderType="MARKET", rules=rules,
                            known_symbols={SYM}, mark_price=PRICE, entry_price=PRICE)
    assert body["slPrice"] == f"{sl:.6f}"


def test_body_refuses_when_no_reference_price_is_supplied(rules):
    """Without a reference price the correct side is unknown - refuse, don't guess."""
    with pytest.raises(OrderValidationError) as e:
        build_order_body(symbol=SYM, qty=1000, side="BUY", tradeSide="OPEN",
                         orderType="MARKET", slPrice=PRICE * 0.9, rules=rules,
                         known_symbols={SYM})
    assert "mark_price" in str(e.value)


# ------------------------------------------------------------- order types
def test_two_place_order_order_types(rules):
    assert ORDER_TYPES == ("LIMIT", "MARKET")


def test_market_order_has_no_price(rules):
    body = build_order_body(symbol=SYM, qty=1000, side="BUY", tradeSide="OPEN",
                            orderType="MARKET", reduceOnly=False,
                            rules=rules, known_symbols={SYM})
    assert "price" not in body and "effect" not in body


def test_limit_order_requires_price_and_defaults_effect(rules):
    body = build_order_body(symbol=SYM, qty=1000, side="BUY", tradeSide="OPEN",
                            orderType="LIMIT", price=PRICE,
                            rules=rules, known_symbols={SYM})
    assert body["price"] == f"{PRICE:.6f}"
    assert body["effect"] == "GTC"


def test_limit_order_without_price_is_refused(rules):
    with pytest.raises(OrderValidationError) as e:
        build_order_body(symbol=SYM, qty=1000, side="BUY", tradeSide="OPEN",
                         orderType="LIMIT", rules=rules, known_symbols={SYM})
    assert "price" in str(e.value)
