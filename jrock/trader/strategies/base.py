"""Strategy contract: every strategy is a pluggable module returning a Signal."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ..indicators import Candle


@dataclass
class Signal:
    strategy: str
    direction: str          # "LONG" | "SHORT" | "NEUTRAL"
    confidence: float       # 0..100
    reason: str
    timeframe: str = ""
    symbol: str = ""
    indicators: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)

    @property
    def is_actionable(self) -> bool:
        return self.direction in ("LONG", "SHORT")


class Strategy(Protocol):
    name: str
    category: str
    description: str
    indicators: str
    min_candles: int

    def evaluate(self, candles: list[Candle], timeframe: str = "",
                 symbol: str = "") -> Signal: ...


class BaseStrategy:
    name = "base"
    category = "misc"
    description = ""
    indicators = ""
    min_candles = 60

    def evaluate(self, candles: list[Candle], timeframe: str = "", symbol: str = "") -> Signal:
        raise NotImplementedError

    # helpers -------------------------------------------------------------
    def neutral(self, reason: str, timeframe: str = "", symbol: str = "",
                indicators: dict | None = None) -> Signal:
        return Signal(self.name, "NEUTRAL", 0.0, reason, timeframe, symbol, indicators or {})

    def signal(self, direction: str, confidence: float, reason: str,
               timeframe: str = "", symbol: str = "",
               indicators: dict | None = None, tags: list[str] | None = None) -> Signal:
        return Signal(self.name, direction, max(0.0, min(100.0, confidence)),
                      reason, timeframe, symbol, indicators or {}, tags or [])

    def enough(self, candles: list[Candle]) -> bool:
        return len(candles) >= self.min_candles
