"""Core value types.

Prices are ``Decimal`` in probability units: 0 < price < 1, the cost in dollars of
a contract paying $1 on YES. Decimal, never float, because edge thresholds are
compared exactly and a sub-cent binary artifact is the difference between
"opportunity" and "noise".

Every book is expressed in YES-price space. A venue quoting a separate NO book is
normalized on ingest: a NO ask of ``q`` becomes a YES bid of ``1 - q``.

SETTLEMENT TERMS ARE CODED, NOT PROSE
-------------------------------------
An earlier version compared ``void_rule`` as free text. That is worse than
useless: two venues never phrase the same rule identically, so economically
equivalent contracts were reported as MISMATCHED — a positive claim that they
settle differently, which was false. Canonical rule fields are now enumerated
codes. Mapping a venue's prose to a code is a human judgement recorded with a
citation in :mod:`emc.registry`; the code is what calculations compare.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum

__all__ = [
    "BookLevel",
    "CrossedBookError",
    "ExtraInnings",
    "ListedPitcherRule",
    "MarketRef",
    "MarketSnapshot",
    "MarketType",
    "MatchStatus",
    "MatchVerdict",
    "OrderBook",
    "PostponementTreatment",
    "SettlementTerms",
    "SuspendedTreatment",
    "TieTreatment",
    "price",
    "utcnow",
]

ONE = Decimal("1")


class CrossedBookError(ValueError):
    """A venue reported a book whose best bid is at or above its best ask.

    A data-quality fault, not an opportunity: the snapshot is internally
    inconsistent and any capacity computed from it is fiction.
    """


def price(value: str | int | float | Decimal) -> Decimal:
    """Coerce a quote to Decimal probability units, rejecting out-of-range values."""
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
    price: Decimal
    size: int

    def __post_init__(self) -> None:
        if not (Decimal(0) < self.price < ONE):
            raise ValueError(f"level price out of range: {self.price}")
        if self.size <= 0:
            raise ValueError(f"level size must be positive, got {self.size}")


@dataclass(frozen=True, slots=True)
class OrderBook:
    """Resting depth for one market in YES-price space.

    ``bids`` strictly descending, ``asks`` strictly ascending. Either side may be
    empty.
    """

    bids: tuple[BookLevel, ...] = ()
    asks: tuple[BookLevel, ...] = ()

    def __post_init__(self) -> None:
        for name, levels, ascending in (("bids", self.bids, False), ("asks", self.asks, True)):
            prices = [lvl.price for lvl in levels]
            ordered = sorted(prices, reverse=not ascending)
            if prices != ordered or len(set(prices)) != len(prices):
                raise ValueError(
                    f"{name} must be strictly {'ascending' if ascending else 'descending'}: {prices}"
                )
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


# --- Canonical settlement codes -------------------------------------------


class MarketType(str, Enum):
    GAME_WINNER = "game_winner"
    RUN_LINE = "run_line"
    TOTAL = "total"
    SERIES_WINNER = "series_winner"


class ExtraInnings(str, Enum):
    """Whether the result includes extra innings, or is regulation-only."""

    INCLUDED = "included"
    REGULATION_ONLY = "regulation_only"


class TieTreatment(str, Enum):
    IMPOSSIBLE = "impossible"      # MLB game-winner: a tie cannot be the final state
    VOID = "void"
    PUSH = "push"


class PostponementTreatment(str, Enum):
    """What happens if the game is not played as scheduled."""

    VOID_IF_NOT_PLAYED_IN_WINDOW = "void_if_not_played_in_window"
    FOLLOWS_RESCHEDULED_GAME = "follows_rescheduled_game"
    VOID_IMMEDIATELY = "void_immediately"


class SuspendedTreatment(str, Enum):
    OFFICIAL_IF_REGULATION_COMPLETE = "official_if_regulation_complete"
    VOID_UNLESS_COMPLETED = "void_unless_completed"
    FOLLOWS_LEAGUE_RULING = "follows_league_ruling"


class ListedPitcherRule(str, Enum):
    NOT_REQUIRED = "not_required"
    BOTH_MUST_START = "both_must_start"
    ONE_MUST_START = "one_must_start"


class VenueChangeTreatment(str, Enum):
    NO_EFFECT = "no_effect"
    VOID_IF_RELOCATED = "void_if_relocated"


@dataclass(frozen=True, slots=True)
class SettlementTerms:
    """The canonical settlement record.

    ``None`` means the venue did not publish this field or the adapter could not
    extract it. Missing fields drive an ``UNVERIFIED`` verdict; they are never
    treated as agreement.

    ``doubleheader_number`` is load-bearing for MLB. Two games between the same
    teams on the same date are distinct events, and a matcher keyed only on teams
    and league would happily pair game 1 against game 2.
    """

    sport: str | None = None
    league: str | None = None
    home_team: str | None = None
    away_team: str | None = None
    game_date: str | None = None                       # ISO date in the league's local convention
    doubleheader_number: int | None = None             # 0 = single game, 1/2 = DH game number
    scheduled_start_utc: datetime | None = None
    market_type: MarketType | None = None
    outcome_team: str | None = None                    # which team this contract pays on
    extra_innings: ExtraInnings | None = None
    tie_treatment: TieTreatment | None = None
    postponement: PostponementTreatment | None = None
    postponement_window_hours: int | None = None
    suspended: SuspendedTreatment | None = None
    listed_pitcher: ListedPitcherRule | None = None
    minimum_innings: Decimal | None = None
    venue_change: VenueChangeTreatment | None = None
    settlement_source: str | None = None
    settlement_deadline_utc: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("scheduled_start_utc", "settlement_deadline_utc"):
            value = getattr(self, name)
            if value is not None and value.tzinfo is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.doubleheader_number is not None and self.doubleheader_number < 0:
            raise ValueError("doubleheader_number must be non-negative")

    @property
    def participants(self) -> frozenset[str]:
        return frozenset(t for t in (self.home_team, self.away_team) if t)


class MatchStatus(str, Enum):
    """Outcome of settlement-equivalence adjudication.

    ``UNVERIFIED`` is not a soft ``MATCHED``. It means the evidence needed to prove
    identical settlement is absent, so the pair carries unmeasured settlement risk.
    ``MISMATCHED`` is a positive claim that the two contracts settle differently,
    and is only issued when two coded fields actually conflict.
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
class MarketRef:
    venue: str
    market_id: str
    title: str | None = None


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """One venue's book for one market at one instant.

    ``captured_at`` is load-bearing: two books read seconds apart can show an edge
    that never simultaneously existed, so the probe compares capture times before
    it compares prices.
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


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
