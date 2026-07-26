"""The measurement: how much capital fits at a given net edge.

A settlement-matched pair supports a two-leg position. Buy YES on the cheaper
venue at its ask ``a``; take the opposite side on the richer venue at its bid
``b`` (executed there as buying NO at ``1 - b``). Cash out per contract is
``a + (1 - b) = 1 - (b - a)``, and the pair returns exactly the payout at
settlement because the two legs resolve on the same event. So:

* gross edge per contract  = ``b - a``
* capital tied up          = ``(1 - (b - a)) * payout``
* profit                   = ``(b - a) * payout - fees``

Two properties of the walk are worth stating because they are where naive
implementations go wrong.

First, capacity is not the top of book. The edge at the touch says nothing about
how much size sits behind it, and the mission's question is specifically about
depth, so every level on both sides is consumed and priced separately.

Second, net edge is not monotone in depth. Gross edge does decay monotonically as
both books are consumed, but per-contract cost is not constant: a fixed per-fill
cost is spread over the size of the slice, so a thin slice at the touch can be
uneconomic while a thick slice one level deeper clears comfortably. A single
contract at a 3-cent gross edge loses money against a 5-cent fill cost; a
thousand contracts at 1 cent do not. Walking the book and stopping at the first
slice that fails a threshold would therefore report zero capacity where real
capacity exists, so this module prices every slice, ranks by realized net edge,
and takes the qualifying set. That is correct under any cost model rather than
only under a strictly proportional one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from emc.fees import VenueCosts
from emc.models import BookLevel, CapacityCurve, CapacityPoint, MarketSnapshot

__all__ = ["DEFAULT_THRESHOLDS", "MatchedSlice", "capacity_curve"]

# Net edge per contract, in dollars on a $1 payout. 0 is break-even after fees;
# 0.005 is half a cent; 0.05 is five cents.
DEFAULT_THRESHOLDS: tuple[Decimal, ...] = (
    Decimal("0"),
    Decimal("0.0025"),
    Decimal("0.005"),
    Decimal("0.01"),
    Decimal("0.02"),
    Decimal("0.05"),
)


@dataclass(frozen=True, slots=True)
class MatchedSlice:
    """One price-level pairing: ``contracts`` buyable at ``buy_price`` while
    simultaneously sellable at ``sell_price``, with costs already applied."""

    contracts: int
    buy_price: Decimal
    sell_price: Decimal
    capital_usd: Decimal
    profit_usd: Decimal
    fees_usd: Decimal

    @property
    def gross_edge(self) -> Decimal:
        return self.sell_price - self.buy_price

    @property
    def net_edge(self) -> Decimal:
        """Profit per contract, in the same units as the thresholds."""
        if self.contracts == 0:
            return Decimal(0)
        return self.profit_usd / Decimal(self.contracts)


def _evaluate(
    buy_price: Decimal,
    sell_price: Decimal,
    contracts: int,
    buy_costs: VenueCosts,
    sell_costs: VenueCosts,
    payout_usd: Decimal,
) -> MatchedSlice:
    """Price one slice of matched depth.

    Each leg's fee is assessed on the cash actually paid on that venue: ``buy_price``
    on the cheap side, ``1 - sell_price`` on the rich side, since taking the
    opposite side there is a NO purchase. Under a ``price * (1 - price)`` fee the
    two are identical by symmetry; under a notional-proportional fee they are not,
    and cash-paid is the treatment that matches how venues bill.

    Every price level is costed as its own fill. That is conservative: it charges
    any per-fill fixed cost once per level rather than once per aggregate order.
    """
    gross_per_contract = sell_price - buy_price
    capital = (Decimal(1) - gross_per_contract) * payout_usd * Decimal(contracts)
    fees = buy_costs.cost_usd(buy_price, contracts) + sell_costs.cost_usd(
        Decimal(1) - sell_price, contracts
    )
    profit = gross_per_contract * payout_usd * Decimal(contracts) - fees
    return MatchedSlice(
        contracts=contracts,
        buy_price=buy_price,
        sell_price=sell_price,
        capital_usd=capital,
        profit_usd=profit,
        fees_usd=fees,
    )


def _walk(
    asks: Sequence[BookLevel],
    bids: Sequence[BookLevel],
    buy_costs: VenueCosts,
    sell_costs: VenueCosts,
    payout_usd: Decimal,
) -> list[MatchedSlice]:
    """Consume both books level by level while the cross is positive."""
    slices: list[MatchedSlice] = []
    i = j = 0
    ask_left = asks[0].size if asks else 0
    bid_left = bids[0].size if bids else 0

    while i < len(asks) and j < len(bids):
        ask, bid = asks[i], bids[j]
        if bid.price <= ask.price:
            break  # books no longer cross; nothing deeper can cross either
        take = min(ask_left, bid_left)
        if take > 0:
            slices.append(
                _evaluate(ask.price, bid.price, take, buy_costs, sell_costs, payout_usd)
            )
        ask_left -= take
        bid_left -= take
        if ask_left == 0:
            i += 1
            ask_left = asks[i].size if i < len(asks) else 0
        if bid_left == 0:
            j += 1
            bid_left = bids[j].size if j < len(bids) else 0

    return slices


def _effective_cap(buy_costs: VenueCosts, sell_costs: VenueCosts) -> int | None:
    caps = [c for c in (buy_costs.max_contracts, sell_costs.max_contracts) if c is not None]
    return min(caps) if caps else None


def _aggregate(
    slices: Sequence[MatchedSlice],
    threshold: Decimal,
    cap: int | None,
    buy_costs: VenueCosts,
    sell_costs: VenueCosts,
    payout_usd: Decimal,
) -> CapacityPoint:
    """Sum the qualifying slices, truncating at the position cap.

    ``slices`` must be sorted by net edge descending, so the qualifying set is a
    prefix and the cap consumes the most profitable contracts first. When the cap
    lands mid-slice the partial slice is re-priced rather than pro-rated, because
    a per-fill fixed cost does not scale with size.
    """
    contracts = 0
    capital = Decimal(0)
    profit = Decimal(0)
    worst: Decimal | None = None

    for sl in slices:
        if sl.net_edge < threshold:
            break
        take = sl.contracts
        if cap is not None:
            remaining = cap - contracts
            if remaining <= 0:
                break
            take = min(take, remaining)
        part = (
            sl
            if take == sl.contracts
            else _evaluate(sl.buy_price, sl.sell_price, take, buy_costs, sell_costs, payout_usd)
        )
        # Re-pricing a truncated slice can drop it below the threshold, because a
        # per-fill cost is spread over fewer contracts. Do not count it if so.
        if part.net_edge < threshold:
            break
        contracts += part.contracts
        capital += part.capital_usd
        profit += part.profit_usd
        worst = part.net_edge if worst is None else min(worst, part.net_edge)

    return CapacityPoint(
        min_net_edge=threshold,
        contracts=contracts,
        capital_usd=capital,
        profit_usd=profit,
        worst_net_edge=worst,
    )


def capacity_curve(
    buy_from: MarketSnapshot,
    sell_to: MarketSnapshot,
    costs: Mapping[str, VenueCosts],
    thresholds: Sequence[Decimal] = DEFAULT_THRESHOLDS,
    default_costs: VenueCosts | None = None,
) -> CapacityCurve:
    """Capacity for buying ``buy_from`` and taking the other side on ``sell_to``.

    Caller is responsible for having established that the two snapshots are
    settlement-matched and captured close enough together to be comparable;
    :func:`emc.probe.run_probe` enforces both before calling here.

    Raises ``ValueError`` if a venue has no cost model and no default is given,
    because silently assuming zero cost would inflate every number downstream.
    """
    if buy_from.payout_usd != sell_to.payout_usd:
        raise ValueError(
            f"payout mismatch: {buy_from.payout_usd} vs {sell_to.payout_usd}; "
            "legs must pay the same amount to be matched"
        )

    buy_costs = _resolve_costs(costs, buy_from.venue, default_costs)
    sell_costs = _resolve_costs(costs, sell_to.venue, default_costs)

    slices = _walk(
        buy_from.book.asks, sell_to.book.bids, buy_costs, sell_costs, buy_from.payout_usd
    )
    slices.sort(key=lambda s: s.net_edge, reverse=True)
    cap = _effective_cap(buy_costs, sell_costs)

    points = tuple(
        _aggregate(slices, t, cap, buy_costs, sell_costs, buy_from.payout_usd)
        for t in sorted(thresholds)
    )
    return CapacityCurve(buy_venue=buy_from.venue, sell_venue=sell_to.venue, points=points)


def _resolve_costs(
    costs: Mapping[str, VenueCosts], venue: str, default: VenueCosts | None
) -> VenueCosts:
    if venue in costs:
        return costs[venue]
    if default is not None:
        return default
    raise ValueError(f"no cost model for venue {venue!r} and no default_costs provided")
