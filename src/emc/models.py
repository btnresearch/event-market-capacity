"""Core value types.

Prices are ``Decimal`` in probability units: 0 < price < 1, where price is the
cost in dollars of a contract that pays $1 if the event resolves YES. Decimal is
used rather than float because edge thresholds are compared exactly and a
sub-cent float artifact is the difference between "opportunity" and "noise".

Every book in this package is expressed in YES-price space. A venue that quotes
a separate NO book is normalized on ingest: buying NO at price ``q`` is
economically identical to selling YES at ``1 - q``, so a NO ask of ``q`` becomes
a YES bid of ``1 - q``. This normalization is what lets a two-leg
settlement-matched position be measured as a single cross-venue spread.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

__all__ = [
    "BookLevel",
    "CapacityCurve",
    "CapacityPoint",
    "CrossedBookError",
    "MarketRef",
    "MarketSnapshot",
    "MatchStatus",
    "MatchVerdict",
    "OrderBook",
    "SettlementTerms",
    "price",
]

ONE = Decimal("1")


class CrossedBookError(ValueError):
    """Raised when a venue reports a book whose best bid is at or above its best ask.

    This is a data-quality fault, not an opportunity. A single-venue crossed book
    means the snapshot is internally inconsistent (mid-update read, stale side,
    or a parser bug), and any capacity computed from it is fiction.
    """


def price(value: str | int | float | Decimal) -> Decimal:
    """Coerce a quote to Decimal probability units, rejecting out-of-range values.

    Floats are routed through ``repr`` so that ``0.07`` becomes ``Decimal("0.07")``
    rather than its binary expansion.
    """
    if isinstance(value, Decimal):
        out = value
    elif isinstance(value, float):
        out = Decimal(repr(value))
    else:
        out = Decimal(value)
    if not (Decimal(0) < out < ONE):
        raise ValueError(f"price must be strictly between 0 and 1, got {out}")
    return out


@dataclass(frozen=True, slots=True)
class BookLevel:
    """One resting price level. ``size`` is in contracts."""

    price: Decimal
    size: int

    def __post_init__(self) -> None:
        if not (Decimal(0) < self.price < ONE):
            raise ValueError(f"level price out of range: {self.price}")
        if self.size <= 0:
            raise ValueError(f"level size must be positive, got {self.size}")


@dataclass(frozen=True, slots=True)
class OrderBook:
    """Resting depth on one side of one market, in YES-price space.

    ``bids`` must be strictly descending, ``asks`` strictly ascending. Either side
    may be empty; an empty side simply means no capacity in that direction.
    """

    bids: tuple[BookLevel, ...] = ()
    asks: tuple[BookLevel, ...] = ()

    def __post_init__(self) -> None:
        for name, levels, ascending in (("bids", self.bids, False), ("asks", self.asks, True)):
            prices = [lvl.price for lvl in levels]
            ordered = sorted(prices, reverse=not ascending)
            if prices != ordered or len(set(prices)) != len(prices):
                raise ValueError(f"{name} must be strictly {'ascending' if ascending else 'descending'}: {prices}")
        if self.bids and self.asks and self.bids[0].price >= self.asks[0].price:
            raise CrossedBookError(
                f"best bid {self.bids[0].price} >= best ask {self.asks[0].price}"
            )

    @property
    def best_bid(self) -> Decimal | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Decimal | None:
        return self.asks[0].price if self.asks else None

    @property
    def bid_depth(self) -> int:
        return sum(lvl.size for lvl in self.bids)

    @property
    def ask_depth(self) -> int:
        return sum(lvl.size for lvl in self.asks)


class MatchStatus(str, Enum):
    """Outcome of settlement-equivalence adjudication.

    ``UNVERIFIED`` is not a soft ``MATCHED``. It means the evidence needed to
    prove the two contracts settle identically is absent, so the pair carries
    unmeasured settlement risk and must not be counted as capacity.
    """

    MATCHED = "matched"
    MISMATCHED = "mismatched"
    UNVERIFIED = "unverified"


@dataclass(frozen=True, slots=True)
class MatchVerdict:
    status: MatchStatus
    reasons: tuple[str, ...] = ()

    @property
    def is_matched(self) -> bool:
        return self.status is MatchStatus.MATCHED


@dataclass(frozen=True, slots=True)
class SettlementTerms:
    """The resolution contract, normalized for comparison across venues.

    A field left as ``None`` means "the venue did not publish this, or the
    adapter could not extract it". Missing fields drive an ``UNVERIFIED`` verdict
    rather than being treated as agreement.
    """

    event_key: str | None = None
    league: str | None = None
    participants: frozenset[str] = frozenset()
    scheduled_start_utc: datetime | None = None
    market_type: str | None = None
    outcome: str | None = None
    settlement_source: str | None = None
    includes_overtime: bool | None = None
    void_rule: str | None = None

    def __post_init__(self) -> None:
        start = self.scheduled_start_utc
        if start is not None and start.tzinfo is None:
            raise ValueError("scheduled_start_utc must be timezone-aware")


@dataclass(frozen=True, slots=True)
class MarketRef:
    """Enough to identify a market on a venue without having fetched its book."""

    venue: str
    market_id: str
    title: str | None = None


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """One venue's book for one market at one instant.

    ``captured_at`` is load-bearing. Two books read seconds apart can show an
    edge that never simultaneously existed, so the probe compares capture times
    before it compares prices.
    """

    venue: str
    market_id: str
    book: OrderBook
    captured_at: datetime
    settlement: SettlementTerms = field(default_factory=SettlementTerms)
    payout_usd: Decimal = ONE
    title: str | None = None

    def __post_init__(self) -> None:
        if self.captured_at.tzinfo is None:
            raise ValueError("captured_at must be timezone-aware")
        if self.payout_usd <= 0:
            raise ValueError("payout_usd must be positive")

    @property
    def ref(self) -> MarketRef:
        return MarketRef(venue=self.venue, market_id=self.market_id, title=self.title)


@dataclass(frozen=True, slots=True)
class CapacityPoint:
    """Capacity available at or above one net-edge threshold.

    ``capital_usd`` is the cash a settlement-matched two-leg position ties up:
    buying YES at the cheap venue's ask and NO at the rich venue's implied ask
    costs ``ask + (1 - bid)`` per contract and returns exactly the payout at
    settlement. ``profit_usd`` is that payout minus total cost minus fees.
    """

    min_net_edge: Decimal
    contracts: int
    capital_usd: Decimal
    profit_usd: Decimal
    worst_net_edge: Decimal | None

    @property
    def return_on_capital(self) -> Decimal | None:
        if self.capital_usd <= 0:
            return None
        return self.profit_usd / self.capital_usd


@dataclass(frozen=True, slots=True)
class CapacityCurve:
    """Capacity as a function of the net edge demanded, for one matched pair."""

    buy_venue: str
    sell_venue: str
    points: tuple[CapacityPoint, ...] = ()

    def at(self, threshold: Decimal) -> CapacityPoint | None:
        for point in self.points:
            if point.min_net_edge == threshold:
                return point
        return None

    @property
    def has_capacity(self) -> bool:
        return any(p.contracts > 0 for p in self.points)


def utcnow() -> datetime:
    """Timezone-aware now, for callers that need a capture timestamp."""
    return datetime.now(timezone.utc)
