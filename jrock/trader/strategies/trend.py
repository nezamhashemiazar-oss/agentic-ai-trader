"""Trend strategies: MA200+MACD, Vegas bullish, TEMA/VWAP, DEMA55, VWAP confirm,
Three White Soldiers."""
from __future__ import annotations

from ..indicators import (Candle, crossed_above, dema, ema, last, macd, rsi, sma,
                          tema, vwap)
from .base import BaseStrategy, Signal


class Ma200CrossMacdConfirm(BaseStrategy):
    name = "ma200_cross_macd_confirm"
    category = "trend"
    description = "Major trend turn bullish"
    indicators = "MA + MACD"
    min_candles = 210

    def evaluate(self, candles: list[Candle], timeframe: str = "", symbol: str = "") -> Signal:
        if not self.enough(candles):
            return self.neutral(f"need {self.min_candles} candles", timeframe, symbol)
        closes = [c.close for c in candles]
        ma200 = sma(closes, 200)
        line, sig, hist = macd(closes)
        m = last(ma200)
        if m is None:
            return self.neutral("MA200 not ready", timeframe, symbol)

        cross = crossed_above(closes, ma200)
        above = closes[-1] > m
        macd_bull = last(line) is not None and last(sig) is not None and last(line) > last(sig)
        hist_pos = last(hist) is not None and last(hist) > 0

        conf = 0.0
        if cross:
            conf += 45
        elif above:
            conf += 25
        if macd_bull:
            conf += 25
        if hist_pos:
            conf += 15
        dist = (closes[-1] - m) / m * 100 if m else 0
        if 0 < dist < 5:
            conf += 10

        ind = {"ma200": m, "close": closes[-1], "dist_pct": round(dist, 2), "macd_bull": macd_bull}
        if conf >= 60 and above and macd_bull:
            return self.signal("LONG", conf, "MA200 reclaim + MACD confirm", timeframe, symbol, ind)
        return self.neutral("no MA200 bullish turn", timeframe, symbol, ind)


class VegasTunnelBullish(BaseStrategy):
    name = "vegas_tunnel_bullish"
    category = "trend"
    description = "Tunnel-up golden cross long"
    indicators = "EMA + EMA CROSS"
    min_candles = 200

    def evaluate(self, candles: list[Candle], timeframe: str = "", symbol: str = "") -> Signal:
        if not self.enough(candles):
            return self.neutral(f"need {self.min_candles} candles", timeframe, symbol)
        closes = [c.close for c in candles]
        e144 = ema(closes, 144)
        e169 = ema(closes, 169)
        e12 = ema(closes, 12)
        a, b = last(e144), last(e169)
        if a is None or b is None:
            return self.neutral("EMA144/169 not ready", timeframe, symbol)

        tunnel_up = a > b
        golden = crossed_above(e144, e169)
        price_above = closes[-1] > max(a, b)
        fast_above = (last(e12) or closes[-1]) > max(a, b)

        conf = 0.0
        if tunnel_up:
            conf += 40
        if golden:
            conf += 30
        if price_above:
            conf += 15
        if fast_above:
            conf += 10
        if len(closes) >= 576:
            e576 = last(ema(closes, 576))
            if e576 is not None and closes[-1] > e576:
                conf += 5

        ind = {"ema144": a, "ema169": b, "ema12": last(e12), "close": closes[-1]}
        if conf >= 60 and tunnel_up:
            return self.signal("LONG", conf, "Vegas tunnel up + golden cross", timeframe, symbol, ind)
        return self.neutral("no tunnel-up golden cross", timeframe, symbol, ind)


class TemaCrossVwap(BaseStrategy):
    name = "tema_cross_above_vwap"
    category = "trend"
    description = "Intraday bullish alignment"
    indicators = "TEMA + VWAP"
    min_candles = 60

    def evaluate(self, candles: list[Candle], timeframe: str = "", symbol: str = "") -> Signal:
        if not self.enough(candles):
            return self.neutral(f"need {self.min_candles} candles", timeframe, symbol)
        closes = [c.close for c in candles]
        t = tema(closes, 20)
        vw = vwap(candles, period=96)
        tv, v = last(t), last(vw)
        if tv is None or v is None:
            return self.neutral("TEMA/VWAP not ready", timeframe, symbol)

        cross = crossed_above(t, vw)
        above = tv > v and closes[-1] > v
        slope = len(t) > 3 and t[-1] is not None and t[-4] is not None and t[-1] > t[-4]

        conf = 0.0
        if cross:
            conf += 45
        elif above:
            conf += 28
        if closes[-1] > v:
            conf += 15
        if slope:
            conf += 15
        dist = (closes[-1] - v) / v * 100 if v else 0
        if 0 < dist < 1.5:
            conf += 10

        ind = {"tema": tv, "vwap": v, "slope_up": slope, "dist_pct": round(dist, 3)}
        if conf >= 60 and above:
            return self.signal("LONG", conf, "TEMA above VWAP, aligned", timeframe, symbol, ind)
        return self.neutral("no TEMA/VWAP alignment", timeframe, symbol, ind)


