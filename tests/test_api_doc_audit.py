"""Regression tests for the official-doc audit fixes.

Every assertion here pins a parameter name, a path, or a required field that
was WRONG in the engine before this change. Each one would have failed at the
exchange, so each one is worth a test.

The docs audited (2026-11-07):
  Tp Sl private WS channel, funding rate (single/batch/history), kline,
  trading pairs, the 7 tp_sl REST endpoints, batch_order, adjust_position_margin.
"""
from __future__ import annotations

import asyncio

import pytest

from jrock.config import Settings
from jrock.trader.client import BitunixClient


def _client() -> tuple[BitunixClient, list[dict]]:
    """A client whose transport is captured instead of sent."""
    c = BitunixClient(Settings())
    calls: list[dict] = []

    async def fake_request(method, path, **kw):
        calls.append({"method": method, "path": path,
                      "body": kw.get("body"), "params": kw.get("params")})
        return {"ok": True}

    c.request = fake_request  # type: ignore[assignment]
    return c, calls


# ==================================================== tp_sl: the wrong paths
def test_pending_tp_sl_uses_the_plural_path():
    """Was get_pending_order (singular) - the documented path is plural."""
    c, calls = _client()
    asyncio.run(c.get_pending_tp_sl_order(symbol="BTCUSDT"))
    assert calls[0]["path"] == "/api/v1/futures/tpsl/get_pending_orders"


def test_history_tp_sl_uses_the_plural_path():
    """Was get_history_order (singular) - the documented path is plural."""
    c, calls = _client()
    asyncio.run(c.get_history_tp_sl_order(symbol="BTCUSDT"))
    assert calls[0]["path"] == "/api/v1/futures/tpsl/get_history_orders"


def test_tp_sl_paging_uses_skip_and_limit_not_page_and_pagesize():
    """The docs specify skip/limit (max 100). page/pageSize is not a parameter."""
    c, calls = _client()
    asyncio.run(c.get_pending_tp_sl_order(symbol="BTCUSDT", skip=20, limit=50))
    params = calls[0]["params"]
    assert params["skip"] == 20 and params["limit"] == 50
    assert "page" not in params and "pageSize" not in params


def test_history_tp_sl_uses_starttime_endtime():
    c, calls = _client()
    asyncio.run(c.get_history_tp_sl_order(symbol="BTCUSDT",
                                          start_time=1, end_time=2))
    params = calls[0]["params"]
    assert params["startTime"] == 1 and params["endTime"] == 2


def test_cancel_tp_sl_requires_symbol_as_well_as_orderid():
    """Was orderId only - the docs require BOTH."""
    c, calls = _client()
    asyncio.run(c.cancel_tp_sl_order("BTCUSDT", "123"))
    body = calls[0]["body"]
    assert body == {"symbol": "BTCUSDT", "orderId": "123"}


def test_cancel_tp_sl_signature_refuses_a_missing_symbol():
    c, _ = _client()
    with pytest.raises(TypeError):
        asyncio.run(c.cancel_tp_sl_order("123"))  # type: ignore[call-arg]


# ==================================================== batch_order
def test_batch_order_sends_symbol_and_orderlist():
    """Was orderList only - symbol is a REQUIRED parameter."""
    c, calls = _client()
    asyncio.run(c.batch_order("BTCUSDT", [{"qty": "1"}]))
    body = calls[0]["body"]
    assert body["symbol"] == "BTCUSDT"
    assert body["orderList"] == [{"qty": "1"}]


def test_batch_order_refuses_more_than_five_orders():
    """The docs cap orderList at 5."""
    c, _ = _client()
    with pytest.raises(ValueError, match="at most 5"):
        asyncio.run(c.batch_order("BTCUSDT", [{}] * 6))


def test_batch_order_refuses_an_empty_list():
    c, _ = _client()
    with pytest.raises(ValueError, match="non-empty"):
        asyncio.run(c.batch_order("BTCUSDT", []))


# ==================================================== adjust_position_margin
def test_adjust_position_margin_accepts_side_instead_of_positionid():
    """The docs say "Either side or positionId required" - side alone is valid."""
    c, calls = _client()
    asyncio.run(c.adjust_position_margin("BTCUSDT", "100", side="LONG"))
    body = calls[0]["body"]
    assert body["side"] == "LONG"
    assert "positionId" not in body
    assert body["amount"] == "100" and body["marginCoin"] == "USDT"


def test_adjust_position_margin_accepts_positionid():
    c, calls = _client()
    asyncio.run(c.adjust_position_margin("BTCUSDT", "100", position_id="P1"))
    body = calls[0]["body"]
    assert body["positionId"] == "P1" and "side" not in body


def test_adjust_position_margin_refuses_neither():
    c, _ = _client()
    with pytest.raises(ValueError, match="positionId or side"):
        asyncio.run(c.adjust_position_margin("BTCUSDT", "100"))


