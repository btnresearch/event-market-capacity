"""Capital allocation and episode collapsing.

These two behaviours are the difference between QLP and a number that looks like
QLP but is inflated by double-counted capital and by the polling rate.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from conftest import T0, levels

from emc.fees import FeeSchedule, VenueCosts, ZeroFee
from emc.locked import evaluate_locked, no_side_ladder, optimize_locked
from emc.models import MarketRef
from emc.qlp import (
    Observation,
    allocate_capital,
    collapse_episodes,
    qlp_from_episodes,
)


def free(venue: str) -> VenueCosts:
    z = ZeroFee(source="test-only")
    return VenueCosts(venue=venue, schedule=FeeSchedule(venue=venue, taker=z, maker=z))


def observation(pair_key: str, *, at=T0, yes_size=1000, no_size=1000, yes="0.40", no_bid="0.55"):
    """A locked pair costing (yes + 1-no_bid) per contract with zero fees."""
    yes_ladder = levels((yes, yes_size))
    no_ladder = no_side_ladder(levels((no_bid, no_size)))
    yc, nc = free("kalshi"), free("polymarket")
    quote = optimize_locked(yes_ladder, no_ladder, yc, nc)
    assert quote is not None
    return Observation(
        pair_key=pair_key,
        observed_at=at,
        yes_ref=MarketRef("kalshi", f"k-{pair_key}"),
        no_ref=MarketRef("polymarket", f"p-{pair_key}"),
        yes_ladder=yes_ladder,
        no_ladder=no_ladder,
        yes_costs=yc,
        no_costs=nc,
        quote=quote,
    )


# --- shared capital -------------------------------------------------------


def test_two_simultaneous_opportunities_share_one_budget():
    """The defect this metric exists to remove.

    Two pairs each wanting 1000 contracts at 0.40 need $400 of kalshi capital
    each. With $400 available in total, exactly one full position fits.
    """
    a, b = observation("game-a"), observation("game-b")
    alloc = allocate_capital([a, b], {"kalshi": Decimal("400"), "polymarket": Decimal("450")})

    assert alloc.capital_used["kalshi"] <= Decimal("400")
    assert alloc.capital_used["polymarket"] <= Decimal("450")
    # Uncapped sum would be $300 of locked profit; the budget admits $150.
    assert a.locked_profit_usd + b.locked_profit_usd == Decimal("300.00")
    assert alloc.locked_profit_usd == Decimal("150.00")


def test_allocator_never_exceeds_any_venue_budget():
    obs = [observation(f"g{i}") for i in range(5)]
    budget = {"kalshi": Decimal("600"), "polymarket": Decimal("675")}
    alloc = allocate_capital(obs, budget)
    for venue, cap in budget.items():
        assert alloc.capital_used.get(venue, Decimal(0)) <= cap


def test_partial_fit_is_repriced_rather_than_dropped():
    """A pair that does not fit whole is taken at the size that does fit."""
    alloc = allocate_capital(
        [observation("game-a")], {"kalshi": Decimal("200"), "polymarket": Decimal("225")}
    )
    assert len(alloc.taken) == 1
    _, quote = alloc.taken[0]
    assert quote.q_yes == 500  # $200 / $0.40
    assert quote.locked_profit_usd == Decimal("75.00")


def test_allocator_prefers_the_higher_return_on_capital():
    """Scarce capital goes to the more productive opportunity first."""
    thin = observation("thin", yes="0.50", no_bid="0.53")   # 3c on 0.97 capital
    fat = observation("fat", yes="0.40", no_bid="0.55")     # 15c on 0.85 capital
    alloc = allocate_capital([thin, fat], {"kalshi": Decimal("400"), "polymarket": Decimal("450")})
    assert alloc.taken[0][0].pair_key == "fat"


def test_venue_without_a_budget_is_skipped_not_treated_as_unlimited():
    """A missing balance must not manufacture capacity."""
    alloc = allocate_capital([observation("game-a")], {"kalshi": Decimal("10000")})
    assert alloc.taken == ()
    assert "polymarket" in alloc.skipped[0][1]


def test_zero_capital_yields_no_capacity():
    alloc = allocate_capital(
        [observation("game-a")], {"kalshi": Decimal("0.01"), "polymarket": Decimal("0.01")}
    )
    assert alloc.locked_profit_usd == Decimal(0)
    assert alloc.taken == ()


def test_allocation_return_on_capital_is_none_when_nothing_deployed():
    assert allocate_capital([], {"kalshi": Decimal("100")}).return_on_capital is None


def test_allocation_reports_a_reason_for_every_skip():
    alloc = allocate_capital(
        [observation("a"), observation("b")],
        {"kalshi": Decimal("400"), "polymarket": Decimal("450")},
    )
    assert len(alloc.taken) + len(alloc.skipped) == 2
    for _, reason in alloc.skipped:
        assert reason


# --- episodes -------------------------------------------------------------


def test_a_continuously_stale_price_is_one_episode():
    """Polling a stale quote 20 times is one opportunity, not twenty.

    Without this, QLP scales with the polling rate, which is a measurement
    artifact of our own choosing.
    """
    obs = [observation("game-a", at=T0 + timedelta(milliseconds=500 * i)) for i in range(20)]
    episodes = collapse_episodes(obs, max_gap=timedelta(seconds=5))

    assert len(episodes) == 1
    assert episodes[0].observation_count == 20
    assert episodes[0].peak_locked_profit_usd == Decimal("150.00")
    # Summing polls would report 20x this.
    assert qlp_from_episodes(episodes) == Decimal("150.00")


def test_a_gap_longer_than_the_tolerance_starts_a_new_episode():
    obs = [
        observation("game-a", at=T0),
        observation("game-a", at=T0 + timedelta(seconds=1)),
        observation("game-a", at=T0 + timedelta(minutes=10)),
    ]
    episodes = collapse_episodes(obs, max_gap=timedelta(seconds=5))
    assert len(episodes) == 2
    assert [e.observation_count for e in episodes] == [2, 1]


def test_distinct_pairs_are_distinct_episodes():
    obs = [observation("game-a", at=T0), observation("game-b", at=T0)]
    assert len(collapse_episodes(obs)) == 2


def test_episode_reports_its_peak_and_when_it_occurred():
    small = observation("game-a", at=T0, yes_size=100, no_size=100)
    big = observation("game-a", at=T0 + timedelta(seconds=1))
    episodes = collapse_episodes([small, big], max_gap=timedelta(seconds=5))

    assert len(episodes) == 1
    assert episodes[0].peak_locked_profit_usd == big.locked_profit_usd
    assert episodes[0].peak_at == T0 + timedelta(seconds=1)
    assert episodes[0].duration_seconds == Decimal("1.0")


def test_qlp_of_no_episodes_is_zero():
    assert qlp_from_episodes([]) == Decimal(0)


def test_episodes_are_ordered_by_first_appearance():
    obs = [
        observation("late", at=T0 + timedelta(seconds=30)),
        observation("early", at=T0),
    ]
    episodes = collapse_episodes(obs)
    assert [e.pair_key for e in episodes] == ["early", "late"]
