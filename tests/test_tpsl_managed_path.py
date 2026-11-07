"""Regression tests for the SECOND 30029: the running-engine TP/SL path.

The 58f901c fix made `plan_tpsl` mark-aware, but the management loop
(`manage_positions`: breakeven + trailing) built its own stop-loss with a
hardcoded `round_price(..., 4)` and never fetched the mark price at all.

On AKEUSDT (quotePrecision 6, price ~0.005) rounding to 4 decimals turns
0.005051 into 0.005100 - above the mark - and the exchange answers:

    [30029] SL price must be less than mark price: 0.0051

The `0.0051` in that message is the fingerprint: four decimals on a symbol
that trades in six.

These tests pin the whole class of bug:
  * the exact reported failure, replayed
  * both directions (LONG and SHORT)
  * direction-aware rounding (a clamp must never round back across the mark)
  * the running-engine path end to end, with a fake client
  * the mark price being read FRESH at order time, not reused
"""
from __future__ import annotations

import asyncio

import pytest

from jrock.config import Settings
from jrock.trader.engine import TraderEngine
from jrock.trader.risk import (PairRules, ceil_price, clamp_sl, clamp_tp,
                               floor_price, format_price, round_price,
                               tick_size, validate_tpsl_side)

# Real AKEUSDT rules, fetched from the live public API.
AKE = PairRules(
    symbol="AKEUSDT", min_trade_volume=1.0, max_market_order_volume=100_000_000.0,
    max_limit_order_volume=1_500_000_000.0, base_precision=0, quote_precision=6,
    min_leverage=1, max_leverage=50, default_leverage=10,
    price_protect_scope=0.05, symbol_status="OPEN", api_supported=True,
)

PAIR_ROW = {
    "symbol": "AKEUSDT", "minTradeVolume": "1",
    "maxMarketOrderVolume": "100000000", "maxLimitOrderVolume": "1500000000",
    "basePrecision": 0, "quotePrecision": 6, "maxLeverage": 50, "minLeverage": 1,
    "defaultLeverage": 10, "priceProtectScope": "0.05", "symbolStatus": "OPEN",
    "isApiSupported": True,
}


# --------------------------------------------------------------- the reported bug
def test_reported_30029_is_reproduced_by_4dp_rounding():
    """The exact live failure: 4-decimal rounding pushes the stop above the mark."""
    entry, mark = 0.005051, 0.005062
    old_sl = round_price(entry, 4)          # what the engine used to send
    assert old_sl == 0.0051
    assert old_sl >= mark, "the old value is the one the exchange rejected"
    # ... and the message the user saw names exactly that number.
    assert f"{old_sl:.4f}" == "0.0051"


def test_clamp_sl_fixes_the_reported_case():
    entry, mark = 0.005051, 0.005062
    sl = clamp_sl(side="LONG", sl=entry, entry=entry, mark=mark, rules=AKE)
    assert sl < mark
    assert sl < entry
    assert validate_tpsl_side(symbol="AKEUSDT", side="LONG", entry=entry,
                              mark=mark, sl=sl, tp=None, rules=AKE) == []


def test_clamp_sl_keeps_six_decimals_not_four():
    """A 6-decimal symbol must never be rounded to 4 decimals."""
    sl = clamp_sl(side="LONG", sl=0.005051, entry=0.005051, mark=0.005062, rules=AKE)
    assert format_price(sl, AKE) == "0.005047"
    assert len(format_price(sl, AKE).split(".")[1]) == 6


# ------------------------------------------------------------------- both directions
@pytest.mark.parametrize("entry,mark", [
    (0.005051, 0.005062),   # mark above entry
    (0.005051, 0.005051),   # mark == entry
    (0.005051, 0.005040),   # mark below entry
])
def test_long_stop_is_always_below_both(entry, mark):
    sl = clamp_sl(side="LONG", sl=entry, entry=entry, mark=mark, rules=AKE)
    assert sl < min(entry, mark)
    assert validate_tpsl_side(symbol="AKEUSDT", side="LONG", entry=entry,
                              mark=mark, sl=sl, tp=None, rules=AKE) == []


@pytest.mark.parametrize("entry,mark", [
    (0.005051, 0.005040),   # mark below entry
    (0.005051, 0.005051),   # mark == entry
    (0.005051, 0.005060),   # mark above entry
])
def test_short_stop_is_always_above_both(entry, mark):
    sl = clamp_sl(side="SHORT", sl=entry, entry=entry, mark=mark, rules=AKE)
    assert sl > max(entry, mark)
    assert validate_tpsl_side(symbol="AKEUSDT", side="SHORT", entry=entry,
                              mark=mark, sl=sl, tp=None, rules=AKE) == []


