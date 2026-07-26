"""QLP: fee-net, full-depth, settlement-matched, delay-surviving quoted locked
profit per slate, subject to a capital cap.

Three properties of this metric are easy to get wrong and are handled explicitly.

CAPITAL IS SHARED
-----------------
Two simultaneous opportunities on the same venue compete for the same balance.
Summing per-pair capacity double-counts it. An earlier version of this repository
did exactly that: with a 1,000-contract cap on each venue it reported 2,000
contracts across two simultaneous pairs. Allocation here is a single pass over a
shared per-venue budget, and an opportunity that does not fit is re-optimized
against the capital that actually remains rather than being taken at full size.

A CONTINUOUSLY STALE PRICE IS ONE OPPORTUNITY
---------------------------------------------
Polling a stale quote every 500ms for a minute does not produce 120
opportunities. Observations are collapsed into episodes keyed by the pair, and an
episode contributes its peak locked profit once. Counting polls would inflate QLP
by the polling rate, which is a measurement artifact of our own choosing.

QUOTED IS NOT REALIZED
----------------------
Everything here is quoted opportunity: what the book showed. It is an upper bound
on what could have been filled, and nothing in this module should be read as
revenue. Realized capture requires fills, settlement, and cash reconciliation.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from emc.fees import VenueCosts
from emc.locked import LockedQuote, optimize_locked
from emc.models import BookLevel, MarketRef

__all__ = [
    "Allocation",
    "Episode",
    "Observation",
    "allocate_capital",
    "collapse_episodes",
    "qlp_from_episodes",
]


@dataclass(frozen=True, slots=True)
class Observation:
    """One candidate locked position seen at one instant.

    Carries its ladders and cost models so the allocator can re-price it at a
    smaller size against remaining capital instead of taking it or dropping it
    whole.
    """

    pair_key: str
    observed_at: datetime
    yes_ref: MarketRef
    no_ref: MarketRef
    yes_ladder: tuple[BookLevel, ...]
    no_ladder: tuple[BookLevel, ...]
    yes_costs: VenueCosts
    no_costs: VenueCosts
    quote: LockedQuote
    payout_yes_usd: Decimal = Decimal("1")
    payout_no_usd: Decimal = Decimal("1")

    @property
    def locked_profit_usd(self) -> Decimal:
        return self.quote.locked_profit_usd

    def reprice(self, capital_available: Mapping[str, Decimal]) -> LockedQuote | None:
        """Re-optimize under per-venue capital that actually remains."""
        yes_cap = capital_available.get(self.yes_costs.venue)
        no_cap = capital_available.get(self.no_costs.venue)
        yes_costs = _with_capital(self.yes_costs, yes_cap)
        no_costs = _with_capital(self.no_costs, no_cap)
        return optimize_locked(
            self.yes_ladder,
            self.no_ladder,
            yes_costs,
            no_costs,
            self.payout_yes_usd,
            self.payout_no_usd,
        )


def _with_capital(costs: VenueCosts, available: Decimal | None) -> VenueCosts:
    from dataclasses import replace

    if available is None:
        return costs
    limit = available if costs.max_capital_usd is None else min(costs.max_capital_usd, available)
    return replace(costs, max_capital_usd=limit)


@dataclass(frozen=True, slots=True)
class Allocation:
    """The result of fitting simultaneous opportunities into a shared budget."""

    taken: tuple[tuple[Observation, LockedQuote], ...] = ()
    skipped: tuple[tuple[Observation, str], ...] = ()
    capital_used: Mapping[str, Decimal] = field(default_factory=dict)

    @property
    def locked_profit_usd(self) -> Decimal:
        return sum((q.locked_profit_usd for _, q in self.taken), Decimal(0))

    @property
    def capital_usd(self) -> Decimal:
        return sum(self.capital_used.values(), Decimal(0))

    @property
    def return_on_capital(self) -> Decimal | None:
        if self.capital_usd <= 0:
            return None
        return self.locked_profit_usd / self.capital_usd


def allocate_capital(
    observations: Iterable[Observation],
    venue_capital: Mapping[str, Decimal],
) -> Allocation:
    """Fit simultaneous opportunities into a shared per-venue budget.

    Opportunities are considered in descending return on capital, so scarce
    capital goes to its most productive use first. This is greedy, not optimal —
    the exact problem is a multi-dimensional knapsack — and greedy-by-ratio can
    trail the optimum. It is used deliberately: it never overstates capacity,
    which is the direction that matters for a screen designed to kill hypotheses.

    A venue absent from ``venue_capital`` is treated as having no budget rather
        than unlimited budget, so a missing balance cannot manufacture capacity.
    """
    remaining: dict[str, Decimal] = {v: Decimal(c) for v, c in venue_capital.items()}
    taken: list[tuple[Observation, LockedQuote]] = []
    skipped: list[tuple[Observation, str]] = []
    used: dict[str, Decimal] = {v: Decimal(0) for v in remaining}

    ordered = sorted(
        observations,
        key=lambda o: (o.quote.return_on_capital or Decimal(0), o.locked_profit_usd),
        reverse=True,
    )

    for obs in ordered:
        venues = {obs.yes_costs.venue, obs.no_costs.venue}
        unknown = venues - set(remaining)
        if unknown:
            skipped.append((obs, f"no capital budget for venue(s) {sorted(unknown)}"))
            continue

        quote = obs.quote
        needed = quote.capital_per_venue
        if any(needed.get(v, Decimal(0)) > remaining[v] for v in venues):
            quote = obs.reprice(remaining)
            if quote is None:
                skipped.append((obs, "no positive locked profit within remaining capital"))
                continue
            needed = quote.capital_per_venue
            if any(needed.get(v, Decimal(0)) > remaining[v] for v in venues):
                skipped.append((obs, "still exceeds remaining capital after repricing"))
                continue

        for venue, amount in needed.items():
            remaining[venue] -= amount
            used[venue] = used.get(venue, Decimal(0)) + amount
        taken.append((obs, quote))

    return Allocation(
        taken=tuple(taken),
        skipped=tuple(skipped),
        capital_used={v: amount for v, amount in used.items() if amount > 0},
    )


@dataclass(frozen=True, slots=True)
class Episode:
    """A contiguous run of observations of the same opportunity.

    ``peak_locked_profit_usd`` is what the episode contributes to QLP. Summing
    every poll would multiply the answer by the polling rate.
    """

    pair_key: str
    first_seen: datetime
    last_seen: datetime
    observation_count: int
    peak_locked_profit_usd: Decimal
    peak_at: datetime

    @property
    def duration_seconds(self) -> Decimal:
        return Decimal(str((self.last_seen - self.first_seen).total_seconds()))


def collapse_episodes(
    observations: Sequence[Observation],
    max_gap: timedelta = timedelta(seconds=5),
) -> tuple[Episode, ...]:
    """Group observations into episodes, splitting on gaps longer than ``max_gap``.

    Two appearances of the same mispricing separated by a long quiet period are
    two episodes; consecutive polls of one continuously stale price are one.
    """
    by_pair: dict[str, list[Observation]] = {}
    for obs in observations:
        by_pair.setdefault(obs.pair_key, []).append(obs)

    episodes: list[Episode] = []
    for pair_key, obs_list in by_pair.items():
        obs_list.sort(key=lambda o: o.observed_at)
        run: list[Observation] = []
        for obs in obs_list:
            if run and (obs.observed_at - run[-1].observed_at) > max_gap:
                episodes.append(_episode_from(pair_key, run))
                run = []
            run.append(obs)
        if run:
            episodes.append(_episode_from(pair_key, run))

    episodes.sort(key=lambda e: (e.first_seen, e.pair_key))
    return tuple(episodes)


def _episode_from(pair_key: str, run: Sequence[Observation]) -> Episode:
    peak = max(run, key=lambda o: o.locked_profit_usd)
    return Episode(
        pair_key=pair_key,
        first_seen=run[0].observed_at,
        last_seen=run[-1].observed_at,
        observation_count=len(run),
        peak_locked_profit_usd=peak.locked_profit_usd,
        peak_at=peak.observed_at,
    )


def qlp_from_episodes(episodes: Iterable[Episode]) -> Decimal:
    """Sum of each episode's peak locked profit. Quoted, not realized."""
    return sum((e.peak_locked_profit_usd for e in episodes), Decimal(0))
