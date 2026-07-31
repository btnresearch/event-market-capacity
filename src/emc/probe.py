"""Orchestration: snapshots in, QLP report out.

Gates, in order, before a pair contributes measured capacity:

1. **Simultaneity.** Books read seconds apart can show an edge that never
   simultaneously existed. Pairs whose captures are further apart than
   ``max_skew`` are reported separately and excluded.
2. **Settlement equivalence.** Only pairs the adjudicator proves settle
   identically are eligible. See :mod:`emc.settlement`.
3. **Exact locked profit under real depth and real fees.** See :mod:`emc.locked`.
4. **Shared capital.** Simultaneous opportunities compete for one per-venue
   budget. See :mod:`emc.qlp`.

Everything rejected is counted and surfaced. "We verified this pair and there was
no locked profit" and "we could not verify anything" are different results and the
report distinguishes them.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum

from emc.fees import VenueCosts
from emc.locked import LockedQuote, no_side_ladder, optimize_locked
from emc.models import MarketRef, MarketSnapshot, MatchStatus, MatchVerdict, SettlementTerms
from emc.qlp import Allocation, Observation, allocate_capital
from emc.settlement import MatchPolicy, adjudicate, normalize_name

__all__ = ["PairResult", "PairStatus", "ProbeReport", "build_observations", "run_probe"]

DEFAULT_MAX_SKEW = timedelta(seconds=2)


class PairStatus(str, Enum):
    LOCKED_PROFIT = "locked_profit"
    MATCHED_NO_PROFIT = "matched_no_profit"
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
    quote: LockedQuote | None = None


def canonical_event_key(terms: SettlementTerms) -> str | None:
    """Stable identity for an event, or ``None`` if it cannot be identified.

    Includes the doubleheader number: same teams, same date, different game is a
    different event, and a key without it would pair game 1 against game 2.
    """
    if not terms.participants or terms.game_date is None:
        return None
    teams = "|".join(sorted(normalize_name(t) for t in terms.participants))
    league = normalize_name(terms.league) if terms.league else ""
    dh = "?" if terms.doubleheader_number is None else str(terms.doubleheader_number)
    outcome = normalize_name(terms.outcome_team) if terms.outcome_team else "?"
    return f"{league}/{terms.game_date}/dh{dh}/{teams}/on:{outcome}"


@dataclass(frozen=True, slots=True)
class ProbeReport:
    generated_at: datetime
    pairs: tuple[PairResult, ...]
    observations: tuple[Observation, ...]
    allocation: Allocation
    snapshots_considered: int
    candidates_considered: int
    skipped_unidentifiable: int
    counts: Mapping[str, int] = field(default_factory=dict)

    @property
    def uncapped_locked_profit_usd(self) -> Decimal:
        """Sum of per-pair locked profit ignoring shared capital. Theoretical only."""
        return sum((o.locked_profit_usd for o in self.observations), Decimal(0))

    @property
    def capital_constrained_locked_profit_usd(self) -> Decimal:
        return self.allocation.locked_profit_usd

    def as_dict(self) -> dict:
        return {
            "generated_at": self.generated_at.isoformat(),
            "snapshots_considered": self.snapshots_considered,
            "candidates_considered": self.candidates_considered,
            "skipped_unidentifiable": self.skipped_unidentifiable,
            "counts": dict(self.counts),
            "uncapped_locked_profit_usd": str(self.uncapped_locked_profit_usd),
            "capital_constrained_locked_profit_usd": str(
                self.capital_constrained_locked_profit_usd
            ),
            "capital_used_usd": {
                v: str(amount) for v, amount in self.allocation.capital_used.items()
            },
            "pairs": [
                {
                    "status": p.status.value,
                    "left": f"{p.left.venue}:{p.left.market_id}",
                    "right": f"{p.right.venue}:{p.right.market_id}",
                    "skew_seconds": str(p.skew_seconds),
                    "verdict": None
                    if p.verdict is None
                    else {"status": p.verdict.status.value, "reasons": list(p.verdict.reasons)},
                    "locked_profit_usd": None if p.quote is None else str(p.quote.locked_profit_usd),
                    "contracts": None if p.quote is None else p.quote.q_yes,
                }
                for p in self.pairs
            ],
        }


def build_observations(
    snapshots: Iterable[MarketSnapshot],
    costs: Mapping[str, VenueCosts],
    policy: MatchPolicy | None = None,
    max_skew: timedelta = DEFAULT_MAX_SKEW,
) -> tuple[list[Observation], list[PairResult], int, int]:
    """Pair, adjudicate, and price. Returns (observations, results, candidates, unidentifiable)."""
    snaps = list(snapshots)
    buckets: dict[str, list[MarketSnapshot]] = defaultdict(list)
    unidentifiable = 0
    for snap in snaps:
        key = canonical_event_key(snap.settlement)
        if key is None:
            unidentifiable += 1
        else:
            buckets[key].append(snap)

    observations: list[Observation] = []
    results: list[PairResult] = []
    candidates = 0

    for key, group in buckets.items():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                if a.venue == b.venue:
                    continue
                candidates += 1
                result, obs = _evaluate_pair(key, a, b, costs, policy, max_skew)
                results.append(result)
                if obs is not None:
                    observations.append(obs)

    return observations, results, candidates, unidentifiable


def _resolve(costs: Mapping[str, VenueCosts], venue: str) -> VenueCosts:
    if venue not in costs:
        raise ValueError(
            f"no cost model for venue {venue!r}; supply one rather than assuming zero cost"
        )
    return costs[venue]


def _evaluate_pair(
    pair_key: str,
    a: MarketSnapshot,
    b: MarketSnapshot,
    costs: Mapping[str, VenueCosts],
    policy: MatchPolicy | None,
    max_skew: timedelta,
) -> tuple[PairResult, Observation | None]:
    skew = abs(a.captured_at - b.captured_at)
    skew_seconds = Decimal(str(skew.total_seconds()))

    if skew > max_skew:
        return PairResult(PairStatus.STALE_SKEW, a.ref, b.ref, skew_seconds), None

    verdict = adjudicate(a.settlement, b.settlement, policy)
    if verdict.status is MatchStatus.MISMATCHED:
        return PairResult(PairStatus.MISMATCHED, a.ref, b.ref, skew_seconds, verdict), None
    if verdict.status is MatchStatus.UNVERIFIED:
        return PairResult(PairStatus.UNVERIFIED, a.ref, b.ref, skew_seconds, verdict), None

    costs_a, costs_b = _resolve(costs, a.venue), _resolve(costs, b.venue)
    observed_at = max(a.captured_at, b.captured_at)

    # Both directions: buy YES on one venue and NO on the other. With two
    # internally uncrossed books at most one can lock, but both are priced rather
    # than inferred so a crossed-book fault surfaces as a number.
    best: tuple[LockedQuote, MarketSnapshot, MarketSnapshot] | None = None
    for yes_snap, no_snap in ((a, b), (b, a)):
        yes_costs = _resolve(costs, yes_snap.venue)
        no_costs = _resolve(costs, no_snap.venue)
        quote = optimize_locked(
            yes_snap.book.asks,
            no_side_ladder(no_snap.book.bids),
            yes_costs,
            no_costs,
            yes_snap.payout_usd,
            no_snap.payout_usd,
        )
        if quote is not None and (best is None or quote.locked_profit_usd > best[0].locked_profit_usd):
            best = (quote, yes_snap, no_snap)

    if best is None:
        return (
            PairResult(PairStatus.MATCHED_NO_PROFIT, a.ref, b.ref, skew_seconds, verdict),
            None,
        )

    quote, yes_snap, no_snap = best
    observation = Observation(
        pair_key=pair_key,
        observed_at=observed_at,
        yes_ref=yes_snap.ref,
        no_ref=no_snap.ref,
        yes_ladder=yes_snap.book.asks,
        no_ladder=no_side_ladder(no_snap.book.bids),
        yes_costs=_resolve(costs, yes_snap.venue),
        no_costs=_resolve(costs, no_snap.venue),
        quote=quote,
        payout_yes_usd=yes_snap.payout_usd,
        payout_no_usd=no_snap.payout_usd,
    )
    return (
        PairResult(PairStatus.LOCKED_PROFIT, a.ref, b.ref, skew_seconds, verdict, quote),
        observation,
    )


def run_probe(
    snapshots: Iterable[MarketSnapshot],
    costs: Mapping[str, VenueCosts],
    venue_capital: Mapping[str, Decimal],
    policy: MatchPolicy | None = None,
    max_skew: timedelta = DEFAULT_MAX_SKEW,
) -> ProbeReport:
    """Measure locked-profit capacity across a set of simultaneous snapshots.

    ``venue_capital`` is required, not optional. Capacity without a capital budget
    is a number with no economic meaning, and defaulting to unlimited capital
    would silently reproduce the double-counting this metric exists to remove.
    """
    snaps = list(snapshots)
    observations, results, candidates, unidentifiable = build_observations(
        snaps, costs, policy, max_skew
    )
    counts = {status.value: 0 for status in PairStatus}
    for res in results:
        counts[res.status.value] += 1

    return ProbeReport(
        generated_at=datetime.now(timezone.utc),
        pairs=tuple(results),
        observations=tuple(observations),
        allocation=allocate_capital(observations, venue_capital),
        snapshots_considered=len(snaps),
        candidates_considered=candidates,
        skipped_unidentifiable=unidentifiable,
        counts=counts,
    )