def test_short_stop_is_strictly_beyond_the_limit_not_on_it():
    """A stop exactly ON the limit is still rejected (30023/30031)."""
    entry, mark = 0.005051, 0.005040
    sl = clamp_sl(side="SHORT", sl=entry, entry=entry, mark=mark, rules=AKE)
    limit = max(entry, mark) + 3 * tick_size(AKE)
    assert sl > limit


@pytest.mark.parametrize("side,entry,mark", [
    ("LONG", 0.005051, 0.005062),
    ("SHORT", 0.005051, 0.005040),
])
def test_tp_is_always_on_the_profit_side(side, entry, mark):
    tp = clamp_tp(side=side, tp=entry, entry=entry, mark=mark, rules=AKE)
    if side == "LONG":
        assert tp > max(entry, mark)
    else:
        assert tp < min(entry, mark)
    assert validate_tpsl_side(symbol="AKEUSDT", side=side, entry=entry,
                              mark=mark, sl=None, tp=tp, rules=AKE) == []


# ------------------------------------------------------------------- rounding rules
def test_round_price_is_half_up_not_bankers():
    """Python's round() is banker's: the same distance rounds both ways."""
    # 0.5 exactly at the 6th decimal, in both directions
    assert round(0.0047845, 6) == 0.004784      # down (even)
    assert round(0.0047855, 6) == 0.004785      # down (even) - same distance!
    # ours is deterministic half-up: always away from zero
    assert round_price(0.0047845, 6) == 0.004785
    assert round_price(0.0047855, 6) == 0.004786


def test_floor_and_ceil_are_directional():
    assert floor_price(0.0047839, 6) == 0.004783
    assert ceil_price(0.0047831, 6) == 0.004784


@pytest.mark.parametrize("mark", [0.0047831, 0.0047834, 0.0047835, 0.0047836])
def test_sub_tick_mark_cannot_be_crossed_by_rounding(mark):
    """A mark with more decimals than the tick must not let the clamp round back."""
    entry = 0.0047830
    sl = clamp_sl(side="SHORT", sl=0.004700, entry=entry, mark=mark, rules=AKE)
    assert sl > max(entry, mark)


def test_format_price_does_not_undo_the_clamp():
    """format_price used to re-round with round(); it must agree with round_price."""
    for v in (0.0047831, 0.0047834, 0.0047835, 0.0047836):
        assert float(format_price(v, AKE)) == round_price(v, 6)


# ------------------------------------------------------- running-engine path (e2e)
class FakeClient:
    """Fake Bitunix client: records every TP/SL modification it is asked to send."""

    def __init__(self, mark: float, last: float = 0.005051,
                 entry: float = 0.005000, side: str = "LONG"):
        self.mark, self.last, self.entry, self.side = mark, last, entry, side
        self.modified: list[dict] = []
        self.ticker_calls = 0

    async def get_tickers(self, sym):
        self.ticker_calls += 1
        return [{"symbol": sym, "lastPrice": str(self.last),
                 "markPrice": str(self.mark)}]

    async def get_pending_positions(self, sym=None):
        # liqPrice must be on the correct side for the direction, as the
        # exchange reports it: below entry for a LONG, above it for a SHORT.
        liq = "0.004600" if self.side == "LONG" else "0.005400"
        return [{"positionId": "P1", "symbol": "AKEUSDT", "side": self.side,
                 "avgOpenPrice": str(self.entry), "qty": "4000",
                 "leverage": "10", "liqPrice": liq,
                 "markPrice": str(self.mark)}]

    async def get_trading_pairs(self, symbols=None):
        return [PAIR_ROW]

    async def modify_position_tp_sl_order(self, sym, pid, tp_price=None, sl_price=None,
                                          tp_stop_type=None, sl_stop_type=None):
        self.modified.append({"symbol": sym, "positionId": pid,
                              "tpPrice": tp_price, "slPrice": sl_price,
                              "tpStopType": tp_stop_type,
                              "slStopType": sl_stop_type})
        return {"orderId": "T1"}

    async def get_kline(self, *a, **k):
        return []


def _manage(mark, **kw):
    s = Settings()
    s.market_data_source = "rest"
    s.breakeven_pct = 0.6
    e = TraderEngine(s)
    e.client = FakeClient(mark, **kw)

    async def go():
        await e.load_rules(["AKEUSDT"])
        return await e.manage_positions()

    return asyncio.run(go()), e.client, e


