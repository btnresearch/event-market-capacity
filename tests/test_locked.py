"""Exact locked-profit arithmetic.

Every number in this file is computed by hand in the docstring or comment that
introduces it, so a failure says which of the two is wrong rather than only that
they disagree.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from conftest import levels

from emc.fees import (
    Fill,
    KALSHI_TAKER,
    POLYMARKET_SPORTS_TAKER,
    Role,
    VenueCosts,
    ZeroFee,
    preset_costs,
)
from emc.locked import (
    InsufficientDepth,
    consume_depth,
    evaluate_locked,
    no_side_ladder,
    optimize_locked,
)

FREE_K = VenueCosts(venue="kalshi", schedule=preset_costs("kalshi").schedule, role=Role.TAKER)


def free(venue: str) -> VenueCosts:
    """Zero-fee costs, for isolating the depth arithmetic from the fee arithmetic."""
    from emc.fees import FeeSchedule

    z = ZeroFee(source="test-only")
    return VenueCosts(venue=venue, schedule=FeeSchedule(venue=venue, taker=z, maker=z))


# --- depth consumption -----------------------------------------------------


def test_consume_depth_walks_levels_in_order():
    fills = consume_depth(levels(("0.40", 100), ("0.42", 50)), 130)
    assert [(f.price, f.contracts) for f in fills] == [
        (Decimal("0.40"), 100),
        (Decimal("0.42"), 30),
    ]


def test_consume_depth_stops_exactly_at_the_requested_quantity():
    fills = consume_depth(levels(("0.40", 100), ("0.42", 50)), 100)
    assert len(fills) == 1 and fills[0].contracts == 100


def test_consume_depth_refuses_to_invent_depth():
    """Extrapolating the last level would be inventing capacity."""
    with pytest.raises(InsufficientDepth, match="only 150 are quoted"):
        consume_depth(levels(("0.40", 100), ("0.42", 50)), 151)


def test_consume_depth_of_zero_is_empty():
    assert consume_depth(levels(("0.40", 100)), 0) == ()


def test_consume_depth_rejects_negative_quantity():
    with pytest.raises(ValueError):
        consume_depth(levels(("0.40", 100)), -1)


def test_no_side_ladder_reflects_yes_bids_through_one_and_ascends():
    """A YES bid at 0.70 is an offer to sell NO at 0.30."""
    ladder = no_side_ladder(levels(("0.70", 800), ("0.69", 200)))
    assert [(lvl.price, lvl.size) for lvl in ladder] == [
        (Decimal("0.30"), 800),
        (Decimal("0.31"), 200),
    ]


# --- locked profit under both settlement outcomes -------------------------


def test_balanced_position_pays_the_same_under_both_outcomes():
    """100 YES + 100 NO at 0.40/0.55 costs 95c and returns $1 either way."""
    quote = evaluate_locked(
        levels(("0.40", 100)), levels(("0.55", 100)), 100, 100, free("a"), free("b")
    )
    assert quote.capital_usd == Decimal("95.00")
    assert quote.profit_if_yes_usd == Decimal("5.00")
    assert quote.profit_if_no_usd == Decimal("5.00")
    assert quote.locked_profit_usd == Decimal("5.00")
    assert quote.is_locked


def test_unequal_quantities_are_priced_by_the_worst_outcome():
    """Buying 100 YES against only 60 NO leaves 40 contracts of naked exposure.

    min() must report the NO branch, which is deeply negative, not the YES branch.
    """
    quote = evaluate_locked(
        levels(("0.40", 100)), levels(("0.55", 100)), 100, 60, free("a"), free("b")
    )
    # cash = 40.00 + 33.00 = 73.00; YES pays 100, NO pays 60.
    assert quote.capital_usd == Decimal("73.00")
    assert quote.profit_if_yes_usd == Decimal("27.00")
    assert quote.profit_if_no_usd == Decimal("-13.00")
    assert quote.locked_profit_usd == Decimal("-13.00")
    assert not quote.is_locked


def test_locked_profit_never_reports_the_favourable_branch():
    """A position that wins big on one side and loses on the other is not locked."""
    quote = evaluate_locked(
        levels(("0.10", 100)), levels(("0.10", 100)), 100, 1, free("a"), free("b")
    )
    assert quote.profit_if_yes_usd > 0
    assert quote.locked_profit_usd < 0


def test_capital_includes_fees_not_just_premium():
    k = preset_costs("kalshi", Role.TAKER)
    quote = evaluate_locked(levels(("0.50", 100)), levels(("0.45", 100)), 100, 100, k, free("b"))
    # kalshi taker fee on 100 @0.50 = ceil(0.07*100*0.25) = 1.75
    assert quote.yes_fee_usd == Decimal("1.75")
    assert quote.capital_usd == Decimal("50.00") + Decimal("45.00") + Decimal("1.75")


def test_capital_per_venue_splits_by_leg():
    quote = evaluate_locked(
        levels(("0.40", 100)), levels(("0.55", 100)), 100, 100, free("kalshi"), free("polymarket")
    )
    assert quote.capital_per_venue == {
        "kalshi": Decimal("40.00"),
        "polymarket": Decimal("55.00"),
    }


def test_vwap_across_levels_is_used_not_top_of_book():
    """The second level must raise the cost; a top-of-book calculation would not."""
    quote = evaluate_locked(
        levels(("0.40", 50), ("0.44", 50)), levels(("0.55", 100)), 100, 100, free("a"), free("b")
    )
    # yes cash = 50*0.40 + 50*0.44 = 42.00
    assert quote.yes_cash_usd == Decimal("42.00")
    assert quote.locked_profit_usd == Decimal("100") - Decimal("42.00") - Decimal("55.00")


# --- the fee floor, which is the decisive economics ----------------------


def test_two_cent_cross_is_not_locked_profit_at_taker_fees():
    """A 2c gross cross near 0.50 loses money: the fee floor there is ~2.97c."""
    k, p = preset_costs("kalshi", Role.TAKER), preset_costs("polymarket", Role.TAKER)
    best = optimize_locked(
        levels(("0.54", 800)), no_side_ladder(levels(("0.56", 600))), k, p
    )
    assert best is None


def test_eight_cent_cross_clears_the_taker_fee_floor():
    """An 8c gross cross at 0.62/0.70 nets ~5.3c per contract."""
    k, p = preset_costs("kalshi", Role.TAKER), preset_costs("polymarket", Role.TAKER)
    best = optimize_locked(
        levels(("0.62", 1000)), no_side_ladder(levels(("0.70", 800))), k, p
    )
    assert best is not None
    assert best.q_yes == best.q_no == 800
    # gross 0.08*800 = 64.00
    # kalshi  ceil(0.07*800*0.62*0.38) = ceil(13.1936) = 13.20
    # poly     0.05*800*0.30*0.70      = 8.40  (no rounding)
    assert best.yes_fee_usd == Decimal("13.20")
    assert best.no_fee_usd == Decimal("8.40")
    assert best.locked_profit_usd == Decimal("64.00") - Decimal("13.20") - Decimal("8.40")


def test_maker_fees_change_the_answer_on_the_same_book():
    """The same 2c cross that fails at taker fees clears at maker fees.

    This is why the fee schedule, not the spread, is the binding constraint.
    """
    ladder_yes, ladder_no = levels(("0.54", 800)), no_side_ladder(levels(("0.56", 600)))
    taker = optimize_locked(
        ladder_yes, ladder_no, preset_costs("kalshi", Role.TAKER),
        preset_costs("polymarket", Role.TAKER),
    )
    maker = optimize_locked(
        ladder_yes, ladder_no, preset_costs("kalshi", Role.MAKER),
        preset_costs("polymarket", Role.MAKER),
    )
    assert taker is None
    assert maker is not None and maker.locked_profit_usd > 0


# --- integer optimization ------------------------------------------------


def test_optimizer_is_bounded_by_the_thinner_side():
    best = optimize_locked(
        levels(("0.40", 10_000)), no_side_ladder(levels(("0.55", 100))), free("a"), free("b")
    )
    assert best is not None and best.q_yes == 100


def test_optimizer_returns_none_when_no_quantity_is_profitable():
    best = optimize_locked(
        levels(("0.60", 100)), no_side_ladder(levels(("0.55", 100))), free("a"), free("b")
    )
    assert best is None


def test_optimizer_returns_none_on_an_empty_ladder():
    assert optimize_locked((), no_side_ladder(levels(("0.55", 100))), free("a"), free("b")) is None
    assert optimize_locked(levels(("0.40", 100)), (), free("a"), free("b")) is None


def test_optimizer_stops_before_unprofitable_depth():
    """Deeper levels that destroy the lock must not be taken."""
    best = optimize_locked(
        levels(("0.40", 100), ("0.80", 900)),
        no_side_ladder(levels(("0.55", 1000))),
        free("a"),
        free("b"),
    )
    assert best is not None and best.q_yes == 100


def test_unequal_payouts_produce_unequal_optimal_quantities():
    """With a $2 YES payout, half as many YES contracts balance the branches."""
    best = optimize_locked(
        levels(("0.80", 500)),
        levels(("0.55", 500)),
        free("a"),
        free("b"),
        payout_yes_usd=Decimal("2"),
        payout_no_usd=Decimal("1"),
    )
    assert best is not None
    assert best.q_no == best.q_yes * 2
    assert best.profit_if_yes_usd == best.profit_if_no_usd


def test_optimizer_rejects_non_positive_payouts():
    with pytest.raises(ValueError, match="payouts must be positive"):
        optimize_locked(
            levels(("0.40", 100)), levels(("0.55", 100)), free("a"), free("b"),
            payout_yes_usd=Decimal("0"),
        )


# --- capital and size caps ------------------------------------------------


def test_capital_cap_reduces_the_position():
    best = optimize_locked(
        levels(("0.40", 1000)),
        no_side_ladder(levels(("0.55", 1000))),
        free("a"),
        free("b"),
        max_capital_usd=Decimal("95"),
    )
    assert best is not None
    # A pair costs 0.40 + (1-0.55) = 0.85/contract, so $95 admits 111 contracts.
    assert best.q_yes == 111
    assert best.capital_usd == Decimal("94.35")
    assert best.capital_usd <= Decimal("95")


def test_per_venue_capital_cap_binds_independently():
    a = free("a")
    from dataclasses import replace

    a = replace(a, max_capital_usd=Decimal("20"))
    best = optimize_locked(
        levels(("0.40", 1000)), no_side_ladder(levels(("0.55", 1000))), a, free("b")
    )
    assert best is not None
    assert best.capital_per_venue["a"] <= Decimal("20")


def test_contract_cap_binds():
    best = optimize_locked(
        levels(("0.40", 1000)),
        no_side_ladder(levels(("0.55", 1000))),
        free("a"),
        free("b"),
        max_contracts=7,
    )
    assert best is not None and best.q_yes == 7


def test_capital_cap_that_admits_nothing_returns_none():
    best = optimize_locked(
        levels(("0.40", 1000)),
        no_side_ladder(levels(("0.55", 1000))),
        free("a"),
        free("b"),
        max_capital_usd=Decimal("0.50"),
    )
    assert best is None
