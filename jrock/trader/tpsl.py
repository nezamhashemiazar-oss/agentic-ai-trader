"""Agent-decided TP/SL.

The user sets only TWO numbers:
    breakeven_pct            - move SL to entry once ROI >= this %
    trailing_trigger_roi_pct - start trailing once ROI >= this %

Everything else is derived by the agent from signal strength:
    * SL distance is anchored to the liquidation price: the SL sits
      `liq_sl_distance_pct` (default 50%) of the way from entry to liq.
    * TP is a multiple of the SL distance, scaled by confidence/strength:
        weak 1.2R, normal 1.8R, strong 2.5R, extreme 3.2R
      and capped so the implied ROI stays sane.
"""
from __future__ import annotations

from dataclasses import dataclass

from .risk import (PairRules, Tier, TpSlSideError, clamp_sl, clamp_tp,
                   liquidation_price, min_stop_distance, pick_tier,
                   round_price, sl_from_liq_distance)

R_MULTIPLE = {"none": 1.0, "weak": 1.2, "normal": 1.8, "strong": 2.5, "extreme": 3.2}


@dataclass
class TpSlPlan:
    side: str
    entry: float
    sl: float
    tp: float
    liq: float
    sl_roi_pct: float
    tp_roi_pct: float
    rr: float
    strength: str
    breakeven_pct: float
    trailing_trigger_roi_pct: float
    tier_level: int
    mmr: float
    notes: str = ""

    def as_dict(self) -> dict:
        return {
            "side": self.side, "entry": self.entry, "sl": self.sl, "tp": self.tp,
            "liq": self.liq, "sl_roi_pct": round(self.sl_roi_pct, 2),
            "tp_roi_pct": round(self.tp_roi_pct, 2), "rr": round(self.rr, 2),
            "strength": self.strength, "breakeven_pct": self.breakeven_pct,
            "trailing_trigger_roi_pct": self.trailing_trigger_roi_pct,
            "tier_level": self.tier_level, "mmr": self.mmr, "notes": self.notes,
        }


