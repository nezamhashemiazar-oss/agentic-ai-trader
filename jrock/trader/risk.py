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
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal


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
    """Round to `precision` decimals, half-up.

    Deliberately NOT Python's built-in `round()`, which is banker's rounding:
    round(0.0047845, 6) == 0.004784 but round(0.0047855, 6) == 0.004786, so the
    same distance from the mark can round in either direction. For a price that
    must stay on one side of the mark that is a coin flip, and it is how a stop
    that was valid when planned lands on the wrong side when sent.
    """
    p = max(0, int(precision))
    q = Decimal(1).scaleb(-p)
    return float(Decimal(repr(float(price))).quantize(q, rounding=ROUND_HALF_UP))


def floor_price(price: float, precision: int) -> float:
    """Round DOWN to the tick. Use for anything that must stay BELOW a level."""
    p = max(0, int(precision))
    q = Decimal(1).scaleb(-p)
    return float(Decimal(repr(float(price))).quantize(q, rounding=ROUND_FLOOR))


def ceil_price(price: float, precision: int) -> float:
    """Round UP to the tick. Use for anything that must stay ABOVE a level."""
    p = max(0, int(precision))
    q = Decimal(1).scaleb(-p)
    return float(Decimal(repr(float(price))).quantize(q, rounding=ROUND_CEILING))


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
    """Format a price to the symbol's quotePrecision as a string.

    Uses half-up rounding, matching `round_price`. A price that has already been
    clamped to the correct side of the mark must not be re-rounded the other way
    here - that would undo the clamp after it was validated.
    """
    p = max(0, int(rules.quote_precision))
    if p == 0:
        return str(int(round_price(price, 0)))
    return f"{round_price(price, p):.{p}f}"


def qty_from_notional(notional_usd: float, price: float, rules: PairRules) -> float:
    """Base-coin qty for a given USD notional."""
    if price <= 0:
        return 0.0
    return round_step(notional_usd / price, rules.base_precision)


def notional_from_qty(qty: float, price: float) -> float:
    return qty * price


def margin_required(notional: float, leverage: int) -> float:
    return notional / max(1, leverage)


#: Bitunix USDT-M futures taker fee at VIP 0, as a fraction of notional.
#: A MARKET order is always a taker order, so this is charged on ENTRY and is
#: deducted from the available balance at the same moment the margin is locked.
#: Ignoring it is what produced 20003: the engine sized the order so that
#: margin == available balance exactly, the local pre-flight passed (it only
#: compared margin), and the exchange then wanted margin + fee and refused.
TAKER_FEE_RATE = 0.0006


def fee_for_notional(notional: float, fee_rate: float = TAKER_FEE_RATE) -> float:
    """Entry fee charged on a MARKET order of `notional` USDT."""
    return max(0.0, notional) * max(0.0, fee_rate)


def max_notional_for_balance(equity: float, leverage: int,
                             fee_rate: float = TAKER_FEE_RATE,
                             safety_pct: float = 0.0) -> float:
    """Largest notional whose margin PLUS entry fee still fits `equity`.

    Opening a position costs ``notional/leverage`` of margin and
    ``notional*fee_rate`` of taker fee, both taken from the available balance::

        notional/leverage + notional*fee_rate <= equity
        notional <= equity / (1/leverage + fee_rate)

    `safety_pct` reserves extra headroom (percent of equity) so a price move
    between sizing and the request landing cannot push the cost back over the
    balance. Without the fee term this returns ``equity*leverage``, which is
    exactly the value that made margin == equity and triggered 20003.
    """
    if equity <= 0 or leverage <= 0:
        return 0.0
    lev = max(1, int(leverage))
    usable = equity * (1.0 - max(0.0, min(0.9, safety_pct / 100.0)))
    denom = 1.0 / lev + max(0.0, fee_rate)
    if denom <= 0:
        return 0.0
    return usable / denom


def required_cost(notional: float, leverage: int,
                  fee_rate: float = TAKER_FEE_RATE) -> float:
    """Total balance an order consumes: initial margin + entry fee."""
    return margin_required(notional, leverage) + fee_for_notional(notional, fee_rate)


def spendable_balance(account_row: dict) -> float:
    """The balance a NEW order may spend, from a get_single_account row.

    Bitunix documents `available` as the "Available quantity in the account"
    and states "this field + crossUnrealizedPNL = Actual maximum open amount".
    `available` already excludes the margin locked by open positions and the
    `frozen` amount locked by resting orders, so it is the correct number to
    size against.

    Total equity (available + margin + unrealized) is NOT: sizing against it
    makes the engine believe it can afford an order the exchange then refuses
    with 20003 Insufficient balance. `crossUnrealizedPNL` is spendable only for
    CROSS positions; the engine defaults to ISOLATION, so it is deliberately
    not added here.
    """
    if not isinstance(account_row, dict):
        return 0.0
    try:
        return max(0.0, float(account_row.get("available", 0) or 0))
    except (TypeError, ValueError):
        return 0.0


