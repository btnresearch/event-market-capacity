"""Exact locked-profit calculation for a settlement-matched contract pair.

This replaces the earlier "cross-venue spread walk". That approach assumed equal
quantities on both legs, charged fees per book level rather than per order, and
expressed its threshold in units that silently changed meaning when the contract
payout was not $1. All three are wrong for this mission, so it is gone.

WHAT IS COMPUTED
----------------
A locked position buys ``q_yes`` contracts of the YES side on one venue and
``q_no`` contracts of the NO side on another. Both settle on the same event under
the same rules, so exactly one side pays out:

    cash_out      = yes_cash + no_cash + yes_fee + no_fee
    profit_if_yes = q_yes * payout_yes - cash_out
    profit_if_no  = q_no  * payout_no  - cash_out
    locked_profit = min(profit_if_yes, profit_if_no)

``min`` is the whole point. A position is only locked to the extent of its worst
settlement outcome. When quantities are equal and payouts are equal the two
branches coincide and the position is genuinely state-independent; when they are
not, the imbalance is directional exposure and ``min`` prices it correctly. There
is no probability anywhere in this module, because the outcome set is enumerable
and the P&L in each state is exactly computable.

Prices are consumed level by level against real depth. There is no top-of-book
shortcut and no VWAP approximation of the fee: each fill is charged at its own
level and the order's fee is rounded once (see :mod:`emc.fees`).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from emc.fees import Fill, VenueCosts
from emc.models import BookLevel

__all__ = [
    "InsufficientDepth",
    "LockedQuote",
    "consume_depth",
    "evaluate_locked",
    "no_side_ladder",
    "optimize_locked",
]

ONE = Decimal("1")


class InsufficientDepth(ValueError):
    """Raised when a requested quantity exceeds the depth actually quoted.

    Never satisfied by extrapolating the last level. Invented depth is invented
    capacity, which is the exact failure this repository exists to avoid.
    """


def consume_depth(levels: Sequence[BookLevel], contracts: int) -> tuple[Fill, ...]:
    """Walk an ascending ask ladder and return the exact fills for ``contracts``.

    ``levels`` must be ascending in price: the ladder of prices at which the side
    in question can be BOUGHT.
    """
    if contracts < 0:
        raise ValueError(f"contracts must be non-negative, got {contracts}")
    if contracts == 0:
        return ()
    fills: list[Fill] = []
    remaining = contracts
    for level in levels:
        if remaining == 0:
            break
        take = min(remaining, level.size)
        fills.append(Fill(price=level.price, contracts=take))
        remaining -= take
    if remaining > 0:
        available = sum(lvl.size for lvl in levels)
        raise InsufficientDepth(
            f"requested {contracts} contracts but only {available} are quoted"
        )
    return tuple(fills)


def no_side_ladder(yes_bids: Sequence[BookLevel]) -> tuple[BookLevel, ...]:
    """Convert a YES bid ladder into the ask ladder for BUYING the NO side.

    A resting YES bid at ``b`` is a counterparty willing to buy YES at ``b``,
    which is the same as being willing to sell NO at ``1 - b``. So the NO buy
    ladder is the YES bid ladder reflected through 1, ascending.
    """
    reflected = [BookLevel(price=ONE - lvl.price, size=lvl.size) for lvl in yes_bids]
    reflected.sort(key=lambda lvl: lvl.price)
    return tuple(reflected)


@dataclass(frozen=True, slots=True)
class LockedQuote:
    """The exact economics of one candidate locked position."""

    q_yes: int
    q_no: int
    yes_venue: str
    no_venue: str
    yes_fills: tuple[Fill, ...]
    no_fills: tuple[Fill, ...]
    yes_fee_usd: Decimal
    no_fee_usd: Decimal
    payout_yes_usd: Decimal
    payout_no_usd: Decimal

    @property
    def yes_cash_usd(self) -> Decimal:
        return sum((f.cash_usd for f in self.yes_fills), Decimal(0))

    @property
    def no_cash_usd(self) -> Decimal:
        return sum((f.cash_usd for f in self.no_fills), Decimal(0))

    @property
    def fees_usd(self) -> Decimal:
        return self.yes_fee_usd + self.no_fee_usd

    @property
    def capital_usd(self) -> Decimal:
        """Cash committed: both legs' premium plus both legs' fees."""
        return self.yes_cash_usd + self.no_cash_usd + self.fees_usd

    @property
    def profit_if_yes_usd(self) -> Decimal:
        return Decimal(self.q_yes) * self.payout_yes_usd - self.capital_usd

    @property
    def profit_if_no_usd(self) -> Decimal:
        return Decimal(self.q_no) * self.payout_no_usd - self.capital_usd

    @property
    def locked_profit_usd(self) -> Decimal:
        """Profit guaranteed under the worst settlement outcome."""
        return min(self.profit_if_yes_usd, self.profit_if_no_usd)

    @property
    def is_locked(self) -> bool:
        return self.locked_profit_usd > 0

    @property
    def capital_per_venue(self) -> dict[str, Decimal]:
        """Capital committed on each venue, for allocation against venue balances."""
        out: dict[str, Decimal] = {}
        out[self.yes_venue] = out.get(self.yes_venue, Decimal(0)) + self.yes_cash_usd + self.yes_fee_usd
        out[self.no_venue] = out.get(self.no_venue, Decimal(0)) + self.no_cash_usd + self.no_fee_usd
        return out

    @property
    def return_on_capital(self) -> Decimal | None:
        if self.capital_usd <= 0:
            return None
        return self.locked_profit_usd / self.capital_usd


def evaluate_locked(
    yes_ladder: Sequence[BookLevel],
    no_ladder: Sequence[BookLevel],
    q_yes: int,
    q_no: int,
    yes_costs: VenueCosts,
    no_costs: VenueCosts,
    payout_yes_usd: Decimal = ONE,
    payout_no_usd: Decimal = ONE,
) -> LockedQuote:
    """Price one specific integer quantity pair exactly.

    Raises :class:`InsufficientDepth` rather than truncating, so a caller can
    never silently receive a smaller position than it asked for.
    """
    yes_fills = consume_depth(yes_ladder, q_yes)
    no_fills = consume_depth(no_ladder, q_no)
    return LockedQuote(
        q_yes=q_yes,
        q_no=q_no,
        yes_venue=yes_costs.venue,
        no_venue=no_costs.venue,
        yes_fills=yes_fills,
        no_fills=no_fills,
        yes_fee_usd=yes_costs.fee_usd(yes_fills),
        no_fee_usd=no_costs.fee_usd(no_fills),
        payout_yes_usd=payout_yes_usd,
        payout_no_usd=payout_no_usd,
    )


def _cumulative_boundaries(levels: Sequence[BookLevel]) -> list[int]:
    """Cumulative contract counts at each level boundary."""
    out: list[int] = []
    running = 0
    for level in levels:
        running += level.size
        out.append(running)
    return out


def optimize_locked(
    yes_ladder: Sequence[BookLevel],
    no_ladder: Sequence[BookLevel],
    yes_costs: VenueCosts,
    no_costs: VenueCosts,
    payout_yes_usd: Decimal = ONE,
    payout_no_usd: Decimal = ONE,
    max_capital_usd: Decimal | None = None,
    max_contracts: int | None = None,
    fine_sweep_limit: int = 2_000,
) -> LockedQuote | None:
    """Find the integer quantity pair maximizing locked profit. ``None`` if none is positive.

    Candidate quantities are the cumulative level boundaries of both ladders plus
    any binding cap. Between two boundaries the marginal price is constant, so
    locked profit is linear in quantity there and its maximum on the segment is at
    an endpoint. The one wrinkle is Kalshi's round-up-to-a-cent, which adds a
    sawtooth of at most $0.01 per order; when total depth is at or below
    ``fine_sweep_limit`` every quantity is evaluated so the result is exact, and
    above it the reported optimum can understate the true optimum by less than one
    cent per order. Understating is the acceptable direction.

    For each ``q_yes`` the matching ``q_no`` is the quantity that equalizes the two
    settlement branches, ``q_yes * payout_yes / payout_no``, together with its
    integer neighbours. Equalizing is optimal because ``min`` of two functions that
    move in opposite directions with the imbalance is maximized where they meet.
    """
    yes_depth = sum(lvl.size for lvl in yes_ladder)
    no_depth = sum(lvl.size for lvl in no_ladder)
    if yes_depth == 0 or no_depth == 0:
        return None
    if payout_no_usd <= 0 or payout_yes_usd <= 0:
        raise ValueError("payouts must be positive")

    ratio = payout_yes_usd / payout_no_usd
    ceiling = min(yes_depth, int(Decimal(no_depth) / ratio) if ratio > 0 else no_depth)
    if max_contracts is not None:
        ceiling = min(ceiling, max_contracts)
    if yes_costs.max_contracts is not None:
        ceiling = min(ceiling, yes_costs.max_contracts)
    if no_costs.max_contracts is not None:
        ceiling = min(ceiling, no_costs.max_contracts)
    if ceiling <= 0:
        return None

    if min(yes_depth, no_depth) <= fine_sweep_limit:
        candidates = set(range(1, ceiling + 1))
    else:
        candidates = {1, ceiling}
        for boundary in _cumulative_boundaries(yes_ladder):
            candidates.update({boundary - 1, boundary, boundary + 1})
        for boundary in _cumulative_boundaries(no_ladder):
            scaled = int(Decimal(boundary) / ratio)
            candidates.update({scaled - 1, scaled, scaled + 1})
        candidates = {q for q in candidates if 1 <= q <= ceiling}

    # Only an explicit total cap constrains total capital. A venue's own limit is a
    # limit on THAT venue's leg and is enforced per venue below; folding it into a
    # total cap would treat a $200 kalshi balance as a $200 ceiling on both legs
    # combined and understate the position by roughly half.
    capital_cap = max_capital_usd

    best: LockedQuote | None = None
    for q_yes in sorted(candidates):
        exact = Decimal(q_yes) * ratio
        for q_no in _neighbours(exact, no_depth):
            try:
                quote = evaluate_locked(
                    yes_ladder, no_ladder, q_yes, q_no, yes_costs, no_costs,
                    payout_yes_usd, payout_no_usd,
                )
            except InsufficientDepth:
                continue
            if capital_cap is not None and quote.capital_usd > capital_cap:
                continue
            if _exceeds_venue_capital(quote, yes_costs, no_costs):
                continue
            if quote.locked_profit_usd <= 0:
                continue
            if best is None or quote.locked_profit_usd > best.locked_profit_usd:
                best = quote
    return best


def _neighbours(exact: Decimal, depth: int) -> list[int]:
    base = int(exact)
    return [q for q in {base - 1, base, base + 1, base + 2} if 1 <= q <= depth]


def _exceeds_venue_capital(
    quote: LockedQuote, yes_costs: VenueCosts, no_costs: VenueCosts
) -> bool:
    per_venue = quote.capital_per_venue
    for costs in (yes_costs, no_costs):
        limit = costs.max_capital_usd
        if limit is not None and per_venue.get(costs.venue, Decimal(0)) > limit:
            return True
    return False
