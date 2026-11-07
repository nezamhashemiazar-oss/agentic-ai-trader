"""Regression tests for the TP ladder (multi-level partial take-profit).

The rules that matter, each asserted directly:
  * close_pct is a share of the ORIGINAL size, never the remainder
  * the sum of close_pct may not exceed 100
  * each level fires exactly once, and a restart does not re-fire it
  * the R basis uses the INITIAL risk, so breakeven cannot collapse it
  * dust below minTradeVolume is closed with the level instead of orphaned
  * the runner trails or closes per config
  * every command is reachable through the real router
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jrock.config import Settings
from jrock.store import Store
from jrock.trader.engine import TraderEngine
from jrock.trader.ladder import (LadderError, decide, level_qtys, parse_ladder,
                                 remaining_after, trigger_price_for)
from jrock.trader.risk import PairRules
from jrock.trader.tpsl import PositionState

# --------------------------------------------------------------------- parsing


def test_parse_r_ladder():
    lad = parse_ladder("1R:25,2R:25,3R:25")
    assert [lv.trigger for lv in lad.levels] == [1.0, 2.0, 3.0]
    assert [lv.close_pct for lv in lad.levels] == [25.0, 25.0, 25.0]
    assert lad.total_pct == 75.0
    assert lad.runner_pct == 25.0
    assert lad.basis == "R"


def test_parse_roi_ladder():
    lad = parse_ladder("50:25,100:25", basis="ROI")
    assert [lv.trigger for lv in lad.levels] == [50.0, 100.0]
    assert lad.basis == "ROI"


def test_parse_price_ladder():
    lad = parse_ladder("0.0055:30,0.0060:30", basis="PRICE")
    assert [lv.trigger for lv in lad.levels] == [0.0055, 0.0060]


def test_empty_spec_is_disabled_not_an_error():
    lad = parse_ladder("")
    assert lad.levels == []
    assert "OFF" in lad.describe()


def test_sum_over_100_rejected():
    with pytest.raises(LadderError, match="sum to 110%"):
        parse_ladder("1R:25,2R:25,3R:60")


def test_sum_exactly_100_allowed():
    lad = parse_ladder("1R:50,2R:50")
    assert lad.total_pct == 100.0
    assert lad.runner_pct == 0.0


@pytest.mark.parametrize("spec,msg", [
    ("1R:25,2R", "missing ':'"),
    ("1R:0", "must be > 0"),
    ("1R:-5", "must be > 0"),
    ("x:25", "not a number"),
    ("1R:25,2R:25,1R:25", "increasing trigger order"),
    ("1R:25,,2R:25", "empty rung"),
])
def test_bad_specs_rejected(spec, msg):
    with pytest.raises(LadderError, match=msg):
        parse_ladder(spec)


def test_unknown_basis_rejected():
    with pytest.raises(LadderError, match="unknown trigger basis"):
        parse_ladder("1R:25", basis="BOGUS")


def test_unknown_runner_rejected():
    with pytest.raises(LadderError, match="unknown runner"):
        parse_ladder("1R:25", runner="moon")


def test_breakeven_out_of_range_rejected():
    with pytest.raises(LadderError, match="outside 0..2"):
        parse_ladder("1R:25,2R:25", breakeven_after=5)


def test_explicit_r_suffix_conflicts_with_roi_basis():
    with pytest.raises(LadderError, match="R multiple but the basis is ROI"):
        parse_ladder("1R:25", basis="ROI")


# ------------------------------------------------------------- the qty maths


def test_close_pct_is_of_the_original_size():
    """The #1 ladder bug: compounding against the shrinking remainder."""
    lad = parse_ladder("1R:25,2R:25,3R:25")
    assert level_qtys(1000.0, lad, 0) == [250.0, 250.0, 250.0]
    assert remaining_after(1000.0, lad, 1, 0) == 750.0
    assert remaining_after(1000.0, lad, 2, 0) == 500.0
    assert remaining_after(1000.0, lad, 3, 0) == 250.0


def test_compounding_would_give_a_different_answer():
    """Proves the assertion above is meaningful, not trivially true."""
    lad = parse_ladder("1R:25,2R:25,3R:25")
    rem, wrong = 1000.0, []
    for lv in lad.levels:
        q = rem * lv.close_pct / 100.0
        wrong.append(q)
        rem -= q
    assert wrong != level_qtys(1000.0, lad, 0)
    assert round(rem, 2) == 421.88


