"""Multi-timeframe, multi-strategy aggregation.

Rules (user-configurable):
  * a strategy vote counts only if its confidence >= tf_min_confidence for that TF
  * a timeframe is "valid" only if its best confidence >= tf_min_confidence
  * a trade needs >= min_agreeing strategies on the SAME direction
  * the aggregate confidence must be >= min_confidence
  * confidence is a weighted blend of agreement count, mean confidence and TF spread
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .strategies import Signal

TF_WEIGHTS = {"1m": 0.6, "3m": 0.8, "5m": 1.0, "15m": 1.3, "30m": 1.5,
              "1h": 1.7, "2h": 1.8, "4h": 2.0, "1d": 2.2}


@dataclass
class Aggregate:
    symbol: str
    direction: str = "NEUTRAL"
    confidence: float = 0.0
    agreeing: int = 0
    opposing: int = 0
    per_tf: dict[str, list[Signal]] = field(default_factory=dict)
    votes_long: list[Signal] = field(default_factory=list)
    votes_short: list[Signal] = field(default_factory=list)
    reason: str = ""
    strength: str = "none"     # none | weak | normal | strong | extreme

    @property
    def actionable(self) -> bool:
        return self.direction in ("LONG", "SHORT")

    def summary(self) -> str:
        return (f"{self.symbol} {self.direction} conf={self.confidence:.1f} "
                f"agree={self.agreeing} oppose={self.opposing} strength={self.strength}")


def _strength(conf: float, agreeing: int) -> str:
    if conf >= 90 and agreeing >= 5:
        return "extreme"
    if conf >= 82:
        return "strong"
    if conf >= 70:
        return "normal"
    if conf >= 60:
        return "weak"
    return "none"


def aggregate(symbol: str, per_tf: dict[str, list[Signal]], *,
              min_agreeing: int = 3, min_confidence: float = 70.0,
              tf_min_confidence: float = 60.0) -> Aggregate:
    agg = Aggregate(symbol=symbol, per_tf=per_tf)

    long_votes: list[Signal] = []
    short_votes: list[Signal] = []
    for tf, signals in per_tf.items():
        for s in signals:
            if not s.is_actionable:
                continue
            if s.confidence < tf_min_confidence:
                continue
            if s.direction == "LONG":
                long_votes.append(s)
            else:
                short_votes.append(s)

    # de-duplicate: one vote per (strategy, timeframe) - keep the strongest
    def dedupe(votes: list[Signal]) -> list[Signal]:
        best: dict[tuple[str, str], Signal] = {}
        for s in votes:
            k = (s.strategy, s.timeframe)
            if k not in best or s.confidence > best[k].confidence:
                best[k] = s
        return list(best.values())

    long_votes = dedupe(long_votes)
    short_votes = dedupe(short_votes)

    # count distinct strategies (a strategy agreeing on 3 TFs is still 1 strategy,
    # but multi-TF agreement raises confidence)
    def distinct(votes: list[Signal]) -> int:
        return len({s.strategy for s in votes})

    n_long, n_short = distinct(long_votes), distinct(short_votes)
    if n_long == 0 and n_short == 0:
        agg.reason = "no actionable signals above tf_min_confidence"
        return agg

    if n_long >= n_short:
        direction, votes, opposing = "LONG", long_votes, short_votes
        agg.agreeing, agg.opposing = n_long, n_short
    else:
        direction, votes, opposing = "SHORT", short_votes, long_votes
        agg.agreeing, agg.opposing = n_short, n_long

    agg.direction = direction
    agg.votes_long, agg.votes_short = long_votes, short_votes

    if agg.agreeing < min_agreeing:
        agg.direction = "NEUTRAL"
        agg.reason = f"only {agg.agreeing} agreeing strategies (need {min_agreeing})"
        return agg

    # weighted confidence
    total_w = 0.0
    weighted = 0.0
    tfs_hit: set[str] = set()
    for s in votes:
        w = TF_WEIGHTS.get(s.timeframe, 1.0)
        weighted += s.confidence * w
        total_w += w
        tfs_hit.add(s.timeframe)
    mean_conf = weighted / total_w if total_w else 0.0

    agreement_bonus = min(12.0, (agg.agreeing - min_agreeing) * 3.0)
    tf_bonus = min(8.0, (len(tfs_hit) - 1) * 2.5)
    conflict_penalty = min(20.0, agg.opposing * 4.0)

    conf = mean_conf + agreement_bonus + tf_bonus - conflict_penalty
    conf = max(0.0, min(100.0, conf))
    agg.confidence = conf
    agg.strength = _strength(conf, agg.agreeing)

    if conf < min_confidence:
        agg.direction = "NEUTRAL"
        agg.reason = f"confidence {conf:.1f} < min {min_confidence}"
        return agg

    agg.reason = (f"{agg.agreeing} strategies agree {direction} across "
                  f"{len(tfs_hit)} TF(s), mean {mean_conf:.1f}, conf {conf:.1f}")
    return agg
