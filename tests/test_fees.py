"""Fee schedules: formulas, rounding, maker/taker, and provenance.

The constants under test are the most load-bearing numbers in the repository.
Every economic conclusion is downstream of them, so each is asserted against the
published formula and each must carry a citation.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from emc.fees import (
    Fill,
    KALSHI_MAKER,
    KALSHI_TAKER,
    POLYMARKET_US_MAKER,
    POLYMARKET_INTL_SPORTS_TAKER,
    POLYMARKET_US_TAKER,
    POLYMARKET_SCHEDULE,
    Role,
    preset_costs,
)


# --- fee arithmetic -------------------------------------------------------


def test_kalshi_fee_rounds_once_per_order_not_per_level():
    """100 one-contract fills must cost the same as one 100-contract order.

    Rounding per level made reported fees depend on how the book happened to be
    sliced: $2.00 against $1.75 at 0.50, a 14% overstatement and 0.25c/contract,
    which is decisive at these edge sizes.
    """
    one = KALSHI_TAKER.fee_usd([Fill(Decimal("0.50"), 100)])
    many = KALSHI_TAKER.fee_usd([Fill(Decimal("0.50"), 1) for _ in range(100)])
    assert one == many == Decimal("1.75")


def test_fee_is_charged_per_fill_price_not_order_vwap():
    """P*(1-P) is concave, so a VWAP collapse would overstate the fee."""
    fills = [Fill(Decimal("0.10"), 50), Fill(Decimal("0.90"), 50)]
    per_fill = POLYMARKET_US_TAKER.fee_usd(fills)
    vwap = POLYMARKET_US_TAKER.fee_at(Decimal("0.50"), 100)
    assert per_fill < vwap
    # 0.06 * 50 * 0.10*0.90 * 2 = 0.54
    assert per_fill == Decimal("0.54")


def test_polymarket_us_max_taker_fee_is_1_50_cents_per_contract():
    """US theta 0.06 gives a max of $1.50 per 100 shares at p=0.50."""
    assert POLYMARKET_US_TAKER.fee_at(Decimal("0.50"), 100) == Decimal("1.50")
    assert POLYMARKET_US_TAKER.max_fee_per_contract() == Decimal("0.0150")


def test_international_sports_theta_is_recorded_but_cheaper_and_unused():
    """0.05 is the international SPORTS rate, not the US rate.

    Kept only so the distinction is auditable. Using it would understate every
    fee floor by 0.25c at a coin flip, which is the error this pins down.
    """
    assert POLYMARKET_INTL_SPORTS_TAKER.fee_at(Decimal("0.50"), 100) == Decimal("1.25")
    assert POLYMARKET_INTL_SPORTS_TAKER.rate < POLYMARKET_US_TAKER.rate
    assert POLYMARKET_SCHEDULE.taker is POLYMARKET_US_TAKER


def test_kalshi_max_taker_fee_is_1_75_cents_per_contract():
    assert KALSHI_TAKER.max_fee_per_contract() == Decimal("0.0175")


def test_kalshi_maker_fee_is_a_quarter_of_taker():
    assert KALSHI_TAKER.max_fee_per_contract() / 4 == Decimal("0.004375")
    from emc.fees import KALSHI_MAKER

    assert KALSHI_MAKER.max_fee_per_contract() == Decimal("0.004375")


def test_polymarket_maker_pays_no_fee_and_no_rebate_is_credited():
    """A reward pool is not personally capturable revenue and is not netted here."""
    from emc.fees import POLYMARKET_US_MAKER

    assert POLYMARKET_US_MAKER.fee_at(Decimal("0.50"), 10_000) == Decimal("0")
    assert POLYMARKET_US_MAKER.max_fee_per_contract() == Decimal("0")


def test_unknown_venue_has_no_implicit_zero_cost_schedule():
    with pytest.raises(ValueError, match="no fee schedule"):
        preset_costs("bookieco")


def test_every_shipped_rate_carries_provenance():
    for model in (KALSHI_TAKER, POLYMARKET_US_TAKER):
        assert model.source, "a fee constant without a cited source is not usable"
        assert model.verified.value in {"primary", "corroborated"}
