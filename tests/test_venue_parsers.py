"""Parser tests.

These are the only tests that pin the ingest layer, because the venue hosts are
unreachable from the development environment. They assert the parsers against
hand-written payloads in the documented shape, which proves the normalization
logic is self-consistent. They do NOT prove the documented shape matches the live
API. That gap is real and is recorded in docs/METHOD.md.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from conftest import T0, load_json

from emc.models import CrossedBookError
from emc.venues.base import PayloadShapeError
from emc.venues.kalshi import parse_markets, parse_orderbook
from emc.venues.polymarket import parse_book, parse_gamma_markets

# --- Kalshi ----------------------------------------------------------------


def test_kalshi_no_bids_become_yes_asks_reflected_through_one():
    """The normalization most likely to be silently wrong.

    A NO bid at 45c is a standing offer to sell YES at 55c. Reflecting the wrong
    way, or not at all, would invent a huge fake spread.
    """
    snap = parse_orderbook(load_json("kalshi_orderbook.json"), "NBA-TEST", T0)

    assert [(lvl.price, lvl.size) for lvl in snap.book.asks] == [
        (Decimal("0.54"), 100),  # from the 46c NO bid
        (Decimal("0.55"), 400),  # from the 45c NO bid
    ]


def test_kalshi_yes_bids_pass_through_and_merge_duplicates():
    snap = parse_orderbook(load_json("kalshi_orderbook.json"), "NBA-TEST", T0)
    assert [(lvl.price, lvl.size) for lvl in snap.book.bids] == [
        (Decimal("0.52"), 300),
        (Decimal("0.51"), 300),  # 200 + 100 merged
    ]


def test_kalshi_zero_size_levels_are_dropped():
    snap = parse_orderbook(load_json("kalshi_orderbook.json"), "NBA-TEST", T0)
    assert all(lvl.size > 0 for lvl in snap.book.bids + snap.book.asks)
    assert Decimal("0.40") not in {lvl.price for lvl in snap.book.bids}


def test_kalshi_book_is_uncrossed_after_normalization():
    snap = parse_orderbook(load_json("kalshi_orderbook.json"), "NBA-TEST", T0)
    assert snap.book.best_bid < snap.book.best_ask


def test_kalshi_accepts_a_bare_orderbook_object():
    payload = {"yes": [[50, 10]], "no": [[45, 10]]}
    snap = parse_orderbook(payload, "X", T0)
    assert snap.book.best_bid == Decimal("0.50")
    assert snap.book.best_ask == Decimal("0.55")


def test_kalshi_empty_ladders_produce_an_empty_book():
    snap = parse_orderbook({"orderbook": {"yes": [], "no": []}}, "X", T0)
    assert snap.book.bids == () and snap.book.asks == ()


def test_kalshi_crossed_payload_raises_rather_than_being_repaired():
    # A 60c YES bid against a 45c NO bid implies a 55c ask below the 60c bid.
    with pytest.raises(CrossedBookError):
        parse_orderbook({"orderbook": {"yes": [[60, 10]], "no": [[45, 10]]}}, "X", T0)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"orderbook": []},
        {"orderbook": {"yes": "nope", "no": []}},
        {"orderbook": {"yes": [[50]], "no": []}},
        {"orderbook": {"yes": [["abc", 10]], "no": []}},
        {"orderbook": {"yes": [[50, "many"]], "no": []}},
    ],
)
def test_kalshi_shape_drift_fails_loudly(payload):
    with pytest.raises(PayloadShapeError):
        parse_orderbook(payload, "X", T0)


def test_kalshi_market_list_parses_tickers_and_titles():
    refs = parse_markets(load_json("kalshi_markets.json"))
    assert [r.market_id for r in refs] == ["NBA-LALBOS-LAL", "MLB-NYYBOS-NYY"]
    assert refs[0].venue == "kalshi"
    assert refs[0].title == "Lakers to beat Celtics"


def test_kalshi_market_list_rejects_entries_without_a_ticker():
    with pytest.raises(PayloadShapeError):
        parse_markets({"markets": [{"title": "no ticker"}]})


# --- Polymarket ------------------------------------------------------------


def test_polymarket_yes_token_book_passes_through_sorted():
    snap = parse_book(load_json("polymarket_book.json"), "0xTOKEN", T0)
    assert [(lvl.price, lvl.size) for lvl in snap.book.bids] == [
        (Decimal("0.52"), 250),  # 250.5 floored
        (Decimal("0.51"), 100),
    ]
    assert [(lvl.price, lvl.size) for lvl in snap.book.asks] == [
        (Decimal("0.54"), 120),
        (Decimal("0.55"), 300),
    ]


def test_polymarket_no_token_book_is_reflected_and_sides_swap():
    """A NO book's bids become YES asks and its asks become YES bids."""
    snap = parse_book(load_json("polymarket_book.json"), "0xNO", T0, is_no_token=True)

    # NO asks 0.54/0.55 -> YES bids 0.46/0.45
    assert [(lvl.price, lvl.size) for lvl in snap.book.bids] == [
        (Decimal("0.46"), 120),
        (Decimal("0.45"), 300),
    ]
    # NO bids 0.52/0.51 -> YES asks 0.48/0.49
    assert [(lvl.price, lvl.size) for lvl in snap.book.asks] == [
        (Decimal("0.48"), 250),
        (Decimal("0.49"), 100),
    ]