def tier_leverage_violation(tiers: list["Tier"], notional: float,
                            leverage: int) -> str:
    """Empty string when `leverage` is allowed for `notional`, else the reason.

    The tier a position's notional lands in caps the leverage that may be set
    for it. Checking this against the ACTUAL order notional is what keeps a
    valid order from being refused: checking it against `available * leverage`
    (a notional far larger than the order) lands in a higher tier and produces
    a bogus 20003 Insufficient balance for an order that would have been fine.
    """
    t = pick_tier(tiers, notional)
    if leverage <= t.leverage:
        return ""
    return (f"leverage {leverage}x exceeds the maximum {t.leverage}x allowed for "
            f"a {notional:.2f} USDT position (risk tier {t.level}). The exchange "
            f"would require {margin_required(notional, t.leverage):.4f} USDT "
            f"margin, not {margin_required(notional, leverage):.4f}. "
            f"Lower the leverage or reduce the position size.")


def tier_for_leverage(tiers: list["Tier"], leverage: int) -> "Tier | None":
    """The largest tier whose leverage cap still permits `leverage`.

    Used to build the PROVISIONAL plan that sizes the order. The tier the final
    notional lands in is checked separately (`tier_leverage_violation`); this
    one only has to be a band where the configured leverage is valid, so the
    liquidation formula does not degenerate while the size is computed.
    Returns None when no tier allows the leverage at any size.
    """
    if not tiers:
        return Tier(1, 0.0, float("inf"), 125, 0.004)
    best: "Tier | None" = None
    for t in tiers:
        if t.leverage >= leverage:
            if best is None or t.end_value > best.end_value:
                best = t
    return best


#: The three order units Bitunix documents in
#: "Explanation of the Order Units in Futures Trading":
#:   QUANTITY - base-coin quantity (the `qty` field of place_order)
#:   NOTIONAL - position value in USDT
#:   COST     - margin paid in USDT (initial margin, before fees)
ORDER_UNITS = ("QUANTITY", "NOTIONAL", "COST")


def qty_from_unit(amount: float, unit: str, price: float, leverage: int,
                  rules: PairRules) -> float:
    """Convert an order size expressed in one of the three documented units
    into the base-coin quantity that place_order's `qty` field requires.

        QUANTITY  qty = amount
        NOTIONAL  qty = amount / price
        COST      qty = amount * leverage / price

    The wire format is always base-coin qty; the unit only changes how the
    caller specifies the size. An unknown unit raises rather than guessing.
    """
    u = (unit or "QUANTITY").strip().upper()
    if u not in ORDER_UNITS:
        raise ValueError(
            f"unknown order unit {unit!r}. Allowed: {', '.join(ORDER_UNITS)}.")
    if price <= 0:
        return 0.0
    if u == "QUANTITY":
        raw = amount
    elif u == "NOTIONAL":
        raw = amount / price
    else:  # COST
        raw = amount * max(1, leverage) / price
    return round_step(raw, rules.base_precision)


def unit_from_qty(qty: float, unit: str, price: float, leverage: int) -> float:
    """Inverse of qty_from_unit: express a base-coin qty in the given unit."""
    u = (unit or "QUANTITY").strip().upper()
    if u not in ORDER_UNITS:
        raise ValueError(
            f"unknown order unit {unit!r}. Allowed: {', '.join(ORDER_UNITS)}.")
    notional = qty * price
    if u == "QUANTITY":
        return qty
    if u == "NOTIONAL":
        return notional
    return margin_required(notional, leverage)


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


def max_leverage_for_stop(*, entry: float, mark: float, side: str,
                          maintenance_margin_rate: float,
                          rules: "PairRules", min_ticks: int = 3) -> int:
    """Highest leverage at which a valid stop-loss can still fit.

    A stop must sit strictly between the liquidation price and the mark-side
    limit, keeping `min_ticks` clear of both. The limit is the WORSE of the
    entry and the mark, exactly as `clamp_sl` uses it:

        LONG : limit = min(entry, mark) - d,  need liq < limit
        SHORT: limit = max(entry, mark) + d,  need liq > limit

    At high leverage the liquidation price creeps toward the entry, and once it
    crosses that limit there is no room left at all - the position would be
    liquidated the moment it opened. Solving the liquidation formula for L:

        LONG : L < 1 / (1 + mmr - limit/entry)
        SHORT: L < 1 / (limit/entry - 1 + mmr)

    Returns 0 when no leverage works, which happens only when the mark has
    moved past the entry by more than the maintenance margin allows (at 1x the
    liquidation price is just `mmr` away from the entry).
    """
    if entry <= 0 or mark <= 0:
        return 0
    d = min_stop_distance(rules, min_ticks)
    mmr = max(0.0, maintenance_margin_rate)
    if side.upper() in ("LONG", "BUY"):
        limit = min(entry, mark) - d
        denom = 1.0 + mmr - limit / entry
    else:
        limit = max(entry, mark) + d
        denom = limit / entry - 1.0 + mmr
    if denom <= 0:
        return 0
    return max(0, int(1.0 / denom))


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