class Dema55TrendFollow(BaseStrategy):
    name = "dema55_bullish_trend_follow"
    category = "trend"
    description = "Mid-term bullish momentum"
    indicators = "DEMA + MACD"
    min_candles = 80

    def evaluate(self, candles: list[Candle], timeframe: str = "", symbol: str = "") -> Signal:
        if not self.enough(candles):
            return self.neutral(f"need {self.min_candles} candles", timeframe, symbol)
        closes = [c.close for c in candles]
        d = dema(closes, 55)
        line, sig, hist = macd(closes)
        dv = last(d)
        if dv is None:
            return self.neutral("DEMA55 not ready", timeframe, symbol)

        above = closes[-1] > dv
        slope = len(d) > 5 and d[-1] is not None and d[-6] is not None and d[-1] > d[-6]
        macd_bull = last(line) is not None and last(sig) is not None and last(line) > last(sig)
        hist_pos = last(hist) is not None and last(hist) > 0

        conf = 0.0
        if above:
            conf += 30
        if slope:
            conf += 25
        if macd_bull:
            conf += 25
        if hist_pos:
            conf += 10
        dist = (closes[-1] - dv) / dv * 100 if dv else 0
        if 0 < dist < 4:
            conf += 10

        ind = {"dema55": dv, "slope_up": slope, "macd_bull": macd_bull, "dist_pct": round(dist, 2)}
        if conf >= 60 and above and slope:
            return self.signal("LONG", conf, "DEMA55 trend follow", timeframe, symbol, ind)
        return self.neutral("no DEMA55 trend", timeframe, symbol, ind)


class VwapBullishConfirmation(BaseStrategy):
    name = "vwap_bullish_confirmation"
    category = "trend"
    description = "Intraday bullish confirmation"
    indicators = "MACD + PRICE"
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

        above = closes[-1] > v
        reclaimed = crossed_above(closes, vw)
        macd_bull = last(line) is not None and last(sig) is not None and last(line) > last(sig)
        hist_pos = last(hist) is not None and last(hist) > 0

        conf = 0.0
        if reclaimed:
            conf += 40
        elif above:
            conf += 25
        if macd_bull:
            conf += 25
        if hist_pos:
            conf += 15
        dist = (closes[-1] - v) / v * 100 if v else 0
        if 0 < dist < 1.0:
            conf += 10

        ind = {"vwap": v, "close": closes[-1], "reclaimed": reclaimed, "dist_pct": round(dist, 3)}
        if conf >= 60 and above and macd_bull:
            return self.signal("LONG", conf, "VWAP bullish confirmation", timeframe, symbol, ind)
        return self.neutral("no VWAP bullish confirmation", timeframe, symbol, ind)


class ThreeWhiteSoldiers(BaseStrategy):
    name = "three_white_soldiers_trend_confirm"
    category = "trend"
    description = "Three White Soldiers continuation"
    indicators = "Candlestick + RSI + EMA"
    min_candles = 60

    def evaluate(self, candles: list[Candle], timeframe: str = "", symbol: str = "") -> Signal:
        if not self.enough(candles):
            return self.neutral(f"need {self.min_candles} candles", timeframe, symbol)
        closes = [c.close for c in candles]
        r = rsi(closes, 14)
        e20 = ema(closes, 20)
        rv = last(r)
        if rv is None or len(candles) < 3:
            return self.neutral("RSI not ready", timeframe, symbol)

        c1, c2, c3 = candles[-3], candles[-2], candles[-1]
        soldiers = (all(c.close > c.open for c in (c1, c2, c3))
                    and c2.close > c1.close and c3.close > c2.close
                    and all((c.close - c.open) / max(c.high - c.low, 1e-12) > 0.5
                            for c in (c1, c2, c3)))
        healthy = 50 <= rv <= 75
        above_ema = last(e20) is not None and closes[-1] > last(e20)

        conf = 0.0
        if soldiers:
            conf += 50
        if healthy:
            conf += 25
        elif rv > 75:
            conf += 5
        if above_ema:
            conf += 15
        if len(closes) > 5 and closes[-1] > closes[-5]:
            conf += 10

        ind = {"soldiers": soldiers, "rsi": round(rv, 2), "ema20": last(e20)}
        if conf >= 60 and soldiers:
            return self.signal("LONG", conf, "Three white soldiers", timeframe, symbol, ind)
        return self.neutral("no three white soldiers", timeframe, symbol, ind)
