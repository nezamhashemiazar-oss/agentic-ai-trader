"""Reversal strategies: hammer, morning star, shooting star, dark cloud cover."""
from __future__ import annotations

from ..indicators import Candle, crossed_above, crossed_below, ema, last, macd, rsi
from .base import BaseStrategy, Signal


def _body(c: Candle) -> float:
    return abs(c.close - c.open)


def _range(c: Candle) -> float:
    return max(c.high - c.low, 1e-12)


def is_hammer(c: Candle) -> bool:
    body = _body(c)
    rng = _range(c)
    lower = min(c.open, c.close) - c.low
    upper = c.high - max(c.open, c.close)
    return body / rng < 0.35 and lower / rng > 0.55 and upper / rng < 0.2


def is_shooting_star(c: Candle) -> bool:
    body = _body(c)
    rng = _range(c)
    upper = c.high - max(c.open, c.close)
    lower = min(c.open, c.close) - c.low
    return body / rng < 0.35 and upper / rng > 0.55 and lower / rng < 0.2


def is_bullish(c: Candle) -> bool:
    return c.close > c.open


def is_bearish(c: Candle) -> bool:
    return c.close < c.open


class HammerOversoldBounce(BaseStrategy):
    name = "hammer_oversold_bounce"
    category = "reversal"
    description = "Hammer candle at oversold lows"
    indicators = "Candlestick + RSI + EMA"
    min_candles = 60

    def evaluate(self, candles: list[Candle], timeframe: str = "", symbol: str = "") -> Signal:
        if not self.enough(candles):
            return self.neutral(f"need {self.min_candles} candles", timeframe, symbol)
        closes = [c.close for c in candles]
        r = rsi(closes, 14)
        e50 = ema(closes, 50)
        rv = last(r)
        if rv is None:
            return self.neutral("RSI not ready", timeframe, symbol)

        hammer = is_hammer(candles[-1])
        oversold = rv <= 35
        near_ema = last(e50) is not None and abs(closes[-1] - last(e50)) / last(e50) < 0.03
        downtrend = last(e50) is not None and closes[-1] < last(e50)

        conf = 0.0
        if hammer:
            conf += 40
        if oversold:
            conf += 30
        elif rv <= 45:
            conf += 15
        if downtrend:
            conf += 15
        if near_ema:
            conf += 10

        ind = {"rsi": round(rv, 2), "hammer": hammer, "ema50": last(e50)}
        if conf >= 60 and hammer:
            return self.signal("LONG", conf, f"Hammer at RSI {rv:.1f}", timeframe, symbol, ind)
        return self.neutral("no hammer setup", timeframe, symbol, ind)


class MorningStarDivergence(BaseStrategy):
    name = "morning_star_bullish_divergence"
    category = "reversal"
    description = "Bottom pattern with divergence"
    indicators = "Candlestick + MACD"
    min_candles = 60

    def evaluate(self, candles: list[Candle], timeframe: str = "", symbol: str = "") -> Signal:
        if not self.enough(candles):
            return self.neutral(f"need {self.min_candles} candles", timeframe, symbol)
        closes = [c.close for c in candles]
        line, sig, hist = macd(closes)
        if len(candles) < 3:
            return self.neutral("not enough candles", timeframe, symbol)

        c1, c2, c3 = candles[-3], candles[-2], candles[-1]
        star = (is_bearish(c1) and _body(c2) / _range(c2) < 0.3
                and is_bullish(c3) and c3.close > (c1.open + c1.close) / 2)
        # bullish divergence: price lower low, MACD histogram higher low
        div = False
        if len(closes) >= 30 and last(hist) is not None and hist[-10] is not None:
            low_now = min(closes[-10:])
            low_prev = min(closes[-30:-10])
            div = low_now < low_prev and (last(hist) or 0) > (hist[-10] or 0)

        conf = 0.0
        if star:
            conf += 45
        if div:
            conf += 30
        if last(line) is not None and last(sig) is not None and last(line) > last(sig):
            conf += 15
        if last(hist) is not None and last(hist) > 0:
            conf += 10

        ind = {"morning_star": star, "divergence": div, "hist": last(hist)}
        if conf >= 60 and star:
            return self.signal("LONG", conf, "Morning star + MACD divergence", timeframe, symbol, ind)
        return self.neutral("no morning star", timeframe, symbol, ind)


class ShootingStarOverbought(BaseStrategy):
    name = "shooting_star_overbought_alert"
    category = "reversal"
    description = "Overbought shooting star warning"
    indicators = "Candlestick + RSI + EMA"
    min_candles = 60

    def evaluate(self, candles: list[Candle], timeframe: str = "", symbol: str = "") -> Signal:
        if not self.enough(candles):
            return self.neutral(f"need {self.min_candles} candles", timeframe, symbol)
        closes = [c.close for c in candles]
        r = rsi(closes, 14)
        e50 = ema(closes, 50)
        rv = last(r)
        if rv is None:
            return self.neutral("RSI not ready", timeframe, symbol)

        star = is_shooting_star(candles[-1])
        overbought = rv >= 65
        extended = last(e50) is not None and closes[-1] > last(e50)

        conf = 0.0
        if star:
            conf += 40
        if overbought:
            conf += 30
        elif rv >= 55:
            conf += 15
        if extended:
            conf += 15
        if last(e50) is not None and (closes[-1] - last(e50)) / last(e50) > 0.02:
            conf += 10

        ind = {"rsi": round(rv, 2), "shooting_star": star, "ema50": last(e50)}
        if conf >= 60 and star:
            return self.signal("SHORT", conf, f"Shooting star at RSI {rv:.1f}", timeframe, symbol, ind)
        return self.neutral("no shooting star", timeframe, symbol, ind)


class DarkCloudCoverDeathCross(BaseStrategy):
    name = "dark_cloud_cover_death_cross"
    category = "reversal"
    description = "Bearish top with MACD cross"
    indicators = "Candlestick + MACD"
    min_candles = 60

    def evaluate(self, candles: list[Candle], timeframe: str = "", symbol: str = "") -> Signal:
        if not self.enough(candles):
            return self.neutral(f"need {self.min_candles} candles", timeframe, symbol)
        closes = [c.close for c in candles]
        line, sig, hist = macd(closes)
        if len(candles) < 2:
            return self.neutral("not enough candles", timeframe, symbol)

        prev, cur = candles[-2], candles[-1]
        dark_cloud = (is_bullish(prev) and is_bearish(cur)
                      and cur.open > prev.close
                      and cur.close < (prev.open + prev.close) / 2
                      and cur.close > prev.open)
        macd_cross = crossed_below(line, sig)
        macd_bear = last(line) is not None and last(sig) is not None and last(line) < last(sig)

        conf = 0.0
        if dark_cloud:
            conf += 45
        if macd_cross:
            conf += 30
        elif macd_bear:
            conf += 18
        if last(hist) is not None and last(hist) < 0:
            conf += 12

        ind = {"dark_cloud": dark_cloud, "macd_cross": macd_cross, "hist": last(hist)}
        if conf >= 60 and dark_cloud:
            return self.signal("SHORT", conf, "Dark cloud cover + MACD cross", timeframe, symbol, ind)
        return self.neutral("no dark cloud cover", timeframe, symbol, ind)
