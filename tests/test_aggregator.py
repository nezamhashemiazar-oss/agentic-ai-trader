from jrock.trader.aggregator import aggregate
from jrock.trader.strategies.base import Signal


def sig(name, direction, conf, tf="5m"):
    return Signal(name, direction, conf, "test", tf, "BTCUSDT")


def test_no_signals_is_neutral():
    agg = aggregate("BTCUSDT", {"5m": [sig("a", "NEUTRAL", 0)]})
    assert agg.direction == "NEUTRAL"
    assert not agg.actionable


def test_below_min_agreeing_is_neutral():
    per_tf = {"5m": [sig("a", "LONG", 80), sig("b", "LONG", 80)]}
    agg = aggregate("BTCUSDT", per_tf, min_agreeing=3, min_confidence=70)
    assert agg.direction == "NEUTRAL"
    assert "only 2" in agg.reason


def test_three_agreeing_passes():
    per_tf = {"5m": [sig("a", "LONG", 80), sig("b", "LONG", 80), sig("c", "LONG", 80)]}
    agg = aggregate("BTCUSDT", per_tf, min_agreeing=3, min_confidence=70)
    assert agg.direction == "LONG"
    assert agg.agreeing == 3
    assert agg.confidence >= 70


def test_tf_min_confidence_filters():
    per_tf = {"5m": [sig("a", "LONG", 50), sig("b", "LONG", 50), sig("c", "LONG", 50)]}
    agg = aggregate("BTCUSDT", per_tf, min_agreeing=3, min_confidence=70,
                    tf_min_confidence=60)
    assert agg.direction == "NEUTRAL"


def test_multi_timeframe_boosts_confidence():
    one_tf = {"5m": [sig("a", "LONG", 80), sig("b", "LONG", 80), sig("c", "LONG", 80)]}
    multi_tf = {
        "5m": [sig("a", "LONG", 80, "5m"), sig("b", "LONG", 80, "5m"),
               sig("c", "LONG", 80, "5m")],
        "15m": [sig("a", "LONG", 80, "15m"), sig("b", "LONG", 80, "15m"),
                sig("c", "LONG", 80, "15m")],
    }
    a1 = aggregate("BTCUSDT", one_tf, min_agreeing=3, min_confidence=70)
    a2 = aggregate("BTCUSDT", multi_tf, min_agreeing=3, min_confidence=70)
    assert a2.confidence > a1.confidence


def test_conflict_penalty():
    per_tf = {
        "5m": [sig("a", "LONG", 85), sig("b", "LONG", 85), sig("c", "LONG", 85)],
        "15m": [sig("d", "SHORT", 85), sig("e", "SHORT", 85)],
    }
    agg = aggregate("BTCUSDT", per_tf, min_agreeing=3, min_confidence=70)
    assert agg.opposing == 2
    assert agg.direction == "LONG"


def test_duplicate_strategy_across_tf_counts_once():
    per_tf = {
        "1m": [sig("a", "LONG", 80, "1m")],
        "3m": [sig("a", "LONG", 80, "3m")],
        "5m": [sig("a", "LONG", 80, "5m")],
    }
    agg = aggregate("BTCUSDT", per_tf, min_agreeing=3, min_confidence=70)
    assert agg.agreeing == 1
    assert agg.direction == "NEUTRAL"
