"""Builders shared by tests that need snapshots on arbitrary venue names."""

from __future__ import annotations

from conftest import T0, levels, terms

from emc.models import MarketSnapshot, OrderBook


def two_venue_snapshots(
    cheap_venue: str, rich_venue: str
) -> tuple[MarketSnapshot, MarketSnapshot]:
    """A settlement-matched pair that crosses by 5 cents, on caller-named venues.

    Used to exercise cost-model resolution for venues the package ships no
    defaults for.
    """
    settlement = terms()
    cheap = MarketSnapshot(
        venue=cheap_venue,
        market_id="cheap",
        book=OrderBook(asks=levels(("0.40", 100))),
        captured_at=T0,
        settlement=settlement,
    )
    rich = MarketSnapshot(
        venue=rich_venue,
        market_id="rich",
        book=OrderBook(bids=levels(("0.45", 100))),
        captured_at=T0,
        settlement=settlement,
    )
    return cheap, rich
