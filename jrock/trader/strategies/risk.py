"""Risk strategies: VWAP breakdown alert."""
from __future__ import annotations

from ..indicators import Candle, crossed_below, last, macd, vwap
from .base import BaseStrategy, Signal


class VwapBreakdownAlert(BaseStrategy):
    name = "vwap_breakdown_alert"
    category = "risk"
    description = "Intraday risk alert"
    indicators = "VWAP + MACD"
    min_candles = 60

    def evaluate(self, candles: list[Candle], timeframe: str = "", symbol: str = "") -> Signal:
        if not self.enough(candles):
            return self.neutral(f"need {self.min_candles} candles", timeframe, symbol)
        closes = [c.close for c in candles]
        vw = vwap(candles, period=96)
        line, sig, hist = macd(closes)

        v = last(vw)
        if v is None:
            return self.neutral("VWAP not ready", timeframe, symbol)

        below = closes[-1] < v
        broke = crossed_below([c.close for c in candles], vw)
        macd_bear = (last(line) is not None and last(sig) is not None and last(line) < last(sig))
        hist_neg = last(hist) is not None and last(hist) < 0

        conf = 0.0
        if broke:
            conf += 40
        elif below:
            conf += 22
        if macd_bear:
            conf += 25
        if hist_neg:
            conf += 15
        dist = abs(closes[-1] - v) / v * 100 if v else 0
        if dist > 0.3:
            conf += 10

        ind = {"vwap": round(v, 6), "close": closes[-1], "macd": last(line), "dist_pct": round(dist, 3)}
        if conf >= 60 and below:
            return self.signal("SHORT", conf, f"VWAP breakdown ({dist:.2f}% below)", timeframe, symbol, ind,
                               tags=["risk_alert"])
        return self.neutral("price above VWAP", timeframe, symbol, ind)