def test_polymarket_reflection_keeps_the_book_uncrossed():
    snap = parse_book(load_json("polymarket_book.json"), "0xNO", T0, is_no_token=True)
    assert snap.book.best_bid < snap.book.best_ask


def test_polymarket_sizes_are_floored_never_rounded_up():
    payload = {"bids": [{"price": "0.50", "size": "9.99"}], "asks": []}
    snap = parse_book(payload, "0xT", T0)
    assert snap.book.bids[0].size == 9


def test_polymarket_sub_contract_sizes_are_dropped():
    payload = {"bids": [{"price": "0.50", "size": "0.4"}], "asks": []}
    assert parse_book(payload, "0xT", T0).book.bids == ()


def test_polymarket_missing_sides_produce_an_empty_book():
    snap = parse_book({"market": "0xm"}, "0xT", T0)
    assert snap.book.bids == () and snap.book.asks == ()


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"bids": "nope", "asks": []},
        {"bids": [{"price": "0.5"}], "asks": []},
        {"bids": [{"price": "abc", "size": "10"}], "asks": []},
    ],
)
def test_polymarket_shape_drift_fails_loudly(payload):
    with pytest.raises(PayloadShapeError):
        parse_book(payload, "0xT", T0)


def test_gamma_decodes_token_ids_from_both_string_and_list_forms():
    refs = parse_gamma_markets(load_json("polymarket_gamma.json"))
    assert [r.market_id for r in refs] == ["0xLALBOS-LAL-YES", "0xNYYBOS-NYY-YES"]
    assert refs[0].venue == "polymarket"
    assert refs[0].title == "Will the Lakers beat the Celtics?"


@pytest.mark.parametrize(
    "payload",
    [
        {"markets": []},
        [{"question": "no tokens"}],
        [{"clobTokenIds": "not json"}],
        [{"clobTokenIds": []}],
    ],
)
def test_gamma_shape_drift_fails_loudly(payload):
    with pytest.raises(PayloadShapeError):
        parse_gamma_markets(payload)


# --- shared ----------------------------------------------------------------


def test_parsers_stamp_the_supplied_capture_time():
    """Capture time must be injected, not read from the payload.

    The skew gate in emc.probe depends on this being when the read happened.
    """
    kalshi = parse_orderbook({"orderbook": {"yes": [[50, 1]], "no": []}}, "X", T0)
    poly = parse_book({"bids": [{"price": "0.50", "size": "1"}]}, "0xT", T0)
    assert kalshi.captured_at == T0 == poly.captured_at


def test_parsers_leave_settlement_terms_empty_when_none_are_supplied():
    """The negative result that drives emc.registry.

    Neither venue's book payload carries settlement rules, so an adapter reading
    only public market data cannot populate them, and the pair cannot be proven
    matched without human verification.
    """
    snap = parse_orderbook({"orderbook": {"yes": [[50, 1]], "no": []}}, "X", T0)
    assert snap.settlement.extra_innings is None
    assert snap.settlement.listed_pitcher is None
    assert snap.settlement.postponement is None
    assert snap.settlement.suspended is None
    assert snap.settlement.doubleheader_number is None
    assert snap.settlement.settlement_source is None
    assert snap.settlement.participants == frozenset()
