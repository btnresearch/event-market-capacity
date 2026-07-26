from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from conftest import T0, levels, other_game, snapshot, terms

from emc.models import ExtraInnings
from emc.probe import PairStatus, canonical_event_key, run_probe
from emc.registry import SettlementRegistry
from emc.serde import load_snapshots


def of(report, status):
    return [p for p in report.pairs if p.status is status]


# --- gates ----------------------------------------------------------------


def test_capture_skew_beyond_tolerance_excludes_the_pair(taker_costs, ample_capital):
    buy = snapshot("kalshi", asks=levels(("0.62", 1000)))
    sell = snapshot(
        "polymarket", bids=levels(("0.70", 800)), captured_at=T0 + timedelta(seconds=30)
    )
    report = run_probe([buy, sell], taker_costs, ample_capital)

    assert report.counts[PairStatus.STALE_SKEW.value] == 1
    assert report.capital_constrained_locked_profit_usd == Decimal(0)
    assert of(report, PairStatus.STALE_SKEW)[0].skew_seconds == Decimal("30.0")


def test_skew_is_checked_before_settlement_so_stale_pairs_are_not_miscounted(
    taker_costs, ample_capital
):
    buy = snapshot("kalshi", asks=levels(("0.62", 1000)), settlement=terms(listed_pitcher=None))
    sell = snapshot(
        "polymarket", bids=levels(("0.70", 800)), captured_at=T0 + timedelta(seconds=30)
    )
    report = run_probe([buy, sell], taker_costs, ample_capital)
    assert report.counts[PairStatus.STALE_SKEW.value] == 1
    assert report.counts[PairStatus.UNVERIFIED.value] == 0


def test_mismatched_settlement_suppresses_a_large_apparent_edge(taker_costs, ample_capital):
    """13c apparent cross, killed by an extra-innings conflict. No number leaks out."""
    buy = snapshot("kalshi", asks=levels(("0.32", 1000)))
    sell = snapshot(
        "polymarket",
        bids=levels(("0.45", 1000)),
        settlement=terms(extra_innings=ExtraInnings.REGULATION_ONLY),
    )
    report = run_probe([buy, sell], taker_costs, ample_capital)

    assert report.counts[PairStatus.MISMATCHED.value] == 1
    assert report.capital_constrained_locked_profit_usd == Decimal(0)
    assert of(report, PairStatus.MISMATCHED)[0].quote is None


def test_unverified_settlement_also_suppresses_capacity(taker_costs, ample_capital):
    buy = snapshot("kalshi", asks=levels(("0.62", 1000)))
    sell = snapshot(
        "polymarket", bids=levels(("0.70", 800)), settlement=terms(listed_pitcher=None)
    )
    report = run_probe([buy, sell], taker_costs, ample_capital)

    assert report.counts[PairStatus.UNVERIFIED.value] == 1
    assert of(report, PairStatus.UNVERIFIED)[0].quote is None


def test_a_two_cent_cross_is_matched_but_yields_no_locked_profit(taker_costs, ample_capital):
    """Verified pair, real cross, still no opportunity: fees exceed the spread.

    This status is the whole point of the metric. It says "we looked and the
    economics do not work", which is different from "we could not verify".
    """
    buy = snapshot("kalshi", asks=levels(("0.54", 800)))
    sell = snapshot("polymarket", bids=levels(("0.56", 600)))
    report = run_probe([buy, sell], taker_costs, ample_capital)

    assert report.counts[PairStatus.MATCHED_NO_PROFIT.value] == 1
    assert report.capital_constrained_locked_profit_usd == Decimal(0)


def test_an_eight_cent_cross_clears_the_fee_floor(taker_costs, ample_capital):
    buy = snapshot("kalshi", asks=levels(("0.62", 1000)))
    sell = snapshot("polymarket", bids=levels(("0.70", 800)))
    report = run_probe([buy, sell], taker_costs, ample_capital)

    assert report.counts[PairStatus.LOCKED_PROFIT.value] == 1
    quote = of(report, PairStatus.LOCKED_PROFIT)[0].quote
    assert quote.locked_profit_usd == Decimal("42.40")


