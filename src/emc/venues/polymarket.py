"""Polymarket public market-data adapter (read-only, unauthenticated).

PAYLOAD SHAPES IN THIS MODULE ARE UNVERIFIED AGAINST THE LIVE API, for the same
reason as :mod:`emc.venues.kalshi`: the development environment denies outbound
access to the venue host. Capture a real ``/book`` response and reconcile it with
``tests/data/polymarket_book.json`` before trusting live output.

Polymarket quotes each outcome as its own token, with prices already in
probability units as decimal strings. A book fetched for the YES token needs no
reflection. A book fetched for the NO token is reflected through 1 on ingest, so
that everything downstream lives in YES-price space: a NO bid at ``q`` is a YES
ask at ``1 - q``, and a NO ask at ``q`` is a YES bid at ``1 - q``. Reflecting a
book swaps which side is which, which is easy to get wrong and is therefore
covered by a test.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from emc.models import BookLevel, MarketRef, MarketSnapshot, OrderBook, SettlementTerms
from emc.venues.base import PayloadShapeError, get_json

__all__ = ["PolymarketVenue", "parse_book", "parse_gamma_markets"]

VENUE = "polymarket"
DEFAULT_CLOB_URL = "https://clob.polymarket.com"
DEFAULT_GAMMA_URL = "https://gamma-api.polymarket.com"


def _levels(raw: Sequence[Any], *, reflect: bool, descending: bool) -> tuple[BookLevel, ...]:
    """Build a monotonic ladder from ``[{"price": "0.51", "size": "100"}, ...]``."""
    totals: dict[Decimal, int] = {}
    for entry in raw:
        if not isinstance(entry, dict) or "price" not in entry or "size" not in entry:
            raise PayloadShapeError(f"expected {{price, size}} object, got {entry!r}")
        try:
            price = Decimal(str(entry["price"]))
            # Sizes arrive as decimal strings and can be fractional; floor to whole
            # contracts so capacity is never rounded upward.
            size = int(Decimal(str(entry["size"])))
        except Exception as exc:  # noqa: BLE001
            raise PayloadShapeError(f"unparseable level {entry!r}") from exc
        if reflect:
            price = Decimal(1) - price
        if size <= 0 or not (Decimal(0) < price < Decimal(1)):
            continue
        totals[price] = totals.get(price, 0) + size
    return tuple(BookLevel(price=p, size=totals[p]) for p in sorted(totals, reverse=descending))


def parse_book(
    payload: Any,
    market_id: str,
    captured_at: datetime,
    settlement: SettlementTerms | None = None,
    *,
    is_no_token: bool = False,
    title: str | None = None,
) -> MarketSnapshot:
    """Turn a ``GET /book?token_id=...`` payload into a snapshot in YES space.

    When ``is_no_token`` is set, the incoming bids become YES asks and the incoming
    asks become YES bids, both reflected through 1.

    May raise :class:`~emc.models.CrossedBookError`; see
    :func:`emc.venues.kalshi.parse_orderbook` for why that is not swallowed.
    """
    if not isinstance(payload, dict):
        raise PayloadShapeError(f"expected object, got {type(payload).__name__}")
    raw_bids, raw_asks = payload.get("bids") or [], payload.get("asks") or []
    if not isinstance(raw_bids, list) or not isinstance(raw_asks, list):
        raise PayloadShapeError("'bids'/'asks' must be lists")

    if is_no_token:
        bids = _levels(raw_asks, reflect=True, descending=True)
        asks = _levels(raw_bids, reflect=True, descending=False)
    else:
        bids = _levels(raw_bids, reflect=False, descending=True)
        asks = _levels(raw_asks, reflect=False, descending=False)

    return MarketSnapshot(
        venue=VENUE,
        market_id=market_id,
        book=OrderBook(bids=bids, asks=asks),
        captured_at=captured_at,
        settlement=settlement or SettlementTerms(),
        title=title,
    )


def parse_gamma_markets(payload: Any) -> tuple[MarketRef, ...]:
    """Turn a Gamma ``GET /markets`` list into market references.

    Gamma identifies tradable outcomes by CLOB token id, which is what ``/book``
    takes, so the token id is used as the market id rather than the slug.
    """
    if not isinstance(payload, list):
        raise PayloadShapeError(f"expected a list of markets, got {type(payload).__name__}")
    refs: list[MarketRef] = []
    for market in payload:
        if not isinstance(market, dict):
            raise PayloadShapeError(f"market entry must be an object, got {market!r}")
        token_ids = market.get("clobTokenIds")
        if isinstance(token_ids, str):
            import json

            try:
                token_ids = json.loads(token_ids)
            except json.JSONDecodeError as exc:
                raise PayloadShapeError(f"unparseable clobTokenIds: {token_ids!r}") from exc
        if not isinstance(token_ids, list) or not token_ids:
            raise PayloadShapeError(f"market missing usable clobTokenIds: {market!r}")
        refs.append(
            MarketRef(venue=VENUE, market_id=str(token_ids[0]), title=market.get("question"))
        )
    return tuple(refs)


class PolymarketVenue:
    """Read-only client. Issues unauthenticated GETs and nothing else."""

    venue = VENUE

    def __init__(
        self,
        clob_url: str = DEFAULT_CLOB_URL,
        gamma_url: str = DEFAULT_GAMMA_URL,
        timeout: float = 10.0,
    ) -> None:
        self.clob_url = clob_url.rstrip("/")
        self.gamma_url = gamma_url.rstrip("/")
        self.timeout = timeout

    def list_sports_markets(self, limit: int = 100, tag: str | None = None) -> tuple[MarketRef, ...]:
        params: dict[str, Any] = {"limit": limit, "closed": "false"}
        if tag:
            params["tag"] = tag
        return parse_gamma_markets(get_json(f"{self.gamma_url}/markets", params, self.timeout))

    def fetch_snapshot(
        self,
        market_id: str,
        settlement: SettlementTerms | None = None,
        *,
        is_no_token: bool = False,
    ) -> MarketSnapshot:
        payload = get_json(f"{self.clob_url}/book", {"token_id": market_id}, self.timeout)
        return parse_book(
            payload,
            market_id,
            datetime.now(timezone.utc),
            settlement,
            is_no_token=is_no_token,
        )
