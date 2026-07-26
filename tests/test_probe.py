from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from conftest import T0, levels, snapshot, terms

from emc.probe import PairStatus, run_probe
from emc.registry import SettlementRegistry
from emc.serde import load_snapshots

ZERO = Decimal("0")


def _pair(report, status):
    return [p for p in report.pairs if p.status is status]


# --- gates -----------------------------------------------------------------


def test_capture_skew_beyond_tolerance_excludes_the_pair(zero_costs):
    buy = snapshot("kalshi", asks=levels(("0.40", 100)))
    sell = snapshot(
        "polymarket", bids=levels(("0.45", 100)), captured_at=T0 + timedelta(seconds=30)
    )
    report = run_probe([buy, sell], zero_costs)

    assert report.counts[PairStatus.STALE_SKEW.value] == 1
    assert not report.has_capacity
    assert _pair(report, PairStatus.STALE_SKEW)[0].skew_seconds == Decimal("30.0")


def test_skew_tolerance_is_configurable(zero_costs):
    buy = snapshot("kalshi", asks=levels(("0.40", 100)))
    sell = snapshot(
        "polymarket", bids=levels(("0.45", 100)), captured_at=T0 + timedelta(seconds=30)
    )
    report = run_probe([buy, sell], zero_costs, max_skew=timedelta(seconds=60))
    assert report.counts[PairStatus.MATCHED_WITH_CAPACITY.value] == 1


def test_skew_is_checked_before_settlement_so_stale_pairs_are_not_miscounted(zero_costs):
    buy = snapshot("kalshi", asks=levels(("0.40", 100)), settlement=terms(void_rule=None))
    sell = snapshot(
        "polymarket", bids=levels(("0.45", 100)), captured_at=T0 + timedelta(seconds=30)
    )
    report = run_probe([buy, sell], zero_costs)
    assert report.counts[PairStatus.STALE_SKEW.value] == 1
    assert report.counts[PairStatus.UNVERIFIED.value] == 0


def test_mismatched_settlement_suppresses_a_large_apparent_edge(zero_costs):
    buy = snapshot("kalshi", asks=levels(("0.32", 1000)))
    sell = snapshot(
        "polymarket", bids=levels(("0.45", 1000)), settlement=terms(includes_overtime=False)
    )
    report = run_probe([buy, sell], zero_costs)

    assert report.counts[PairStatus.MISMATCHED.value] == 1
    assert not report.has_capacity
    # No curve is computed at all for a rejected pair, so no number can leak out.
    assert _pair(report, PairStatus.MISMATCHED)[0].curve is None


def test_unverified_settlement_also_suppresses_capacity(zero_costs):
    buy = snapshot("kalshi", asks=levels(("0.62", 1000)))
    sell = snapshot("polymarket", bids=levels(("0.70", 800)), settlement=terms(void_rule=None))
    report = run_probe([buy, sell], zero_costs)

    assert report.counts[PairStatus.UNVERIFIED.value] == 1
    assert not report.has_capacity
    assert _pair(report, PairStatus.UNVERIFIED)[0].curve is None


def test_matched_pair_with_no_cross_is_distinguished_from_a_rejected_one(zero_costs):
    buy = snapshot("kalshi", asks=levels(("0.55", 1000)))
    sell = snapshot("polymarket", bids=levels(("0.54", 1000)))
    report = run_probe([buy, sell], zero_costs)

    assert report.counts[PairStatus.MATCHED_NO_CAPACITY.value] == 1
    assert not report.has_capacity
    # The distinction is the point: we verified this pair and there was no edge.
    assert _pair(report, PairStatus.MATCHED_NO_CAPACITY)[0].curve is not None


# --- pairing ---------------------------------------------------------------


def test_same_venue_pairs_are_not_measured(zero_costs):
    a = snapshot("kalshi", asks=levels(("0.40", 100)), market_id="a")
    b = snapshot("kalshi", bids=levels(("0.45", 100)), market_id="b")
    report = run_probe([a, b], zero_costs)
    assert report.candidates_considered == 0


def test_markets_without_participants_are_counted_as_unpairable(zero_costs):
    orphan = snapshot(
        "kalshi", asks=levels(("0.40", 100)), settlement=terms(participants=frozenset())
    )
    report = run_probe([orphan], zero_costs)
    assert report.skipped_unpairable == 1
    assert report.candidates_considered == 0


def test_different_events_are_never_paired(zero_costs):
    lakers = snapshot("kalshi", asks=levels(("0.40", 100)))
    yankees = snapshot(
        "polymarket",
        bids=levels(("0.90", 100)),
        settlement=terms(
            league="MLB", participants=frozenset({"New York Yankees", "Boston Red Sox"})
        ),
    )
    report = run_probe([lakers, yankees], zero_costs)
    assert report.candidates_considered == 0
    assert not report.has_capacity


