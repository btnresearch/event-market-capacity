from __future__ import annotations

from decimal import Decimal

import pytest
from conftest import levels, snapshot

from emc.depth import DEFAULT_THRESHOLDS, capacity_curve
from emc.fees import FixedPerFillFee, KalshiStyleFee, VenueCosts, ZeroFee

ZERO = Decimal("0")


def curve(buy, sell, costs, thresholds=DEFAULT_THRESHOLDS, **kw):
    return capacity_curve(buy, sell, costs, thresholds, **kw)


def test_books_that_do_not_cross_have_no_capacity(zero_costs):
    buy = snapshot("kalshi", asks=levels(("0.55", 1000)))
    sell = snapshot("polymarket", bids=levels(("0.54", 1000)))
    result = curve(buy, sell, zero_costs)
    assert not result.has_capacity
    assert result.at(ZERO).contracts == 0
    assert result.at(ZERO).worst_net_edge is None


def test_equal_prices_are_not_capacity(zero_costs):
    buy = snapshot("kalshi", asks=levels(("0.54", 1000)))
    sell = snapshot("polymarket", bids=levels(("0.54", 1000)))
    assert not curve(buy, sell, zero_costs).has_capacity


def test_single_level_cross_computes_capital_and_profit_exactly(zero_costs):
    buy = snapshot("kalshi", asks=levels(("0.40", 100)))
    sell = snapshot("polymarket", bids=levels(("0.45", 100)))
    point = curve(buy, sell, zero_costs).at(ZERO)

    # Buying YES at 0.40 and NO at 1-0.45=0.55 costs 0.95 and returns 1.00.
    assert point.contracts == 100
    assert point.capital_usd == Decimal("95.00")
    assert point.profit_usd == Decimal("5.00")
    assert point.worst_net_edge == Decimal("0.05")
    assert point.return_on_capital == Decimal("5") / Decimal("95")


def test_capacity_is_limited_by_the_thinner_side(zero_costs):
    buy = snapshot("kalshi", asks=levels(("0.40", 100)))
    sell = snapshot("polymarket", bids=levels(("0.45", 10_000)))
    assert curve(buy, sell, zero_costs).at(ZERO).contracts == 100


def test_walk_consumes_multiple_levels_on_both_sides(zero_costs):
    buy = snapshot("kalshi", asks=levels(("0.40", 100), ("0.41", 100)))
    sell = snapshot("polymarket", bids=levels(("0.45", 150), ("0.44", 50)))
    point = curve(buy, sell, zero_costs).at(ZERO)
    # 100@(0.40,0.45), 50@(0.41,0.45), 50@(0.41,0.44)
    assert point.contracts == 200
    assert point.profit_usd == Decimal("100") * Decimal("0.05") + Decimal("50") * Decimal(
        "0.04"
    ) + Decimal("50") * Decimal("0.03")


def test_walk_stops_when_the_books_stop_crossing(zero_costs):
    buy = snapshot("kalshi", asks=levels(("0.40", 100), ("0.60", 5_000)))
    sell = snapshot("polymarket", bids=levels(("0.45", 100), ("0.44", 5_000)))
    assert curve(buy, sell, zero_costs).at(ZERO).contracts == 100


def test_thresholds_filter_out_thin_slices(zero_costs):
    buy = snapshot("kalshi", asks=levels(("0.40", 100), ("0.44", 900)))
    sell = snapshot("polymarket", bids=levels(("0.45", 1000)))
    result = curve(buy, sell, zero_costs)
    # 100 contracts at 5c edge, 900 more at only 1c.
    assert result.at(ZERO).contracts == 1000
    assert result.at(Decimal("0.01")).contracts == 1000
    assert result.at(Decimal("0.02")).contracts == 100
    assert result.at(Decimal("0.05")).contracts == 100


def test_worst_net_edge_reports_the_marginal_slice(zero_costs):
    buy = snapshot("kalshi", asks=levels(("0.40", 100), ("0.44", 900)))
    sell = snapshot("polymarket", bids=levels(("0.45", 1000)))
    result = curve(buy, sell, zero_costs)
    assert result.at(ZERO).worst_net_edge == Decimal("0.01")
    assert result.at(Decimal("0.02")).worst_net_edge == Decimal("0.05")


def test_fees_can_erase_an_apparent_edge_entirely():
    """A 1-cent gross cross is not capacity once a coin-flip fee is applied."""
    costs = {
        "kalshi": VenueCosts(venue="kalshi", taker_fee=KalshiStyleFee()),
        "polymarket": VenueCosts(venue="polymarket", taker_fee=ZeroFee()),
    }
    buy = snapshot("kalshi", asks=levels(("0.50", 1000)))
    sell = snapshot("polymarket", bids=levels(("0.51", 1000)))
    result = curve(buy, sell, costs)
    # gross 0.01/contract; fee 0.07*0.50*0.50 = 0.0175/contract.
    assert result.at(ZERO).contracts == 0
    assert not result.has_capacity