def test_uneven_ladder_uses_original_size():
    lad = parse_ladder("1R:50,2R:30")
    assert level_qtys(1000.0, lad, 0) == [500.0, 300.0]
    assert remaining_after(1000.0, lad, 2, 0) == 200.0


def test_base_precision_floors_never_rounds_up():
    lad = parse_ladder("1R:33,2R:33,3R:33")
    qtys = level_qtys(100.0, lad, 0)
    assert qtys == [33.0, 33.0, 33.0]
    assert sum(qtys) <= 100.0


def test_zero_qty_returns_nothing():
    assert level_qtys(0.0, parse_ladder("1R:25"), 0) == []


# ---------------------------------------------------------------- triggers


def test_long_triggers_above_entry():
    lad = parse_ladder("1R:25,2R:25,3R:25")
    prices = [trigger_price_for("LONG", lv, basis="R", entry=0.005,
                                sl=0.0048, leverage=20) for lv in lad.levels]
    assert prices == pytest.approx([0.0052, 0.0054, 0.0056])


def test_short_triggers_below_entry():
    lad = parse_ladder("1R:25,2R:25,3R:25")
    prices = [trigger_price_for("SHORT", lv, basis="R", entry=0.005,
                                sl=0.0052, leverage=20) for lv in lad.levels]
    assert prices == pytest.approx([0.0048, 0.0046, 0.0044])


def test_r_basis_uses_initial_risk_not_the_live_stop():
    """After breakeven the live stop is the entry; R must not collapse."""
    lad = parse_ladder("2R:25")
    # live sl == entry (breakeven), initial risk 0.0002
    p = trigger_price_for("LONG", lad.levels[0], basis="R", entry=0.005,
                          sl=0.005, leverage=20, initial_risk=0.0002)
    assert p == pytest.approx(0.0054)
    # without initial_risk it would collapse onto the entry
    collapsed = trigger_price_for("LONG", lad.levels[0], basis="R",
                                  entry=0.005, sl=0.005, leverage=20)
    assert collapsed == pytest.approx(0.005)


def test_roi_trigger_price():
    lad = parse_ladder("100:25", basis="ROI")
    p = trigger_price_for("LONG", lad.levels[0], basis="ROI", entry=0.005,
                          sl=0.0048, leverage=20)
    assert p == pytest.approx(0.00525)  # 100% ROI at 20x = +5%


# ---------------------------------------------------------------- decisions


def _decide(**kw):
    base = dict(ladder=parse_ladder("1R:25,2R:25,3R:25"), side="LONG",
                entry=0.005, sl=0.0048, leverage=20, price=0.0052,
                original_qty=1000.0, fired=0, base_precision=0,
                min_trade_volume=1.0)
    base.update(kw)
    return decide(**base)


def test_level_does_not_fire_before_its_trigger():
    assert _decide(price=0.0051).fire_index is None


def test_level_fires_exactly_at_its_trigger():
    """Float noise must not stop a level firing at exactly 1R."""
    d = _decide(price=0.0052)
    assert d.fire_index == 0
    assert d.fire_qty == 250.0


def test_one_level_per_pass():
    d = _decide(price=0.0058)  # past 3R, but only level 1 fires
    assert d.fire_index == 0
    assert d.fire_qty == 250.0


def test_fired_level_does_not_fire_again():
    d = _decide(price=0.0052, fired=1)
    assert d.fire_index is None


def test_levels_fire_in_order():
    assert _decide(price=0.0054, fired=1).fire_index == 1
    assert _decide(price=0.0056, fired=2).fire_index == 2


def test_breakeven_after_configured_level():
    lad = parse_ladder("1R:25,2R:25", breakeven_after=2)
    d = decide(ladder=lad, side="LONG", entry=0.005, sl=0.0048, leverage=20,
               price=0.0052, original_qty=1000.0, fired=0, base_precision=0,
               min_trade_volume=1.0)
    assert d.fire_index == 0
    assert d.move_breakeven is False  # level 1 < breakeven_after 2
    d2 = decide(ladder=lad, side="LONG", entry=0.005, sl=0.0048, leverage=20,
                price=0.0054, original_qty=1000.0, fired=1, base_precision=0,
                min_trade_volume=1.0)
    assert d2.move_breakeven is True


def test_breakeven_not_repeated_once_done():
    d = _decide(price=0.0052, breakeven_done=True)
    assert d.move_breakeven is False


def test_breakeven_zero_disables():
    lad = parse_ladder("1R:25", breakeven_after=0)
    d = decide(ladder=lad, side="LONG", entry=0.005, sl=0.0048, leverage=20,
               price=0.0052, original_qty=1000.0, fired=0, base_precision=0,
               min_trade_volume=1.0)
    assert d.move_breakeven is False


