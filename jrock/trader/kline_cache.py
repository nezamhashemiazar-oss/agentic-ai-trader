"""Kline cache fed by the WebSocket, with candle-appropriate staleness.

WHY THIS IS NOT THE PRICE CACHE
-------------------------------
A price is a point: it is either fresh (seconds) or it is a lie. A candle is an
interval: a 1m candle is *legitimately* up to 60s old, and a 1d candle is
legitimately up to 24h old. So the staleness rule here is a MULTIPLE OF THE
INTERVAL (`KLINE_MAX_AGE_SEC` is expressed as `max_age_mult * interval`), not a
flat number of seconds.

THE FRAME HAS NO TIME FIELD
---------------------------
Verified live against wss://fapi.bitunix.com/public/ on 2026-11-07: a
`market_kline_1min` push carries

    {"ch":"market_kline_1min","symbol":"BTCUSDT","ts":1794011592257,
     "data":{"o":"...","h":"...","l":"...","c":"...","b":"...","q":"..."}}

`data` has exactly the keys o/h/l/c/b/q - there is NO `time`. The REST kline
endpoint DOES return `time` (ms). So the candle's open time must be DERIVED by
flooring the frame `ts` to the interval boundary:

    open_time = (ts_ms // interval_ms) * interval_ms

Getting this wrong is silent: every candle would collapse onto one key and the
series would never grow.

REPLACE vs APPEND
-----------------
The channel pushes the *current, still-forming* candle every 500ms. So the same
open time arrives over and over with a changing close. The cache must REPLACE
the last candle when the open time matches and APPEND when a newer open time
arrives. Appending blindly would grow the series by one every 500ms and corrupt
every indicator - so this is tested explicitly.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .indicators import Candle

#: A candle set is stale when the newest candle's open time is older than
#: `max_age_mult * interval`. 2x gives headroom for the forming candle.
DEFAULT_MAX_AGE_MULT = 2.0

#: Hard cap on candles held per (symbol, interval). Bounded, never unbounded.
DEFAULT_MAX_CANDLES = 500

#: The REST kline endpoint returns at most this many candles per call
#: (verified live: limit=200/300/500 all return exactly 200).
REST_KLINE_MAX = 200

#: interval -> seconds. Both the engine's short spellings and the exchange's
#: documented ones, so either can be used as a cache key.
INTERVAL_SECONDS: dict[str, int] = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "8h": 28800,
    "12h": 43200, "1d": 86400, "3d": 259200, "1w": 604800, "1M": 2592000,
    # documented spellings pass through
    "1min": 60, "3min": 180, "5min": 300, "15min": 900, "30min": 1800,
    "60min": 3600, "1day": 86400, "3day": 259200, "1week": 604800,
    "1month": 2592000,
}


def interval_seconds(interval: str) -> int:
    """Seconds per candle. Raises for an interval the exchange does not document."""
    sec = INTERVAL_SECONDS.get((interval or "").strip())
    if sec is None:
        raise ValueError(
            f"unknown kline interval {interval!r}. "
            f"Valid: {', '.join(sorted(INTERVAL_SECONDS))}"
        )
    return sec


def candle_open_time(ts_ms: int, interval: str) -> int:
    """The open time (ms) of the candle a frame at `ts_ms` belongs to.

    The kline push carries no time field, so this is the only way to key a
    candle. Floors to the interval boundary.
    """
    step = interval_seconds(interval) * 1000
    return (int(ts_ms) // step) * step


@dataclass
class KlineResult:
    """What the cache can serve, and if it cannot, why not."""

    candles: list[Candle]
    source: str                 # "ws" | "rest"
    reason: str = ""
    age_s: float | None = None

    @property
    def usable(self) -> bool:
        return self.source == "ws" and bool(self.candles)


@dataclass
class KlineStats:
    hits: int = 0
    misses: int = 0
    stale_rejections: int = 0
    short_series: int = 0
    rest_fallbacks: int = 0
    ws_updates: int = 0
    rest_updates: int = 0
    replaced: int = 0
    appended: int = 0

    def as_dict(self) -> dict:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "stale_rejections": self.stale_rejections,
            "short_series": self.short_series,
            "rest_fallbacks": self.rest_fallbacks,
            "ws_updates": self.ws_updates,
            "rest_updates": self.rest_updates,
            "replaced": self.replaced,
            "appended": self.appended,
        }


class KlineCache:
    """Rolling candles per (symbol, interval), fed by WS, seeded once by REST."""

    def __init__(self, max_age_mult: float = DEFAULT_MAX_AGE_MULT,
                 max_candles: int = DEFAULT_MAX_CANDLES):
        self.max_age_mult = float(max_age_mult)
        self.max_candles = int(max_candles)
        self._series: dict[tuple[str, str], list[Candle]] = {}
        self._source: dict[tuple[str, str], str] = {}
        self.stats = KlineStats()

    # ------------------------------------------------------------- writes
    def put_ws(self, symbol: str, interval: str, candle: Candle) -> None:
        """Store a WS candle: replace the forming one, append a new one.

        A frame for a candle older than the newest is ignored unless we already
        hold that exact open time (then it is replaced in place).
        """
        if not symbol or candle is None:
            return
        key = (symbol.upper(), interval)
        series = self._series.setdefault(key, [])

        if series and series[-1].time == candle.time:
            series[-1] = candle
            self.stats.replaced += 1
        elif not series or candle.time > series[-1].time:
            series.append(candle)
            self.stats.appended += 1
        else:
            for i in range(len(series) - 1, -1, -1):
                if series[i].time == candle.time:
                    series[i] = candle
                    self.stats.replaced += 1
                    break
            else:
                return  # an old candle we never held: nothing to do

        if len(series) > self.max_candles:
            del series[:len(series) - self.max_candles]
        self._source[key] = "ws"
        self.stats.ws_updates += 1

    def put_rest(self, symbol: str, interval: str, candles: list[Candle]) -> None:
        """Seed/merge a REST series. Never overwrites a candle WS already gave us."""
        if not symbol or not candles:
            return
        key = (symbol.upper(), interval)
        merged: dict[int, Candle] = {c.time: c for c in self._series.get(key, [])}
        for c in candles:
            merged.setdefault(c.time, c)
        self._series[key] = [merged[t] for t in sorted(merged)][-self.max_candles:]
        if self._source.get(key) != "ws":
            self._source[key] = "rest"
        self.stats.rest_updates += 1

    # -------------------------------------------------------------- reads
    def max_age(self, interval: str) -> float:
        return self.max_age_mult * interval_seconds(interval)

    def age(self, symbol: str, interval: str) -> float | None:
        """Age of the NEWEST candle's open time, in seconds."""
        series = self._series.get((symbol.upper(), interval))
        if not series:
            return None
        return time.time() - (series[-1].time / 1000.0)

    def count(self, symbol: str, interval: str) -> int:
        return len(self._series.get((symbol.upper(), interval), []))

    def source(self, symbol: str, interval: str) -> str | None:
        return self._source.get((symbol.upper(), interval))

    def keys(self) -> list[tuple[str, str]]:
        return sorted(self._series)

    def try_get(self, symbol: str, interval: str, limit: int,
                min_required: int | None = None) -> KlineResult:
        """A usable series, or a result naming why the caller must use REST.

        `min_required` is the shortest series the caller can compute on. It
        defaults to `limit`. The engine passes the length REST would have
        returned, so serving from the cache is never worse than the REST path
        it replaces.
        """
        key = (symbol.upper(), interval)
        series = self._series.get(key)
        need = limit if min_required is None else min_required

        if not series:
            self.stats.misses += 1
            return KlineResult([], "rest", "no cached candles")

        age = self.age(symbol, interval)
        cap = self.max_age(interval)
        if age is not None and age > cap:
            self.stats.stale_rejections += 1
            return KlineResult(
                [], "rest",
                f"stale: newest candle {age:.0f}s old (max {cap:.0f}s)", age)

        if len(series) < need:
            self.stats.short_series += 1
            return KlineResult(
                [], "rest",
                f"short series: {len(series)} candles, need {need}", age)

        src = self._source.get(key, "ws")
        if src != "ws":
            # Seeded from REST and no WS frame has landed yet: the caller must
            # fetch, because we cannot claim this series is live.
            self.stats.misses += 1
            return KlineResult([], "rest", "cache holds REST-seeded candles only", age)

        self.stats.hits += 1
        return KlineResult(list(series[-limit:]), "ws", "", age)

    def note_rest_fallback(self) -> None:
        self.stats.rest_fallbacks += 1

    def clear(self) -> None:
        self._series.clear()
        self._source.clear()
