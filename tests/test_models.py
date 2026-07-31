from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from conftest import levels, snapshot

from emc.models import BookLevel, CrossedBookError, OrderBook, price


def test_price_converts_float_without_binary_drift():
    assert price(0.07) == Decimal("0.07")
    assert price("0.545") == Decimal("0.545")


@pytest.mark.parametrize("bad", [0, 1, -0.5, 1.5, "0", "1"])
def test_price_rejects_values_outside_the_open_unit_interval(bad):
    with pytest.raises(ValueError):
        price(bad)


def test_level_rejects_nonpositive_size():
    with pytest.raises(ValueError):
        BookLevel(price=Decimal("0.5"), size=0)


def test_bids_must_be_strictly_descending():
    with pytest.raises(ValueError, match="descending"):
        OrderBook(bids=levels(("0.51", 10), ("0.52", 10)))


def test_asks_must_be_strictly_ascending():
    with pytest.raises(ValueError, match="ascending"):
        OrderBook(asks=levels(("0.55", 10), ("0.54", 10)))


def test_duplicate_prices_on_one_side_are_rejected():
    with pytest.raises(ValueError):
        OrderBook(bids=levels(("0.51", 10), ("0.51", 10)))


def test_crossed_book_is_a_data_fault_not_an_opportunity():
    with pytest.raises(CrossedBookError):
        OrderBook(bids=levels(("0.55", 10)), asks=levels(("0.54", 10)))


def test_locked_book_is_also_rejected():
    with pytest.raises(CrossedBookError):
        OrderBook(bids=levels(("0.54", 10)), asks=levels(("0.54", 10)))


def test_one_sided_books_are_allowed():
    book = OrderBook(asks=levels(("0.54", 10)))
    assert book.best_bid is None
    assert book.best_ask == Decimal("0.54")
    assert book.bid_depth == 0
    assert book.ask_depth == 10


def test_depth_sums_all_levels():
    book = OrderBook(bids=levels(("0.52", 300), ("0.51", 200)))
    assert book.bid_depth == 500


def test_naive_capture_time_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        snapshot("kalshi", captured_at=datetime(2026, 7, 26, 22, 0))




def test_snapshot_ref_carries_venue_and_id():
    snap = snapshot("kalshi", market_id="ABC")
    assert snap.ref.venue == "kalshi"
    assert snap.ref.market_id == "ABC"


def test_scheduled_start_must_be_timezone_aware():
    from emc.models import SettlementTerms

    with pytest.raises(ValueError, match="timezone-aware"):
        SettlementTerms(scheduled_start_utc=datetime(2026, 7, 27, 23, 0))

    aware = SettlementTerms(scheduled_start_utc=datetime(2026, 7, 27, 23, 0, tzinfo=timezone.utc))
    assert aware.scheduled_start_utc is not None


def test_settlement_deadline_must_be_timezone_aware():
    from emc.models import SettlementTerms

    with pytest.raises(ValueError, match="timezone-aware"):
        SettlementTerms(settlement_deadline_utc=datetime(2026, 7, 28, 3, 0))


def test_participants_are_derived_from_home_and_away():
    from emc.models import SettlementTerms

    terms = SettlementTerms(home_team="Boston Red Sox", away_team="New York Yankees")
    assert terms.participants == frozenset({"Boston Red Sox", "New York Yankees"})
    assert SettlementTerms().participants == frozenset()


def test_doubleheader_number_must_be_non_negative():
    from emc.models import SettlementTerms

    with pytest.raises(ValueError, match="doubleheader_number"):
        SettlementTerms(doubleheader_number=-1)