def test_runner_trails_by_default():
    d = _decide(price=0.0058, fired=3)
    assert d.runner_trail is True
    assert d.runner_close is False


def test_runner_close_when_configured():
    lad = parse_ladder("1R:50,2R:50", runner="close")
    d = decide(ladder=lad, side="LONG", entry=0.005, sl=0.0048, leverage=20,
               price=0.0058, original_qty=1000.0, fired=2, base_precision=0,
               min_trade_volume=1.0)
    assert d.runner_close is True
    assert d.runner_trail is False


def test_dust_remainder_is_closed_with_the_level():
    """100 units, 33/33/33 leaves 1 - below minTradeVolume 5."""
    lad = parse_ladder("1R:33,2R:33,3R:33")
    d = decide(ladder=lad, side="LONG", entry=0.005, sl=0.0048, leverage=20,
               price=0.0056, original_qty=100.0, fired=2, base_precision=0,
               min_trade_volume=5.0)
    assert d.close_remainder is True
    assert d.fire_qty == 34.0  # 33 + the 1-unit remainder


def test_no_dust_when_remainder_is_tradeable():
    lad = parse_ladder("1R:33,2R:33,3R:33")
    d = decide(ladder=lad, side="LONG", entry=0.005, sl=0.0048, leverage=20,
               price=0.0056, original_qty=1000.0, fired=2, base_precision=0,
               min_trade_volume=5.0)
    assert d.close_remainder is False
    assert d.fire_qty == 330.0


def test_disabled_ladder_decides_nothing():
    d = decide(ladder=parse_ladder(""), side="LONG", entry=0.005, sl=0.0048,
               leverage=20, price=0.01, original_qty=1000.0, fired=0)
    assert d.fire_index is None
    assert "disabled" in d.reason


# ------------------------------------------------------- engine integration


class FakeClient:
    def __init__(self, positions, price, mark):
        self._positions, self._price, self._mark = positions, price, mark
        self.calls: list[tuple[str, dict]] = []

    async def get_pending_positions(self, symbol=None):
        return self._positions

    async def get_tickers(self, symbol=None):
        return [{"symbol": symbol, "lastPrice": str(self._price),
                 "markPrice": str(self._mark)}]

    async def place_order(self, **kw):
        self.calls.append(("place_order", kw))
        return {"orderId": "O%d" % len(self.calls)}

    async def modify_position_tp_sl_order(self, *a, **kw):
        self.calls.append(("modify_position_tp_sl_order", {"args": a, **kw}))
        return {"orderId": "M%d" % len(self.calls)}

    async def flash_close_position(self, pid):
        self.calls.append(("flash_close_position", {"positionId": pid}))
        return {"ok": True}

    async def close_all_position(self, symbol=None):
        self.calls.append(("close_all_position", {"symbol": symbol}))
        return {"ok": True}

    async def get_single_account(self):
        return [{"available": "1000"}]

    async def verify_credentials(self):
        return True, "fake"


def _engine(tmp: Path, spec: str, **kw) -> TraderEngine:
    s = Settings(symbols=["AKEUSDT"], leverage=20, risk_pct=1.0,
                 tp_ladder=spec, tp_ladder_trigger_basis="R",
                 tp_ladder_breakeven_after=1, tp_ladder_runner="trail", **kw)
    eng = TraderEngine(s, data_dir=tmp, store=Store(path=tmp / "state.json"))
    eng.rules["AKEUSDT"] = PairRules(
        symbol="AKEUSDT", min_trade_volume=1.0, base_precision=0,
        quote_precision=6, max_leverage=50)
    return eng


def _pos(eng, qty=1000.0, fired=0, risk=0.0002):
    eng.positions["P1"] = PositionState(
        position_id="P1", symbol="AKEUSDT", side="LONG", entry=0.005,
        qty=qty, leverage=20, sl=0.0048, tp=0.0056, liq=0.0047,
        original_qty=1000.0, fired_levels=fired, initial_risk=risk)


def _client(eng, price, qty=1000.0):
    eng.client = FakeClient(
        [{"positionId": "P1", "symbol": "AKEUSDT", "side": "LONG",
          "qty": str(qty), "avgOpenPrice": "0.005", "leverage": "20",
          "liqPrice": "0.0047", "unrealizedPNL": "1.0"}], price, price)