def margin_budget(equity: float, margin_pct: float,
                  safety_pct: float = 0.0) -> float:
    """The margin a single order may lock, as a percent of the spendable balance.

    ``MARGIN_AMOUNT_PCT`` is the user-facing knob: "risk at most this share of
    my available balance as margin on one position". It is deliberately a
    MARGIN budget, not a notional budget, because margin is what the exchange
    actually deducts from `available` when the order lands - so the number the
    user sets is the number they see disappear from their balance.

    The safety headroom is applied here as well as in
    ``max_notional_for_balance`` so the two caps agree: the budget is already
    net of the reserve, and the notional cap derived from the same equity is
    too, so neither can be the looser of the two by accident.

    ``margin_pct <= 0`` disables the budget (returns the whole equity), which
    keeps the pre-existing behaviour for anyone who has not set the knob.
    """
    if equity <= 0:
        return 0.0
    usable = equity * (1.0 - max(0.0, min(0.9, safety_pct / 100.0)))
    if margin_pct <= 0:
        return usable
    return usable * (min(100.0, margin_pct) / 100.0)


def max_notional_for_margin(margin_usdt: float, leverage: int,
                            fee_rate: float = TAKER_FEE_RATE) -> float:
    """Largest notional whose margin PLUS entry fee fits `margin_usdt`.

    Same arithmetic as ``max_notional_for_balance``, but the budget is an
    explicit margin amount rather than the whole balance::

        notional/leverage + notional*fee_rate <= margin_usdt
        notional <= margin_usdt / (1/leverage + fee_rate)

    The fee term is kept because the exchange charges margin and fee together
    from the same balance; dropping it is what produced 20003.
    """
    if margin_usdt <= 0 or leverage <= 0:
        return 0.0
    lev = max(1, int(leverage))
    denom = 1.0 / lev + max(0.0, fee_rate)
    if denom <= 0:
        return 0.0
    return margin_usdt / denom


def position_size_by_margin(equity: float, margin_pct: float, entry: float,
                            leverage: int, rules: PairRules,
                            fee_rate: float = TAKER_FEE_RATE,
                            safety_pct: float = 0.0) -> float:
    """Base-coin qty whose INITIAL MARGIN is ``margin_pct``% of the balance.

    This is the TARGET form of ``MARGIN_AMOUNT_PCT``: the user says "use 10% of
    my balance as margin on this position", and the size is derived FROM that
    number rather than merely capped by it.

    ``position_size_by_risk`` treats the same setting as an upper bound, and
    that is why the configured percentage looked like it did nothing: the
    risk-based size was almost always the smaller of the two, so the margin
    actually locked was whatever risk sizing happened to produce (5% when the
    user asked for 10%, and so on). A cap that never binds is indistinguishable
    from a setting that is ignored.

        margin_target = equity * pct/100
        notional      = margin_target / (1/leverage + fee_rate)
        qty           = notional / entry

    The fee term is kept for the same reason as everywhere else: margin and fee
    are deducted together, so a notional that spends the whole margin budget on
    margin alone would be refused with 20003.
    """
    if equity <= 0 or entry <= 0 or margin_pct <= 0:
        return 0.0
    budget = margin_budget(equity, margin_pct, safety_pct)
    notional = max_notional_for_margin(budget, leverage, fee_rate)
    if notional <= 0:
        return 0.0
    qty = round_step(notional / entry, rules.base_precision)
    if qty < rules.min_trade_volume:
        return 0.0
    if rules.max_market_order_volume and qty > rules.max_market_order_volume:
        qty = round_step(rules.max_market_order_volume, rules.base_precision)
    return qty