def test_maker_role_changes_the_verdict_on_the_same_books(
    taker_costs, maker_costs, ample_capital
):
    snaps = [
        snapshot("kalshi", asks=levels(("0.54", 800))),
        snapshot("polymarket", bids=levels(("0.56", 600))),
    ]
    as_taker = run_probe(snaps, taker_costs, ample_capital)
    as_maker = run_probe(snaps, maker_costs, ample_capital)

    assert as_taker.counts[PairStatus.MATCHED_NO_PROFIT.value] == 1
    assert as_maker.counts[PairStatus.LOCKED_PROFIT.value] == 1


# --- event identity -------------------------------------------------------


def test_canonical_key_includes_the_doubleheader_number():
    k1 = canonical_event_key(terms(doubleheader_number=1))
    k2 = canonical_event_key(terms(doubleheader_number=2))
    assert k1 != k2
    assert "dh1" in k1 and "dh2" in k2


def test_canonical_key_is_none_without_teams_or_date():
    assert canonical_event_key(terms(home_team=None, away_team=None)) is None
    assert canonical_event_key(terms(game_date=None)) is None


def test_canonical_key_is_order_independent_for_home_and_away():
    a = canonical_event_key(terms())
    b = canonical_event_key(terms(home_team="New York Yankees", away_team="Boston Red Sox"))
    assert a == b


def test_unidentifiable_markets_are_counted_not_paired(taker_costs, ample_capital):
    orphan = snapshot("kalshi", asks=levels(("0.40", 100)), settlement=terms(game_date=None))
    report = run_probe([orphan], taker_costs, ample_capital)
    assert report.skipped_unidentifiable == 1
    assert report.candidates_considered == 0


def test_same_venue_pairs_are_not_measured(taker_costs, ample_capital):
    a = snapshot("kalshi", asks=levels(("0.40", 100)), market_id="a")
    b = snapshot("kalshi", bids=levels(("0.45", 100)), market_id="b")
    assert run_probe([a, b], taker_costs, ample_capital).candidates_considered == 0


def test_different_events_are_never_paired(taker_costs, ample_capital):
    a = snapshot("kalshi", asks=levels(("0.40", 100)))
    b = snapshot("polymarket", bids=levels(("0.90", 100)), settlement=other_game())
    report = run_probe([a, b], taker_costs, ample_capital)
    assert report.candidates_considered == 0


def test_probe_picks_the_profitable_direction(taker_costs, ample_capital):
    """Argument order must not decide the answer."""
    cheap = snapshot("kalshi", asks=levels(("0.62", 1000)))
    rich = snapshot("polymarket", bids=levels(("0.70", 800)))
    for pair in ([cheap, rich], [rich, cheap]):
        report = run_probe(pair, taker_costs, ample_capital)
        obs = report.observations[0]
        assert obs.yes_ref.venue == "kalshi"
        assert obs.no_ref.venue == "polymarket"


# --- shared capital -------------------------------------------------------


def test_simultaneous_pairs_do_not_double_count_venue_capital(taker_costs):
    """Two 8c crosses on different games competing for one kalshi balance."""
    g1, g2 = terms(), other_game()
    snaps = [
        snapshot("kalshi", asks=levels(("0.62", 1000)), market_id="k1", settlement=g1),
        snapshot("polymarket", bids=levels(("0.70", 800)), market_id="p1", settlement=g1),
        snapshot("kalshi", asks=levels(("0.62", 1000)), market_id="k2", settlement=g2),
        snapshot("polymarket", bids=levels(("0.70", 800)), market_id="p2", settlement=g2),
    ]
    budget = {"kalshi": Decimal("500"), "polymarket": Decimal("500")}
    report = run_probe(snaps, taker_costs, budget)

    assert report.candidates_considered == 2
    assert report.uncapped_locked_profit_usd == Decimal("84.80")
    assert report.capital_constrained_locked_profit_usd < report.uncapped_locked_profit_usd
    assert report.allocation.capital_used["kalshi"] <= Decimal("500")
    assert report.allocation.capital_used["polymarket"] <= Decimal("500")