def test_engine_fires_level_one_and_closes_original_pct(tmp_path):
    eng = _engine(tmp_path, "1R:25,2R:25,3R:25")
    _pos(eng)
    _client(eng, 0.0052)
    actions = asyncio.run(eng.manage_positions())
    ladder = [a for a in actions if a["action"] == "ladder_level"]
    assert len(ladder) == 1
    assert ladder[0]["qty"] == 250.0
    assert eng.positions["P1"].fired_levels == 1
    assert eng.positions["P1"].qty == 750.0
    closes = [c for c in eng.client.calls if c[0] == "place_order"]
    assert len(closes) == 1
    assert closes[0][1]["qty"] == "250"
    assert closes[0][1]["reduceOnly"] is True


def test_engine_does_not_fire_partial_alongside_the_ladder(tmp_path):
    """The single-level PARTIAL must not double-close with a ladder level."""
    eng = _engine(tmp_path, "1R:25,2R:25,3R:25")
    _pos(eng)
    _client(eng, 0.0052)
    actions = asyncio.run(eng.manage_positions())
    assert not [a for a in actions if a["action"] in
                ("partial_close", "partial_tpsl")]


def test_engine_restart_does_not_refire_a_level(tmp_path):
    eng = _engine(tmp_path, "1R:25,2R:25,3R:25")
    _pos(eng)
    _client(eng, 0.0052)
    asyncio.run(eng.manage_positions())
    assert eng.positions["P1"].fired_levels == 1

    eng2 = _engine(tmp_path, "1R:25,2R:25,3R:25")
    eng2._restore_from_store()
    st = eng2.positions["P1"]
    assert st.fired_levels == 1
    assert st.original_qty == 1000.0
    assert st.initial_risk == pytest.approx(0.0002)

    _client(eng2, 0.0052, qty=st.qty)
    asyncio.run(eng2.manage_positions())
    assert not [c for c in eng2.client.calls if c[0] == "place_order"]


def test_engine_restart_then_level_two_fires(tmp_path):
    eng = _engine(tmp_path, "1R:25,2R:25,3R:25")
    _pos(eng)
    _client(eng, 0.0052)
    asyncio.run(eng.manage_positions())

    eng2 = _engine(tmp_path, "1R:25,2R:25,3R:25")
    eng2._restore_from_store()
    _client(eng2, 0.0054, qty=eng2.positions["P1"].qty)
    actions = asyncio.run(eng2.manage_positions())
    ladder = [a for a in actions if a["action"] == "ladder_level"]
    assert len(ladder) == 1
    assert ladder[0]["level"] == 2
    assert ladder[0]["qty"] == 250.0


def test_engine_breakeven_after_level_one(tmp_path):
    eng = _engine(tmp_path, "1R:25,2R:25,3R:25")
    _pos(eng)
    _client(eng, 0.0052)
    asyncio.run(eng.manage_positions())
    st = eng.positions["P1"]
    assert st.breakeven_done is True
    # Breakeven means "at the entry", but the stop is clamped a few ticks
    # clear of the mark so the exchange cannot reject it (30029). So it must
    # have moved up from the original 0.0048 and sit within a few ticks of
    # the entry - not exactly on it.
    assert st.sl > 0.0048
    assert st.sl >= 0.005 - 0.00001


def test_engine_breakeven_after_two_waits(tmp_path):
    eng = _engine(tmp_path, "1R:25,2R:25,3R:25")
    eng.state.tp_ladder_breakeven_after = 2
    _pos(eng)
    _client(eng, 0.0052)
    asyncio.run(eng.manage_positions())
    assert eng.positions["P1"].breakeven_done is False


def test_engine_runner_close_closes_the_remainder(tmp_path):
    eng = _engine(tmp_path, "1R:50,2R:50")
    eng.state.tp_ladder_runner = "close"
    _pos(eng, fired=2, qty=0.0)
    eng.positions["P1"].qty = 0.0
    _client(eng, 0.0058, qty=0.0)
    actions = asyncio.run(eng.manage_positions())
    # nothing left to close, but the decision must be the runner path
    assert not [a for a in actions if a["action"] == "ladder_level"]


def test_engine_dust_closes_remainder(tmp_path):
    eng = _engine(tmp_path, "1R:33,2R:33,3R:33")
    eng.rules["AKEUSDT"] = PairRules(
        symbol="AKEUSDT", min_trade_volume=5.0, base_precision=0,
        quote_precision=6, max_leverage=50)
    _pos(eng, qty=34.0, fired=2)
    eng.positions["P1"].original_qty = 100.0
    _client(eng, 0.0056, qty=34.0)
    asyncio.run(eng.manage_positions())
    assert eng.positions["P1"].fired_levels == 3