def position_size_by_risk(equity: float, risk_pct: float, entry: float,
                          stop: float, leverage: int, rules: PairRules,
                          fee_rate: float = TAKER_FEE_RATE,
                          safety_pct: float = 0.0,
                          margin_pct: float = 0.0) -> float:
    """Size so that hitting the stop loses ~risk_pct of equity.

    The size is capped by what the balance can actually PAY FOR, not by
    ``equity * leverage``. Opening a MARKET order costs margin AND a taker fee,
    so the cap is ``max_notional_for_balance`` - using ``equity * leverage``
    made margin equal the whole balance, the local pre-flight passed, and the
    exchange rejected the order with 20003 once it added the fee.

    ``margin_pct`` (``MARGIN_AMOUNT_PCT``) adds a SECOND, tighter cap: the
    margin locked by this one order may not exceed that percent of the
    spendable balance. It composes with the balance cap by taking the MINIMUM
    of the two notionals, so it can only ever make the order smaller - it can
    never talk the engine into an order the balance cannot pay for. The
    risk-based size is applied first and then clamped, so a wide stop still
    cannot push the margin past the budget.
    """
    stop_dist = abs(entry - stop)
    if stop_dist <= 0 or entry <= 0:
        return 0.0
    risk_usd = equity * (risk_pct / 100.0)
    qty = risk_usd / stop_dist
    # Cap 1: what the whole spendable balance can pay for (margin + fee).
    max_notional = max_notional_for_balance(equity, leverage, fee_rate, safety_pct)
    # Cap 2: the configured share of the balance, as margin. Never looser than
    # cap 1, because both are derived from the same equity and the same reserve.
    if margin_pct > 0:
        budget = margin_budget(equity, margin_pct, safety_pct)
        max_notional = min(max_notional,
                           max_notional_for_margin(budget, leverage, fee_rate))
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


def clamp_sl(*, side: str, sl: float, entry: float, mark: float,
             rules: "PairRules", min_ticks: int = 3,
             liq: float | None = None) -> float:
    """Force a stop-loss onto the correct side of BOTH entry and mark.

    This is THE single place a stop-loss price is made safe, and every path that
    sends an SL must go through it - the entry plan (`plan_tpsl`) and the
    running-engine updates (breakeven, trailing) alike. The previous fix only
    covered `plan_tpsl`, so the management path kept sending stops that were
    never checked against the mark price at all.

    Rounding is direction-aware: a LONG stop is floored and a SHORT stop is
    ceiled, so rounding can only ever move the stop FURTHER from the mark. Plain
    nearest-rounding can move it back across the mark by up to half a tick,
    which is exactly the 30029/30031 rejection.
    """
    p = max(0, int(rules.quote_precision))
    d = min_stop_distance(rules, min_ticks)
    tick = tick_size(rules)
    is_long = side.upper() in ("LONG", "BUY")

    # The exchange requires the stop to be STRICTLY beyond the limit, so the
    # clamp must land strictly beyond it too - a stop that sits exactly on the
    # limit is still rejected (30023/30029).
    #
    # The liquidation guard only applies when `liq` is on the side a real
    # liquidation would be (below entry for a LONG, above it for a SHORT). A
    # liq price on the wrong side is stale or belongs to the other direction;
    # honouring it would push the stop across the mark and cause the very
    # rejection this function exists to prevent.
    if is_long:
        limit = min(entry, mark) - d
        out = floor_price(min(sl, limit), p)
        if out >= limit:
            out = floor_price(limit - tick, p)
        if liq is not None and 0 < liq < entry and out <= liq:
            out = ceil_price(liq + d, p)
        return out

    limit = max(entry, mark) + d
    out = ceil_price(max(sl, limit), p)
    if out <= limit:
        out = ceil_price(limit + tick, p)
    if liq is not None and liq > entry and out >= liq:
        out = floor_price(liq - d, p)
    return out


def clamp_tp(*, side: str, tp: float, entry: float, mark: float,
             rules: "PairRules", min_ticks: int = 3) -> float:
    """Force a take-profit onto the correct side of BOTH entry and mark.

    Mirror of `clamp_sl`: a LONG TP is ceiled and a SHORT TP is floored, so
    rounding only ever moves the target further from the mark.
    """
    p = max(0, int(rules.quote_precision))
    d = min_stop_distance(rules, min_ticks)
    tick = tick_size(rules)
    if side.upper() in ("LONG", "BUY"):
        limit = max(entry, mark) + d
        out = ceil_price(max(tp, limit), p)
        if out <= limit:
            out = ceil_price(limit + tick, p)
        return out
    limit = min(entry, mark) - d
    out = floor_price(min(tp, limit), p)
    if out >= limit:
        out = floor_price(limit - tick, p)
    return out


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

    # A non-positive price means "not set" (PositionState defaults sl/tp to 0.0
    # for a position the engine did not open itself). Treating 0.0 as a real
    # price would flag every such position as a violation.
    if sl is not None and sl <= 0:
        sl = None
    if tp is not None and tp <= 0:
        tp = None

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
