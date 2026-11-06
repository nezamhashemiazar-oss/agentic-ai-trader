"""Bitunix WebSocket client: public + private channels, auto-reconnect, signed login.

Public channels (wss://fapi.bitunix.com/public/):
    depth_books, market_kline_<interval>, market_price, market_ticker,
    market_tickers, market_trade
Private channels (wss://fapi.bitunix.com/private/):
    balance, order, position, tpsl

Login signature (official, docs/bitunix/WebSocket.md):
    sign = sha256(sha256(nonce + timestamp + apiKey) + secretKey)
    (same double-SHA256 scheme as REST, with timestamp in SECONDS)

NOTE the order is nonce THEN timestamp - the same order as REST. An earlier
version of this file had timestamp first, which the exchange rejects.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import string
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import websockets

log = logging.getLogger("jrock.ws")

Handler = Callable[[dict], Awaitable[None] | None]

PUBLIC_CHANNELS = {
    "depth": "depth_books",
    "market_price": "market_price",
    "ticker": "market_ticker",
    "tickers": "market_tickers",
    "trade": "market_trade",
}
PRIVATE_CHANNELS = {"balance": "balance", "order": "order", "position": "position", "tpsl": "tpsl"}


def kline_channel(interval: str) -> str:
    """1m -> market_kline_1min, 15m -> market_kline_15min, 1h -> market_kline_1hour."""
    unit = interval[-1]
    n = interval[:-1]
    suffix = {"m": "min", "h": "hour", "d": "day", "w": "week", "M": "month"}.get(unit, "min")
    return f"market_kline_{n}{suffix}"


def _nonce(n: int = 32) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def ws_login_sign(api_key: str, secret: str, timestamp: int, nonce: str) -> str:
    """sign = sha256(sha256(nonce + timestamp + apiKey) + secret).

    Order is nonce, then timestamp - per docs/bitunix/WebSocket.md line 226.
    """
    digest = hashlib.sha256(f"{nonce}{timestamp}{api_key}".encode()).hexdigest()
    return hashlib.sha256((digest + secret).encode()).hexdigest()


@dataclass
class WSConfig:
    public_url: str = "wss://fapi.bitunix.com/public/"
    private_url: str = "wss://fapi.bitunix.com/private/"
    api_key: str = ""
    api_secret: str = ""
    ping_interval: float = 20.0
    reconnect_base: float = 1.0
    reconnect_max: float = 60.0


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
        self._stop = asyncio.Event()
        self.connected = False
        self.reconnects = 0
        self.last_message_ts: float = 0.0

    def subscribe(self, ch: str, symbol: str | None = None) -> None:
        item = {"ch": ch}
        if symbol:
            item["symbol"] = symbol
        if item not in self.subs:
            self.subs.append(item)

    def on(self, ch: str, handler: Handler) -> None:
        self.handlers.setdefault(ch, []).append(handler)

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name=f"ws-{'priv' if self.private else 'pub'}")

    async def stop(self) -> None:
        self._stop.set()
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    async def _run(self) -> None:
        backoff = self.cfg.reconnect_base
        while not self._stop.is_set():
            try:
                async with websockets.connect(self.url, ping_interval=None,
                                              max_size=8 * 1024 * 1024) as ws:
                    self._ws = ws
                    self.connected = True
                    backoff = self.cfg.reconnect_base
                    if self.private:
                        await self._login(ws)
                    if self.subs:
                        await ws.send(json.dumps({"op": "subscribe", "args": self.subs}))
                    log.info("ws connected (%s), %d subs", self.url, len(self.subs))
                    await self._pump(ws)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self.connected = False
                if self._stop.is_set():
                    break
                self.reconnects += 1
                log.warning("ws error (%s): %s - reconnecting in %.1fs", self.url, e, backoff)
                await asyncio.sleep(backoff)
                backoff = min(self.cfg.reconnect_max, backoff * 2)

    async def _login(self, ws) -> None:
        if not self.cfg.api_key or not self.cfg.api_secret:
            raise RuntimeError("private WS needs api_key/api_secret")
        ts = int(time.time())
        nonce = _nonce()
        payload = {
            "op": "login",
            "args": [{
                "apiKey": self.cfg.api_key,
                "timestamp": ts,
                "nonce": nonce,
                "sign": ws_login_sign(self.cfg.api_key, self.cfg.api_secret, ts, nonce),
            }],
        }
        await ws.send(json.dumps(payload))
        raw = await asyncio.wait_for(ws.recv(), timeout=10)
        msg = json.loads(raw)
        if msg.get("code") not in (0, None) and msg.get("msg") not in ("Success", None):
            raise RuntimeError(f"ws login failed: {msg}")

    async def _pump(self, ws) -> None:
        ping_task = asyncio.create_task(self._pinger(ws))
        try:
            async for raw in ws:
                self.last_message_ts = time.time()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if msg.get("op") == "ping":
                    await ws.send(json.dumps({"op": "pong", "pong": msg.get("ping")}))
                    continue
                ch = msg.get("ch") or msg.get("channel") or ""
                for key, handlers in self.handlers.items():
                    if key and key in ch:
                        for h in handlers:
                            try:
                                res = h(msg)
                                if asyncio.iscoroutine(res):
                                    await res
                            except Exception as e:  # noqa: BLE001
                                log.error("handler error on %s: %s", ch, e)
        finally:
            ping_task.cancel()

    async def _pinger(self, ws) -> None:
        while True:
            await asyncio.sleep(self.cfg.ping_interval)
            try:
                await ws.send(json.dumps({"op": "ping", "ping": int(time.time())}))
            except Exception:  # noqa: BLE001
                return


class MarketDataFeed:
    """Convenience wrapper: one public WS for a set of symbols/timeframes."""

    def __init__(self, cfg: WSConfig, symbols: list[str], timeframes: list[str]):
        self.ws = BitunixWebSocket(cfg, private=False)
        self.symbols = symbols
        self.timeframes = timeframes
        self.latest_price: dict[str, float] = {}
        self.latest_kline: dict[tuple[str, str], dict] = {}
        self._wire()

    def _wire(self) -> None:
        for s in self.symbols:
            self.ws.subscribe("market_price", s)
            self.ws.subscribe("market_ticker", s)
            for tf in self.timeframes:
                self.ws.subscribe(kline_channel(tf), s)
        self.ws.on("market_price", self._on_price)
        self.ws.on("market_kline", self._on_kline)

    def _on_price(self, msg: dict) -> None:
        d = msg.get("data") or {}
        sym = d.get("symbol") or d.get("s")
        px = d.get("price") or d.get("p") or d.get("lastPrice")
        if sym and px:
            try:
                self.latest_price[sym] = float(px)
            except (TypeError, ValueError):
                pass

    def _on_kline(self, msg: dict) -> None:
        ch = msg.get("ch", "")
        d = msg.get("data") or {}
        sym = d.get("symbol") or d.get("s")
        for tf in self.timeframes:
            if kline_channel(tf) == ch and sym:
                self.latest_kline[(sym, tf)] = d

    async def start(self) -> None:
        await self.ws.start()

    async def stop(self) -> None:
        await self.ws.stop()
