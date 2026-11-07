"""Regression tests for the validated order builder (10002 Parameter error)."""
import pytest

from jrock.trader.errors import BitunixAPIError
from jrock.trader.order_builder import (OrderValidationError, build_order_body,
                                        debug_order_enabled, log_order_body)
from jrock.trader.risk import PairRules

# AKEUSDT: basePrecision 0 (integer qty), quotePrecision 6, minTradeVolume 1
AKE = PairRules(symbol="AKEUSDT", min_trade_volume=1.0,
                max_market_order_volume=100000000.0, max_limit_order_volume=1500000000.0,
                base_precision=0, quote_precision=6, min_leverage=1, max_leverage=50,
                default_leverage=10, price_protect_scope=0.05,
                symbol_status="OPEN", api_supported=True)

BTC = PairRules(symbol="BTCUSDT", min_trade_volume=0.0001,
                max_market_order_volume=120.0, max_limit_order_volume=1200.0,
                base_precision=4, quote_precision=1, min_leverage=1, max_leverage=200,
                default_leverage=10, price_protect_scope=0.01,
                symbol_status="OPEN", api_supported=True)


def test_from_api_preserves_zero_precision():
    """The 10002 root cause: basePrecision 0 must NOT be coerced to the default."""
    d = {"symbol": "AKEUSDT", "basePrecision": 0, "quotePrecision": 6,
         "minTradeVolume": "1", "maxMarketOrderVolume": "100000000",
         "maxLeverage": 50, "minLeverage": 1, "defaultLeverage": 10,
         "priceProtectScope": "0.05", "symbolStatus": "OPEN", "isApiSupported": True}
    r = PairRules.from_api(d)
    assert r.base_precision == 0
    assert r.quote_precision == 6
    assert r.min_trade_volume == 1.0


def test_akeusdt_qty_formatted_to_integer():
    """AKEUSDT requires integer qty; a 4-decimal qty is the 10002."""
    body = build_order_body(symbol="AKEUSDT", qty=1686.3406, side="buy",
                            orderType="market", tradeSide="open",
                            rules=AKE, known_symbols={"AKEUSDT"})
    assert body["qty"] == "1686"          # floored to basePrecision 0
    assert body["side"] == "BUY"          # normalized
    assert body["orderType"] == "MARKET"
    assert body["tradeSide"] == "OPEN"
    assert "price" not in body            # absent for MARKET
    assert "effect" not in body


def test_lowercase_side_normalized():
    body = build_order_body(symbol="BTCUSDT", qty="0.5", side="buy",
                            orderType="limit", tradeSide="open", price="60000",
                            rules=BTC, known_symbols={"BTCUSDT"})
    assert body["side"] == "BUY"


def test_float_qty_sent_as_string():
    """A float qty is accepted as input but ALWAYS serialized as a string."""
    body = build_order_body(symbol="BTCUSDT", qty=0.5, side="BUY",
                            orderType="MARKET", tradeSide="OPEN",
                            rules=BTC, known_symbols={"BTCUSDT"})
    assert body["qty"] == "0.5000"   # formatted to basePrecision 4
    assert isinstance(body["qty"], str)


def test_qty_below_min_rejected():
    with pytest.raises(OrderValidationError) as e:
        build_order_body(symbol="AKEUSDT", qty="0.5", side="BUY",
                         orderType="MARKET", tradeSide="OPEN",
                         rules=AKE, known_symbols={"AKEUSDT"})
    assert "minTradeVolume" in str(e.value)


def test_price_on_market_rejected():
    with pytest.raises(OrderValidationError) as e:
        build_order_body(symbol="BTCUSDT", qty="0.5", side="BUY",
                         orderType="MARKET", tradeSide="OPEN", price="60000",
                         rules=BTC, known_symbols={"BTCUSDT"})
    assert "price" in str(e.value) and "MARKET" in str(e.value)


def test_missing_price_on_limit_rejected():
    with pytest.raises(OrderValidationError) as e:
        build_order_body(symbol="BTCUSDT", qty="0.5", side="BUY",
                         orderType="LIMIT", tradeSide="OPEN",
                         rules=BTC, known_symbols={"BTCUSDT"})
    assert "price" in str(e.value) and "LIMIT" in str(e.value)


