"""Price cache fed by the WebSocket, with a hard staleness rule.

The engine must never trade on a price it cannot date. Every quote carries:

    price        the number
    source       "ws" or "rest"
    exchange_ts  the exchange's own timestamp (seconds), when it sent one
    received_ts  our local clock when the frame arrived

`get_fresh()` returns a quote ONLY if it is younger than `max_age`. Anything
older is refused, counted as a stale rejection, and the caller falls back to
REST. That is the whole point: a silent stale price is how a bot trades on a
market that moved 3% two minutes ago.

The cache is only consulted when MARKET_DATA_SOURCE=ws. With `rest` the engine
behaves exactly as it did before this module existed - no cache, no socket.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

#: Default max age for a cached price used in a trading decision.
DEFAULT_MAX_AGE_SEC = 5.0


@dataclass
class PriceQuote:
    symbol: str
    price: float
    source: str                 # "ws" | "rest"
    exchange_ts: float          # seconds; 0.0 when the frame carried none
    received_ts: float          # local time.time() when we stored it

    def age(self, now: float | None = None) -> float:
        return (now if now is not None else time.time()) - self.received_ts

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "price": self.price,
            "source": self.source,
            "exchange_ts": self.exchange_ts,
            "received_ts": self.received_ts,
            "age_s": round(self.age(), 3),
        }


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    stale_rejections: int = 0
    rest_fallbacks: int = 0
    ws_updates: int = 0
    rest_updates: int = 0

    def as_dict(self) -> dict:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "stale_rejections": self.stale_rejections,
            "rest_fallbacks": self.rest_fallbacks,
            "ws_updates": self.ws_updates,
            "rest_updates": self.rest_updates,
        }


class PriceCache:
    """Latest price per symbol, with source + timestamps + counters."""

    def __init__(self, max_age: float = DEFAULT_MAX_AGE_SEC):
        self.max_age = float(max_age)
        self._quotes: dict[str, PriceQuote] = {}
        self.stats = CacheStats()

    # ------------------------------------------------------------- writes
    def put_ws(self, symbol: str, price: float, exchange_ts: float = 0.0) -> None:
        if not symbol or price is None or price <= 0:
            return
        sym = symbol.upper()
        self._quotes[sym] = PriceQuote(
            symbol=sym, price=float(price), source="ws",
            exchange_ts=float(exchange_ts or 0.0), received_ts=time.time(),
        )
        self.stats.ws_updates += 1

    def put_rest(self, symbol: str, price: float) -> None:
        if not symbol or price is None or price <= 0:
            return
        sym = symbol.upper()
        self._quotes[sym] = PriceQuote(
            symbol=sym, price=float(price), source="rest",
            exchange_ts=0.0, received_ts=time.time(),
        )
        self.stats.rest_updates += 1

    # -------------------------------------------------------------- reads
    def age(self, symbol: str) -> float | None:
        q = self._quotes.get(symbol.upper())
        return q.age() if q else None

    def peek(self, symbol: str) -> PriceQuote | None:
        """The stored quote regardless of age. For display only."""
        return self._quotes.get(symbol.upper())

    def get_fresh(self, symbol: str) -> PriceQuote | None:
        """A quote younger than max_age, or None.

        Counts a hit on success. A present-but-old quote is NOT a hit: the
        caller must fall back, and `note_stale()` records why.
        """
        q = self._quotes.get(symbol.upper())
        if q is None:
            return None
        if q.age() > self.max_age:
            return None
        self.stats.hits += 1
        return q

    def note_miss(self) -> None:
        self.stats.misses += 1

    def note_stale(self) -> None:
        self.stats.stale_rejections += 1

    def note_rest_fallback(self) -> None:
        self.stats.rest_fallbacks += 1

    def symbols(self) -> list[str]:
        return sorted(self._quotes)

    def clear(self) -> None:
        self._quotes.clear()
