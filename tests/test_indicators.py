import math

from jrock.trader.indicators import (Candle, bollinger, crossed_above, crossed_below,
                                     dema, ema, macd, rsi, sma, tema, to_candles, vwap)


def test_sma_basic():
    out = sma([1, 2, 3, 4, 5], 3)
    assert out[0] is None and out[1] is None
    assert out[2] == 2.0
    assert out[4] == 4.0


def test_ema_converges():
    vals = [10.0] * 50
    out = ema(vals, 10)
    assert out[-1] == 10.0


def test_rsi_all_up_is_100():
    vals = [float(i) for i in range(1, 40)]
    out = rsi(vals, 14)
    assert out[-1] is not None and out[-1] > 99


def test_rsi_all_down_is_0():
    vals = [float(i) for i in range(40, 1, -1)]
    out = rsi(vals, 14)
    assert out[-1] is not None and out[-1] < 1


def test_macd_shapes():
    vals = [float(i) for i in range(1, 100)]
    line, sig, hist = macd(vals)
    assert len(line) == len(sig) == len(hist) == 99
    assert line[-1] is not None


def test_bollinger_bands_ordered():
    vals = [100 + math.sin(i / 3) * 5 for i in range(60)]
    up, mid, low = bollinger(vals, 20, 2.0)
    assert up[-1] > mid[-1] > low[-1]


def test_crossed_above():
    a = [1, 2, 3, 4]
    b = [2, 2, 2, 2]
    # the cross happens at index 2 (2 -> 3 over 2), not at the last bar
    assert crossed_above(a, b, i=2) is True
    assert crossed_above(a, b) is False  # last bar: 3 -> 4 is already above
    assert crossed_below(a, b) is False


def test_crossed_below():
    a = [4, 3, 2, 1]
    b = [2, 2, 2, 2]
    # at i=2 the value is exactly equal (2 vs 2); the cross is at i=3
    assert crossed_below(a, b, i=2) is False
    assert crossed_below(a, b, i=3) is True
    assert crossed_below(a, b) is True


def test_dema_tema_lengths():
    vals = [float(i) for i in range(1, 120)]
    assert len(dema(vals, 20)) == len(vals)
    assert len(tema(vals, 20)) == len(vals)


def test_to_candles_sorts_and_parses():
    raw = [
        {"time": 2, "open": "1", "high": "2", "low": "0.5", "close": "1.5", "baseVol": "10"},
        {"time": 1, "open": "1", "high": "2", "low": "0.5", "close": "1.2", "baseVol": "5"},
    ]
    cs = to_candles(raw)
    assert [c.time for c in cs] == [1, 2]
    assert cs[0].close == 1.2


def test_vwap_between_low_and_high():
    cs = [Candle(i, 100, 102, 98, 101, base_vol=10) for i in range(20)]
    v = vwap(cs)
    assert v[-1] is not None and 98 <= v[-1] <= 102
