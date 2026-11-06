"""The embedded API reference must match the real client methods.

This is the guard against drift: if someone adds a client method without
documenting it (or documents an endpoint the client cannot call), this fails.
"""
import inspect

from jrock.trader.api_reference import ENDPOINTS, all_endpoints, describe_endpoint
from jrock.trader.client import BitunixClient

# client methods that are helpers, not endpoints
HELPERS = {"start", "close", "sign", "request", "sync_time", "ping",
           "verify_credentials", "_headers", "__aenter__", "__aexit__"}


def client_endpoint_methods() -> set[str]:
    out = set()
    for name, _ in inspect.getmembers(BitunixClient, predicate=inspect.isfunction):
        if name.startswith("_") or name in HELPERS:
            continue
        out.add(name)
    return out


def test_every_client_method_is_documented():
    missing = client_endpoint_methods() - set(ENDPOINTS)
    assert not missing, f"client methods with no API reference entry: {sorted(missing)}"


def test_every_documented_endpoint_exists_on_client():
    extra = set(ENDPOINTS) - client_endpoint_methods()
    assert not extra, f"documented endpoints with no client method: {sorted(extra)}"


def test_all_required_endpoints_present():
    required = {
        "get_tickers", "change_log", "adjust_position_margin", "change_leverage",
        "change_margin_mode", "change_position_mode", "get_leverage_and_margin_mode",
        "get_single_account", "asset_query", "get_depth", "get_funding_rate_batch",
        "get_funding_rate_history", "get_funding_rate", "get_kline",
        "get_trading_pairs", "get_history_positions", "get_pending_positions",
        "get_position_tiers", "cancel_tp_sl_order", "get_history_tp_sl_order",
        "get_pending_tp_sl_order", "modify_position_tp_sl_order", "modify_tp_sl_order",
        "place_position_tp_sl_order", "place_tp_sl_order", "batch_order",
        "cancel_all_orders", "cancel_orders", "close_all_position",
        "flash_close_position", "get_history_orders", "get_history_trades",
        "get_order_detail", "get_pending_orders", "modify_order", "place_order",
    }
    missing = required - set(ENDPOINTS)
    assert not missing, f"spec-required endpoints missing: {sorted(missing)}"


def test_place_order_contract():
    ep = ENDPOINTS["place_order"]
    assert ep.method == "POST"
    assert ep.path == "/api/v1/futures/trade/place_order"
    assert ep.signed is True
    required = {p.name for p in ep.params if p.required}
    assert required == {"symbol", "qty", "side", "tradeSide", "orderType"}


def test_validate_catches_missing_and_unknown():
    ep = ENDPOINTS["place_order"]
    errs = ep.validate({"symbol": "BTCUSDT"})
    assert any("missing required" in e for e in errs)
    errs = ep.validate({"symbol": "BTCUSDT", "qty": "1", "side": "BUY",
                        "tradeSide": "OPEN", "orderType": "MARKET", "bogus": 1})
    assert any("unknown params" in e for e in errs)


def test_validate_passes_on_good_call():
    ep = ENDPOINTS["place_order"]
    errs = ep.validate({"symbol": "BTCUSDT", "qty": "0.01", "side": "BUY",
                        "tradeSide": "OPEN", "orderType": "MARKET"})
    assert errs == []


def test_public_endpoints_are_unsigned():
    for name in ("get_tickers", "get_kline", "get_trading_pairs", "get_position_tiers"):
        assert ENDPOINTS[name].signed is False, f"{name} should be public"


def test_describe_endpoint_works():
    out = describe_endpoint("get_kline")
    assert "/api/v1/futures/market/kline" in out
    assert "interval" in out
    assert "unknown endpoint" in describe_endpoint("nope")


def test_endpoint_count():
    assert len(all_endpoints()) >= 36
