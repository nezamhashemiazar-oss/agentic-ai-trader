"""Kline cache + WS-fed candle path.

Every test runs without network: the REST client is a stub and the WS server is
a local fake. The point is to prove the *decision logic* - when the cache is
used, when it is refused, and that `rest` mode never opens a socket.

The single most important test here is `test_open_time_derived_from_ts`: the
live kline push carries NO time field, so the candle's open time must be
derived by flooring `ts`. Get that wrong and every candle collapses onto one
key and the series never grows - silently.
"""
from __future__ import annotations

import asyncio
import json
import socket
import time

import pytest

from jrock.config import Settings
from jrock.trader.engine import TraderEngine
from jrock.trader.indicators import Candle
from jrock.trader.kline_cache import (KlineCache, candle_open_time,
                                      interval_seconds)

SYM = "BTCUSDT"


# ------------------------------------------------------------------ helpers

def mk_candle(t: int, close: float = 100.0) -> Candle:
    return Candle(time=t, open=close - 1, high=close + 1, low=close - 2,
                  close=close, base_vol=1.0, quote_vol=100.0)


#: A recent 1m boundary. The staleness guard is real, so a fixture series must
#: be recent or every "fresh cache" test would be testing the stale path.
NOW_1M = int(time.time() // 60 * 60) * 1000


def mk_series(n: int, start: int | None = None, step: int = 60_000):
    """`n` candles ending at the current 1m boundary, oldest first."""
    if start is None:
        start = NOW_1M - (n - 1) * step
    return [mk_candle(start + i * step, 100.0 + i) for i in range(n)]


class FakeClient:
    """Stands in for BitunixClient: counts every REST kline call."""

    def __init__(self, n_candles: int = 200):
        self.n_candles = n_candles
        self.kline_calls = 0
        self.ticker_calls = 0
        self.started = False

    async def start(self):
        self.started = True

    async def close(self):
        self.started = False

    async def get_kline(self, symbol, interval, limit=200, **kw):
        self.kline_calls += 1
        n = min(limit, self.n_candles)
        # newest-first, exactly like the real endpoint
        raw = [{"time": str(c.time), "open": str(c.open), "high": str(c.high),
                "low": str(c.low), "close": str(c.close),
                "baseVol": str(c.base_vol), "quoteVol": str(c.quote_vol)}
               for c in reversed(mk_series(n))]
        return raw

    async def get_tickers(self, symbols=None):
        self.ticker_calls += 1
        return [{"symbol": symbols, "lastPrice": "100.0"}]

    async def get_trading_pairs(self, symbols=None):
        return []

    async def get_position_tiers(self, symbol):
        return []


def make_engine(tmp_path, source="ws", max_age_mult=2.0, n_candles=200):
    s = Settings(symbols=[SYM], timeframes=["1m", "5m"],
                 market_data_source=source, kline_max_age_mult=max_age_mult)
    eng = TraderEngine(s, data_dir=tmp_path)
    eng.client = FakeClient(n_candles)
    return eng


# ------------------------------------------------------- open-time derivation

def test_open_time_derived_from_ts():
    """The live push has no `time`; the open time comes from flooring `ts`."""
    ts = 1794011592257                      # a real captured frame ts
    assert candle_open_time(ts, "1m") == 1794011580000
    assert candle_open_time(ts, "5m") == 1794011400000
    assert candle_open_time(ts, "1h") == 1794009600000
    # flooring is idempotent on a boundary
    assert candle_open_time(1794011580000, "1m") == 1794011580000


def test_interval_seconds_known_and_unknown():
    assert interval_seconds("1m") == 60
    assert interval_seconds("1h") == 3600
    assert interval_seconds("1d") == 86400
    with pytest.raises(ValueError):
        interval_seconds("7m")


# ------------------------------------------------------------- cache unit

def test_cache_hit_returns_ws_candles():
    c = KlineCache()
    for cd in mk_series(10):
        c.put_ws(SYM, "1m", cd)
    res = c.try_get(SYM, "1m", 5, min_required=5)
    assert res.usable
    assert res.source == "ws"
    assert len(res.candles) == 5
    assert c.stats.hits == 1


def test_still_forming_candle_replaced_not_appended():
    """The channel re-pushes the same open time every 500ms."""
    c = KlineCache()
    t = 1_700_000_000_000
    c.put_ws(SYM, "1m", mk_candle(t, 100.0))
    c.put_ws(SYM, "1m", mk_candle(t, 101.0))
    c.put_ws(SYM, "1m", mk_candle(t, 102.0))
    assert c.count(SYM, "1m") == 1
    assert c.stats.replaced == 2
    assert c.stats.appended == 1
    assert c._series[(SYM, "1m")][-1].close == 102.0


def test_new_candle_appends():
    c = KlineCache()
    t = 1_700_000_000_000
    c.put_ws(SYM, "1m", mk_candle(t, 100.0))
    c.put_ws(SYM, "1m", mk_candle(t + 60_000, 101.0))
    assert c.count(SYM, "1m") == 2
    assert c.stats.appended == 2
    assert c._series[(SYM, "1m")][-1].time == t + 60_000


def test_out_of_order_old_candle_replaces_in_place():
    c = KlineCache()
    t = 1_700_000_000_000
    for i in range(3):
        c.put_ws(SYM, "1m", mk_candle(t + i * 60_000, 100.0 + i))
    c.put_ws(SYM, "1m", mk_candle(t, 999.0))          # a late frame for candle 0
    assert c.count(SYM, "1m") == 3
    assert c._series[(SYM, "1m")][0].close == 999.0


def test_bounded_list_does_not_grow_past_cap():
    c = KlineCache(max_candles=50)
    for i in range(200):
        c.put_ws(SYM, "1m", mk_candle(1_700_000_000_000 + i * 60_000, 100.0))
    assert c.count(SYM, "1m") == 50
    # the newest survive, the oldest are dropped
    assert c._series[(SYM, "1m")][-1].time == 1_700_000_000_000 + 199 * 60_000


def test_put_rest_does_not_overwrite_ws_candles():
    c = KlineCache()
    t = 1_700_000_000_000
    c.put_ws(SYM, "1m", mk_candle(t, 500.0))
    c.put_rest(SYM, "1m", [mk_candle(t, 1.0), mk_candle(t + 60_000, 2.0)])
    assert c._series[(SYM, "1m")][0].close == 500.0     # WS value kept
    assert c.count(SYM, "1m") == 2


def test_stale_candle_set_rejected():
    c = KlineCache(max_age_mult=0.001)                  # 1m * 0.001 = 0.06s
    for cd in mk_series(10, start=int((time.time() - 600) * 1000)):
        c.put_ws(SYM, "1m", cd)
    res = c.try_get(SYM, "1m", 5, min_required=5)
    assert not res.usable
    assert "stale" in res.reason
    assert c.stats.stale_rejections == 1


def test_short_series_rejected():
    c = KlineCache()
    for cd in mk_series(10):
        c.put_ws(SYM, "1m", cd)
    res = c.try_get(SYM, "1m", 200, min_required=200)
    assert not res.usable
    assert "short series" in res.reason
    assert c.stats.short_series == 1


def test_rest_seeded_only_is_not_served_as_ws():
    c = KlineCache()
    c.put_rest(SYM, "1m", mk_series(200))
    res = c.try_get(SYM, "1m", 200, min_required=200)
    assert not res.usable
    assert "REST-seeded" in res.reason


def test_max_age_scales_with_interval():
    c = KlineCache(max_age_mult=2.0)
    assert c.max_age("1m") == 120.0
    assert c.max_age("1h") == 7200.0
    assert c.max_age("1d") == 172800.0


# ------------------------------------------------------- engine accessor

@pytest.mark.asyncio
async def test_ws_mode_uses_cache_and_skips_rest(tmp_path):
    eng = make_engine(tmp_path, source="ws")
    for cd in mk_series(200):
        eng.kline_cache.put_ws(SYM, "1m", cd)
    before = eng.client.kline_calls
    candles = await eng.get_candles(SYM, "1m", 200)
    assert len(candles) == 200
    assert eng.client.kline_calls == before          # REST bypassed
    assert eng.kline_cache.stats.hits == 1


@pytest.mark.asyncio
async def test_ws_mode_cache_miss_falls_back_to_rest(tmp_path):
    eng = make_engine(tmp_path, source="ws")
    candles = await eng.get_candles(SYM, "1m", 200)
    assert len(candles) == 200
    assert eng.client.kline_calls == 1
    assert eng.kline_cache.stats.misses == 1
    assert eng.kline_cache.stats.rest_fallbacks == 1


@pytest.mark.asyncio
async def test_ws_mode_stale_candles_rejected_and_falls_back(tmp_path):
    eng = make_engine(tmp_path, source="ws", max_age_mult=0.001)
    # a series whose newest candle is ~200 minutes old
    for cd in mk_series(200, start=NOW_1M - 200 * 60_000):
        eng.kline_cache.put_ws(SYM, "1m", cd)
    before = eng.client.kline_calls
    candles = await eng.get_candles(SYM, "1m", 200)
    assert len(candles) == 200                       # from REST, not the frozen cache
    assert eng.client.kline_calls == before + 1
    assert eng.kline_cache.stats.stale_rejections == 1
    assert eng.kline_cache.stats.rest_fallbacks == 1


@pytest.mark.asyncio
async def test_short_series_falls_back_to_rest(tmp_path):
    eng = make_engine(tmp_path, source="ws")
    for cd in mk_series(10):
        eng.kline_cache.put_ws(SYM, "1m", cd)
    before = eng.client.kline_calls
    candles = await eng.get_candles(SYM, "1m", 200)
    assert len(candles) == 200
    assert eng.client.kline_calls == before + 1
    assert eng.kline_cache.stats.short_series == 1


@pytest.mark.asyncio
async def test_rest_mode_never_touches_kline_cache(tmp_path):
    eng = make_engine(tmp_path, source="rest")
    for cd in mk_series(200):
        eng.kline_cache.put_ws(SYM, "1m", cd)        # would win if consulted
    candles = await eng.get_candles(SYM, "1m", 200)
    assert len(candles) == 200
    assert eng.client.kline_calls == 1
    assert eng.kline_cache.stats.hits == 0
    # rest mode never writes to the cache either: the pre-seeded key is untouched
    assert eng.kline_cache.count(SYM, "1m") == 200
    assert eng.kline_cache.stats.rest_updates == 0


@pytest.mark.asyncio
async def test_ws_disconnect_keeps_get_candles_working(tmp_path):
    """A dead feed must not stall the engine: REST still answers."""
    eng = make_engine(tmp_path, source="ws")
    eng.feed = None
    eng.kline_cache.clear()
    candles = await eng.get_candles(SYM, "1m", 200)
    assert len(candles) == 200
    assert eng.client.kline_calls == 1


@pytest.mark.asyncio
async def test_rest_kline_counter_drops_when_cache_warm(tmp_path):
    eng = make_engine(tmp_path, source="ws")
    for cd in mk_series(200):
        eng.kline_cache.put_ws(SYM, "1m", cd)
    for _ in range(5):
        await eng.get_candles(SYM, "1m", 200)
    assert eng.client.kline_calls == 0
    assert eng.kline_cache.stats.hits == 5


@pytest.mark.asyncio
async def test_warmup_seeds_from_rest_exactly_once(tmp_path):
    eng = make_engine(tmp_path, source="ws")
    calls = await eng.warmup_klines()
    # one call per (symbol, interval) key
    assert calls == len(eng.kline_keys())
    assert eng.kline_warmup_calls == calls
    assert eng.client.kline_calls == calls
    assert eng.kline_cache.source(SYM, "1m") == "rest"
    # a second warmup is a separate explicit call, not automatic
    assert eng.client.kline_calls == calls


@pytest.mark.asyncio
async def test_warmup_is_a_noop_in_rest_mode(tmp_path):
    eng = make_engine(tmp_path, source="rest")
    assert await eng.warmup_klines() == 0
    assert eng.client.kline_calls == 0


@pytest.mark.asyncio
async def test_rest_mode_opens_no_socket(tmp_path):
    attempts: list = []
    real = socket.socket.connect

    def spy(self, addr):
        attempts.append(addr)
        return real(self, addr)

    socket.socket.connect = spy
    try:
        eng = make_engine(tmp_path, source="rest")
        await eng.start_feed()
        assert eng.feed is None
        assert eng.feed_connected() is False
        assert eng.kline_warmup_calls == 0
    finally:
        socket.socket.connect = real
    assert attempts == []


# ------------------------------------------------------------- reporting

def test_kline_timeframes_always_includes_5m(tmp_path):
    """The trailing-stop ATR reads 5m; it must be subscribed or it polls REST."""
    eng = make_engine(tmp_path)
    eng.state.timeframes = ["1m", "15m"]
    assert "5m" in eng.kline_timeframes()


def test_kline_keys_cover_every_symbol_and_interval(tmp_path):
    eng = make_engine(tmp_path)
    eng.state.symbols = ["BTCUSDT", "ETHUSDT"]
    eng.state.timeframes = ["1m", "5m"]
    keys = eng.kline_keys()
    assert ("BTCUSDT", "1m") in keys
    assert ("ETHUSDT", "5m") in keys
    assert len(keys) == 4


def test_kline_cache_report_mentions_counters(tmp_path):
    eng = make_engine(tmp_path)
    for cd in mk_series(5):
        eng.kline_cache.put_ws(SYM, "1m", cd)
    rep = eng.kline_cache_report()
    assert "kline_cache=" in rep
    assert "replaced=" in rep
    assert "appended=" in rep
    assert f"{SYM} 1m: 5 candles" in rep


def test_subscription_warning_when_interval_missing(tmp_path):
    eng = make_engine(tmp_path)

    class FakeWS:
        connected = True

    class FakeFeed:
        ws = FakeWS()
        symbols = [SYM]
        timeframes = ["1m"]                          # 5m missing

    eng.feed = FakeFeed()
    warns = eng.kline_subscription_warnings()
    assert any("5m" in w for w in warns)


# --------------------------------------------------- real fake WS server

@pytest.mark.asyncio
async def test_feed_kline_frame_populates_cache(tmp_path):
    """A frame with the REAL shape (no time field) must land in the cache."""
    from jrock.trader.ws import Event, MarketDataFeed, WSConfig

    kc = KlineCache()
    feed = MarketDataFeed(WSConfig(), [SYM], ["1m"], kline_cache=kc)
    ev = Event(channel="market_kline_1min", symbol=SYM, ts=1794011592257,
               data={"o": "75898.5", "h": "75899.0", "l": "75898.0",
                     "c": "75898.6", "b": "1.5", "q": "113847.9"})
    feed._on_kline(ev)
    assert kc.count(SYM, "1m") == 1
    cd = kc._series[(SYM, "1m")][0]
    assert cd.time == 1794011580000                  # derived from ts
    assert cd.close == 75898.6
    assert cd.base_vol == 1.5
    assert cd.quote_vol == 113847.9


@pytest.mark.asyncio
async def test_feed_kline_repeated_frames_replace(tmp_path):
    from jrock.trader.ws import Event, MarketDataFeed, WSConfig

    kc = KlineCache()
    feed = MarketDataFeed(WSConfig(), [SYM], ["1m"], kline_cache=kc)
    for close in ("100.0", "101.0", "102.0"):
        feed._on_kline(Event(channel="market_kline_1min", symbol=SYM,
                             ts=1794011592257,
                             data={"o": "99", "h": "103", "l": "98",
                                   "c": close, "b": "1", "q": "1"}))
    assert kc.count(SYM, "1m") == 1
    assert kc._series[(SYM, "1m")][0].close == 102.0


@pytest.mark.asyncio
async def test_feed_ignores_malformed_kline():
    from jrock.trader.ws import Event, MarketDataFeed, WSConfig

    kc = KlineCache()
    feed = MarketDataFeed(WSConfig(), [SYM], ["1m"], kline_cache=kc)
    feed._on_kline(Event(channel="market_kline_1min", symbol=SYM, ts=1794011592257,
                         data={"o": "not-a-number", "c": "x"}))
    assert kc.keys() == []


@pytest.mark.asyncio
async def test_engine_ws_klines_end_to_end_through_fake_server(tmp_path):
    """Engine + real socket: get_candles returns ws-sourced candles, REST untouched."""
    import websockets

    from jrock.trader.ws import WSConfig

    async def handler(ws):
        await ws.send(json.dumps({"op": "connect", "data": {"result": True}}))
        async for raw in ws:
            msg = json.loads(raw)
            if msg.get("op") != "subscribe":
                continue
            for arg in msg.get("args", []):
                ch = arg.get("ch", "")
                if not ch.startswith("market_kline_"):
                    continue
                # push 200 distinct candles, then keep re-pushing the last one
                base = NOW_1M - 199 * 60_000
                for i in range(200):
                    await ws.send(json.dumps({
                        "ch": ch, "symbol": arg["symbol"],
                        "ts": base + i * 60_000 + 30_000,
                        "data": {"o": "100", "h": "101", "l": "99",
                                 "c": str(100 + i), "b": "1", "q": "100"},
                    }))
                for _ in range(3):
                    await ws.send(json.dumps({
                        "ch": ch, "symbol": arg["symbol"],
                        "ts": base + 199 * 60_000 + 30_000,
                        "data": {"o": "100", "h": "101", "l": "99",
                                 "c": "999", "b": "1", "q": "100"},
                    }))

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    eng = make_engine(tmp_path, source="ws")
    eng.s.bitunix_ws_public = f"ws://127.0.0.1:{port}/public/"
    eng.s.bitunix_ws_private = f"ws://127.0.0.1:{port}/private/"
    await eng.start_feed()
    try:
        for _ in range(200):
            if eng.kline_cache.count(SYM, "1m") >= 200:
                break
            await asyncio.sleep(0.02)
        assert eng.kline_cache.count(SYM, "1m") == 200, "candles did not arrive"
        assert eng.kline_cache.source(SYM, "1m") == "ws"
        before = eng.client.kline_calls
        candles = await eng.get_candles(SYM, "1m", 200)
        assert len(candles) == 200
        assert eng.client.kline_calls == before, "REST was used despite a warm cache"
        assert candles[-1].close == 999.0            # the replaced forming candle
    finally:
        await eng.stop_feed()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_clean_shutdown_leaves_no_tasks(tmp_path):
    import websockets

    from jrock.trader.ws import WSConfig

    async def handler(ws):
        await ws.send(json.dumps({"op": "connect", "data": {"result": True}}))
        async for _ in ws:
            pass

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    eng = make_engine(tmp_path, source="ws")
    eng.s.bitunix_ws_public = f"ws://127.0.0.1:{port}/public/"
    await eng.start_feed()
    await eng.stop_feed()
    assert eng.feed is None
    await asyncio.sleep(0.05)
    pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    assert pending == []
    server.close()
    await server.wait_closed()
