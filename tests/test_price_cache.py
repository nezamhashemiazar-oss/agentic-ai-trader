"""Price cache + MARKET_DATA_SOURCE=ws wiring.

Every test here runs without network: the REST client is a stub and the WS
server is a local fake. The point is to prove the *decision logic* - when the
cache is used, when it is refused, and that `rest` mode never opens a socket.
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from jrock.config import Settings
from jrock.trader.engine import TraderEngine
from jrock.trader.price_cache import PriceCache


# ------------------------------------------------------------------ helpers

class FakeClient:
    """Stands in for BitunixClient: counts every REST price call."""

    def __init__(self, price: float = 100.0):
        self.price = price
        self.ticker_calls = 0
        self.started = False

    async def start(self):
        self.started = True

    async def close(self):
        self.started = False

    async def get_tickers(self, symbols=None):
        self.ticker_calls += 1
        return [{"symbol": symbols, "lastPrice": str(self.price)}]

    async def get_trading_pairs(self, symbols=None):
        return []

    async def get_position_tiers(self, symbol):
        return []


def make_engine(tmp_path, source="ws", max_age=5.0, price=100.0):
    s = Settings(symbols=["BTCUSDT"], market_data_source=source,
                 price_max_age_sec=max_age)
    eng = TraderEngine(s, data_dir=tmp_path)
    eng.client = FakeClient(price)
    return eng


# ------------------------------------------------------------------ cache unit

def test_cache_hit_returns_ws_price():
    c = PriceCache(max_age=5.0)
    c.put_ws("BTCUSDT", 101.5, exchange_ts=time.time())
    q = c.get_fresh("BTCUSDT")
    assert q is not None
    assert q.price == 101.5
    assert q.source == "ws"
    assert c.stats.hits == 1


def test_cache_miss_returns_none():
    c = PriceCache()
    assert c.get_fresh("BTCUSDT") is None
    assert c.stats.hits == 0


def test_stale_quote_is_not_fresh():
    c = PriceCache(max_age=0.05)
    c.put_ws("BTCUSDT", 100.0)
    time.sleep(0.12)
    assert c.get_fresh("BTCUSDT") is None
    # the quote is still there for display
    assert c.peek("BTCUSDT").price == 100.0


def test_put_ws_ignores_bad_prices():
    c = PriceCache()
    c.put_ws("BTCUSDT", 0)
    c.put_ws("BTCUSDT", -5)
    c.put_ws("", 100)
    assert c.symbols() == []


# ------------------------------------------------------- engine accessor

@pytest.mark.asyncio
async def test_ws_mode_uses_cache_and_skips_rest(tmp_path):
    eng = make_engine(tmp_path, source="ws")
    eng.price_cache.put_ws("BTCUSDT", 123.45)
    px = await eng.get_price("BTCUSDT")
    assert px == 123.45
    assert eng.client.ticker_calls == 0          # REST bypassed
    assert eng.price_cache.stats.hits == 1


@pytest.mark.asyncio
async def test_ws_mode_cache_miss_falls_back_to_rest(tmp_path):
    eng = make_engine(tmp_path, source="ws", price=99.0)
    px = await eng.get_price("BTCUSDT")
    assert px == 99.0
    assert eng.client.ticker_calls == 1
    assert eng.price_cache.stats.misses == 1
    assert eng.price_cache.stats.rest_fallbacks == 1
    # the REST answer is cached, so the next read is a hit
    assert eng.price_cache.peek("BTCUSDT").source == "rest"


@pytest.mark.asyncio
async def test_ws_mode_stale_price_rejected_and_falls_back(tmp_path):
    eng = make_engine(tmp_path, source="ws", max_age=0.05, price=88.0)
    eng.price_cache.put_ws("BTCUSDT", 777.0)
    time.sleep(0.12)
    px = await eng.get_price("BTCUSDT")
    assert px == 88.0                              # NOT the stale 777
    assert eng.price_cache.stats.stale_rejections == 1
    assert eng.price_cache.stats.rest_fallbacks == 1
    assert eng.client.ticker_calls == 1


@pytest.mark.asyncio
async def test_rest_mode_never_touches_cache(tmp_path):
    eng = make_engine(tmp_path, source="rest", price=55.0)
    eng.price_cache.put_ws("BTCUSDT", 999.0)       # would win if consulted
    px = await eng.get_price("BTCUSDT")
    assert px == 55.0
    assert eng.client.ticker_calls == 1
    assert eng.price_cache.stats.hits == 0


@pytest.mark.asyncio
async def test_rest_mode_opens_no_socket(tmp_path):
    eng = make_engine(tmp_path, source="rest")
    await eng.start_feed()
    assert eng.feed is None
    assert eng.feed_connected() is False


@pytest.mark.asyncio
async def test_ws_disconnect_keeps_get_price_working(tmp_path):
    """A dead feed must not stall the engine: REST still answers."""
    eng = make_engine(tmp_path, source="ws", price=42.0)
    eng.feed = None                                # simulate never-connected
    px = await eng.get_price("BTCUSDT")
    assert px == 42.0
    assert eng.client.ticker_calls == 1


@pytest.mark.asyncio
async def test_rest_call_counter_drops_when_cache_warm(tmp_path):
    eng = make_engine(tmp_path, source="ws", price=10.0)
    for _ in range(5):
        await eng.get_price("BTCUSDT")             # 1 REST call, then 4 hits
    assert eng.client.ticker_calls == 1
    assert eng.price_cache.stats.hits == 4


# ------------------------------------------------------------- reporting

def test_market_data_report_rest(tmp_path):
    eng = make_engine(tmp_path, source="rest")
    assert eng.market_data_report() == "rest (REST polling)"


def test_market_data_report_ws_not_connected(tmp_path):
    eng = make_engine(tmp_path, source="ws")
    assert "NOT connected" in eng.market_data_report()
    assert "falling back to REST" in eng.market_data_report()


def test_market_data_report_ws_connected(tmp_path):
    eng = make_engine(tmp_path, source="ws")

    class FakeWS:
        connected = True

    class FakeFeed:
        ws = FakeWS()
        symbols = ["BTCUSDT", "ETHUSDT"]

    eng.feed = FakeFeed()
    assert eng.market_data_report() == "ws (connected, 2 symbols)"


# ------------------------------------------------------- feed -> cache wiring

def test_feed_price_frame_updates_cache():
    from jrock.trader.ws import Event, MarketDataFeed, WSConfig

    feed = MarketDataFeed(WSConfig(), ["BTCUSDT"], ["1m"])
    ev = Event(channel="price", symbol="BTCUSDT", ts=1794000000000,
               data={"mp": "75762.1", "ip": "75798.3", "fr": "0.0089"})
    feed._on_price(ev)
    q = feed.cache.get_fresh("BTCUSDT")
    assert q is not None
    assert q.price == 75762.1
    assert q.source == "ws"
    assert q.exchange_ts == pytest.approx(1794000000.0)


def test_feed_ticker_frame_updates_cache():
    from jrock.trader.ws import Event, MarketDataFeed, WSConfig

    feed = MarketDataFeed(WSConfig(), ["BTCUSDT"], ["1m"])
    ev = Event(channel="ticker", symbol="BTCUSDT", ts=1794000000000,
               data={"s": "BTCUSDT", "la": "75900.5"})
    feed._on_ticker(ev)
    assert feed.cache.get_fresh("BTCUSDT").price == 75900.5


def test_feed_ignores_malformed_price():
    from jrock.trader.ws import Event, MarketDataFeed, WSConfig

    feed = MarketDataFeed(WSConfig(), ["BTCUSDT"], ["1m"])
    feed._on_price(Event(channel="price", symbol="BTCUSDT", ts=0,
                         data={"mp": "not-a-number"}))
    assert feed.cache.symbols() == []


# --------------------------------------------------- real fake WS server

@pytest.mark.asyncio
async def test_feed_receives_from_local_ws_server(tmp_path):
    """End-to-end through a real socket, no network."""
    import websockets

    from jrock.trader.ws import MarketDataFeed, WSConfig

    async def handler(ws):
        await ws.send(json.dumps({"op": "connect", "data": {"result": True}}))
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("op") == "subscribe":
                await ws.send(json.dumps({
                    "ch": "price", "symbol": "BTCUSDT", "ts": 1794000000000,
                    "data": {"mp": "65000.5", "ip": "65001.0"},
                }))

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    cfg = WSConfig(public_url=f"ws://127.0.0.1:{port}/public/",
                   ping_interval=60, pong_timeout=30)
    feed = MarketDataFeed(cfg, ["BTCUSDT"], ["1m"])
    await feed.start()
    try:
        for _ in range(100):
            if feed.cache.get_fresh("BTCUSDT"):
                break
            await asyncio.sleep(0.02)
        q = feed.cache.get_fresh("BTCUSDT")
        assert q is not None, "no price frame reached the cache"
        assert q.price == 65000.5
        assert q.source == "ws"
    finally:
        await feed.stop()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_engine_ws_mode_end_to_end_through_fake_server(tmp_path):
    """Engine + real socket: get_price returns a ws-sourced quote, REST untouched."""
    import websockets

    from jrock.trader.ws import WSConfig

    async def handler(ws):
        await ws.send(json.dumps({"op": "connect", "data": {"result": True}}))
        async for raw in ws:
            if json.loads(raw).get("op") == "subscribe":
                await ws.send(json.dumps({
                    "ch": "price", "symbol": "BTCUSDT", "ts": 1794000000000,
                    "data": {"mp": "61000.25"},
                }))

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    eng = make_engine(tmp_path, source="ws", price=1.0)
    eng.s.bitunix_ws_public = f"ws://127.0.0.1:{port}/public/"
    eng.s.bitunix_ws_private = f"ws://127.0.0.1:{port}/private/"
    await eng.start_feed()
    try:
        for _ in range(100):
            if eng.price_cache.get_fresh("BTCUSDT"):
                break
            await asyncio.sleep(0.02)
        px = await eng.get_price("BTCUSDT")
        assert px == 61000.25
        assert eng.client.ticker_calls == 0
        assert eng.price_cache.peek("BTCUSDT").source == "ws"
    finally:
        await eng.stop_feed()
        server.close()
        await server.wait_closed()