def test_breakeven_sends_a_stop_below_the_mark():
    """The live failure, end to end: breakeven must not send 0.0051."""
    actions, client, _ = _manage(0.005062)
    assert [a["action"] for a in actions] == ["breakeven"]
    assert len(client.modified) == 1
    sent = client.modified[0]["slPrice"]
    assert sent != "0.0051", "the 4-decimal value is the one the exchange rejected"
    assert float(sent) < 0.005062
    assert len(sent.split(".")[1]) == 6


def test_breakeven_uses_the_mark_not_the_last_price():
    """mark 0.005062 is above last 0.005051; the stop must clear the mark."""
    _, client, _ = _manage(0.005062, last=0.005051)
    assert float(client.modified[0]["slPrice"]) < 0.005062


def test_breakeven_reads_the_mark_fresh_at_order_time():
    """The mark must be fetched during management, not reused from entry."""
    _, client, _ = _manage(0.005062)
    assert client.ticker_calls >= 1


def test_short_breakeven_sends_a_stop_above_the_mark():
    # SHORT entered at 0.005000, price fell to 0.004940 -> ROI > 0.6%
    actions, client, _ = _manage(0.004940, last=0.004940, entry=0.005000, side="SHORT")
    assert [a["action"] for a in actions] == ["breakeven"]
    sent = float(client.modified[0]["slPrice"])
    assert sent > 0.004940


def test_no_tpsl_is_sent_when_the_mark_makes_it_impossible():
    """If the mark has run past the entry, breakeven must not send a bad stop."""
    # LONG entered at 0.005000, mark has fallen to 0.004900: a breakeven stop at
    # the entry would be ABOVE the mark -> 30029. The clamp must move it below.
    actions, client, _ = _manage(0.004900, entry=0.005000)
    for m in client.modified:
        assert float(m["slPrice"]) < 0.004900


def test_managed_tp_is_also_clamped():
    """A stored TP on the wrong side must not be forwarded verbatim."""
    s = Settings()
    s.market_data_source = "rest"
    s.breakeven_pct = 0.6
    e = TraderEngine(s)
    e.client = FakeClient(0.005062)
    e.positions["P1"] = __import__(
        "jrock.trader.tpsl", fromlist=["PositionState"]).PositionState(
        position_id="P1", symbol="AKEUSDT", side="LONG", entry=0.005000,
        qty=4000.0, leverage=10, sl=0.0, tp=0.005010, liq=0.004600)

    async def go():
        await e.load_rules(["AKEUSDT"])
        return await e.manage_positions()

    asyncio.run(go())
    for m in e.client.modified:
        if m["tpPrice"]:
            assert float(m["tpPrice"]) > 0.005062


# ------------------------------------------------------- conservative mark fallback
def test_conservative_mark_widens_in_the_safe_direction():
    long_ref = TraderEngine._conservative_mark(0.005000, "LONG", AKE)
    short_ref = TraderEngine._conservative_mark(0.005000, "SHORT", AKE)
    assert long_ref < 0.005000, "a LONG stop must clear a LOWER mark"
    assert short_ref > 0.005000, "a SHORT stop must clear a HIGHER mark"


def test_conservative_mark_covers_the_protection_band():
    last = 0.005000
    scope = AKE.price_protect_scope
    assert TraderEngine._conservative_mark(last, "LONG", AKE) == pytest.approx(
        last * (1 - scope))
    assert TraderEngine._conservative_mark(last, "SHORT", AKE) == pytest.approx(
        last * (1 + scope))


# ------------------------------------------------------------- zero means "not set"
def test_zero_sl_is_treated_as_unset_not_as_a_violation():
    """PositionState defaults sl/tp to 0.0 for positions the engine did not open."""
    assert validate_tpsl_side(symbol="AKEUSDT", side="LONG", entry=0.005,
                              mark=0.005, sl=0.0, tp=0.0, rules=AKE) == []


