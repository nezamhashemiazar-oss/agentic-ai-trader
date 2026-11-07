"""Bitunix WebSocket client: public + private channels, heartbeat, reconnect.

Everything here is checked against the official docs and against live frames
captured from wss://fapi.bitunix.com/public/ on 2026-11-06.

CHANNEL NAMES (this is where the old code was wrong)
----------------------------------------------------
The docs and the live socket agree on these exact strings:

    price            market price / index price / funding rate
    ticker           one symbol, 24h rolling stats
    tickers          all symbols, aggregated
    trade            public trades
    depth_books      full snapshot then deltas
    depth_book1/5/15 N levels
    market_kline_<interval>   e.g. market_kline_1min, market_kline_60min

The previous version used market_price / market_ticker / market_tickers /
market_trade. Those are NOT valid channel names: the server accepts the
subscribe frame, replies {"op":"connect","data":{"result":true}}, and then
never pushes anything. Verified live - see the audit in the README.

KLINE INTERVALS
---------------
Valid: 1min 3min 5min 15min 30min 60min 2h 4h 6h 8h 12h 1day 3day 1week 1month
There is no "1hour" - subscribing to market_kline_1hour returns data={}.

HEARTBEAT
---------
Client sends {"op":"ping","ping":<unix seconds>}; the server answers
{"op":"ping","ts":...,"pong":<echoed>,"ping":<server seconds>}.
The server does NOT send its own ping (verified: 45s idle, zero frames), so
the client must drive the heartbeat. A missing pong means a dead socket.

LOGIN (private)
---------------
    sign = sha256( sha256(nonce + timestamp + apiKey) + secretKey )
timestamp is SECONDS. nonce is a 32-char random string. Same order as REST.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import string
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import websockets

from .indicators import Candle
from .kline_cache import KlineCache, candle_open_time

log = logging.getLogger("jrock.ws")

Handler = Callable[["Event"], Awaitable[None] | None]

# ---------------------------------------------------------------- channels

#: Public channel names, exactly as the exchange spells them.
PUBLIC_CHANNELS: dict[str, str] = {
    "price": "price",
    "ticker": "ticker",
    "tickers": "tickers",
    "trade": "trade",
    "depth": "depth_books",
    "depth_books": "depth_books",
    "depth_book1": "depth_book1",
    "depth_book5": "depth_book5",
    "depth_book15": "depth_book15",
}

#: Private channel names.
PRIVATE_CHANNELS: dict[str, str] = {
    "balance": "balance",
    "order": "order",
    "position": "position",
    "tpsl": "tpsl",
}

#: interval -> documented kline suffix. "1h" is 60min, NOT 1hour.
KLINE_INTERVALS: dict[str, str] = {
    "1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min", "30m": "30min",
    "1h": "60min", "2h": "2h", "4h": "4h", "6h": "6h", "8h": "8h", "12h": "12h",
    "1d": "1day", "3d": "3day", "1w": "1week", "1M": "1month",
    # already-documented spellings pass through
    "1min": "1min", "3min": "3min", "5min": "5min", "15min": "15min",
    "30min": "30min", "60min": "60min", "1day": "1day", "3day": "3day",
    "1week": "1week", "1month": "1month",
}


def kline_channel(interval: str) -> str:
    """'1m' -> 'market_kline_1min', '1h' -> 'market_kline_60min'.

    Raises ValueError for an interval the exchange does not document, instead
    of silently subscribing to a channel that will never push.
    """
    key = interval.strip()
    suffix = KLINE_INTERVALS.get(key)
    if suffix is None:
        raise ValueError(
            f"unsupported kline interval {interval!r}. "
            f"Valid: {', '.join(sorted(KLINE_INTERVALS))}"
        )
    return f"market_kline_{suffix}"


def _nonce(n: int = 32) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(random.choices(alphabet, k=n))


def ws_login_sign(api_key: str, secret: str, timestamp: int, nonce: str) -> str:
    """sign = sha256(sha256(nonce + timestamp + apiKey) + secretKey).

    Order is nonce, then timestamp - per the official docs and the Go demo
    (open_api_ws_sign.go -> generateSign).
    """
    digest = hashlib.sha256(f"{nonce}{timestamp}{api_key}".encode()).hexdigest()
    return hashlib.sha256((digest + secret).encode()).hexdigest()


# ---------------------------------------------------------------- frames

def build_ping(ts: int | None = None) -> dict:
    return {"op": "ping", "ping": int(ts if ts is not None else time.time())}


def build_subscribe(subs: list[dict]) -> dict:
    return {"op": "subscribe", "args": [dict(s) for s in subs]}


def build_unsubscribe(subs: list[dict]) -> dict:
    return {"op": "unsubscribe", "args": [dict(s) for s in subs]}


def build_login(api_key: str, secret: str, ts: int | None = None,
                nonce: str | None = None) -> dict:
    """The documented login frame. Never logs the secret or the signature."""
    timestamp = int(ts if ts is not None else time.time())
    n = nonce or _nonce()
    return {
        "op": "login",
        "args": [{
            "apiKey": api_key,
            "timestamp": timestamp,
            "nonce": n,
            "sign": ws_login_sign(api_key, secret, timestamp, n),
        }],
    }


@dataclass
class Event:
    """A typed push message."""
    channel: str
    symbol: str | None
    ts: int
    data: Any
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_message(cls, msg: dict) -> "Event | None":
        ch = msg.get("ch")
        if not ch:
            return None
        return cls(
            channel=ch,
            symbol=msg.get("symbol"),
            ts=int(msg.get("ts") or 0),
            data=msg.get("data"),
            raw=msg,
        )


# ---------------------------------------------------------------- config

@dataclass
class WSConfig:
    public_url: str = "wss://fapi.bitunix.com/public/"
    private_url: str = "wss://fapi.bitunix.com/private/"
    api_key: str = ""
    api_secret: str = ""
    ping_interval: float = 20.0
    pong_timeout: float = 10.0
    reconnect_base: float = 1.0
    reconnect_max: float = 60.0
    jitter: float = 0.5
    queue_max: int = 1000
    login_timeout: float = 10.0

    @classmethod
    def from_settings(cls, s: Any) -> "WSConfig":
        return cls(
            public_url=getattr(s, "bitunix_ws_public", cls.public_url),
            private_url=getattr(s, "bitunix_ws_private", cls.private_url),
            api_key=getattr(s, "bitunix_api_key", ""),
            api_secret=getattr(s, "bitunix_api_secret", ""),
        )


# ---------------------------------------------------------------- client

class BitunixWebSocket:
    """One connection (public or private) with subscriptions + auto-reconnect."""

    def __init__(self, cfg: WSConfig, private: bool = False):
        self.cfg = cfg
        self.private = private
        self.url = cfg.private_url if private else cfg.public_url

        self.subs: list[dict] = []
        self.handlers: dict[str, list[Handler]] = {}

        self._ws: Any = None
        self._task: asyncio.Task | None = None
        self._worker: asyncio.Task | None = None
        self._queue: asyncio.Queue | None = None
        self._stop = asyncio.Event()
        self._pong = asyncio.Event()

        self.connected = False
        self.logged_in = False
        self.reconnects = 0
        self.messages = 0
        self.dropped = 0
        self.pings_sent = 0
        self.pongs_received = 0
        self.last_message_ts = 0.0
        self.last_pong_ts = 0.0
        self.connected_since = 0.0
        self.last_error = ""

    # ---------------------------------------------------------- public API
    def subscribe(self, ch: str, symbol: str | None = None) -> None:
        item: dict = {"ch": ch}
        if symbol:
            item["symbol"] = symbol
        if item not in self.subs:
            self.subs.append(item)

    def unsubscribe(self, ch: str, symbol: str | None = None) -> None:
        item: dict = {"ch": ch}
        if symbol:
            item["symbol"] = symbol
        if item in self.subs:
            self.subs.remove(item)

    def on(self, ch: str, handler: Handler) -> None:
        """Register a handler. `ch` may be an exact channel, a prefix
        (e.g. 'market_kline' catches every interval) or '*' for everything."""
        self.handlers.setdefault(ch, []).append(handler)

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._queue = asyncio.Queue(maxsize=self.cfg.queue_max)
        self._worker = asyncio.create_task(
            self._dispatch_loop(), name=f"ws-dispatch-{'priv' if self.private else 'pub'}")
        self._task = asyncio.create_task(
            self._run(), name=f"ws-{'priv' if self.private else 'pub'}")

    async def stop(self) -> None:
        """Close the socket and cancel every task. Idempotent."""
        self._stop.set()
        ws = self._ws
        if ws is not None:
            try:
                await ws.close()
            except Exception:  # noqa: BLE001
                pass
        for task in (self._task, self._worker):
            if task and not task.done():
                task.cancel()
        for task in (self._task, self._worker):
            if task:
                try:
                    await task
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        self._task = None
        self._worker = None
        self.connected = False
        self.logged_in = False

    def stats(self) -> dict:
        now = time.time()
        return {
            "url": self.url,
            "private": self.private,
            "connected": self.connected,
            "logged_in": self.logged_in,
            "uptime_s": round(now - self.connected_since, 1) if self.connected_since else 0.0,
            "reconnects": self.reconnects,
            "subscriptions": len(self.subs),
            "messages": self.messages,
            "dropped": self.dropped,
            "pings_sent": self.pings_sent,
            "pongs_received": self.pongs_received,
            "last_message_age_s": round(now - self.last_message_ts, 1) if self.last_message_ts else None,
            "last_error": self.last_error,
        }

    # ---------------------------------------------------------- internals
    def _backoff(self, attempt: int) -> float:
        raw = min(self.cfg.reconnect_max, self.cfg.reconnect_base * (2 ** attempt))
        return raw * (1.0 - self.cfg.jitter * random.random())

    async def _run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    self.url, ping_interval=None, max_size=8 * 1024 * 1024,
                ) as ws:
                    self._ws = ws
                    self.connected = True
                    self.connected_since = time.time()
                    attempt = 0

                    await self._read_connect_ack(ws)
                    if self.private:
                        await self._login(ws)
                    if self.subs:
                        await ws.send(json.dumps(build_subscribe(self.subs)))
                    log.info("ws connected %s (%d subs)", self.url, len(self.subs))

                    await self._pump(ws)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self.last_error = f"{type(e).__name__}: {e}"
                if self._stop.is_set():
                    break
                self.reconnects += 1
                delay = self._backoff(attempt)
                attempt += 1
                log.warning("ws error (%s): %s - reconnect #%d in %.1fs",
                            self.url, e, self.reconnects, delay)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=delay)
                    break  # stop() was called during the wait
                except asyncio.TimeoutError:
                    pass
            finally:
                self.connected = False
                self.logged_in = False
                self._ws = None

    async def _read_connect_ack(self, ws) -> None:
        """The server's first frame is {"op":"connect","data":{"result":true}}."""
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=self.cfg.login_timeout)
        except asyncio.TimeoutError:
            return
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        if msg.get("op") == "connect" and not (msg.get("data") or {}).get("result", True):
            raise RuntimeError(f"ws connect rejected: {msg}")

    async def _login(self, ws) -> None:
        if not self.cfg.api_key or not self.cfg.api_secret:
            raise RuntimeError("private WS needs BITUNIX_API_KEY and BITUNIX_API_SECRET")
        frame = build_login(self.cfg.api_key, self.cfg.api_secret)
        await ws.send(json.dumps(frame))
        # The docs do not define a login ack; the Go demo does not wait for one.
        # Read one frame if it arrives, fail on an explicit error, otherwise go on.
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=self.cfg.login_timeout)
        except asyncio.TimeoutError:
            self.logged_in = True
            log.debug("ws login: no ack within %.0fs, continuing", self.cfg.login_timeout)
            return
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            self.logged_in = True
            return
        code = msg.get("code")
        if code not in (None, 0, "0"):
            raise RuntimeError(f"ws login failed: code={code} msg={msg.get('msg')}")
        self.logged_in = True
        log.debug("ws login ok")

    async def _pump(self, ws) -> None:
        pinger = asyncio.create_task(self._pinger(ws), name="ws-pinger")
        try:
            async for raw in ws:
                self.last_message_ts = time.time()
                self.messages += 1
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    log.debug("ws non-json frame ignored: %r", raw[:120])
                    continue

                # heartbeat reply
                if msg.get("op") == "ping" and "pong" in msg:
                    self.pongs_received += 1
                    self.last_pong_ts = time.time()
                    self._pong.set()
                    continue
                if msg.get("op") in ("connect", "subscribe", "unsubscribe", "login"):
                    continue

                ev = Event.from_message(msg)
                if ev is None:
                    log.debug("ws frame with no channel ignored: %r", raw[:120])
                    continue
                self._enqueue(ev)
        finally:
            pinger.cancel()
            try:
                await pinger
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    def _enqueue(self, ev: Event) -> None:
        """Bounded queue: a slow handler drops events instead of growing RAM."""
        q = self._queue
        if q is None:
            return
        try:
            q.put_nowait(ev)
        except asyncio.QueueFull:
            self.dropped += 1
            if self.dropped % 100 == 1:
                log.warning("ws backpressure: dropped %d events (queue full)", self.dropped)

    async def _dispatch_loop(self) -> None:
        q = self._queue
        assert q is not None
        while True:
            ev = await q.get()
            try:
                for h in self._handlers_for(ev.channel):
                    try:
                        res = h(ev)
                        if asyncio.iscoroutine(res):
                            await res
                    except Exception as e:  # noqa: BLE001
                        log.error("handler error on %s: %s", ev.channel, e)
            finally:
                q.task_done()

    def _handlers_for(self, ch: str) -> list[Handler]:
        out = list(self.handlers.get(ch, []))
        best = ""
        for key in self.handlers:
            if key in ("*", ch):
                continue
            if ch.startswith(key) and len(key) > len(best):
                best = key
        if best:
            out += self.handlers[best]
        out += self.handlers.get("*", [])
        return out

    async def _pinger(self, ws) -> None:
        """Send the documented ping; a missing pong means the socket is dead."""
        while True:
            await asyncio.sleep(self.cfg.ping_interval)
            self._pong.clear()
            try:
                await ws.send(json.dumps(build_ping()))
                self.pings_sent += 1
            except Exception:  # noqa: BLE001
                return
            try:
                await asyncio.wait_for(self._pong.wait(), timeout=self.cfg.pong_timeout)
            except asyncio.TimeoutError:
                log.warning("ws heartbeat: no pong in %.0fs - closing to force reconnect",
                            self.cfg.pong_timeout)
                self.last_error = "heartbeat timeout (no pong)"
                try:
                    await ws.close()
                except Exception:  # noqa: BLE001
                    pass
                return