def plan_tpsl(*, side: str, entry: float, leverage: int, notional: float,
              strength: str, confidence: float, tiers: list[Tier],
              rules: PairRules, liq_sl_distance_pct: float = 50.0,
              breakeven_pct: float = 0.6,
              trailing_trigger_roi_pct: float = 25.0,
              mark: float | None = None,
              min_stop_ticks: int = 3) -> TpSlPlan:
    """Build a TP/SL plan that is guaranteed to sit on the correct side.

    `mark` is the exchange mark price. The exchange validates the trigger
    against BOTH the mark price and the average entry price, so a stop that is
    only correct relative to `entry` can still be rejected (30029/30031) when
    the two differ. When `mark` is supplied the stop is clamped to clear the
    worse of the two by at least `min_stop_ticks` ticks.
    """
    tier = pick_tier(tiers, notional)
    mmr = tier.maintenance_margin_rate
    liq = liquidation_price(entry, leverage, side, mmr)

    is_long = side.upper() in ("LONG", "BUY")
    d = min_stop_distance(rules, min_stop_ticks)
    ref = mark if (mark is not None and mark > 0) else entry

    sl = sl_from_liq_distance(entry, liq, side, liq_sl_distance_pct)
    sl = round_price(sl, rules.quote_precision)

    # --- clamp the SL to the correct side of BOTH entry and mark.
    # A high-leverage stop can land within a tick of the mark price; rounding
    # then collapses it onto the mark and the exchange rejects the order.
    # clamp_sl is shared with the running-engine path (breakeven/trailing) so
    # there is exactly one implementation of this rule.
    sl = clamp_sl(side=side, sl=sl, entry=entry, mark=ref, rules=rules,
                  min_ticks=min_stop_ticks, liq=liq)

    # --- feasibility: the stop must fit BETWEEN the liquidation price and the
    # mark-side limit. At high leverage with a coarse tick there may be no room
    # at all; refuse with the numbers instead of emitting an invalid stop.
    if is_long:
        if not (liq < sl < min(entry, ref)):
            raise TpSlSideError(
                f"{rules.symbol} LONG: no valid stop-loss exists. The stop must "
                f"sit between the liquidation price {liq:.{rules.quote_precision}f} "
                f"and {min(entry, ref):.{rules.quote_precision}f} "
                f"(entry={entry:.{rules.quote_precision}f}, "
                f"mark={ref:.{rules.quote_precision}f}), keeping "
                f"{d:.{rules.quote_precision}f} clear of both. "
                f"Lower the leverage or widen the tick.")
    else:
        if not (max(entry, ref) < sl < liq):
            raise TpSlSideError(
                f"{rules.symbol} SHORT: no valid stop-loss exists. The stop must "
                f"sit between {max(entry, ref):.{rules.quote_precision}f} and the "
                f"liquidation price {liq:.{rules.quote_precision}f} "
                f"(entry={entry:.{rules.quote_precision}f}, "
                f"mark={ref:.{rules.quote_precision}f}), keeping "
                f"{d:.{rules.quote_precision}f} clear of both. "
                f"Lower the leverage or widen the tick.")

    sl_dist = abs(entry - sl)
    mult = R_MULTIPLE.get(strength, 1.8)
    mult *= 0.85 + (max(0.0, min(100.0, confidence)) / 100.0) * 0.3

    tp_dist = sl_dist * mult
    tp = entry + tp_dist if is_long else entry - tp_dist
    tp = round_price(tp, rules.quote_precision)

    # --- clamp the TP to the correct side of BOTH entry and mark.
    tp = clamp_tp(side=side, tp=tp, entry=entry, mark=ref, rules=rules,
                  min_ticks=min_stop_ticks)

    def roi(p: float) -> float:
        raw = (p - entry) / entry if side.upper() in ("LONG", "BUY") else (entry - p) / entry
        return raw * leverage * 100.0

    sl_roi = roi(sl)
    tp_roi = roi(tp)
    rr = abs(tp_roi / sl_roi) if sl_roi else 0.0

    notes = []
    if sl_roi and abs(sl_roi) > 60:
        notes.append(f"SL ROI {sl_roi:.1f}% is wide - consider lower leverage")
    if tp_roi > 400:
        notes.append("TP ROI very high; may take long to fill")

    return TpSlPlan(
        side=side.upper(), entry=entry, sl=sl, tp=tp, liq=liq,
        sl_roi_pct=sl_roi, tp_roi_pct=tp_roi, rr=rr, strength=strength,
        breakeven_pct=breakeven_pct,
        trailing_trigger_roi_pct=trailing_trigger_roi_pct,
        tier_level=tier.level, mmr=mmr, notes="; ".join(notes),
    )


@dataclass
class PositionState:
    position_id: str
    symbol: str
    side: str
    entry: float
    qty: float
    leverage: int
    sl: float
    tp: float
    liq: float
    breakeven_done: bool = False
    trailing_active: bool = False
    trailing_stop: float | None = None
    partial_done: bool = False
    opened_ts: float = 0.0
    peak_roi: float = 0.0


def should_breakeven(state: PositionState, roi: float, breakeven_pct: float) -> bool:
    return (not state.breakeven_done) and roi >= breakeven_pct


def should_trail(state: PositionState, roi: float, trigger_roi_pct: float) -> bool:
    return roi >= trigger_roi_pct


def next_trailing_stop(state: PositionState, price: float, atr_value: float,
                       min_step_pct: float = 0.15) -> float | None:
    """Ratchet the trailing stop; never loosen it."""
    if atr_value <= 0:
        atr_value = price * 0.002
    if state.side.upper() in ("LONG", "BUY"):
        candidate = price - atr_value * 1.5
        if state.trailing_stop is None or candidate > state.trailing_stop * (1 + min_step_pct / 100):
            return candidate
    else:
        candidate = price + atr_value * 1.5
        if state.trailing_stop is None or candidate < state.trailing_stop * (1 - min_step_pct / 100):
            return candidate
    return None


def mid_management_action(state: PositionState, roi: float, reversal: bool,
                          reversal_confidence: float, min_reversal_conf: float = 70.0,
                          partial_at_roi: float = 50.0) -> str:
    """Returns: "hold" | "partial_close" | "close_reversal" | "tighten"."""
    if reversal and reversal_confidence >= min_reversal_conf:
        return "close_reversal"
    if not state.partial_done and roi >= partial_at_roi:
        return "partial_close"
    if roi >= 30:
        return "tighten"
    return "hold"
