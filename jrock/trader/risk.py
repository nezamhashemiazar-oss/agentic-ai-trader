"""Order-unit conversion, liquidation price, tiered risk limits, position sizing.

Bitunix futures notes (from the official docs + support article):
  * `qty` in place_order is the BASE-COIN amount (e.g. BTC for BTCUSDT), not contracts.
  * `minTradeVolume` / `maxMarketOrderVolume` are base-coin amounts.
  * `basePrecision` = max decimals for qty; `quotePrecision` = max decimals for price.
  * Liquidation (isolated, one-way):
        liq = entry * (1 -/+ 1/leverage + maintenanceMarginRate)   (long / short)
    i.e. long:  liq = entry * (1 - 1/L + mmr)
         short: liq = entry * (1 + 1/L - mmr)
  * Tiered risk: the maintenance margin rate depends on the position notional tier
    (get_position_tiers). Higher notional -> higher mmr -> liquidation closer.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PairRules:
    symbol: str
    min_trade_volume: float = 0.0
    max_market_order_volume: float = 0.0
    max_limit_order_volume: float = 0.0
    base_precision: int = 4
    quote_precision: int = 2
    min_leverage: int = 1
    max_leverage: int = 125
    default_leverage: int = 20
    price_protect_scope: float = 0.02
    symbol_status: str = "OPEN"
    api_supported: bool = True

    @classmethod
    def from_api(cls, d: dict) -> "PairRules":
        def f(k, default=0.0):
            v = d.get(k, default)
            if v is None or v == "":
                return default
            try:
                return float(v)
            except (TypeError, ValueError):
                return default

        def i(k, default=0):
            v = d.get(k, default)
            if v is None or v == "":
                return default
            try:
                return int(v)
            except (TypeError, ValueError):
                return default

        return cls(
            symbol=d.get("symbol", ""),
            min_trade_volume=f("minTradeVolume"),
            max_market_order_volume=f("maxMarketOrderVolume"),
            max_limit_order_volume=f("maxLimitOrderVolume"),
            base_precision=i("basePrecision", 4),
            quote_precision=i("quotePrecision", 2),
            min_leverage=i("minLeverage", 1),
            max_leverage=i("maxLeverage", 125),
            default_leverage=i("defaultLeverage", 20),
            price_protect_scope=f("priceProtectScope", 0.02),
            symbol_status=d.get("symbolStatus", "OPEN"),
            api_supported=bool(d.get("isApiSupported", True)),
        )


@dataclass
class Tier:
    level: int
    start_value: float
    end_value: float
    leverage: int
    maintenance_margin_rate: float

    @classmethod
    def from_api(cls, d: dict) -> "Tier":
        return cls(
            level=int(d.get("level", 1)),
            start_value=float(d.get("startValue", 0) or 0),
            end_value=float(d.get("endValue", 0) or 0),
            leverage=int(d.get("leverage", 1) or 1),
            maintenance_margin_rate=float(d.get("maintenanceMarginRate", 0.004) or 0.004),
        )


def round_step(value: float, precision: int) -> float:
    """Round DOWN to the allowed precision (never over-size an order)."""
    if precision < 0:
        precision = 0
    factor = 10 ** precision
    return int(value * factor) / factor


def round_price(price: float, precision: int) -> float:
    return round(price, max(0, precision))


def format_qty(qty: float, rules: "PairRules") -> str:
    """Format a base-coin qty to the symbol's basePrecision as a string.

    basePrecision == 0 means integer qty (e.g. AKEUSDT). round_step already
    floors to the precision; this renders it without a trailing '.0'.
    """
    p = max(0, int(rules.base_precision))
    if p == 0:
        return str(int(round_step(qty, 0)))
    return f"{round_step(qty, p):.{p}f}"


def format_price(price: float, rules: "PairRules") -> str:
    """Format a price to the symbol's quotePrecision as a string."""
    p = max(0, int(rules.quote_precision))
    if p == 0:
        return str(int(round(price, 0)))
    return f"{round(price, p):.{p}f}"


