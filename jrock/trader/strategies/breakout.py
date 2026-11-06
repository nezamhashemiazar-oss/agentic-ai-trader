"""Breakout strategies: Bollinger upper band, mid-band MACD cross."""
from __future__ import annotations

from ..indicators import Candle, bollinger, crossed_above, last, macd, rsi
from .base import BaseStrategy, Signal


class BollingerUpperBreakout(BaseStrategy):
    name = "bollinger_upper_band_breakout"
    category = "breakout"
    description = "Upper band breakout with momentum"
    indicators = "BB + RSI"
    min_candles = 60

    def evaluate(self, candles: list[Candle], timeframe: str = "", symbol: str = "") -> Signal:
        if not self.enough(candles):
            return self.neutral(f"need {self.min_candles} candles", timeframe, symbol)
        closes = [c.close for c in candles]
        up, mid, low = bollinger(closes, 20, 2.0)
        r = rsi(closes, 14)
        u, m, rv = last(up), last(mid), last(r)
        if u is None or m is None or rv is None:
            return self.neutral("BB/RSI not ready", timeframe, symbol)

        broke = closes[-1] > u
        prev_below = len(closes) > 1 and up[-2] is not None and closes[-2] <= up[-2]
        fresh = broke and prev_below
        momentum = 55 <= rv <= 78
        width = (u - (last(low) or u)) / m * 100 if m else 0

        conf = 0.0
        if fresh:
            conf += 45
        elif broke:
            conf += 28
        if momentum:
            conf += 25
        elif rv > 78:
            conf += 8
        if width > 1.0:
            conf += 10
        if closes[-1] > m:
            conf += 10

        ind = {"upper": u, "mid": m, "rsi": round(rv, 2), "fresh_break": fresh}
        if conf >= 60 and broke:
            return self.signal("LONG", conf, f"BB upper breakout, RSI {rv:.1f}", timeframe, symbol, ind)
        return self.neutral("no BB breakout", timeframe, symbol, ind)


class MidBandMacdCross(BaseStrategy):
    name = "mid_band_macd_cross"
    category = "breakout"
    description = "Bullish MACD above mid-band"
    indicators = "MACD + BB + RSI"
    min_candles = 60

    def evaluate(self, candles: list[Candle], timeframe: str = "", symbol: str = "") -> Signal:
        if not self.enough(candles):
            return self.neutral(f"need {self.min_candles} candles", timeframe, symbol)
        closes = [c.close for c in candles]
        up, mid, low = bollinger(closes, 20, 2.0)
        line, sig, hist = macd(closes)
        r = rsi(closes, 14)
        m, rv = last(mid), last(r)
        if m is None or rv is None:
            return self.neutral("BB/RSI not ready", timeframe, symbol)

        cross = crossed_above(line, sig)
        above_mid = closes[-1] > m
        macd_bull = last(line) is not None and last(sig) is not None and last(line) > last(sig)
        healthy = 45 <= rv <= 70

        conf = 0.0
        if cross:
            conf += 40
        elif macd_bull:
            conf += 20
        if above_mid:
            conf += 25
        if healthy:
            conf += 20
        if last(hist) is not None and last(hist) > 0:
            conf += 10

        ind = {"mid": m, "rsi": round(rv, 2), "macd_cross": cross, "above_mid": above_mid}
        if conf >= 60 and above_mid and macd_bull:
            return self.signal("LONG", conf, "MACD cross above BB mid-band", timeframe, symbol, ind)
        return self.neutral("no mid-band MACD cross", timeframe, symbol, ind)
