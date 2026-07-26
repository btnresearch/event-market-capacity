"""Kalshi public market-data adapter (read-only, unauthenticated).

PAYLOAD SHAPES IN THIS MODULE ARE UNVERIFIED AGAINST THE LIVE API. They were
written from the documented shape of the public ``trade-api/v2`` endpoints and
have never been exercised against the real service from this repository, because
the environment this was developed in denies outbound access to the venue host.
Before trusting any live output, capture one real payload, diff it against
``tests/data/kalshi_*.json``, and update the parser. ``PayloadShapeError`` is
raised on anything unexpected specifically so that a shape drift fails loudly
instead of producing a plausible wrong book.

The normalization that matters here: Kalshi's order book publishes resting *bids*
on both the YES and NO sides. A NO bid at ``q`` cents is a standing willingness
to buy NO at ``q``, which is economically a standing offer to sell YES at
``100 - q``. So the YES ask ladder is the NO bid ladder reflected through 100.
Getting this backwards would invent an enormous fake spread, so it is asserted in
``tests/test_venue_parsers.py``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from emc.models import BookLevel, MarketRef, MarketSnapshot, OrderBook, SettlementTerms
from emc.venues.base import PayloadShapeError, get_json

__all__ = ["KalshiVenue", "parse_markets", "parse_orderbook"]

VENUE = "kalshi"
DEFAULT_BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"


def _cents_to_price(cents: Any) -> Decimal:
    try:
        value = Decimal(str(cents)) / Decimal(100)
    except Exception as exc:  # noqa: BLE001
        raise PayloadShapeError(f"unparseable cent price: {cents!r}") from exc
    return value


def _merge_levels(raw: Sequence[Any], *, reflect: bool) -> tuple[BookLevel, ...]:
    """Build a monotonic ladder from ``[[price_cents, size], ...]``.

    Duplicate prices are summed, zero and out-of-range levels are dropped, and the
    result is sorted. ``reflect`` maps NO bids to YES asks via ``100 - price``.
    """
    totals: dict[Decimal, int] = {}
    for entry in raw:
        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            raise PayloadShapeError(f"expected [price, size] pair, got {entry!r}")
        price = _cents_to_price(entry[0])
        if reflect:
            price = Decimal(1) - price
        try:
            size = int(entry[1])
        except (TypeError, ValueError) as exc:
            raise PayloadShapeError(f"unparseable size: {entry[1]!r}") from exc
        if size <= 0 or not (Decimal(0) < price < Decimal(1)):
            continue
        totals[price] = totals.get(price, 0) + size
    return tuple(
        BookLevel(price=p, size=totals[p]) for p in sorted(totals, reverse=not reflect)
    )


def parse_orderbook(
    payload: Any,
    market_id: str,
    captured_at: datetime,
    settlement: SettlementTerms | None = None,
    title: str | None = None,
) -> MarketSnapshot:
    """Turn a ``GET /markets/{ticker}/orderbook`` payload into a snapshot.

    May raise :class:`~emc.models.CrossedBookError` if the venue returns a book
    whose sides overlap. That is propagated rather than repaired: a crossed
    single-venue book is a bad read, and quietly cleaning it up would hand the
    capacity walk fabricated depth.
    """
    if not isinstance(payload, dict):
        raise PayloadShapeError(f"expected object, got {type(payload).__name__}")
    book = payload.get("orderbook", payload)
    if not isinstance(book, dict):
        raise PayloadShapeError("missing 'orderbook' object")

    yes_raw = book.get("yes") or []
    no_raw = book.get("no") or []
    if not isinstance(yes_raw, list) or not isinstance(no_raw, list):
        raise PayloadShapeError("'yes'/'no' must be lists of [price, size]")

    return MarketSnapshot(
        venue=VENUE,
        market_id=market_id,
        book=OrderBook(
            bids=_merge_levels(yes_raw, reflect=False),
            asks=_merge_levels(no_raw, reflect=True),
        ),
        captured_at=captured_at,
        settlement=settlement or SettlementTerms(),
        title=title,
    )


def parse_markets(payload: Any) -> tuple[MarketRef, ...]:
    """Turn a ``GET /markets`` payload into market references."""
    if not isinstance(payload, dict) or not isinstance(payload.get("markets"), list):
        raise PayloadShapeError("expected {'markets': [...]}")
    refs = []
    for market in payload["markets"]:
        if not isinstance(market, dict) or "ticker" not in market:
            raise PayloadShapeError(f"market entry missing 'ticker': {market!r}")
        refs.append(
            MarketRef(venue=VENUE, market_id=str(market["ticker"]), title=market.get("title"))
        )
    return tuple(refs)


class KalshiVenue:
    """Read-only client. Issues unauthenticated GETs and nothing else."""

    venue = VENUE

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def list_sports_markets(self, limit: int = 100, series_ticker: str | None = None) -> tuple[MarketRef, ...]:
        params: dict[str, Any] = {"limit": limit, "status": "open"}
        if series_ticker:
            params["series_ticker"] = series_ticker
        return parse_markets(get_json(f"{self.base_url}/markets", params, self.timeout))

    def fetch_snapshot(
        self, market_id: str, settlement: SettlementTerms | None = None
    ) -> MarketSnapshot:
        url = f"{self.base_url}/markets/{market_id}/orderbook"
        payload = get_json(url, None, self.timeout)
        # Stamped after the response returns, so skew checks compare the latest
        # instant either book could describe.
        return parse_orderbook(payload, market_id, datetime.now(timezone.utc), settlement)
