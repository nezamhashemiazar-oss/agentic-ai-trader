import pytest

from jrock.trader.risk import PairRules, Tier
from jrock.trader.tpsl import (PositionState, mid_management_action,
                               next_trailing_stop, plan_tpsl, should_breakeven,
                               should_trail)

RULES = PairRules("BTCUSDT", base_precision=4, quote_precision=2)
TIERS = [Tier(1, 0, 50000, 125, 0.004), Tier(2, 50000, 200000, 100, 0.005)]


def test_plan_long_sl_below_entry_tp_above():
    p = plan_tpsl(side="LONG", entry=100.0, leverage=10, notional=1000,
                  strength="normal", confidence=75, tiers=TIERS, rules=RULES)
    assert p.sl < 100 < p.tp
    assert p.liq < p.sl < 100
    assert p.rr > 0


def test_plan_short_sl_above_entry_tp_below():
    p = plan_tpsl(side="SHORT", entry=100.0, leverage=10, notional=1000,
                  strength="normal", confidence=75, tiers=TIERS, rules=RULES)
    assert p.tp < 100 < p.sl
    assert p.liq > p.sl > 100


def test_sl_is_halfway_to_liq():
    p = plan_tpsl(side="LONG", entry=100.0, leverage=10, notional=1000,
                  strength="normal", confidence=75, tiers=TIERS, rules=RULES,
                  liq_sl_distance_pct=50)
    expected = 100 - (100 - p.liq) * 0.5
    assert p.sl == pytest.approx(expected, rel=1e-3)


def test_stronger_signal_gives_bigger_tp():
    weak = plan_tpsl(side="LONG", entry=100, leverage=10, notional=1000,
                     strength="weak", confidence=70, tiers=TIERS, rules=RULES)
    strong = plan_tpsl(side="LONG", entry=100, leverage=10, notional=1000,
                       strength="strong", confidence=90, tiers=TIERS, rules=RULES)
    assert strong.tp > weak.tp


def test_higher_tier_raises_mmr():
    p1 = plan_tpsl(side="LONG", entry=100, leverage=10, notional=1000,
                   strength="normal", confidence=75, tiers=TIERS, rules=RULES)
    p2 = plan_tpsl(side="LONG", entry=100, leverage=10, notional=100000,
                   strength="normal", confidence=75, tiers=TIERS, rules=RULES)
    assert p2.mmr > p1.mmr
    assert p2.tier_level == 2


def test_breakeven_trigger():
    st = PositionState("1", "BTCUSDT", "LONG", 100, 1, 10, 95, 110, 90)
    assert not should_breakeven(st, 0.5, 0.6)
    assert should_breakeven(st, 0.7, 0.6)
    st.breakeven_done = True
    assert not should_breakeven(st, 5.0, 0.6)


def test_trailing_trigger():
    st = PositionState("1", "BTCUSDT", "LONG", 100, 1, 10, 95, 110, 90)
    assert not should_trail(st, 20, 25)
    assert should_trail(st, 30, 25)


def test_trailing_stop_ratchets_up_only():
    st = PositionState("1", "BTCUSDT", "LONG", 100, 1, 10, 95, 110, 90)
    s1 = next_trailing_stop(st, 110, 1.0)
    assert s1 is not None
    st.trailing_stop = s1
    s2 = next_trailing_stop(st, 111, 1.0)
    assert s2 is not None and s2 > s1
    # price falls -> no loosening
    assert next_trailing_stop(st, 105, 1.0) is None


def test_trailing_stop_short_ratchets_down():
    st = PositionState("1", "BTCUSDT", "SHORT", 100, 1, 10, 105, 90, 110)
    s1 = next_trailing_stop(st, 90, 1.0)
    assert s1 is not None
    st.trailing_stop = s1
    s2 = next_trailing_stop(st, 89, 1.0)
    assert s2 is not None and s2 < s1


def test_mid_management_reversal_closes():
    st = PositionState("1", "BTCUSDT", "LONG", 100, 1, 10, 95, 110, 90)
    assert mid_management_action(st, 10, True, 80, 70) == "close_reversal"


def test_mid_management_partial_at_roi():
    st = PositionState("1", "BTCUSDT", "LONG", 100, 1, 10, 95, 110, 90)
    assert mid_management_action(st, 60, False, 0, 70) == "partial_close"
    st.partial_done = True
    assert mid_management_action(st, 60, False, 0, 70) == "tighten"


def test_mid_management_hold():
    st = PositionState("1", "BTCUSDT", "LONG", 100, 1, 10, 95, 110, 90)
    assert mid_management_action(st, 5, False, 0, 70) == "hold"