def test_adjust_position_margin_keeps_a_negative_amount():
    """A negative amount reduces margin - it must not be dropped as falsy."""
    c, calls = _client()
    asyncio.run(c.adjust_position_margin("BTCUSDT", "-50", side="SHORT"))
    assert calls[0]["body"]["amount"] == "-50"


# ==================================================== funding rate
def test_funding_rate_batch_does_not_require_symbols():
    """The docs mark `symbols` optional (omit = all symbols)."""
    c, calls = _client()
    asyncio.run(c.get_funding_rate_batch())
    assert calls[0]["path"] == "/api/v1/futures/market/funding_rate/batch"


def test_funding_rate_history_uses_starttime_endtime_limit():
    """Was page/pageSize - the docs specify startTime/endTime/limit."""
    c, calls = _client()
    asyncio.run(c.get_funding_rate_history("BTCUSDT", limit=50))
    params = calls[0]["params"]
    assert params["symbol"] == "BTCUSDT" and params["limit"] == 50
    assert "page" not in params and "pageSize" not in params


def test_funding_rate_single_endpoint_exists():
    c, calls = _client()
    asyncio.run(c.get_funding_rate("BTCUSDT"))
    assert calls[0]["path"] == "/api/v1/futures/market/funding_rate"


# ==================================================== kline / pairs
def test_kline_uses_type_not_klinetype():
    """The price-type selector is `type` (LAST_PRICE | MARK_PRICE)."""
    c, calls = _client()
    asyncio.run(c.get_kline("BTCUSDT", "1m", limit=10))
    params = calls[0]["params"]
    assert "type" in params
    assert "klineType" not in params


def test_trading_pairs_endpoint_path():
    c, calls = _client()
    asyncio.run(c.get_trading_pairs())
    assert calls[0]["path"] == "/api/v1/futures/market/trading_pairs"


# ==================================================== the api reference table
def test_api_reference_matches_the_fixed_paths():
    """The reference table is what a future reader trusts - it must not lie."""
    from jrock.trader import api_reference as ar

    by_name = ar.ENDPOINTS
    assert by_name["get_pending_tp_sl_order"].path.endswith("get_pending_orders")
    assert by_name["get_history_tp_sl_order"].path.endswith("get_history_orders")
    assert by_name["batch_order"].path.endswith("batch_order")
    assert by_name["adjust_position_margin"].path.endswith("adjust_position_margin")


def test_api_reference_marks_batch_order_symbol_required():
    from jrock.trader import api_reference as ar

    ep = ar.ENDPOINTS["batch_order"]
    names = {p.name: p for p in ep.params}
    assert names["symbol"].required, "symbol is required per the docs"
    assert names["orderList"].required


def test_api_reference_marks_cancel_tp_sl_symbol_required():
    from jrock.trader import api_reference as ar

    ep = ar.ENDPOINTS["cancel_tp_sl_order"]
    names = {p.name: p for p in ep.params}
    assert names["symbol"].required and names["orderId"].required


def test_api_reference_marks_adjust_margin_side_optional():
    from jrock.trader import api_reference as ar

    ep = ar.ENDPOINTS["adjust_position_margin"]
    names = {p.name: p for p in ep.params}
    assert not names["side"].required
    assert not names["positionId"].required
    assert names["symbol"].required and names["amount"].required


def test_api_reference_place_tp_sl_documents_tpqty():
    """tpQty/slQty are what make the Partial method possible."""
    from jrock.trader import api_reference as ar

    ep = ar.ENDPOINTS["place_tp_sl_order"]
    names = {p.name for p in ep.params}
    assert {"tpQty", "slQty", "tpStopType", "slStopType"} <= names


# ==================================================== private WS channel
def test_private_feed_subscribes_all_four_private_channels():
    """PRIVATE_CHANNELS was defined but never subscribed - only printed."""
    from jrock.trader.ws import PRIVATE_CHANNELS, PrivateFeed, WSConfig

    pf = PrivateFeed(WSConfig())
    subs = {s["ch"] for s in pf.ws.subs}
    assert subs == set(PRIVATE_CHANNELS.values())
    assert "tpsl" in subs


def test_private_feed_parses_a_tpsl_frame():
    """The documented Tp Sl push shape must update engine-visible state."""
    from jrock.trader.ws import Event, PrivateFeed, WSConfig

    pf = PrivateFeed(WSConfig())
    frame = {"ch": "tpsl", "ts": 1794011946619,
             "data": {"id": "T1", "positionId": "P1", "symbol": "BTCUSDT",
                      "tpPrice": "80000", "slPrice": "70000",
                      "tpQty": "1", "slQty": "1", "status": "NEW",
                      "ctime": 1794011946619}}
    ev = Event.from_message(frame)
    assert ev is not None
    pf._make_handler("tpsl")(ev)
    assert pf.counts["tpsl"] == 1
    assert pf.latest["tpsl"]["symbol"] == "BTCUSDT"
    assert pf.tpsl_events[0]["data"]["tpPrice"] == "80000"


