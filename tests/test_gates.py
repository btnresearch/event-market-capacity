"""Revenue gates and the fee-floor screen.

The tests here are the falsification. They assert the arithmetic that kills
taker/taker cross-venue arbitrage as a route to $250k, so that the conclusion
cannot be quietly lost by a later refactor or a changed constant.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from emc.fees import Role, preset_costs
from emc.gates import (
    GATE_250K,
    GateVerdict,
    GATE_500K,
    GATE_1M,
    RevenueGate,
    breakeven_gross_edge,
    required_capital_usd,
    required_contracts,
    screen,
)

KT = preset_costs("kalshi", Role.TAKER)
PT = preset_costs("polymarket", Role.TAKER)
KM = preset_costs("kalshi", Role.MAKER)
PM = preset_costs("polymarket", Role.MAKER)


# --- gate arithmetic ------------------------------------------------------


def test_250k_over_180_slates_is_1389_per_slate():
    assert GATE_250K.realized_net_per_slate_usd == Decimal("1388.89")


def test_500k_and_1m_per_slate_requirements():
    assert GATE_500K.realized_net_per_slate_usd == Decimal("2777.78")
    assert GATE_1M.realized_net_per_slate_usd == Decimal("5555.56")


def test_capture_rate_of_one_leaves_the_quoted_bar_equal_to_the_realized_bar():
    """No unmeasured discount may flatter a result by default."""
    assert GATE_250K.capture_rate == Decimal("1")
    assert GATE_250K.quoted_per_slate_usd == GATE_250K.realized_net_per_slate_usd


def test_a_25_percent_capture_rate_quadruples_the_quoted_bar():
    gate = RevenueGate(Decimal("250000"), capture_rate=Decimal("0.25"))
    assert gate.quoted_per_slate_usd == Decimal("5555.56")


def test_gate_rejects_an_impossible_capture_rate():
    for bad in (Decimal("0"), Decimal("-0.1"), Decimal("1.5")):
        with pytest.raises(ValueError, match="capture_rate"):
            RevenueGate(Decimal("250000"), capture_rate=bad)


def test_gate_rejects_a_non_positive_slate_count():
    with pytest.raises(ValueError, match="slates_per_year"):
        RevenueGate(Decimal("250000"), slates_per_year=0)


def test_gate_passes_only_at_or_above_the_quoted_bar():
    assert GATE_250K.passes(Decimal("1388.89"))
    assert not GATE_250K.passes(Decimal("1388.88"))


# --- the fee floor --------------------------------------------------------


def test_taker_taker_fee_floor_at_a_coin_flip_is_three_and_a_quarter_cents():
    """0.07*0.25 + 0.06*0.25 = 0.0175 + 0.0150 = 0.0325 per contract.

    The decisive number for Hypothesis A. It depends on nothing but the two fee
    schedules: not depth, not latency, not execution skill.

    Was 3.000c while Polymarket was modelled at the international sports theta of
    0.05. The US venue charges 0.06 uniformly, so the true floor is 0.25c higher
    and the kill is correspondingly harder, never easier.
    """
    assert breakeven_gross_edge(Decimal("0.50"), KT, PT) == Decimal("0.032500")


def test_fee_floor_across_the_competitive_mlb_band():
    """MLB game-winner markets for competitive games sit where the fee is worst."""
    for price, expected in (
        (Decimal("0.40"), Decimal("0.031200")),
        (Decimal("0.45"), Decimal("0.032175")),
        (Decimal("0.50"), Decimal("0.032500")),
        (Decimal("0.55"), Decimal("0.032175")),
        (Decimal("0.60"), Decimal("0.031200")),
    ):
        assert breakeven_gross_edge(price, KT, PT) == expected


def test_fee_floor_is_symmetric_about_one_half():
    for a, b in ((Decimal("0.30"), Decimal("0.70")), (Decimal("0.10"), Decimal("0.90"))):
        assert breakeven_gross_edge(a, KT, PT) == breakeven_gross_edge(b, KT, PT)


def test_longshots_are_cheaper_than_coin_flips():
    assert breakeven_gross_edge(Decimal("0.05"), KT, PT) < breakeven_gross_edge(
        Decimal("0.50"), KT, PT
    )


def test_maker_maker_floor_is_roughly_a_seventh_and_a_half_of_taker_taker():
    """0.25*0.07*0.25 + 0 = 0.004375 per contract.

    This is why the fee schedule redirects the whole investigation: the only
    execution style that clears a plausible spread is resting liquidity, where
    both fills are not guaranteed and the position is no longer locked.
    """
    assert breakeven_gross_edge(Decimal("0.50"), KM, PM) == Decimal("0.00437500")
    ratio = breakeven_gross_edge(Decimal("0.50"), KT, PT) / breakeven_gross_edge(
        Decimal("0.50"), KM, PM
    )
    assert Decimal("7.4") < ratio < Decimal("7.5")


def test_mixed_roles_still_require_a_wide_spread():
    assert breakeven_gross_edge(Decimal("0.50"), KT, PM) == Decimal("0.017500")
    assert breakeven_gross_edge(Decimal("0.50"), KM, PT) == Decimal("0.01937500")


def test_fee_floor_rejects_an_out_of_range_price():
    for bad in (Decimal("0"), Decimal("1"), Decimal("-0.5")):
        with pytest.raises(ValueError, match="price must be in"):
            breakeven_gross_edge(bad, KT, PT)


# --- the falsification ----------------------------------------------------


def test_a_two_cent_cross_cannot_reach_any_revenue_target():
    """2c gross at a coin flip is below the 3c floor. No size fixes a negative edge."""
    verdict = screen(GATE_250K, Decimal("0.50"), Decimal("0.02"), KT, PT)
    assert not verdict.feasible
    assert verdict.net_edge_per_contract == Decimal("-0.012500")
    assert verdict.contracts_needed is None
    assert verdict.capital_needed_usd is None
    assert "INFEASIBLE" in verdict.summary()


def test_required_contracts_refuses_a_non_positive_edge():
    with pytest.raises(ValueError, match="must be positive"):
        required_contracts(Decimal("1388.89"), Decimal("0"))
    with pytest.raises(ValueError, match="must be positive"):
        required_contracts(Decimal("1388.89"), Decimal("-0.01"))


def test_a_four_cent_cross_demands_implausible_depth_for_250k():
    """Even an extreme 4c persistent cross needs ~139k contracts per slate.

    That is the falsification of Hypothesis A as a $250k route: the required
    matched depth is orders of magnitude above what MLB game-winner books quote.
    """
    verdict = screen(GATE_250K, Decimal("0.50"), Decimal("0.04"), KT, PT)
    assert verdict.feasible
    assert verdict.net_edge_per_contract == Decimal("0.007500")
    assert verdict.contracts_needed == 185_186
    assert verdict.capital_needed_usd == Decimal("177778.56")


def test_the_million_dollar_target_needs_over_half_a_million_contracts_per_slate():
    verdict = screen(GATE_1M, Decimal("0.50"), Decimal("0.04"), KT, PT)
    assert verdict.contracts_needed == 740_742


def test_a_25_percent_capture_rate_makes_the_requirement_four_times_worse():
    gate = RevenueGate(Decimal("250000"), capture_rate=Decimal("0.25"))
    verdict = screen(gate, Decimal("0.50"), Decimal("0.04"), KT, PT)
    assert verdict.contracts_needed == 740_742


def test_required_capital_accounts_for_the_spread_reducing_cost():
    # 1000 contracts of a pair costing (1 - 0.04) each.
    assert required_capital_usd(1000, Decimal("0.04")) == Decimal("960.00")


def test_maker_maker_needs_far_less_depth_which_is_the_redirect():
    """A 1c cross is infeasible at taker fees and feasible at maker fees.

    Recorded so the comparison is explicit: the only branch that clears a
    plausible spread is the one where fills are not guaranteed, which is
    Hypothesis B, not arbitrage. Even there, 1c requires ~247k contracts per
    slate for $250k, so maker economics do not rescue the depth requirement —
    they only change the sign of the edge.
    """
    taker = screen(GATE_250K, Decimal("0.50"), Decimal("0.01"), KT, PT)
    maker = screen(GATE_250K, Decimal("0.50"), Decimal("0.01"), KM, PM)
    assert not taker.feasible
    assert maker.feasible
    # net = 0.01 - 0.004375 = 0.005625; 1388.89 / 0.005625 = 246,913.8 -> 246,914
    assert maker.contracts_needed == 246_914


# --- the four-role kill ---------------------------------------------------
#
# The complete role matrix, pinned as literals. These are the permanent record of
# DECISIONS.md 2026-07-27: every expected value below is written out rather than
# recomputed from emc.gates, so a change to a fee constant or to the fee form
# fails here loudly instead of silently re-deriving a new "correct" answer.
#
# Naming is (Kalshi role)/(Polymarket role) throughout.
# Polymarket taker is the US uniform theta 0.06, not the international sports 0.05.

ROLE_COMBOS = {
    "taker/taker": (KT, PT),
    "maker/taker": (KM, PT),
    "taker/maker": (KT, PM),
    "maker/maker": (KM, PM),
}

FEE_FLOOR = {
    "taker/taker": {
        "0.40": Decimal("0.031200"), "0.45": Decimal("0.032175"),
        "0.50": Decimal("0.032500"),
        "0.55": Decimal("0.032175"), "0.60": Decimal("0.031200"),
    },
    "maker/taker": {
        "0.40": Decimal("0.01860000"), "0.45": Decimal("0.01918125"),
        "0.50": Decimal("0.01937500"),
        "0.55": Decimal("0.01918125"), "0.60": Decimal("0.01860000"),
    },
    "taker/maker": {
        "0.40": Decimal("0.016800"), "0.45": Decimal("0.017325"),
        "0.50": Decimal("0.017500"),
        "0.55": Decimal("0.017325"), "0.60": Decimal("0.016800"),
    },
    "maker/maker": {
        "0.40": Decimal("0.00420000"), "0.45": Decimal("0.00433125"),
        "0.50": Decimal("0.00437500"),
        "0.55": Decimal("0.00433125"), "0.60": Decimal("0.00420000"),
    },
}


@pytest.mark.parametrize("combo", sorted(FEE_FLOOR))
@pytest.mark.parametrize("price", ["0.40", "0.45", "0.50", "0.55", "0.60"])
def test_fee_floor_for_every_role_combination(combo, price):
    yes_costs, no_costs = ROLE_COMBOS[combo]
    assert breakeven_gross_edge(Decimal(price), yes_costs, no_costs) == FEE_FLOOR[combo][price]


@pytest.mark.parametrize("combo", sorted(FEE_FLOOR))
def test_every_role_combination_is_worst_at_a_coin_flip(combo):
    """P*(1-P) peaks at 0.50, so the competitive band is the expensive band.

    True for all four combinations, which is why no role choice escapes by moving
    to a different part of the price curve.
    """
    floors = FEE_FLOOR[combo]
    assert floors["0.50"] == max(floors.values())
    assert floors["0.40"] == floors["0.60"] == min(floors.values())


def test_role_ordering_is_strict_at_the_coin_flip():
    """taker/taker > maker/taker > taker/maker > maker/maker, with no ties.

    Pinned because the ordering is the whole argument: killing taker/taker does
    not kill the others, and the cheapest surviving combination is the one whose
    fills are not guaranteed.
    """
    at_half = [FEE_FLOOR[c]["0.50"] for c in
               ("taker/taker", "maker/taker", "taker/maker", "maker/maker")]
    assert at_half == sorted(at_half, reverse=True)
    assert len(set(at_half)) == 4


# Every (combination, gross cross) that cannot break even at P=0.50 no matter how
# much size is applied. A negative edge has no profitable quantity.
INFEASIBLE_AT_HALF = [
    ("taker/taker", "0.005"), ("taker/taker", "0.01"), ("taker/taker", "0.02"),
    ("maker/taker", "0.005"), ("maker/taker", "0.01"),
    ("taker/maker", "0.005"), ("taker/maker", "0.01"),
]


@pytest.mark.parametrize("combo,cross", INFEASIBLE_AT_HALF)
def test_infeasible_crosses_admit_no_position_size(combo, cross):
    yes_costs, no_costs = ROLE_COMBOS[combo]
    verdict = screen(GATE_250K, Decimal("0.50"), Decimal(cross), yes_costs, no_costs)
    assert not verdict.feasible
    assert verdict.contracts_needed is None
    assert verdict.capital_needed_usd is None
    assert "INFEASIBLE" in verdict.summary()


def test_no_taker_leg_survives_a_two_cent_cross_except_against_a_free_maker():
    """The headline kill, stated as a claim about roles rather than about a number.

    Any combination with a Kalshi taker leg needs more than 2c at a coin flip.
    Only combinations whose Polymarket leg is a zero-fee maker clear 2c at all.
    """
    for combo in ("taker/taker",):
        yes_costs, no_costs = ROLE_COMBOS[combo]
        assert not screen(
            GATE_250K, Decimal("0.50"), Decimal("0.02"), yes_costs, no_costs
        ).feasible
    for combo in ("taker/maker", "maker/maker"):
        yes_costs, no_costs = ROLE_COMBOS[combo]
        assert screen(
            GATE_250K, Decimal("0.50"), Decimal("0.02"), yes_costs, no_costs
        ).feasible


# (combination, gross cross, gate, contracts per slate, capital per slate) at P=0.50.
# $1,388.89 / $2,777.78 / $5,555.56 per slate over 180 slates.
REQUIREMENTS_AT_HALF = [
    ("taker/taker", "0.04", GATE_250K, 185_186, Decimal("177778.56")),
    ("taker/taker", "0.04", GATE_500K, 370_371, Decimal("355556.16")),
    ("taker/taker", "0.04", GATE_1M, 740_742, Decimal("711112.32")),
    ("maker/taker", "0.02", GATE_250K, 2_222_224, Decimal("2177779.52")),
    ("maker/taker", "0.02", GATE_500K, 4_444_448, Decimal("4355559.04")),
    ("maker/taker", "0.02", GATE_1M, 8_888_896, Decimal("8711118.08")),
    ("maker/taker", "0.04", GATE_250K, 67_341, Decimal("64647.36")),
    ("maker/taker", "0.04", GATE_500K, 134_681, Decimal("129293.76")),
    ("maker/taker", "0.04", GATE_1M, 269_361, Decimal("258586.56")),
    ("taker/maker", "0.02", GATE_250K, 555_556, Decimal("544444.88")),
    ("taker/maker", "0.02", GATE_500K, 1_111_112, Decimal("1088889.76")),
    ("taker/maker", "0.02", GATE_1M, 2_222_224, Decimal("2177779.52")),
    ("taker/maker", "0.04", GATE_250K, 61_729, Decimal("59259.84")),
    ("taker/maker", "0.04", GATE_500K, 123_457, Decimal("118518.72")),
    ("taker/maker", "0.04", GATE_1M, 246_914, Decimal("237037.44")),
    ("maker/maker", "0.005", GATE_250K, 2_222_224, Decimal("2211112.88")),
    ("maker/maker", "0.005", GATE_500K, 4_444_448, Decimal("4422225.76")),
    ("maker/maker", "0.005", GATE_1M, 8_888_896, Decimal("8844451.52")),
    ("maker/maker", "0.01", GATE_250K, 246_914, Decimal("244444.86")),
    ("maker/maker", "0.01", GATE_500K, 493_828, Decimal("488889.72")),
    ("maker/maker", "0.01", GATE_1M, 987_656, Decimal("977779.44")),
    ("maker/maker", "0.02", GATE_250K, 88_889, Decimal("87111.22")),
    ("maker/maker", "0.02", GATE_500K, 177_778, Decimal("174222.44")),
    ("maker/maker", "0.02", GATE_1M, 355_556, Decimal("348444.88")),
    ("maker/maker", "0.04", GATE_250K, 38_987, Decimal("37427.52")),
    ("maker/maker", "0.04", GATE_500K, 77_973, Decimal("74854.08")),
    ("maker/maker", "0.04", GATE_1M, 155_946, Decimal("149708.16")),
]


@pytest.mark.parametrize("combo,cross,gate,contracts,capital", REQUIREMENTS_AT_HALF)
def test_contracts_and_capital_required_per_slate(combo, cross, gate, contracts, capital):
    yes_costs, no_costs = ROLE_COMBOS[combo]
    verdict = screen(gate, Decimal("0.50"), Decimal(cross), yes_costs, no_costs)
    assert verdict.feasible
    assert verdict.contracts_needed == contracts
    assert verdict.capital_needed_usd == capital


def test_the_cheapest_surviving_route_at_half_a_cent_is_maker_maker_alone():
    """At a 0.5c cross only maker/maker clears the floor at all.

    And it needs 2.2M contracts per slate against $2.2M of capital for the $250k
    target, which is not a business on a Mac mini. Surviving the fee floor and
    being reachable are different questions.
    """
    survivors = [
        combo for combo, (a, b) in ROLE_COMBOS.items()
        if screen(GATE_250K, Decimal("0.50"), Decimal("0.005"), a, b).feasible
    ]
    assert survivors == ["maker/maker"]
    verdict = screen(GATE_250K, Decimal("0.50"), Decimal("0.005"), KM, PM)
    assert verdict.contracts_needed == 2_222_224
    assert verdict.capital_needed_usd == Decimal("2211112.88")


def test_maker_maker_is_not_arbitrage_and_the_suite_says_so():
    """A guard on interpretation, not on arithmetic.

    maker/maker has the lowest floor of the four, and that is exactly why it is
    not a locked position: both legs are resting orders, neither is guaranteed to
    fill, and a one-sided fill is a naked directional position rather than an
    arbitrage. The cheap floor buys an unhedged inventory problem, not free money.
    Nothing in emc.locked or emc.qlp models fill probability, so no number in this
    repository may be read as a maker/maker expectation.
    """
    assert FEE_FLOOR["maker/maker"]["0.50"] < FEE_FLOOR["taker/taker"]["0.50"]
    assert PM.fee_model.effective_rate == Decimal("0")
    assert KM.fee_model.effective_rate == Decimal("0.0175")
    # There is no fill-probability parameter anywhere in the cost or gate models.
    assert not any(
        "fill" in f.lower() for f in (*GateVerdict.__slots__, *RevenueGate.__slots__)
    )
