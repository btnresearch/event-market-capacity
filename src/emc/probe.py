"""Orchestration: snapshots in, capacity report out.

The probe applies three gates before a pair contributes a single contract of
measured capacity, in this order:

1. **Simultaneity.** Two books read even a few seconds apart can show an edge
   that never existed at one instant. Pairs whose captures are further apart than
   ``max_skew`` are reported separately and excluded, never silently merged.
2. **Settlement equivalence.** Only pairs the adjudicator can prove settle
   identically are eligible. See :mod:`emc.settlement` for why this is strict.
3. **Cost-adjusted depth.** Remaining pairs are walked level by level under an
   explicit cost model. See :mod:`emc.depth`.

Everything filtered out is counted and surfaced in the report. A probe that
reports "no capacity" is only informative if it also reports how many candidates
it rejected and why, so the report distinguishes "we looked and there was no
edge" from "we could not verify anything".
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum

from emc.depth import DEFAULT_THRESHOLDS, capacity_curve
from emc.fees import VenueCosts
from emc.models import (
    CapacityCurve,
    CapacityPoint,
    MarketRef,
    MarketSnapshot,
    MatchStatus,
    MatchVerdict,
)
from emc.settlement import MatchPolicy, adjudicate, normalize_name

__all__ = ["PairResult", "PairStatus", "ProbeReport", "run_probe"]

DEFAULT_MAX_SKEW = timedelta(seconds=2)


class PairStatus(str, Enum):
    MATCHED_WITH_CAPACITY = "matched_with_capacity"
    MATCHED_NO_CAPACITY = "matched_no_capacity"
    MISMATCHED = "mismatched"
    UNVERIFIED = "unverified"
    STALE_SKEW = "stale_skew"


@dataclass(frozen=True, slots=True)
class PairResult:
    status: PairStatus
    left: MarketRef
    right: MarketRef
    skew_seconds: Decimal
    verdict: MatchVerdict | None = None
    curve: CapacityCurve | None = None

    @property
    def counted(self) -> bool:
        return self.status is PairStatus.MATCHED_WITH_CAPACITY


@dataclass(frozen=True, slots=True)
class ProbeReport:
    generated_at: datetime
    thresholds: tuple[Decimal, ...]
    pairs: tuple[PairResult, ...]
    aggregate: tuple[CapacityPoint, ...]
    snapshots_considered: int
    candidates_considered: int
    skipped_unpairable: int
    counts: Mapping[str, int] = field(default_factory=dict)

    @property
    def has_capacity(self) -> bool:
        return any(p.contracts > 0 for p in self.aggregate)

    def aggregate_at(self, threshold: Decimal) -> CapacityPoint | None:
        for point in self.aggregate:
            if point.min_net_edge == threshold:
                return point
        return None

    def as_dict(self) -> dict:
        """JSON-ready, with Decimals rendered as strings to avoid float drift."""
        return {
            "generated_at": self.generated_at.isoformat(),
            "snapshots_considered": self.snapshots_considered,
            "candidates_considered": self.candidates_considered,
            "skipped_unpairable": self.skipped_unpairable,
            "counts": dict(self.counts),
            "aggregate": [
                {
                    "min_net_edge": str(p.min_net_edge),
                    "contracts": p.contracts,
                    "capital_usd": str(p.capital_usd),
                    "profit_usd": str(p.profit_usd),
                    "return_on_capital": (
                        None if p.return_on_capital is None else str(p.return_on_capital)
                    ),
                    "worst_net_edge": None if p.worst_net_edge is None else str(p.worst_net_edge),
                }
                for p in self.aggregate
            ],
            "pairs": [
                {
                    "status": pr.status.value,
                    "left": f"{pr.left.venue}:{pr.left.market_id}",
                    "right": f"{pr.right.venue}:{pr.right.market_id}",
                    "skew_seconds": str(pr.skew_seconds),
                    "verdict": None
                    if pr.verdict is None
                    else {"status": pr.verdict.status.value, "reasons": list(pr.verdict.reasons)},
                    "direction": None
                    if pr.curve is None
                    else f"buy {pr.curve.buy_venue} / sell {pr.curve.sell_venue}",
                }
                for pr in self.pairs
            ],
        }


def _bucket_key(snapshot: MarketSnapshot) -> tuple[str, frozenset[str]] | None:
    """Coarse key used only to avoid comparing every market against every other.

    Returns ``None`` for markets that cannot possibly adjudicate as matched
    because they publish no participants. Those are counted as unpairable rather
    than paired and rejected one by one.
    """
    terms = snapshot.settlement
    if not terms.participants:
        return None
    league = normalize_name(terms.league) if terms.league else ""
    return (league, frozenset(normalize_name(p) for p in terms.participants))


def _best_direction(
    a: MarketSnapshot,
    b: MarketSnapshot,
    costs: Mapping[str, VenueCosts],
    thresholds: Sequence[Decimal],
    default_costs: VenueCosts | None,
) -> CapacityCurve:
    """Evaluate both directions and return the one with more capacity.

    With two internally uncrossed books at most one direction can cross, but both
    are evaluated rather than inferred so that a crossed-book data fault surfaces
    as a number instead of a wrong assumption.
    """
    forward = capacity_curve(a, b, costs, thresholds, default_costs)
    reverse = capacity_curve(b, a, costs, thresholds, default_costs)
    fwd = forward.points[0].contracts if forward.points else 0
    rev = reverse.points[0].contracts if reverse.points else 0
    return forward if fwd >= rev else reverse


def run_probe(
    snapshots: Iterable[MarketSnapshot],
    costs: Mapping[str, VenueCosts],
    thresholds: Sequence[Decimal] = DEFAULT_THRESHOLDS,
    policy: MatchPolicy | None = None,
    max_skew: timedelta = DEFAULT_MAX_SKEW,
    default_costs: VenueCosts | None = None,
) -> ProbeReport:
    """Measure cross-venue capacity across a set of simultaneous snapshots."""
    snaps = list(snapshots)
    thresholds = tuple(sorted(thresholds))

    buckets: dict[tuple[str, frozenset[str]], list[MarketSnapshot]] = defaultdict(list)
    unpairable = 0
    for snap in snaps:
        key = _bucket_key(snap)
        if key is None:
            unpairable += 1
        else:
            buckets[key].append(snap)

    results: list[PairResult] = []
    candidates = 0

    for group in buckets.values():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                if a.venue == b.venue:
                    continue  # same-venue pairs are not a cross-venue measurement
                candidates += 1
                results.append(_evaluate_pair(a, b, costs, thresholds, policy, max_skew, default_costs))

    counts: dict[str, int] = {status.value: 0 for status in PairStatus}
    for res in results:
        counts[res.status.value] += 1

    return ProbeReport(
        generated_at=datetime.now(timezone.utc),
        thresholds=thresholds,
        pairs=tuple(results),
        aggregate=_aggregate_across_pairs(results, thresholds),
        snapshots_considered=len(snaps),
        candidates_considered=candidates,
        skipped_unpairable=unpairable,
        counts=counts,
    )


def _evaluate_pair(
    a: MarketSnapshot,
    b: MarketSnapshot,
    costs: Mapping[str, VenueCosts],
    thresholds: Sequence[Decimal],
    policy: MatchPolicy | None,
    max_skew: timedelta,
    default_costs: VenueCosts | None,
) -> PairResult:
    skew = abs(a.captured_at - b.captured_at)
    skew_seconds = Decimal(str(skew.total_seconds()))

    if skew > max_skew:
        return PairResult(PairStatus.STALE_SKEW, a.ref, b.ref, skew_seconds)

    verdict = adjudicate(a.settlement, b.settlement, policy)
    if verdict.status is MatchStatus.MISMATCHED:
        return PairResult(PairStatus.MISMATCHED, a.ref, b.ref, skew_seconds, verdict)
    if verdict.status is MatchStatus.UNVERIFIED:
        return PairResult(PairStatus.UNVERIFIED, a.ref, b.ref, skew_seconds, verdict)

    curve = _best_direction(a, b, costs, thresholds, default_costs)
    status = (
        PairStatus.MATCHED_WITH_CAPACITY if curve.has_capacity else PairStatus.MATCHED_NO_CAPACITY
    )
    return PairResult(status, a.ref, b.ref, skew_seconds, verdict, curve)


def _aggregate_across_pairs(
    results: Sequence[PairResult], thresholds: Sequence[Decimal]
) -> tuple[CapacityPoint, ...]:
    """Sum capacity across pairs.

    Pairs are summed as independent positions. That is the right reading for
    "how much capital fits" only if the pairs do not share a settlement event; a
    portfolio built across correlated events carries correlation the sum does not
    show. The probe reports the sum and leaves that caveat to the reader.
    """
    out: list[CapacityPoint] = []
    for t in thresholds:
        contracts = 0
        capital = Decimal(0)
        profit = Decimal(0)
        worst: Decimal | None = None
        for res in results:
            if res.curve is None:
                continue
            point = res.curve.at(t)
            if point is None or point.contracts == 0:
                continue
            contracts += point.contracts
            capital += point.capital_usd
            profit += point.profit_usd
            if point.worst_net_edge is not None:
                worst = point.worst_net_edge if worst is None else min(worst, point.worst_net_edge)
        out.append(
            CapacityPoint(
                min_net_edge=t,
                contracts=contracts,
                capital_usd=capital,
                profit_usd=profit,
                worst_net_edge=worst,
            )
        )
    return tuple(out)