def qty_from_notional(notional_usd: float, price: float, rules: PairRules) -> float:
    """Base-coin qty for a given USD notional."""
    if price <= 0:
        return 0.0
    return round_step(notional_usd / price, rules.base_precision)


def notional_from_qty(qty: float, price: float) -> float:
    return qty * price


def margin_required(notional: float, leverage: int) -> float:
    return notional / max(1, leverage)


def pick_tier(tiers: list[Tier], notional: float) -> Tier:
    """Tier whose [startValue, endValue) contains the notional."""
    if not tiers:
        return Tier(1, 0.0, float("inf"), 125, 0.004)
    ordered = sorted(tiers, key=lambda t: t.start_value)
    for t in ordered:
        if t.start_value <= notional < t.end_value:
            return t
    return ordered[-1]


def liquidation_price(entry: float, leverage: int, side: str,
                      maintenance_margin_rate: float) -> float:
    """Isolated-margin liquidation estimate (Bitunix formula)."""
    L = max(1, leverage)
    mmr = max(0.0, maintenance_margin_rate)
    if side.upper() in ("LONG", "BUY"):
        p = entry * (1 - 1 / L + mmr)
    else:
        p = entry * (1 + 1 / L - mmr)
    return max(p, 0.0)


def sl_from_liq_distance(entry: float, liq: float, side: str,
                         distance_pct: float = 50.0) -> float:
    """Stop loss placed `distance_pct` of the way from entry toward liq price.

    distance_pct = 50 means the SL sits halfway between entry and liquidation,
    i.e. it triggers at ~50% of the ROI that would be lost at liquidation.
    """
    frac = max(1.0, min(99.0, distance_pct)) / 100.0
    if side.upper() in ("LONG", "BUY"):
        return entry - (entry - liq) * frac
    return entry + (liq - entry) * frac


def roi_pct(entry: float, price: float, side: str, leverage: int) -> float:
    if entry <= 0:
        return 0.0
    raw = (price - entry) / entry if side.upper() in ("LONG", "BUY") else (entry - price) / entry
    return raw * leverage * 100.0


def position_size_by_risk(equity: float, risk_pct: float, entry: float,
                          stop: float, leverage: int, rules: PairRules) -> float:
    """Size so that hitting the stop loses ~risk_pct of equity."""
    stop_dist = abs(entry - stop)
    if stop_dist <= 0 or entry <= 0:
        return 0.0
    risk_usd = equity * (risk_pct / 100.0)
    qty = risk_usd / stop_dist
    max_notional = equity * leverage
    qty = min(qty, max_notional / entry)
    qty = round_step(qty, rules.base_precision)
    if qty < rules.min_trade_volume:
        return 0.0
    if rules.max_market_order_volume and qty > rules.max_market_order_volume:
        qty = round_step(rules.max_market_order_volume, rules.base_precision)
    return qty


class TpSlSideError(ValueError):
    """Raised when a TP/SL price cannot be placed on the correct side.

    Bitunix rejects these with 30020-30033 (wrong side of mark/last price) and
    30024/30025 (wrong side of the liquidation price). We refuse locally with
    the numbers instead of sending a request we know will be rejected.
    """


def tick_size(rules: "PairRules") -> float:
    """Smallest representable price step for the symbol (quotePrecision)."""
    return 10.0 ** (-max(0, int(rules.quote_precision)))


def min_stop_distance(rules: "PairRules", min_ticks: int = 3) -> float:
    """Minimum distance a TP/SL must keep from the reference price.

    Without this, a high-leverage stop can round onto (or past) the mark price
    and the exchange rejects it with 30029/30031. `min_ticks` is configurable
    via TP_SL_MIN_STOP_TICKS.
    """
    return tick_size(rules) * max(1, int(min_ticks))