def test_report_distinguishes_uncapped_from_capital_constrained(taker_costs):
    snaps = [
        snapshot("kalshi", asks=levels(("0.62", 1000))),
        snapshot("polymarket", bids=levels(("0.70", 800))),
    ]
    tiny = run_probe(snaps, taker_costs, {"kalshi": Decimal("62"), "polymarket": Decimal("30")})
    assert tiny.uncapped_locked_profit_usd == Decimal("42.40")
    assert tiny.capital_constrained_locked_profit_usd < Decimal("42.40")


def test_missing_cost_model_is_an_error_not_an_implicit_zero(ample_capital):
    a = snapshot("kalshi", asks=levels(("0.62", 1000)))
    b = snapshot("polymarket", bids=levels(("0.70", 800)))
    import pytest

    with pytest.raises(ValueError, match="no cost model"):
        run_probe([a, b], {}, ample_capital)


# --- end to end on the committed example ---------------------------------


def test_example_exercises_every_gate(example_dir, taker_costs, ample_capital):
    report = run_probe(load_snapshots(example_dir / "snapshots.json"), taker_costs, ample_capital)

    assert report.snapshots_considered == 10
    assert report.candidates_considered == 4  # the doubleheader pair never pairs
    assert report.counts[PairStatus.LOCKED_PROFIT.value] == 1
    assert report.counts[PairStatus.MATCHED_NO_PROFIT.value] == 1
    assert report.counts[PairStatus.MISMATCHED.value] == 1
    assert report.counts[PairStatus.UNVERIFIED.value] == 1


def test_example_doubleheader_pair_is_never_considered(example_dir, taker_costs, ample_capital):
    """Game 1 and game 2 land in different buckets, so they are not even candidates."""
    report = run_probe(load_snapshots(example_dir / "snapshots.json"), taker_costs, ample_capital)
    refs = {f"{p.left.market_id}|{p.right.market_id}" for p in report.pairs}
    assert not any("G1" in r and "G2" in r for r in refs)


def test_registry_converts_an_unverified_pair_into_a_measured_one(
    example_dir, taker_costs, ample_capital
):
    snaps = load_snapshots(example_dir / "snapshots.json")
    registry = SettlementRegistry.load(example_dir / "settlement.json")
    report = run_probe(registry.apply_all(snaps), taker_costs, ample_capital)

    assert report.counts[PairStatus.UNVERIFIED.value] == 0
    assert report.counts[PairStatus.LOCKED_PROFIT.value] == 2


def test_report_serializes_decimals_as_strings(example_dir, taker_costs, ample_capital):
    import json

    report = run_probe(load_snapshots(example_dir / "snapshots.json"), taker_costs, ample_capital)
    payload = report.as_dict()
    text = json.dumps(payload)  # must not raise on Decimal

    assert isinstance(json.loads(text)["uncapped_locked_profit_usd"], str)
    reasons = [p["verdict"]["reasons"] for p in payload["pairs"] if p["verdict"]]
    assert any(reasons), "rejected pairs must explain themselves"


def test_empty_input_reports_every_status_key(taker_costs, ample_capital):
    report = run_probe([], taker_costs, ample_capital)
    assert set(report.counts) == {s.value for s in PairStatus}
    assert report.snapshots_considered == 0
    assert report.uncapped_locked_profit_usd == Decimal(0)


def test_crossed_snapshot_never_reaches_the_probe():
    """A crossed single-venue book is rejected at construction, not measured."""
    import pytest

    from emc.models import CrossedBookError

    with pytest.raises(CrossedBookError):
        snapshot("kalshi", bids=levels(("0.45", 10)), asks=levels(("0.44", 10)))
