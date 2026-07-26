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


def test_taker_taker_fee_floor_at_a_coin_flip_is_three_cents():
    """0.07*0.25 + 0.05*0.25 = 0.0175 + 0.0125 = 0.03 per contract.

    The decisive number for Hypothesis A. It depends on nothing but the two fee
    schedules: not depth, not latency, not execution skill.
    """
    assert breakeven_gross_edge(Decimal("0.50"), KT, PT) == Decimal("0.0300")


def test_fee_floor_across_the_competitive_mlb_band():
    """MLB game-winner markets for competitive games sit where the fee is worst."""
    for price, expected in (
        (Decimal("0.40"), Decimal("0.0288")),
        (Decimal("0.45"), Decimal("0.0297")),
        (Decimal("0.50"), Decimal("0.0300")),
        (Decimal("0.55"), Decimal("0.0297")),
        (Decimal("0.60"), Decimal("0.0288")),
    ):
        assert breakeven_gross_edge(price, KT, PT) == expected


def test_fee_floor_is_symmetric_about_one_half():
    for a, b in ((Decimal("0.30"), Decimal("0.70")), (Decimal("0.10"), Decimal("0.90"))):
        assert breakeven_gross_edge(a, KT, PT) == breakeven_gross_edge(b, KT, PT)


def test_longshots_are_cheaper_than_coin_flips():
    assert breakeven_gross_edge(Decimal("0.05"), KT, PT) < breakeven_gross_edge(
        Decimal("0.50"), KT, PT
    )


def test_maker_maker_floor_is_roughly_a_seventh_of_taker_taker():
    """0.25*0.07*0.25 + 0 = 0.004375 per contract.

    This is why the fee schedule redirects the whole investigation: the only
    execution style that clears a plausible spread is resting liquidity, where
    both fills are not guaranteed and the position is no longer locked.
    """
    assert breakeven_gross_edge(Decimal("0.50"), KM, PM) == Decimal("0.004375")
    ratio = breakeven_gross_edge(Decimal("0.50"), KT, PT) / breakeven_gross_edge(
        Decimal("0.50"), KM, PM
    )
    assert Decimal("6.8") < ratio < Decimal("6.9")


def test_mixed_roles_still_require_a_wide_spread():
    assert breakeven_gross_edge(Decimal("0.50"), KT, PM) == Decimal("0.0175")
    assert breakeven_gross_edge(Decimal("0.50"), KM, PT) == Decimal("0.016875")


def test_fee_floor_rejects_an_out_of_range_price():
    for bad in (Decimal("0"), Decimal("1"), Decimal("-0.5")):
        with pytest.raises(ValueError, match="price must be in"):
            breakeven_gross_edge(bad, KT, PT)


# --- the falsification ----------------------------------------------------


def test_a_two_cent_cross_cannot_reach_any_revenue_target():
    """2c gross at a coin flip is below the 3c floor. No size fixes a negative edge."""
    verdict = screen(GATE_250K, Decimal("0.50"), Decimal("0.02"), KT, PT)
    assert not verdict.feasible
    assert verdict.net_edge_per_contract == Decimal("-0.01")
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
    assert verdict.net_edge_per_contract == Decimal("0.01")
    assert verdict.contracts_needed == 138_889
    assert verdict.capital_needed_usd == Decimal("133333.44")


def test_the_million_dollar_target_needs_over_half_a_million_contracts_per_slate():
    verdict = screen(GATE_1M, Decimal("0.50"), Decimal("0.04"), KT, PT)
    assert verdict.contracts_needed == 555_556


def test_a_25_percent_capture_rate_makes_the_requirement_four_times_worse():
    gate = RevenueGate(Decimal("250000"), capture_rate=Decimal("0.25"))
    verdict = screen(gate, Decimal("0.50"), Decimal("0.04"), KT, PT)
    assert verdict.contracts_needed == 555_556


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
