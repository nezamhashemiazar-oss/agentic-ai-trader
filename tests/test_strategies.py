import math

from jrock.trader.indicators import Candle
from jrock.trader.strategies import build_strategies, registry


def make_candles(n=300, start=100.0, drift=0.0, vol=1.0, wave=0.0):
    out = []
    price = start
    for i in range(n):
        price = price * (1 + drift) + math.sin(i / 5) * wave
        o = price
        c = price * (1 + drift)
        h = max(o, c) * 1.002
        l = min(o, c) * 0.998
        out.append(Candle(i * 60000, o, h, l, c, base_vol=vol * (1 + (i % 5) * 0.1)))
    return out


def test_registry_has_all_unique_strategies():
    """The spec lists 20 entries, but section 6 repeats the 3 classic strategies
    verbatim (Vegas bearish, Golden Cross, Death Cross). There are 16 UNIQUE
    strategies; the 3 duplicates are the same modules, not new ones."""
    reg = registry()
    assert len(reg) == 16, f"expected 16 unique strategies, got {len(reg)}"
    cats = {v["category"] for v in reg.values()}
    assert cats == {"classic", "risk", "reversal", "breakout", "trend"}
    # the 3 spec duplicates must resolve to the same modules
    assert "vegas_tunnel_bearish" in reg
    assert "golden_cross_50_200_long" in reg
    assert "death_cross_50_200_short" in reg


def test_every_strategy_returns_signal_on_uptrend():
    candles = make_candles(400, drift=0.001, wave=0.3)
    for strat in build_strategies():
        sig = strat.evaluate(candles, timeframe="15m", symbol="BTCUSDT")
        assert sig.strategy == strat.name
        assert sig.direction in ("LONG", "SHORT", "NEUTRAL")
        assert 0 <= sig.confidence <= 100


def test_every_strategy_handles_short_data():
    candles = make_candles(5)
    for strat in build_strategies():
        sig = strat.evaluate(candles, timeframe="1m", symbol="BTCUSDT")
        assert sig.direction == "NEUTRAL"
        assert sig.confidence == 0.0


def test_every_strategy_handles_empty():
    for strat in build_strategies():
        sig = strat.evaluate([], timeframe="1m", symbol="BTCUSDT")
        assert sig.direction == "NEUTRAL"


def test_downtrend_produces_short_signals():
    candles = make_candles(400, drift=-0.001, wave=0.3)
    shorts = sum(1 for s in build_strategies()
                 if s.evaluate(candles, timeframe="15m", symbol="BTCUSDT").direction == "SHORT")
    assert shorts >= 1, "a sustained downtrend should trigger at least one short strategy"


def test_uptrend_produces_long_signals():
    candles = make_candles(400, drift=0.0015, wave=0.2)
    longs = sum(1 for s in build_strategies()
                if s.evaluate(candles, timeframe="15m", symbol="BTCUSDT").direction == "LONG")
    assert longs >= 1, "a sustained uptrend should trigger at least one long strategy"
