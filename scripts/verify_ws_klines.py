"""Live verification of the WS kline path. Real socket, real frames.

Run:  python3 scripts/verify_ws_klines.py

Proves, against wss://fapi.bitunix.com/public/:
  1. the kline subscriptions and real market_kline_* frames
  2. get_candles() returns a series whose source is ws
  3. the still-forming candle is REPLACED, not appended
  4. a new candle APPENDS (count grows by exactly one)
  5. REST kline calls drop to zero for a warm key
  6. the fallback: feed killed -> REST answers, counter increments
  7. the staleness guard: frozen cache -> rejected, REST fallback
  8. the short-series guard: too few candles -> REST fallback
  9. MARKET_DATA_SOURCE=rest opens zero sockets
"""
from __future__ import annotations

import asyncio
import json
import logging
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jrock.config import Settings
from jrock.trader.engine import TraderEngine
from jrock.trader.indicators import Candle
from jrock.trader.kline_cache import candle_open_time

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

SYM = "BTCUSDT"


def mk_series(n: int, start: int = 1_700_000_000_000, step: int = 60_000):
    return [Candle(time=start + i * step, open=100.0 + i, high=101.0 + i,
                   low=99.0 + i, close=100.0 + i, base_vol=1.0, quote_vol=100.0)
            for i in range(n)]


class CountingClient:
    """Wraps the real REST client and counts kline calls."""

    def __init__(self, real):
        self._real = real
        self.kline_calls = 0
        self.ticker_calls = 0

    def __getattr__(self, name):
        return getattr(self._real, name)

    async def get_kline(self, symbol, interval, limit=200, **kw):
        self.kline_calls += 1
        return await self._real.get_kline(symbol, interval, limit=limit, **kw)

    async def get_tickers(self, symbols=None):
        self.ticker_calls += 1
        return await self._real.get_tickers(symbols)


def make_engine(source="ws", max_age_mult=2.0):
    s = Settings(symbols=[SYM], timeframes=["1m", "5m"],
                 market_data_source=source, kline_max_age_mult=max_age_mult)
    eng = TraderEngine(s)
    eng.client = CountingClient(eng.client)
    return eng