def test_unknown_symbol_rejected_with_closest():
    with pytest.raises(OrderValidationError) as e:
        build_order_body(symbol="AKEUSDT", qty="1", side="BUY",
                         orderType="MARKET", tradeSide="OPEN",
                         rules=AKE, known_symbols={"BTCUSDT", "ETHUSDT", "CAKEUSDT"})
    assert "not a listed futures symbol" in str(e.value)
    assert "CAKEUSDT" in str(e.value)     # closest match


def test_invalid_side_rejected():
    with pytest.raises(OrderValidationError) as e:
        build_order_body(symbol="BTCUSDT", qty="0.5", side="LONG",
                         orderType="MARKET", tradeSide="OPEN",
                         rules=BTC, known_symbols={"BTCUSDT"})
    assert "side" in str(e.value)


def test_invalid_effect_value_rejected():
    with pytest.raises(OrderValidationError) as e:
        build_order_body(symbol="BTCUSDT", qty="0.5", side="BUY",
                         orderType="LIMIT", tradeSide="OPEN", price="60000",
                         effect="DAY", rules=BTC, known_symbols={"BTCUSDT"})
    assert "effect" in str(e.value)


def test_close_requires_position_id():
    with pytest.raises(OrderValidationError) as e:
        build_order_body(symbol="BTCUSDT", qty="0.5", side="SELL",
                         orderType="MARKET", tradeSide="CLOSE",
                         rules=BTC, known_symbols={"BTCUSDT"})
    assert "positionId" in str(e.value)


def test_reduce_only_must_be_bool():
    with pytest.raises(OrderValidationError) as e:
        build_order_body(symbol="BTCUSDT", qty="0.5", side="SELL",
                         orderType="MARKET", tradeSide="CLOSE", positionId="1",
                         reduceOnly="yes", rules=BTC, known_symbols={"BTCUSDT"})
    assert "reduceOnly" in str(e.value)


def test_client_id_validation():
    with pytest.raises(OrderValidationError) as e:
        build_order_body(symbol="BTCUSDT", qty="0.5", side="BUY",
                         orderType="MARKET", tradeSide="OPEN",
                         clientId="has space!", rules=BTC, known_symbols={"BTCUSDT"})
    assert "clientId" in str(e.value)


def test_tp_sl_strings_and_stop_types():
    body = build_order_body(symbol="BTCUSDT", qty="0.5", side="BUY",
                            orderType="MARKET", tradeSide="OPEN",
                            tpPrice="61000", tpStopType="mark_price",
                            tpOrderType="market", slPrice="59000",
                            slStopType="mark_price", slOrderType="market",
                            rules=BTC, known_symbols={"BTCUSDT"},
                            mark_price=60000, entry_price=60000)
    assert body["tpStopType"] == "MARK_PRICE"
    assert body["slStopType"] == "MARK_PRICE"
    assert body["tpOrderType"] == "MARKET"
    assert body["slOrderType"] == "MARKET"


def test_tp_sl_prices_are_strings():
    """tpPrice/slPrice must be STRINGS respecting quotePrecision (a float is a 10002)."""
    body = build_order_body(symbol="AKEUSDT", qty=1686.3406, side="BUY",
                            orderType="MARKET", tradeSide="OPEN",
                            tpPrice=0.013961, slPrice=0.011752,
                            tpStopType="MARK_PRICE", tpOrderType="MARKET",
                            slStopType="MARK_PRICE", slOrderType="MARKET",
                            rules=AKE, known_symbols={"AKEUSDT"},
                            mark_price=0.0128, entry_price=0.0128)
    assert isinstance(body["tpPrice"], str)
    assert isinstance(body["slPrice"], str)
    assert body["tpPrice"] == "0.013961"   # quotePrecision 6
    assert body["slPrice"] == "0.011752"


def test_debug_order_flag():
    import os
    os.environ["BITUNIX_DEBUG_ORDER"] = "true"
    assert debug_order_enabled()
    os.environ["BITUNIX_DEBUG_ORDER"] = "false"
    assert not debug_order_enabled()


def test_log_order_body_does_not_raise():
    # must not raise even with a BitunixAPIError
    log_order_body({"symbol": "BTCUSDT", "qty": "0.5"},
                   BitunixAPIError(10002, "Parameter Error"))
    log_order_body({"symbol": "BTCUSDT", "qty": "0.5"})