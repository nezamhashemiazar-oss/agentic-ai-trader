"""Classic strategies: Vegas Tunnel bearish, Golden Cross, Death Cross."""
from __future__ import annotations

from ..indicators import Candle, crossed_above, crossed_below, ema, last, sma
from .base import BaseStrategy, Signal


class VegasTunnelBearish(BaseStrategy):
    name = "vegas_tunnel_bearish"
    category = "classic"
    description = "Tunnel-down death cross short"
    indicators = "EMA + EMA CROSS"
    min_candles = 200

    def evaluate(self, candles: list[Candle], timeframe: str = "", symbol: str = "") -> Signal:
        if not self.enough(candles):
            return self.neutral(f"need {self.min_candles} candles", timeframe, symbol)
        closes = [c.close for c in candles]
        e144 = ema(closes, 144)
        e169 = ema(closes, 169)
        e12 = ema(closes, 12)
        e576 = ema(closes, 576) if len(closes) >= 576 else [None] * len(closes)

        a, b = last(e144), last(e169)
        if a is None or b is None:
            return self.neutral("EMA144/169 not ready", timeframe, symbol)

        tunnel_down = a < b
        death_cross = crossed_below(e144, e169)
        price_below = closes[-1] < min(a, b)
        fast_below = (last(e12) or closes[-1]) < min(a, b)

        conf = 0.0
        if tunnel_down:
            conf += 40
        if death_cross:
            conf += 30
        if price_below:
            conf += 15
        if fast_below:
            conf += 10
        if e576 and last(e576) is not None and closes[-1] < last(e576):
            conf += 5

        ind = {"ema144": a, "ema169": b, "ema12": last(e12), "close": closes[-1]}
        if conf >= 60 and tunnel_down:
            return self.signal("SHORT", conf, "Vegas tunnel down + death cross", timeframe, symbol, ind)
        return self.neutral("no tunnel-down death cross", timeframe, symbol, ind)


class GoldenCrossLong(BaseStrategy):
    name = "golden_cross_50_200_long"
    category = "classic"
    description = "Golden cross with volume confirmation"
    indicators = "MA CROSS + VOLUME"
    min_candles = 210

    def evaluate(self, candles: list[Candle], timeframe: str = "", symbol: str = "") -> Signal:
        if not self.enough(candles):
            return self.neutral(f"need {self.min_candles} candles", timeframe, symbol)
        closes = [c.close for c in candles]
        vols = [c.base_vol for c in candles]
        ma50 = sma(closes, 50)
        ma200 = sma(closes, 200)
        if last(ma50) is None or last(ma200) is None:
            return self.neutral("MA not ready", timeframe, symbol)

        cross = crossed_above(ma50, ma200)
        above = last(ma50) > last(ma200)
        avg_vol = sum(vols[-20:]) / 20 if len(vols) >= 20 else 0
        vol_ratio = (vols[-1] / avg_vol) if avg_vol else 0

        conf = 0.0
        if cross:
            conf += 45
        elif above:
            conf += 20
        if vol_ratio >= 1.5:
            conf += 30
        elif vol_ratio >= 1.2:
            conf += 18
        elif vol_ratio >= 1.0:
            conf += 8
        if closes[-1] > (last(ma50) or 0):
            conf += 10

        ind = {"ma50": last(ma50), "ma200": last(ma200), "vol_ratio": round(vol_ratio, 2)}
        if conf >= 60 and above:
            return self.signal("LONG", conf, f"Golden cross, vol x{vol_ratio:.2f}", timeframe, symbol, ind)
        return self.neutral("no golden cross", timeframe, symbol, ind)


class DeathCrossShort(BaseStrategy):
    name = "death_cross_50_200_short"
    category = "classic"
    description = "Death cross with weak volume"
    indicators = "MA CROSS + VOLUME"
    min_candles = 210

    def evaluate(self, candles: list[Candle], timeframe: str = "", symbol: str = "") -> Signal:
        if not self.enough(candles):
            return self.neutral(f"need {self.min_candles} candles", timeframe, symbol)
        closes = [c.close for c in candles]
        vols = [c.base_vol for c in candles]
        ma50 = sma(closes, 50)
        ma200 = sma(closes, 200)
        if last(ma50) is None or last(ma200) is None:
            return self.neutral("MA not ready", timeframe, symbol)

        cross = crossed_below(ma50, ma200)
        below = last(ma50) < last(ma200)
        avg_vol = sum(vols[-20:]) / 20 if len(vols) >= 20 else 0
        vol_ratio = (vols[-1] / avg_vol) if avg_vol else 0

        conf = 0.0
        if cross:
            conf += 45
        elif below:
            conf += 20
        if vol_ratio < 0.9:
            conf += 25
        elif vol_ratio < 1.1:
            conf += 12
        if closes[-1] < (last(ma50) or 0):
            conf += 10

        ind = {"ma50": last(ma50), "ma200": last(ma200), "vol_ratio": round(vol_ratio, 2)}
        if conf >= 60 and below:
            return self.signal("SHORT", conf, f"Death cross, weak vol x{vol_ratio:.2f}", timeframe, symbol, ind)
        return self.neutral("no death cross", timeframe, symbol, ind)