async def main() -> int:
    print("=" * 72)
    print("1) STARTUP: banner + kline subscriptions")
    print("=" * 72)
    eng = make_engine("ws")
    await eng.client.start()
    await eng.start_feed()
    print(f"market data: {eng.market_data_report()}")
    print(f"kline timeframes subscribed: {eng.feed.timeframes}")
    print(f"kline keys needed: {eng.kline_keys()}")
    print(f"warmup REST calls: {eng.kline_warmup_calls}")
    for w in eng.kline_subscription_warnings():
        print(f"  WARNING: {w}")

    print()
    print("=" * 72)
    print("2) REAL FRAMES + get_candles() source")
    print("=" * 72)
    raw_frames: list[dict] = []
    eng.feed.ws.on("market_kline", lambda ev: raw_frames.append(ev.raw)
                   if len(raw_frames) < 3 else None)
    for _ in range(150):
        if len(raw_frames) >= 3:
            break
        await asyncio.sleep(0.2)
    for f in raw_frames:
        print("RAW:", json.dumps(f))
    print(f"frames captured: {len(raw_frames)}")

    candles = await eng.get_candles(SYM, "1m", 200)
    src = eng.kline_cache.source(SYM, "1m")
    print(f"get_candles('{SYM}','1m',200) -> {len(candles)} candles, cache src={src}")
    print(f"  newest candle: time={candles[-1].time} close={candles[-1].close}")
    print(f"  REST kline calls so far: {eng.client.kline_calls} "
          f"(warmup={eng.kline_warmup_calls})")

    print()
    print("=" * 72)
    print("3) REPLACE vs APPEND (the still-forming candle)")
    print("=" * 72)
    key = (SYM, "1m")
    series = eng.kline_cache._series[key]
    n0 = len(series)
    last0 = series[-1]
    print(f"before: count={n0} last_open={last0.time} last_close={last0.close}")
    # record every close seen for the SAME open time
    seen: list[float] = [last0.close]
    for _ in range(75):
        await asyncio.sleep(0.2)
        s = eng.kline_cache._series[key]
        if s[-1].time == last0.time and s[-1].close != seen[-1]:
            seen.append(s[-1].close)
        if len(seen) >= 4:
            break
    series = eng.kline_cache._series[key]
    last1 = series[-1]
    print(f"after : count={len(series)} last_open={last1.time} last_close={last1.close}")
    print(f"closes seen for open={last0.time}: {seen}")
    print(f"same open time (replaced, not appended): {last0.time == last1.time}")
    print(f"count stable: {len(series) == n0}   "
          f"distinct closes for one open time: {len(set(seen))}")
    print(f"stats: replaced={eng.kline_cache.stats.replaced} "
          f"appended={eng.kline_cache.stats.appended}")

    print()
    print("=" * 72)
    print("4) NEW CANDLE APPENDS (wait for the 1m boundary)")
    print("=" * 72)
    n_before = len(eng.kline_cache._series[key])
    open_before = eng.kline_cache._series[key][-1].time
    print(f"before: count={n_before} newest_open={open_before}")
    print("waiting up to 90s for the next 1m candle...")
    grew = False
    for _ in range(450):
        await asyncio.sleep(0.2)
        s = eng.kline_cache._series[key]
        if s[-1].time > open_before:
            grew = True
            break
    s = eng.kline_cache._series[key]
    print(f"after : count={len(s)} newest_open={s[-1].time}")
    print(f"open time advanced: {s[-1].time > open_before}")
    print(f"count grew by exactly one: {len(s) - n_before == 1} "
          f"(delta={len(s) - n_before})")
    print(f"stats: appended={eng.kline_cache.stats.appended}")

    print()
    print("=" * 72)
    print("5) REST BYPASS: a scan loop on a warm cache")
    print("=" * 72)
    before = eng.client.kline_calls
    for _ in range(10):
        await eng.get_candles(SYM, "1m", 200)
        await eng.get_candles(SYM, "5m", 200)
    after = eng.client.kline_calls
    print(f"20 get_candles calls -> REST kline calls: {after - before}")
    print(f"kline cache stats: {eng.kline_cache.stats.as_dict()}")

    print()
    print("=" * 72)
    print("6) FALLBACK: feed killed + cache cleared")
    print("=" * 72)
    await eng.stop_feed()
    eng.kline_cache.clear()
    print(f"feed after stop: {eng.feed}  connected={eng.feed_connected()}")
    print(f"cache keys after clear: {eng.kline_cache.keys()}")
    before = eng.client.kline_calls
    t0 = time.time()
    candles = await eng.get_candles(SYM, "1m", 200)
    dt = (time.time() - t0) * 1000
    print(f"get_candles -> {len(candles)} candles in {dt:.0f}ms")
    print(f"  REST kline calls: {eng.client.kline_calls - before}")
    print(f"  stats: {eng.kline_cache.stats.as_dict()}")

    print()
    print("=" * 72)
    print("7) STALENESS GUARD: frozen cache")
    print("=" * 72)
    eng2 = make_engine("ws", max_age_mult=0.001)   # 1m * 0.001 = 0.06s max age
    await eng2.client.start()
    await eng2.warmup_klines()
    # mark the series as ws-fed so only staleness can reject it
    eng2.kline_cache._source[(SYM, "1m")] = "ws"
    age = eng2.kline_cache.age(SYM, "1m")
    print(f"injected series: {eng2.kline_cache.count(SYM,'1m')} candles, "
          f"newest age={age:.0f}s, max={eng2.kline_cache.max_age('1m'):.2f}s")
    before = eng2.client.kline_calls
    candles = await eng2.get_candles(SYM, "1m", 200)
    print(f"get_candles -> {len(candles)} candles (must be from REST, not the frozen cache)")
    print(f"  REST kline calls: {eng2.client.kline_calls - before}")
    print(f"  stats: {eng2.kline_cache.stats.as_dict()}")
    await eng2.client.close()

    print()
    print("=" * 72)
    print("8) SHORT-SERIES GUARD: ask for more than the cache holds")
    print("=" * 72)
    eng3 = make_engine("ws")
    await eng3.client.start()
    await eng3.warmup_klines()
    # inject a deliberately SHORT but FRESH series: 50 candles, caller wants 200
    now_1m = int(time.time() // 60 * 60) * 1000
    eng3.kline_cache._series[(SYM, "1m")] = mk_series(50, start=now_1m - 49 * 60_000)
    eng3.kline_cache._source[(SYM, "1m")] = "ws"
    have = eng3.kline_cache.count(SYM, "1m")
    age = eng3.kline_cache.age(SYM, "1m")
    print(f"cache holds {have} candles for {SYM} 1m (fresh, age={age:.0f}s), "
          f"caller wants 200")
    before = eng3.client.kline_calls
    candles = await eng3.get_candles(SYM, "1m", 200)
    print(f"get_candles(limit=200) -> {len(candles)} candles")
    print(f"  REST kline calls: {eng3.client.kline_calls - before} (must be 1: fell back)")
    print(f"  stats: {eng3.kline_cache.stats.as_dict()}")
    await eng3.client.close()

    print()
    print("=" * 72)
    print("9) MARKET_DATA_SOURCE=rest OPENS ZERO SOCKETS")
    print("=" * 72)
    attempts: list = []
    import websockets
    real_ws_connect = websockets.connect

    def spy_ws(*a, **k):
        attempts.append(a[0] if a else k.get("uri"))
        return real_ws_connect(*a, **k)

    websockets.connect = spy_ws
    try:
        eng4 = make_engine("rest")
        await eng4.client.start()
        await eng4.start_feed()
        print(f"feed object: {eng4.feed}")
        print(f"feed_connected: {eng4.feed_connected()}")
        print(f"banner line: {eng4.market_data_report()}")
        print(f"kline warmup calls: {eng4.kline_warmup_calls}")
        candles = await eng4.get_candles(SYM, "1m", 200)
        print(f"get_candles -> {len(candles)} candles via REST")
        print(f"kline cache keys: {eng4.kline_cache.keys()} (must be empty)")
        await eng4.client.close()
    finally:
        websockets.connect = real_ws_connect
    print(f"WS connect attempts: {len(attempts)} {attempts[:5]}")

    print()
    print("=" * 72)
    print("10) CLEAN SHUTDOWN")
    print("=" * 72)
    await eng.stop()
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    print(f"pending asyncio tasks after stop: {len(pending)}")
    await eng.client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