def test_probe_picks_the_profitable_direction(zero_costs):
    """Order of arguments must not decide the answer."""
    cheap = snapshot("kalshi", asks=levels(("0.40", 100)))
    rich = snapshot("polymarket", bids=levels(("0.45", 100)))
    for pair in ([cheap, rich], [rich, cheap]):
        report = run_probe(pair, zero_costs)
        curve = _pair(report, PairStatus.MATCHED_WITH_CAPACITY)[0].curve
        assert (curve.buy_venue, curve.sell_venue) == ("kalshi", "polymarket")
        assert curve.at(ZERO).contracts == 100


def test_capacity_sums_across_independent_pairs(zero_costs):
    a1 = snapshot("kalshi", asks=levels(("0.40", 100)), market_id="k1")
    a2 = snapshot("polymarket", bids=levels(("0.45", 100)), market_id="p1")
    other = terms(league="MLB", participants=frozenset({"New York Yankees", "Boston Red Sox"}))
    b1 = snapshot("kalshi", asks=levels(("0.40", 200)), market_id="k2", settlement=other)
    b2 = snapshot("polymarket", bids=levels(("0.45", 200)), market_id="p2", settlement=other)

    report = run_probe([a1, a2, b1, b2], zero_costs)
    assert report.candidates_considered == 2
    assert report.aggregate_at(ZERO).contracts == 300
    assert report.aggregate_at(ZERO).profit_usd == Decimal("15.00")


# --- end to end on the committed example ----------------------------------


def test_example_snapshots_exercise_all_three_outcomes(example_dir, realistic_costs):
    snapshots = load_snapshots(example_dir / "snapshots.json")
    report = run_probe(snapshots, realistic_costs)

    assert report.snapshots_considered == 6
    assert report.candidates_considered == 3
    assert report.counts[PairStatus.MATCHED_WITH_CAPACITY.value] == 1
    assert report.counts[PairStatus.MISMATCHED.value] == 1
    assert report.counts[PairStatus.UNVERIFIED.value] == 1


def test_example_capacity_shows_fees_consuming_most_of_the_gross_edge(
    example_dir, realistic_costs
):
    """The headline result: a 3-cent gross cross is a 1.25-cent net one.

    Kalshi-style fees at a near-coin-flip price take 1.74c per contract of the 3c
    gross edge, and the second slice at 2c gross barely clears zero.
    """
    snapshots = load_snapshots(example_dir / "snapshots.json")
    report = run_probe(snapshots, realistic_costs)

    breakeven = report.aggregate_at(ZERO)
    assert breakeven.contracts == 800
    assert breakeven.capital_usd == Decimal("778.00")
    assert breakeven.profit_usd == Decimal("7.98")

    at_one_cent = report.aggregate_at(Decimal("0.01"))
    assert at_one_cent.contracts == 600
    assert at_one_cent.capital_usd == Decimal("582.00")
    assert at_one_cent.profit_usd == Decimal("7.51")

    # Nothing survives a 2-cent net-edge requirement.
    assert report.aggregate_at(Decimal("0.02")).contracts == 0


def test_registry_converts_an_unverified_pair_into_a_measured_one(
    example_dir, realistic_costs
):
    snapshots = load_snapshots(example_dir / "snapshots.json")
    registry = SettlementRegistry.load(example_dir / "settlement.json")
    report = run_probe(registry.apply_all(snapshots), realistic_costs)

    assert report.counts[PairStatus.MATCHED_WITH_CAPACITY.value] == 2
    assert report.counts[PairStatus.UNVERIFIED.value] == 0
    # The Yankees pair crosses by 8c on 800 contracts.
    assert report.aggregate_at(Decimal("0.05")).contracts == 800
    assert report.aggregate_at(Decimal("0.05")).profit_usd == Decimal("50.75")


def test_report_serializes_decimals_as_strings(example_dir, realistic_costs):
    import json

    snapshots = load_snapshots(example_dir / "snapshots.json")
    payload = run_probe(snapshots, realistic_costs).as_dict()
    text = json.dumps(payload)  # must not raise on Decimal

    assert json.loads(text)["aggregate"][0]["capital_usd"] == "778.00"
    reasons = [p["verdict"]["reasons"] for p in payload["pairs"] if p["verdict"]]
    assert any(reasons), "rejected pairs must explain themselves in the report"


def test_report_counts_every_status_key_even_when_zero(zero_costs):
    report = run_probe([], zero_costs)
    assert set(report.counts) == {s.value for s in PairStatus}
    assert report.snapshots_considered == 0


def test_crossed_snapshot_never_reaches_the_probe():
    """A single-venue crossed book is rejected at construction, not measured.

    Guarding here rather than in the probe means there is no code path on which a
    bad read becomes a capacity number.
    """
    import pytest

    from emc.models import CrossedBookError

    with pytest.raises(CrossedBookError):
        snapshot("kalshi", bids=levels(("0.45", 10)), asks=levels(("0.44", 10)))
