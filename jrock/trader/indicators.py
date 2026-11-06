"""Pure-python indicators (no numpy dependency at runtime).

All functions take/return plain lists of floats, oldest -> newest.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Candle:
    time: int
    open: float
    high: float
    low: float
    close: float
    base_vol: float = 0.0
    quote_vol: float = 0.0


def to_candles(raw: list[dict]) -> list[Candle]:
    out = []
    for r in raw or []:
        try:
            out.append(Candle(
                time=int(r.get("time", 0)),
                open=float(r["open"]), high=float(r["high"]),
                low=float(r["low"]), close=float(r["close"]),
                base_vol=float(r.get("baseVol", 0) or 0),
                quote_vol=float(r.get("quoteVol", 0) or 0),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    out.sort(key=lambda c: c.time)
    return out


def sma(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if period <= 0:
        return out
    run = 0.0
    for i, v in enumerate(values):
        run += v
        if i >= period:
            run -= values[i - period]
        if i >= period - 1:
            out[i] = run / period
    return out


def ema(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if not values or period <= 0 or len(values) < period:
        return out
    k = 2 / (period + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rma(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < period or period <= 0:
        return out
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = (prev * (period - 1) + values[i]) / period
        out[i] = prev
    return out


def dema(values: list[float], period: int) -> list[float | None]:
    e1 = ema(values, period)
    e1v = [v if v is not None else 0.0 for v in e1]
    e2 = ema(e1v, period)
    out: list[float | None] = [None] * len(values)
    for i in range(len(values)):
        if e1[i] is not None and e2[i] is not None:
            out[i] = 2 * e1[i] - e2[i]
    return out


def tema(values: list[float], period: int) -> list[float | None]:
    e1 = ema(values, period)
    e1v = [v if v is not None else 0.0 for v in e1]
    e2 = ema(e1v, period)
    e2v = [v if v is not None else 0.0 for v in e2]
    e3 = ema(e2v, period)
    out: list[float | None] = [None] * len(values)
    for i in range(len(values)):
        if e1[i] is not None and e2[i] is not None and e3[i] is not None:
            out[i] = 3 * e1[i] - 3 * e2[i] + e3[i]
    return out


def rsi(values: list[float], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return out
    gains, losses = [], []
    for i in range(1, len(values)):
        d = values[i] - values[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    ag = rma(gains, period)
    al = rma(losses, period)
    for i in range(len(values)):
        gi = i - 1
        if 0 <= gi < len(ag) and ag[gi] is not None and al[gi] is not None:
            out[i] = 100.0 if al[gi] == 0 else 100 - 100 / (1 + ag[gi] / al[gi])
    return out


def macd(values: list[float], fast: int = 12, slow: int = 26,
         signal: int = 9) -> tuple[list[float | None], list[float | None], list[float | None]]:
    ef = ema(values, fast)
    es = ema(values, slow)
    line: list[float | None] = [None] * len(values)
    for i in range(len(values)):
        if ef[i] is not None and es[i] is not None:
            line[i] = ef[i] - es[i]
    linev = [v if v is not None else 0.0 for v in line]
    sig = ema(linev, signal)
    hist: list[float | None] = [None] * len(values)
    for i in range(len(values)):
        if line[i] is not None and sig[i] is not None:
            hist[i] = line[i] - sig[i]
    return line, sig, hist


def bollinger(values: list[float], period: int = 20,
              mult: float = 2.0) -> tuple[list[float | None], list[float | None], list[float | None]]:
    mid = sma(values, period)
    upper: list[float | None] = [None] * len(values)
    lower: list[float | None] = [None] * len(values)
    for i in range(len(values)):
        if mid[i] is None:
            continue
        window = values[i - period + 1:i + 1]
        m = mid[i]
        var = sum((x - m) ** 2 for x in window) / period
        sd = var ** 0.5
        upper[i] = m + mult * sd
        lower[i] = m - mult * sd
    return upper, mid, lower


def vwap(candles: list[Candle], period: int | None = None) -> list[float | None]:
    out: list[float | None] = [None] * len(candles)
    pv_sum = 0.0
    v_sum = 0.0
    for i, c in enumerate(candles):
        tp = (c.high + c.low + c.close) / 3
        vol = c.base_vol or 1.0
        pv_sum += tp * vol
        v_sum += vol
        if period and i >= period:
            old = candles[i - period]
            otp = (old.high + old.low + old.close) / 3
            ovol = old.base_vol or 1.0
            pv_sum -= otp * ovol
            v_sum -= ovol
        out[i] = pv_sum / v_sum if v_sum else None
    return out


def atr(candles: list[Candle], period: int = 14) -> list[float | None]:
    trs: list[float] = []
    for i, c in enumerate(candles):
        if i == 0:
            trs.append(c.high - c.low)
        else:
            pc = candles[i - 1].close
            trs.append(max(c.high - c.low, abs(c.high - pc), abs(c.low - pc)))
    return rma(trs, period)


def stdev(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    for i in range(len(values)):
        if i < period - 1:
            continue
        w = values[i - period + 1:i + 1]
        m = sum(w) / period
        out[i] = (sum((x - m) ** 2 for x in w) / period) ** 0.5
    return out


def crossed_above(a: list[float | None], b: list[float | None], i: int = -1) -> bool:
    i = len(a) + i if i < 0 else i
    if i < 1 or i >= len(a):
        return False
    a0, a1, b0, b1 = a[i - 1], a[i], b[i - 1], b[i]
    if None in (a0, a1, b0, b1):
        return False
    return a0 <= b0 and a1 > b1


def crossed_below(a: list[float | None], b: list[float | None], i: int = -1) -> bool:
    i = len(a) + i if i < 0 else i
    if i < 1 or i >= len(a):
        return False
    a0, a1, b0, b1 = a[i - 1], a[i], b[i - 1], b[i]
    if None in (a0, a1, b0, b1):
        return False
    return a0 >= b0 and a1 < b1


def last(values: list[float | None], offset: int = 0) -> float | None:
    idx = len(values) - 1 - offset
    if 0 <= idx < len(values):
        return values[idx]
    return None
