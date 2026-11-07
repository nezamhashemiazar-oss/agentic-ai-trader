"""TP ladder - multi-level partial take-profit.

The single-level PARTIAL method closes one fixed percentage at one ROI
threshold. A ladder replaces that with an ORDERED list of levels, each closing
a slice of the position as the trade runs in its favour.

The one rule that is easy to get wrong
--------------------------------------
``close_pct`` is a percentage of the **ORIGINAL** position size, never of the
remaining size. With a 1000-unit position and ``1R:25,2R:25,3R:25`` the levels
close 250 / 250 / 250 and leave 250 - not 250 / 187.5 / 140.6. Compounding the
percentage against the shrinking remainder is the single most common way a
ladder silently closes the wrong quantity, so the arithmetic lives in one
function (:func:`level_qtys`) and is asserted directly by the tests.

Spec syntax
-----------
``1R:25,2R:25,3R:25``      R multiples (default basis)
``50:25,100:25,150:25``    ROI percent, when the basis is ROI
``0.0055:25``              an absolute price

A level is ``<trigger>:<close_pct>``. The trigger is interpreted according to
``basis``: ``R`` (an R multiple), ``ROI`` (a percent) or ``PRICE`` (absolute).
A bare number with no unit is read as an R multiple under the R basis and as a
percent under the ROI basis, so the same spec means the obvious thing in both.

Everything here is pure: no I/O, no exchange calls, no clock. The engine owns
the side effects, which is what makes the rules testable.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: Trigger bases. R = multiple of the initial risk (entry-to-stop distance).
BASES = ("R", "ROI", "PRICE")

#: What to do with whatever is left after the last level.
RUNNERS = ("trail", "close")

#: A ladder may not close more than the whole position.
MAX_TOTAL_PCT = 100.0


class LadderError(ValueError):
    """A ladder spec that cannot be honoured. Never silently repaired."""


@dataclass(frozen=True)
class LadderLevel:
    """One rung: close ``close_pct`` of the ORIGINAL size at ``trigger``."""

    trigger: float
    close_pct: float
    #: Per-level TP trigger type override (MARK_PRICE / LAST_PRICE). None means
    #: "use the engine-wide TP_SL_TRIGGER_TYPE".
    trigger_type: str | None = None

    def as_dict(self) -> dict:
        d = {"trigger": self.trigger, "close_pct": self.close_pct}
        if self.trigger_type:
            d["trigger_type"] = self.trigger_type
        return d


@dataclass
class Ladder:
    """A parsed, validated ladder."""

    levels: list[LadderLevel]
    basis: str = "R"
    breakeven_after: int = 1
    runner: str = "trail"
    raw: str = ""

    # ------------------------------------------------------------------ derived
    @property
    def total_pct(self) -> float:
        return sum(lv.close_pct for lv in self.levels)

    @property
    def runner_pct(self) -> float:
        """Percent of the original left for the runner after every level."""
        return max(0.0, MAX_TOTAL_PCT - self.total_pct)

    def describe(self) -> str:
        if not self.levels:
            return "TP ladder: OFF (single-level PARTIAL applies)"
        rungs = ", ".join(f"{_fmt(lv.trigger)}:{_fmt(lv.close_pct)}"
                          for lv in self.levels)
        return (f"TP ladder: {rungs} (basis={self.basis}, "
                f"total={_fmt(self.total_pct)}%, "
                f"runner={_fmt(self.runner_pct)}% -> {self.runner}, "
                f"breakeven after level {self.breakeven_after})")

    def as_dict(self) -> dict:
        return {"levels": [lv.as_dict() for lv in self.levels],
                "basis": self.basis, "breakeven_after": self.breakeven_after,
                "runner": self.runner, "raw": self.raw}


def _fmt(v: float) -> str:
    return f"{v:g}"


# --------------------------------------------------------------------- parsing
def parse_ladder(spec: str, *, basis: str = "R",
                 breakeven_after: int = 1, runner: str = "trail") -> Ladder:
    """Parse ``"1R:25,2R:25,3R:25"`` into a validated :class:`Ladder`.

    Raises :class:`LadderError` with a message naming the offending rung. An
    empty spec is a valid, disabled ladder - it is how ``ladder off`` is
    expressed, and it falls back to the single-level PARTIAL method.
    """
    basis = (basis or "R").strip().upper()
    if basis not in BASES:
        raise LadderError(
            f"unknown trigger basis {basis!r}. Use one of: {', '.join(BASES)}")
    runner = (runner or "trail").strip().lower()
    if runner not in RUNNERS:
        raise LadderError(
            f"unknown runner {runner!r}. Use one of: {', '.join(RUNNERS)}")

    raw = (spec or "").strip()
    if not raw:
        return Ladder(levels=[], basis=basis, breakeven_after=breakeven_after,
                      runner=runner, raw="")

    levels: list[LadderLevel] = []
    for i, chunk in enumerate(raw.split(","), start=1):
        part = chunk.strip()
        if not part:
            raise LadderError(f"level {i}: empty rung in {raw!r}")
        if ":" not in part:
            raise LadderError(
                f"level {i}: {part!r} is missing ':' - expected "
                f"<trigger>:<close_pct>, e.g. 1R:25")
        trig_s, _, pct_s = part.partition(":")
        trig_s, pct_s = trig_s.strip(), pct_s.strip()
        if not trig_s or not pct_s:
            raise LadderError(
                f"level {i}: {part!r} needs both a trigger and a close_pct")

        trigger = _parse_trigger(trig_s, basis, i)
        try:
            close_pct = float(pct_s.rstrip("%"))
        except ValueError:
            raise LadderError(
                f"level {i}: close_pct {pct_s!r} is not a number") from None
        if close_pct <= 0:
            raise LadderError(
                f"level {i}: close_pct must be > 0, got {_fmt(close_pct)}")
        if close_pct > MAX_TOTAL_PCT:
            raise LadderError(
                f"level {i}: close_pct {_fmt(close_pct)}% exceeds "
                f"{_fmt(MAX_TOTAL_PCT)}%")
        levels.append(LadderLevel(trigger=trigger, close_pct=close_pct))

    # --- ordering: a ladder must fire in the order the trade reaches it.
    # Out-of-order rungs would make "which level fires next" ambiguous, so this
    # is refused rather than sorted - silently reordering a user's spec hides a
    # typo.
    for a, b in zip(levels, levels[1:]):
        if b.trigger <= a.trigger:
            raise LadderError(
                f"levels must be in increasing trigger order: "
                f"{_fmt(a.trigger)} then {_fmt(b.trigger)}")

    total = sum(lv.close_pct for lv in levels)
    if total > MAX_TOTAL_PCT + 1e-9:
        raise LadderError(
            f"close_pct values sum to {_fmt(total)}% - a ladder cannot close "
            f"more than {_fmt(MAX_TOTAL_PCT)}% of the position. "
            f"Reduce a level.")

    if breakeven_after < 0 or breakeven_after > len(levels):
        raise LadderError(
            f"breakeven_after={breakeven_after} is outside 0..{len(levels)} "
            f"(0 disables the breakeven move)")

    return Ladder(levels=levels, basis=basis, breakeven_after=breakeven_after,
                  runner=runner, raw=raw)


def _parse_trigger(text: str, basis: str, index: int) -> float:
    """Read one trigger, honouring an explicit R / % / price suffix."""
    t = text.strip().upper()
    explicit_r = t.endswith("R")
    explicit_pct = t.endswith("%")
    if explicit_r:
        t = t[:-1].strip()
    elif explicit_pct:
        t = t[:-1].strip()
    try:
        value = float(t)
    except ValueError:
        raise LadderError(
            f"level {index}: trigger {text!r} is not a number") from None
    if value <= 0:
        raise LadderError(
            f"level {index}: trigger must be > 0, got {_fmt(value)}")

    # An explicit suffix wins over the basis; a bare number follows the basis.
    if explicit_r:
        if basis != "R":
            raise LadderError(
                f"level {index}: {text!r} is an R multiple but the basis is "
                f"{basis}. Use a plain number or set the basis to R.")
        return value
    if explicit_pct:
        if basis != "ROI":
            raise LadderError(
                f"level {index}: {text!r} is a percent but the basis is "
                f"{basis}. Use a plain number or set the basis to ROI.")
        return value
    return value


# ------------------------------------------------------------------ the maths
def level_qtys(original_qty: float, ladder: Ladder,
               base_precision: int = 0) -> list[float]:
    """Quantity closed at each level, as a share of the ORIGINAL size.

    This is the function the whole feature hinges on. Every level is computed
    against ``original_qty`` - never against the shrinking remainder - so the
    returned quantities are independent of each other and the sum is exactly
    ``total_pct`` of the original.

    Quantities are floored to ``base_precision`` so the engine can never ask the
    exchange for more than it holds.
    """
    if original_qty <= 0:
        return []
    step = 10 ** (-int(base_precision)) if base_precision >= 0 else 1.0
    out: list[float] = []
    for lv in ladder.levels:
        raw = original_qty * lv.close_pct / 100.0
        out.append(_floor_step(raw, step))
    return out


def _floor_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    # Guard against binary-float noise (0.1 * 3 -> 0.30000000000000004).
    n = int(round(value / step, 9))
    return round(n * step, 12)


def remaining_after(original_qty: float, ladder: Ladder, fired: int,
                    base_precision: int = 0) -> float:
    """Quantity still open once the first ``fired`` levels have closed."""
    qtys = level_qtys(original_qty, ladder, base_precision)
    closed = sum(qtys[:max(0, min(fired, len(qtys)))])
    return max(0.0, round(original_qty - closed, 12))


def trigger_price_for(side: str, level: LadderLevel, *, basis: str,
                      entry: float, sl: float, leverage: int,
                      initial_risk: float = 0.0) -> float:
    """The price at which ``level`` fires. LONG moves up, SHORT moves down.

    R     - ``entry +/- risk * R`` where ``risk`` is the INITIAL risk per unit
            (``initial_risk`` when supplied, else ``|entry - sl|``). It must be
            the initial risk: once the stop moves to breakeven, ``|entry - sl|``
            collapses to ~0 and every remaining R level would land on the entry
            price and fire on the next pass.
    ROI   - the price whose ROI equals the level's percent
    PRICE - the level's trigger used verbatim
    """
    is_long = side.upper() in ("LONG", "BUY")
    if basis == "PRICE":
        return level.trigger
    if basis == "ROI":
        frac = level.trigger / 100.0 / max(1, leverage)
        return entry * (1 + frac) if is_long else entry * (1 - frac)
    risk = initial_risk if initial_risk > 0 else abs(entry - sl)
    return entry + risk * level.trigger if is_long else entry - risk * level.trigger


def roi_at(price: float, entry: float, side: str, leverage: int) -> float:
    """ROI percent at ``price`` - the same formula the engine uses elsewhere."""
    if entry <= 0:
        return 0.0
    raw = (price - entry) / entry if side.upper() in ("LONG", "BUY") else (entry - price) / entry
    return raw * leverage * 100.0


# ------------------------------------------------------------------- decisions
@dataclass
class LadderDecision:
    """What the engine should do with one position on this manage pass."""

    #: Index of the level that should fire now, or None.
    fire_index: int | None = None
    #: Quantity to close at that level (of the original size).
    fire_qty: float = 0.0
    #: True when the remainder is dust and the whole thing should be closed.
    close_remainder: bool = False
    #: True when the stop should move to breakeven on this pass.
    move_breakeven: bool = False
    #: True when the runner should be handed to the trailing stop.
    runner_trail: bool = False
    #: True when the runner should simply be closed.
    runner_close: bool = False
    reason: str = ""

    def as_dict(self) -> dict:
        return {"fire_index": self.fire_index, "fire_qty": self.fire_qty,
                "close_remainder": self.close_remainder,
                "move_breakeven": self.move_breakeven,
                "runner_trail": self.runner_trail,
                "runner_close": self.runner_close, "reason": self.reason}


def next_level(ladder: Ladder, fired: int) -> int | None:
    """Index of the next unfired level, or None when the ladder is exhausted."""
    if fired < 0:
        fired = 0
    return fired if fired < len(ladder.levels) else None


def decide(*, ladder: Ladder, side: str, entry: float, sl: float, leverage: int,
           price: float, original_qty: float, fired: int,
           base_precision: int = 0, min_trade_volume: float = 0.0,
           breakeven_done: bool = False, initial_risk: float = 0.0) -> LadderDecision:
    """Decide the single action for this pass.

    One level per pass, in order. A level fires when the price has reached its
    trigger. If closing it would leave a remainder below the symbol's
    ``minTradeVolume`` the remainder is closed too - an unclosable dust
    position is worse than a slightly early exit, because the exchange will
    reject any attempt to flatten it later.
    """
    d = LadderDecision()
    if not ladder.levels:
        d.reason = "ladder disabled"
        return d

    idx = next_level(ladder, fired)
    if idx is None:
        # Every level has fired. Whatever is left is the runner.
        if ladder.runner == "close":
            d.runner_close = True
            d.reason = "ladder exhausted - runner closes"
        else:
            d.runner_trail = True
            d.reason = "ladder exhausted - runner trails"
        return d

    level = ladder.levels[idx]
    trig = trigger_price_for(side, level, basis=ladder.basis, entry=entry,
                             sl=sl, leverage=leverage,
                             initial_risk=initial_risk)
    is_long = side.upper() in ("LONG", "BUY")
    # A level fires when the price REACHES its trigger, so the comparison needs
    # a tolerance: `entry + risk * 1` is 0.005200000000000001 for entry=0.005,
    # and a bare `>=` would refuse to fire at exactly 1R. The tolerance is
    # relative to the trigger so it scales with the price.
    tol = abs(trig) * 1e-9
    reached = (price >= trig - tol) if is_long else (price <= trig + tol)
    if not reached:
        d.reason = (f"level {idx + 1} waits for {_fmt(trig)} "
                    f"(price {_fmt(price)})")
        return d

    qtys = level_qtys(original_qty, ladder, base_precision)
    qty = qtys[idx] if idx < len(qtys) else 0.0
    remaining = remaining_after(original_qty, ladder, idx + 1, base_precision)

    # --- dust guard. If what is left after this level cannot be closed on its
    # own, close it now as part of this level instead of orphaning it.
    if remaining > 0 and min_trade_volume > 0 and remaining < min_trade_volume:
        qty = round(qty + remaining, 12)
        remaining = 0.0
        d.close_remainder = True

    if qty <= 0:
        d.reason = (f"level {idx + 1} reached but its qty rounds to 0 at "
                    f"base_precision={base_precision}")
        return d

    d.fire_index = idx
    d.fire_qty = qty
    d.reason = (f"level {idx + 1} fired at {_fmt(price)} (trigger {_fmt(trig)}): "
                f"close {_fmt(qty)} of {_fmt(original_qty)}")

    # --- breakeven after the configured level. `breakeven_after=1` means the
    # stop moves once level 1 has filled, i.e. on the pass that fires index 0.
    if (ladder.breakeven_after > 0 and not breakeven_done
            and idx + 1 >= ladder.breakeven_after):
        d.move_breakeven = True

    # --- the last level leaves the runner; hand it over immediately.
    if idx + 1 >= len(ladder.levels) and remaining > 0:
        if ladder.runner == "close":
            d.runner_close = True
        else:
            d.runner_trail = True
    return d


def ladder_from_settings(settings) -> Ladder:
    """Build the ladder from a Settings object, tolerating a bad spec.

    A malformed spec must not stop the engine from starting: it is reported and
    the ladder is disabled, which falls back to the single-level PARTIAL method.
    """
    spec = getattr(settings, "tp_ladder", "") or ""
    basis = getattr(settings, "tp_ladder_trigger_basis", "R") or "R"
    be = int(getattr(settings, "tp_ladder_breakeven_after", 1) or 0)
    runner = getattr(settings, "tp_ladder_runner", "trail") or "trail"
    try:
        return parse_ladder(spec, basis=basis, breakeven_after=be, runner=runner)
    except LadderError:
        return Ladder(levels=[], basis=str(basis).upper(),
                      breakeven_after=be, runner=str(runner).lower(), raw=spec)