# ------------------------------------------------- entry path: mark re-read at send
class MovingMarkClient:
    """Returns a DIFFERENT mark on each call, to model a mark that moves while
    the engine is doing its pre-flight round-trips (account, tiers, leverage)."""

    def __init__(self, marks: list[float], last: float = 0.005051):
        self.marks = list(marks)
        self.last = last
        self.placed: list[dict] = []
        self.ticker_calls = 0

    async def get_tickers(self, sym):
        self.ticker_calls += 1
        m = self.marks[min(self.ticker_calls - 1, len(self.marks) - 1)]
        return [{"symbol": sym, "lastPrice": str(self.last), "markPrice": str(m)}]

    async def get_single_account(self):
        return [{"marginCoin": "USDT", "available": "100", "frozen": "0",
                 "margin": "0", "unrealized": "0"}]

    async def get_trading_pairs(self, symbols=None):
        return [PAIR_ROW]

    async def get_position_tiers(self, sym):
        return [{"symbol": "AKEUSDT", "level": 1, "startValue": "0",
                 "endValue": "10000", "leverage": 50,
                 "maintenanceMarginRate": "0.015"}]

    async def get_pending_positions(self, sym=None):
        return []

    async def change_margin_mode(self, *a, **k):
        return {}

    async def change_leverage(self, *a, **k):
        return {}

    async def place_order(self, **b):
        self.placed.append(b)
        return {"orderId": "X1"}


class _Agg:
    symbol = "AKEUSDT"
    direction = "LONG"
    actionable = True
    strength = "MEDIUM"
    confidence = 75.0
    agreeing = 4


def _enter(marks, last=0.005051, leverage=10):
    s = Settings()
    s.market_data_source = "rest"
    e = TraderEngine(s)
    e.client = MovingMarkClient(marks, last=last)
    e.state.leverage = leverage

    async def go():
        await e.load_rules(["AKEUSDT"])
        return await e.try_enter(_Agg())

    return asyncio.run(go()), e.client


def test_entry_reclamps_against_the_mark_read_at_send_time():
    """The mark falls between planning and sending; the stop must still clear it.

    First read (planning) 0.005062, second read (just before send) 0.005040.
    A stop clamped only against the first mark can land above the second.
    """
    r, client = _enter([0.005062, 0.005040])
    assert r["ok"] is True, r
    assert len(client.placed) == 1
    body = client.placed[0]
    assert float(body["slPrice"]) < 0.005040, "must clear the mark at send time"
    assert float(body["tpPrice"]) > 0.005062


def test_entry_reads_the_mark_more_than_once():
    """Planning and sending must not share a single stale mark read."""
    _, client = _enter([0.005062, 0.005040])
    assert client.ticker_calls >= 2


def test_entry_refuses_when_the_mark_makes_the_stop_impossible():
    """If the mark has run past the entry, no order may be sent."""
    # LONG: mark collapses far below the entry between plan and send.
    r, client = _enter([0.005062, 0.004000])
    if r["ok"]:
        # if it did send, the stop must still be valid
        assert float(client.placed[0]["slPrice"]) < 0.004000
    else:
        assert client.placed == []


def test_entry_sends_six_decimals_on_a_six_decimal_symbol():
    r, client = _enter([0.005062, 0.005062])
    assert r["ok"] is True
    for field in ("slPrice", "tpPrice"):
        assert len(client.placed[0][field].split(".")[1]) == 6


# ------------------------------------------- order_builder refuses the bad value
def test_order_builder_refuses_a_4dp_stop_on_a_6dp_symbol():
    """The exact value the exchange rejected must be refused locally."""
    from jrock.trader.order_builder import OrderValidationError, build_order_body

    with pytest.raises(OrderValidationError) as ei:
        build_order_body(
            symbol="AKEUSDT", qty=4000, side="BUY", tradeSide="OPEN",
            orderType="MARKET", slPrice=0.0051, tpPrice=0.0052,
            slStopType="MARK_PRICE", slOrderType="MARKET",
            tpStopType="MARK_PRICE", tpOrderType="MARKET",
            rules=AKE, mark_price=0.005062, entry_price=0.005051)
    assert "30029" in str(ei.value) or "must be BELOW" in str(ei.value)


def test_order_builder_accepts_the_clamped_value():
    from jrock.trader.order_builder import build_order_body

    sl = clamp_sl(side="LONG", sl=0.005051, entry=0.005051, mark=0.005062, rules=AKE)
    tp = clamp_tp(side="LONG", tp=0.005200, entry=0.005051, mark=0.005062, rules=AKE)
    body = build_order_body(
        symbol="AKEUSDT", qty=4000, side="BUY", tradeSide="OPEN",
        orderType="MARKET", slPrice=sl, tpPrice=tp,
        slStopType="MARK_PRICE", slOrderType="MARKET",
        tpStopType="MARK_PRICE", tpOrderType="MARKET",
        rules=AKE, mark_price=0.005062, entry_price=0.005051)
    assert float(body["slPrice"]) < 0.005062
    assert float(body["tpPrice"]) > 0.005062