def test_private_feed_tpsl_buffer_is_bounded():
    from jrock.trader.ws import Event, PrivateFeed, WSConfig

    pf = PrivateFeed(WSConfig())
    h = pf._make_handler("tpsl")
    for i in range(250):
        h(Event.from_message({"ch": "tpsl", "ts": i, "data": {"id": str(i)}}))
    assert len(pf.tpsl_events) == 200, "the buffer must not grow forever"


def test_private_feed_is_not_opened_in_rest_mode():
    """rest mode must still open ZERO sockets."""
    from jrock.trader.engine import TraderEngine

    s = Settings()
    s.market_data_source = "rest"
    e = TraderEngine(s)
    asyncio.run(e.start_feed())
    assert e.feed is None
    assert e.private_feed is None
    assert "not opened" in e.private_feed_report()


def test_private_feed_report_says_why_when_credentials_are_missing():
    from jrock.trader.engine import TraderEngine

    s = Settings()
    s.market_data_source = "ws"
    s.bitunix_api_key = ""
    s.bitunix_api_secret = ""
    e = TraderEngine(s)
    e.private_feed_error = "no BITUNIX_API_KEY/SECRET - private channels not subscribed"
    report = e.private_feed_report()
    assert "NOT connected" in report
    assert "BITUNIX_API_KEY" in report, "it must say WHY, not just fail silently"


# ==================================================== command reachability
def test_new_commands_are_reachable_from_the_agent():
    """A new endpoint that no command can reach is dead code."""
    from jrock.agent import AGENT_BARE, QUERY_COMMANDS

    assert AGENT_BARE["tpsl"] == "/tpsl"
    assert AGENT_BARE["batch"] == "/batch"
    assert "/tpsl" in QUERY_COMMANDS and "/batch" in QUERY_COMMANDS


def test_tpsl_command_lists_all_four_methods():
    from jrock.agent import JRockAgent

    a = JRockAgent(Settings())
    out = asyncio.run(a.cmd_tpsl(""))
    for m in ("POSITION", "PARTIAL", "TRAILING", "ACCOUNT"):
        assert m in out
    assert "exchange-enforced" in out
    assert "software" in out


def test_tpsl_command_sets_the_method():
    from jrock.agent import JRockAgent

    a = JRockAgent(Settings())
    asyncio.run(a.cmd_tpsl("method PARTIAL"))
    assert a.engine.state.tpsl_method == "PARTIAL"


def test_tpsl_command_refuses_an_unknown_method():
    from jrock.agent import JRockAgent

    a = JRockAgent(Settings())
    out = asyncio.run(a.cmd_tpsl("method NOPE"))
    assert "must be one of" in out
    assert a.engine.state.tpsl_method == "POSITION"


def test_tpsl_cancel_requires_both_arguments():
    from jrock.agent import JRockAgent

    a = JRockAgent(Settings())
    out = asyncio.run(a.cmd_tpsl("cancel BTCUSDT"))
    assert "usage" in out.lower()
    assert "symbol and orderId" in out


def test_batch_command_documents_the_required_params():
    from jrock.agent import JRockAgent

    a = JRockAgent(Settings())
    out = asyncio.run(a.cmd_batch(""))
    assert "symbol AND orderList" in out
    assert "max 5" in out


def test_funding_command_has_a_history_subcommand():
    """Funding history is a PUBLIC endpoint - it must work without credentials."""
    from jrock.agent import JRockAgent

    a = JRockAgent(Settings())
    out = asyncio.run(a.cmd_funding("history BTCUSDT"))
    assert "funding rate history" in out
    assert "BTCUSDT" in out


def test_funding_batch_is_filtered_client_side():
    """The live endpoint ignores `symbols` and returns every symbol."""
    from jrock.trader.client import BitunixClient

    rows = [{"symbol": "BTCUSDT", "fundingRate": "0.0001"},
            {"symbol": "ETHUSDT", "fundingRate": "0.0002"},
            {"symbol": "SOLUSDT", "fundingRate": "0.0003"}]
    out = BitunixClient.filter_funding_rows(rows, ["BTCUSDT", "SOLUSDT"])
    assert [r["symbol"] for r in out] == ["BTCUSDT", "SOLUSDT"]
    # no filter -> everything (the documented "omit = all symbols" behaviour)
    assert len(BitunixClient.filter_funding_rows(rows, [])) == 3


def test_funding_history_sends_limit_not_page():
    """The history endpoint takes limit, not page/pageSize."""
    c, calls = _client()
    asyncio.run(c.get_funding_rate_history("BTCUSDT", limit=25))
    params = calls[0]["params"]
    assert params["limit"] == 25
    assert "page" not in params and "pageSize" not in params
