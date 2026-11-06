"""Strategy registry - all 20 strategies, pluggable."""
from __future__ import annotations

from .base import BaseStrategy, Signal
from .breakout import BollingerUpperBreakout, MidBandMacdCross
from .classic import DeathCrossShort, GoldenCrossLong, VegasTunnelBearish
from .reversal import (DarkCloudCoverDeathCross, HammerOversoldBounce,
                       MorningStarDivergence, ShootingStarOverbought)
from .risk import VwapBreakdownAlert
from .trend import (Dema55TrendFollow, Ma200CrossMacdConfirm, TemaCrossVwap,
                    ThreeWhiteSoldiers, VegasTunnelBullish,
                    VwapBullishConfirmation)

ALL_STRATEGIES: list[type[BaseStrategy]] = [
    # classic
    VegasTunnelBearish, GoldenCrossLong, DeathCrossShort,
    # risk
    VwapBreakdownAlert,
    # reversal
    HammerOversoldBounce, MorningStarDivergence, ShootingStarOverbought,
    DarkCloudCoverDeathCross,
    # breakout
    BollingerUpperBreakout, MidBandMacdCross,
    # trend
    Ma200CrossMacdConfirm, VegasTunnelBullish, TemaCrossVwap,
    Dema55TrendFollow, VwapBullishConfirmation, ThreeWhiteSoldiers,
]


def build_strategies() -> list[BaseStrategy]:
    return [cls() for cls in ALL_STRATEGIES]


def registry() -> dict[str, dict]:
    out = {}
    for cls in ALL_STRATEGIES:
        inst = cls()
        out[inst.name] = {
            "name": inst.name,
            "category": inst.category,
            "description": inst.description,
            "indicators": inst.indicators,
            "min_candles": inst.min_candles,
        }
    return out


__all__ = ["ALL_STRATEGIES", "BaseStrategy", "Signal", "build_strategies", "registry"]
