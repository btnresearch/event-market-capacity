from __future__ import annotations

from decimal import Decimal

from emc.fees import BpsFee, FixedPerFillFee, KalshiStyleFee, VenueCosts, ZeroFee


def test_zero_fee_is_zero():
    assert ZeroFee().fee_usd(Decimal("0.5"), 1000) == Decimal(0)


def test_bps_fee_is_proportional_to_notional():
    fee = BpsFee(bps=Decimal("10"))  # 10bps = 0.1%
    assert fee.fee_usd(Decimal("0.50"), 1000) == Decimal("0.5")


def test_fixed_per_fill_does_not_scale_with_size():
    fee = FixedPerFillFee(usd=Decimal("0.05"))
    assert fee.fee_usd(Decimal("0.5"), 1) == Decimal("0.05")
    assert fee.fee_usd(Decimal("0.5"), 100_000) == Decimal("0.05")


def test_fixed_per_fill_is_zero_for_an_empty_slice():
    assert FixedPerFillFee(usd=Decimal("0.05")).fee_usd(Decimal("0.5"), 0) == Decimal(0)


def test_kalshi_style_fee_matches_the_published_form_at_a_coin_flip():
    # 0.07 * 100 * 0.50 * 0.50 = 1.75, already a whole cent.
    assert KalshiStyleFee().fee_usd(Decimal("0.50"), 100) == Decimal("1.75")


def test_kalshi_style_fee_rounds_the_slice_total_up_to_a_cent():
    # 0.07 * 600 * 0.54 * 0.46 = 10.4328 -> 10.44
    assert KalshiStyleFee().fee_usd(Decimal("0.54"), 600) == Decimal("10.44")


def test_kalshi_style_fee_is_symmetric_about_one_half():
    fee = KalshiStyleFee()
    assert fee.fee_usd(Decimal("0.54"), 600) == fee.fee_usd(Decimal("0.46"), 600)


def test_kalshi_style_fee_is_cheaper_on_a_longshot_than_a_coin_flip():
    fee = KalshiStyleFee()
    assert fee.fee_usd(Decimal("0.05"), 1000) < fee.fee_usd(Decimal("0.50"), 1000)


def test_kalshi_style_fee_is_zero_for_an_empty_slice():
    assert KalshiStyleFee().fee_usd(Decimal("0.5"), 0) == Decimal(0)


def test_venue_costs_sum_taker_and_per_fill():
    costs = VenueCosts(
        venue="v",
        taker_fee=BpsFee(bps=Decimal("10")),
        per_fill=FixedPerFillFee(usd=Decimal("0.05")),
    )
    assert costs.cost_usd(Decimal("0.50"), 1000) == Decimal("0.55")


def test_venue_costs_default_to_explicit_zero_rather_than_missing():
    costs = VenueCosts(venue="v")
    assert costs.cost_usd(Decimal("0.5"), 100) == Decimal(0)
    assert costs.max_contracts is None