# ---------------------------------------------------------------- feed

class MarketDataFeed:
    """One public WS for a set of symbols/timeframes, feeding a PriceCache."""

    def __init__(self, cfg: WSConfig, symbols: list[str], timeframes: list[str],
                 cache: "PriceCache | None" = None,
                 kline_cache: KlineCache | None = None):
        from .price_cache import PriceCache

        self.ws = BitunixWebSocket(cfg, private=False)
        self.symbols = [s.upper() for s in symbols]
        self.timeframes = list(timeframes)
        self.cache = cache if cache is not None else PriceCache()
        self.kline_cache = kline_cache
        self.latest_price: dict[str, float] = {}
        self.latest_mark: dict[str, float] = {}
        self.latest_funding: dict[str, float] = {}
        self.latest_kline: dict[tuple[str, str], dict] = {}
        self._wire()

    def _wire(self) -> None:
        for s in self.symbols:
            self.ws.subscribe("price", s)
            self.ws.subscribe("ticker", s)
            for tf in self.timeframes:
                try:
                    self.ws.subscribe(kline_channel(tf), s)
                except ValueError as e:
                    log.warning("skipping timeframe: %s", e)
        self.ws.on("price", self._on_price)
        self.ws.on("ticker", self._on_ticker)
        self.ws.on("market_kline", self._on_kline)

    def _on_price(self, ev: Event) -> None:
        d = ev.data or {}
        sym = ev.symbol or d.get("s")
        if not sym:
            return
        try:
            if d.get("mp"):
                px = float(d["mp"])
                self.latest_price[sym] = px
                self.cache.put_ws(sym, px, exchange_ts=(ev.ts / 1000.0) if ev.ts else 0.0)
            if d.get("ip"):
                self.latest_mark[sym] = float(d["ip"])
            if d.get("fr"):
                self.latest_funding[sym] = float(d["fr"])
        except (TypeError, ValueError):
            pass

    def _on_ticker(self, ev: Event) -> None:
        d = ev.data or {}
        sym = ev.symbol or d.get("s")
        px = d.get("la")
        if sym and px:
            try:
                val = float(px)
                self.latest_price[sym] = val
                self.cache.put_ws(sym, val, exchange_ts=(ev.ts / 1000.0) if ev.ts else 0.0)
            except (TypeError, ValueError):
                pass

    def _on_kline(self, ev: Event) -> None:
        """A kline frame carries o/h/l/c/b/q and NO time field.

        Verified live: the push has no `time`, so the candle's open time is
        derived by flooring the frame `ts` to the interval boundary. Without
        that, every candle collapses onto one key and the series never grows.
        """
        sym = ev.symbol
        if not sym:
            return
        d = ev.data or {}
        for tf in self.timeframes:
            try:
                if kline_channel(tf) != ev.channel:
                    continue
            except ValueError:
                continue
            self.latest_kline[(sym, tf)] = d
            if self.kline_cache is None or not ev.ts:
                return
            try:
                candle = Candle(
                    time=candle_open_time(ev.ts, tf),
                    open=float(d["o"]), high=float(d["h"]),
                    low=float(d["l"]), close=float(d["c"]),
                    base_vol=float(d.get("b") or 0),
                    quote_vol=float(d.get("q") or 0),
                )
            except (KeyError, TypeError, ValueError):
                return
            self.kline_cache.put_ws(sym, tf, candle)
            return

    def price(self, symbol: str) -> float | None:
        return self.latest_price.get(symbol.upper())

    async def start(self) -> None:
        await self.ws.start()

    async def stop(self) -> None:
        await self.ws.stop()