def tpsl_bounds(*, side: str, entry: float, mark: float,
                rules: "PairRules", min_ticks: int = 3) -> tuple[float, float]:
    """(lowest allowed SL, highest allowed SL) for the direction.

    The exchange compares the trigger against BOTH the mark price and the
    average entry price, so the stop must clear the worse of the two:

        LONG : sl < min(entry, mark) - min_dist
        SHORT: sl > max(entry, mark) + min_dist
    """
    d = min_stop_distance(rules, min_ticks)
    if side.upper() in ("LONG", "BUY"):
        return 0.0, min(entry, mark) - d
    return max(entry, mark) + d, float("inf")


def validate_tpsl_side(*, symbol: str, side: str, entry: float, mark: float,
                       sl: float | None, tp: float | None,
                       rules: "PairRules", min_ticks: int = 3,
                       liq: float | None = None) -> list[str]:
    """Return a list of human-readable violations (empty == valid).

    Covers the documented exchange rules:
        30020/30021  TP on the wrong side of mark
        30022/30023  SL on the wrong side of mark
        30026-30033  TP/SL on the wrong side of last price
        30024/30025  SL on the wrong side of the liquidation price
    """
    is_long = side.upper() in ("LONG", "BUY")
    d = min_stop_distance(rules, min_ticks)
    out: list[str] = []

    if sl is not None:
        if is_long:
            limit = min(entry, mark) - d
            if sl >= limit:
                out.append(
                    f"SL {sl:.{rules.quote_precision}f} must be BELOW "
                    f"{limit:.{rules.quote_precision}f} for a LONG "
                    f"(entry={entry:.{rules.quote_precision}f}, "
                    f"mark={mark:.{rules.quote_precision}f}, "
                    f"min distance={d:.{rules.quote_precision}f})")
            if liq is not None and sl <= liq:
                out.append(
                    f"SL {sl:.{rules.quote_precision}f} must be ABOVE the "
                    f"liquidation price {liq:.{rules.quote_precision}f} for a LONG")
        else:
            limit = max(entry, mark) + d
            if sl <= limit:
                out.append(
                    f"SL {sl:.{rules.quote_precision}f} must be ABOVE "
                    f"{limit:.{rules.quote_precision}f} for a SHORT "
                    f"(entry={entry:.{rules.quote_precision}f}, "
                    f"mark={mark:.{rules.quote_precision}f}, "
                    f"min distance={d:.{rules.quote_precision}f})")
            if liq is not None and sl >= liq:
                out.append(
                    f"SL {sl:.{rules.quote_precision}f} must be BELOW the "
                    f"liquidation price {liq:.{rules.quote_precision}f} for a SHORT")

    if tp is not None:
        if is_long:
            limit = max(entry, mark) + d
            if tp <= limit:
                out.append(
                    f"TP {tp:.{rules.quote_precision}f} must be ABOVE "
                    f"{limit:.{rules.quote_precision}f} for a LONG "
                    f"(entry={entry:.{rules.quote_precision}f}, "
                    f"mark={mark:.{rules.quote_precision}f})")
        else:
            limit = min(entry, mark) - d
            if tp >= limit:
                out.append(
                    f"TP {tp:.{rules.quote_precision}f} must be BELOW "
                    f"{limit:.{rules.quote_precision}f} for a SHORT "
                    f"(entry={entry:.{rules.quote_precision}f}, "
                    f"mark={mark:.{rules.quote_precision}f})")

    return [f"{symbol} {side.upper()}: {m}" for m in out]


def validate_price_protection(price: float, mark: float, side: str,
                              scope: float) -> tuple[bool, str]:
    """Bitunix rejects orders outside the price-protection band."""
    if mark <= 0:
        return True, "ok"
    lo = mark * (1 - scope)
    hi = mark * (1 + scope)
    if not (lo <= price <= hi):
        return False, f"price {price} outside protection band [{lo:.6f}, {hi:.6f}]"
    return True, "ok"