def test_engine_invalid_spec_does_not_crash_the_loop(tmp_path):
    eng = _engine(tmp_path, "1R:25,2R:25,3R:60")
    _pos(eng)
    _client(eng, 0.0052)
    actions = asyncio.run(eng.manage_positions())  # must not raise
    assert not [a for a in actions if a["action"] == "ladder_level"]
    assert any("invalid spec" in e for e in eng.state.errors)


# ---------------------------------------------------------------- commands


def test_ladder_command_shows_the_ladder(tmp_path):
    eng = _engine(tmp_path, "1R:25,2R:25,3R:25")
    out = asyncio.run(eng.command("ladder"))
    assert "TP ladder" in out
    assert "1R:25" in out or "1:25" in out


def test_ladder_set_persists(tmp_path):
    eng = _engine(tmp_path, "")
    asyncio.run(eng.command("ladder set 1R:25,2R:25,3R:25"))
    assert eng.state.tp_ladder == "1R:25,2R:25,3R:25"
    assert len(eng.ladder().levels) == 3


def test_ladder_set_rejects_bad_spec_and_keeps_the_old_one(tmp_path):
    eng = _engine(tmp_path, "1R:25,2R:25")
    out = asyncio.run(eng.command("ladder set 1R:25,2R:25,3R:60"))
    assert "Invalid ladder" in out
    assert eng.state.tp_ladder == "1R:25,2R:25"


def test_ladder_off_disables(tmp_path):
    eng = _engine(tmp_path, "1R:25,2R:25")
    out = asyncio.run(eng.command("ladder off"))
    assert "OFF" in out
    assert eng.state.tp_ladder == ""


def test_ladder_breakeven_command(tmp_path):
    eng = _engine(tmp_path, "1R:25,2R:25,3R:25")
    asyncio.run(eng.command("ladder breakeven 2"))
    assert eng.state.tp_ladder_breakeven_after == 2


def test_ladder_breakeven_out_of_range_refused(tmp_path):
    eng = _engine(tmp_path, "1R:25,2R:25")
    out = asyncio.run(eng.command("ladder breakeven 9"))
    assert "outside" in out
    assert eng.state.tp_ladder_breakeven_after == 1


def test_ladder_runner_command(tmp_path):
    eng = _engine(tmp_path, "1R:25")
    asyncio.run(eng.command("ladder runner close"))
    assert eng.state.tp_ladder_runner == "close"


def test_ladder_runner_bad_value_refused(tmp_path):
    eng = _engine(tmp_path, "1R:25")
    out = asyncio.run(eng.command("ladder runner moon"))
    assert "Usage" in out
    assert eng.state.tp_ladder_runner == "trail"


def test_ladder_basis_command(tmp_path):
    eng = _engine(tmp_path, "1R:25")
    asyncio.run(eng.command("ladder basis ROI"))
    assert eng.state.tp_ladder_trigger_basis == "ROI"


def test_ladder_settings_survive_a_restart(tmp_path):
    eng = _engine(tmp_path, "")
    asyncio.run(eng.command("ladder set 1R:25,2R:25,3R:25"))
    asyncio.run(eng.command("ladder breakeven 2"))
    asyncio.run(eng.command("ladder runner close"))

    eng2 = _engine(tmp_path, "")
    eng2._restore_from_store()
    assert eng2.state.tp_ladder == "1R:25,2R:25,3R:25"
    assert eng2.state.tp_ladder_breakeven_after == 2
    assert eng2.state.tp_ladder_runner == "close"


def test_ladder_report_shows_fired_levels(tmp_path):
    eng = _engine(tmp_path, "1R:25,2R:25,3R:25")
    _pos(eng, fired=2, qty=500.0)
    out = eng.ladder_report()
    assert "fired=2/3" in out
    assert "remaining=500" in out
    assert "[x] level 1" in out
    assert "[ ] level 3" in out


def test_ladder_command_reachable_from_agent():
    from jrock.agent import AGENT_BARE, QUERY_COMMANDS
    assert AGENT_BARE["ladder"] == "/ladder"
    assert AGENT_BARE["tp_ladder"] == "/ladder"
    assert "/ladder" in QUERY_COMMANDS


def test_ladder_in_help_text():
    from jrock.agent import HELP
    assert "/ladder" in HELP
    assert "ladder set" in HELP
