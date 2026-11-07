"""Live verification of MARKET_DATA_SOURCE=ws against the real Bitunix feed.

Run:  python scripts/verify_ws_prices.py
"""
from __future__ import annotations

import asyncio
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jrock.config import Settings
from jrock.trader.engine import TraderEngine

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


class CountingClient:
    """Wraps the real REST client and counts price calls."""

    def __init__(self, real):
        self.real = real
        self.ticker_calls = 0

    def __getattr__(self, name):
        return getattr(self.real, name)

    async def get_tickers(self, symbols=None):
        self.ticker_calls += 1
        return await self.real.get_tickers(symbols)


async def main() -> int:
    s = Settings(symbols=["BTCUSDT", "ETHUSDT"], market_data_source="ws",
                 price_max_age_sec=5.0)
    eng = TraderEngine(s, data_dir=Path("/tmp/jrock_ws_verify"))
    eng.client = CountingClient(eng.client)

    print("=" * 70)
    print("1) STARTUP BANNER (ws mode)")
    print("=" * 70)
    await eng.start()
    print(f"Market data: {eng.market_data_report()}")

    print()
    print("=" * 70)
    print("2) WAIT FOR REAL FRAMES")
    print("=" * 70)
    for _ in range(150):
        if eng.price_cache.get_fresh("BTCUSDT") and eng.price_cache.get_fresh("ETHUSDT"):
            break
        await asyncio.sleep(0.1)

    st = eng.feed.ws.stats() if eng.feed else {}
    print(f"connected={st.get('connected')} messages={st.get('messages')} "
          f"subs={st.get('subscriptions')} reconnects={st.get('reconnects')}")
    for sym in ("BTCUSDT", "ETHUSDT"):
        q = eng.price_cache.peek(sym)
        if q:
            print(f"  {sym}: {q.price} src={q.source} age={q.age():.2f}s "
                  f"exchange_ts={q.exchange_ts}")

    print()
    print("=" * 70)
    print("3) get_price() -> source + freshness")
    print("=" * 70)
    before = eng.client.ticker_calls
    px = await eng.get_price("BTCUSDT")
    q = eng.price_cache.peek("BTCUSDT")
    print(f"get_price('BTCUSDT') = {px}")
    print(f"  source={q.source} age={q.age():.2f}s")
    print(f"  REST price calls during this read: {eng.client.ticker_calls - before}")

    print()
    print("=" * 70)
    print("4) REST BYPASS: 20 reads, count REST calls")
    print("=" * 70)
    before = eng.client.ticker_calls
    for _ in range(20):
        await eng.get_price("BTCUSDT")
    print(f"20 get_price calls -> REST calls: {eng.client.ticker_calls - before}")
    print(f"cache stats: {eng.price_cache.stats.as_dict()}")

    print()
    print("=" * 70)
    print("5) FALLBACK: kill the feed + clear cache, get_price must still work")
    print("=" * 70)
    await eng.stop_feed()
    eng.price_cache.clear()          # nothing cached: REST is the only option
    print(f"feed after stop: {eng.feed}  connected={eng.feed_connected()}")
    print(f"cache symbols after clear: {eng.price_cache.symbols()}")
    before = eng.client.ticker_calls
    t0 = time.time()
    px = await eng.get_price("BTCUSDT")
    dt = time.time() - t0
    print(f"get_price('BTCUSDT') = {px} in {dt*1000:.0f}ms")
    print(f"  REST calls: {eng.client.ticker_calls - before}")
    print(f"  stats: {eng.price_cache.stats.as_dict()}")

    print()
    print("=" * 70)
    print("6) STALENESS GUARD: freeze the cache, price must be refused")
    print("=" * 70)
    eng.s.market_data_source = "ws"
    eng.price_cache.max_age = 0.3
    eng.price_cache.put_ws("BTCUSDT", 1.0)   # a deliberately wrong, frozen price
    print(f"injected stale price 1.0, max_age={eng.price_cache.max_age}s")
    await asyncio.sleep(0.5)
    before = eng.client.ticker_calls
    px = await eng.get_price("BTCUSDT")
    print(f"get_price('BTCUSDT') = {px}  (must NOT be 1.0)")
    print(f"  REST calls: {eng.client.ticker_calls - before}")
    print(f"  stats: {eng.price_cache.stats.as_dict()}")

    print()
    print("=" * 70)
    print("7) CLEAN SHUTDOWN")
    print("=" * 70)
    await eng.stop()
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    print(f"pending asyncio tasks after stop: {len(pending)}")
    print("done.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