def test_a_deeper_slice_can_beat_a_thin_touch_slice():
    """Net edge is not monotone in depth, so slices must be ranked, not prefixed.

    One contract at a 3c gross edge cannot carry a 5c per-fill cost. A thousand
    contracts at 1c can. Walking the book and stopping at the first failing slice
    would report zero here.
    """
    costs = {
        "kalshi": VenueCosts(venue="kalshi", taker_fee=ZeroFee()),
        "polymarket": VenueCosts(
            venue="polymarket", per_fill=FixedPerFillFee(usd=Decimal("0.05"))
        ),
    }
    buy = snapshot("kalshi", asks=levels(("0.50", 1), ("0.51", 1000)))
    sell = snapshot("polymarket", bids=levels(("0.53", 1), ("0.52", 1000)))
    result = curve(buy, sell, costs)

    point = result.at(ZERO)
    assert point.contracts == 1000  # the thick 1c slice, not the thin 3c one
    assert point.profit_usd == Decimal("1000") * Decimal("0.01") - Decimal("0.05")


def test_position_cap_truncates_to_the_most_profitable_contracts(zero_costs):
    capped = dict(zero_costs)
    capped["kalshi"] = VenueCosts(venue="kalshi", taker_fee=ZeroFee(), max_contracts=120)
    buy = snapshot("kalshi", asks=levels(("0.40", 100), ("0.44", 900)))
    sell = snapshot("polymarket", bids=levels(("0.45", 1000)))
    point = curve(buy, sell, capped).at(ZERO)
    # 100 at 5c then only 20 of the 1c slice.
    assert point.contracts == 120
    assert point.profit_usd == Decimal("100") * Decimal("0.05") + Decimal("20") * Decimal("0.01")


def test_the_tighter_of_two_venue_caps_binds(zero_costs):
    capped = {
        "kalshi": VenueCosts(venue="kalshi", max_contracts=500),
        "polymarket": VenueCosts(venue="polymarket", max_contracts=50),
    }
    buy = snapshot("kalshi", asks=levels(("0.40", 1000)))
    sell = snapshot("polymarket", bids=levels(("0.45", 1000)))
    assert curve(buy, sell, capped).at(ZERO).contracts == 50


def test_truncated_slice_is_repriced_and_dropped_if_it_no_longer_clears():
    """A cap can make a slice too small to carry its own per-fill cost."""
    costs = {
        "kalshi": VenueCosts(venue="kalshi", max_contracts=2),
        "polymarket": VenueCosts(
            venue="polymarket", per_fill=FixedPerFillFee(usd=Decimal("0.05"))
        ),
    }
    buy = snapshot("kalshi", asks=levels(("0.50", 1000)))
    sell = snapshot("polymarket", bids=levels(("0.51", 1000)))
    # 1000 contracts at 1c clears easily; 2 contracts at 1c earns 0.02 against a 0.05 cost.
    assert curve(buy, sell, costs).at(ZERO).contracts == 0


def test_payout_mismatch_between_legs_is_rejected(zero_costs):
    from dataclasses import replace

    buy = snapshot("kalshi", asks=levels(("0.40", 100)))
    sell = replace(snapshot("polymarket", bids=levels(("0.45", 100))), payout_usd=Decimal("2"))
    with pytest.raises(ValueError, match="payout mismatch"):
        curve(buy, sell, zero_costs)


def test_scaled_payout_scales_capital_and_profit(zero_costs):
    from dataclasses import replace

    buy = replace(snapshot("kalshi", asks=levels(("0.40", 100))), payout_usd=Decimal("10"))
    sell = replace(snapshot("polymarket", bids=levels(("0.45", 100))), payout_usd=Decimal("10"))
    point = curve(buy, sell, zero_costs).at(ZERO)
    assert point.capital_usd == Decimal("950.00")
    assert point.profit_usd == Decimal("50.00")


def test_missing_cost_model_is_an_error_not_an_implicit_zero():
    buy = snapshot("kalshi", asks=levels(("0.40", 100)))
    sell = snapshot("polymarket", bids=levels(("0.45", 100)))
    with pytest.raises(ValueError, match="no cost model"):
        curve(buy, sell, {})


def test_default_costs_are_used_when_a_venue_is_unknown():
    buy = snapshot("kalshi", asks=levels(("0.40", 100)))
    sell = snapshot("polymarket", bids=levels(("0.45", 100)))
    result = curve(buy, sell, {}, default_costs=VenueCosts(venue="any"))
    assert result.at(ZERO).contracts == 100


def test_curve_points_are_ordered_by_threshold(zero_costs):
    buy = snapshot("kalshi", asks=levels(("0.40", 100)))
    sell = snapshot("polymarket", bids=levels(("0.45", 100)))
    result = curve(buy, sell, zero_costs, thresholds=[Decimal("0.05"), ZERO, Decimal("0.01")])
    assert [p.min_net_edge for p in result.points] == [ZERO, Decimal("0.01"), Decimal("0.05")]


def test_capacity_is_non_increasing_in_the_threshold(zero_costs):
    buy = snapshot("kalshi", asks=levels(("0.40", 100), ("0.42", 200), ("0.44", 400)))
    sell = snapshot("polymarket", bids=levels(("0.45", 300), ("0.44", 500)))
    contracts = [p.contracts for p in curve(buy, sell, zero_costs).points]
    assert contracts == sorted(contracts, reverse=True)


def test_direction_is_recorded_on_the_curve(zero_costs):
    buy = snapshot("kalshi", asks=levels(("0.40", 100)))
    sell = snapshot("polymarket", bids=levels(("0.45", 100)))
    result = curve(buy, sell, zero_costs)
    assert (result.buy_venue, result.sell_venue) == ("kalshi", "polymarket")
