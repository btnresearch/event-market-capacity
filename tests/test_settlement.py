from __future__ import annotations

from datetime import datetime, timedelta, timezone

from conftest import terms

from emc.models import (
    ExtraInnings,
    ListedPitcherRule,
    MarketType,
    MatchStatus,
    PostponementTreatment,
    SuspendedTreatment,
)
from emc.settlement import MatchPolicy, adjudicate, normalize_name


def test_identical_fully_specified_terms_match():
    assert adjudicate(terms(), terms()).status is MatchStatus.MATCHED


def test_home_away_ordering_does_not_matter():
    swapped = terms(home_team="New York Yankees", away_team="Boston Red Sox")
    assert adjudicate(terms(), swapped).status is MatchStatus.MATCHED


# --- coded rule fields, not prose ----------------------------------------


def test_equivalent_rules_worded_differently_are_no_longer_a_false_mismatch():
    """The defect that coded fields fix.

    Comparing rule PROSE reported economically identical contracts as MISMATCHED —
    a positive claim they settle differently, which was false. Codes make equality
    a real test.
    """
    a = terms(suspended=SuspendedTreatment.OFFICIAL_IF_REGULATION_COMPLETE)
    b = terms(suspended=SuspendedTreatment.OFFICIAL_IF_REGULATION_COMPLETE)
    assert adjudicate(a, b).status is MatchStatus.MATCHED


def test_conflicting_extra_innings_treatment_is_a_mismatch():
    verdict = adjudicate(terms(), terms(extra_innings=ExtraInnings.REGULATION_ONLY))
    assert verdict.status is MatchStatus.MISMATCHED
    assert any("extra_innings" in r for r in verdict.reasons)


def test_conflicting_listed_pitcher_rule_is_a_mismatch():
    verdict = adjudicate(terms(), terms(listed_pitcher=ListedPitcherRule.BOTH_MUST_START))
    assert verdict.status is MatchStatus.MISMATCHED
    assert any("listed_pitcher" in r for r in verdict.reasons)


def test_conflicting_postponement_treatment_is_a_mismatch():
    verdict = adjudicate(
        terms(), terms(postponement=PostponementTreatment.FOLLOWS_RESCHEDULED_GAME)
    )
    assert verdict.status is MatchStatus.MISMATCHED


def test_conflicting_market_type_is_a_mismatch():
    verdict = adjudicate(terms(), terms(market_type=MarketType.RUN_LINE))
    assert verdict.status is MatchStatus.MISMATCHED


def test_missing_coded_field_is_unverified_not_matched():
    verdict = adjudicate(terms(), terms(listed_pitcher=None))
    assert verdict.status is MatchStatus.UNVERIFIED
    assert any("listed_pitcher" in r for r in verdict.reasons)


def test_missing_on_both_sides_is_still_unverified():
    verdict = adjudicate(terms(suspended=None), terms(suspended=None))
    assert verdict.status is MatchStatus.UNVERIFIED


def test_a_conflict_outranks_a_missing_field():
    """Mismatched is the more actionable verdict: known different, not merely unproven."""
    verdict = adjudicate(
        terms(extra_innings=ExtraInnings.INCLUDED, suspended=None),
        terms(extra_innings=ExtraInnings.REGULATION_ONLY, suspended=None),
    )
    assert verdict.status is MatchStatus.MISMATCHED


# --- MLB event identity ---------------------------------------------------


def test_doubleheader_game_one_and_two_are_not_the_same_event():
    """Same teams, same date, different game. A matcher without this pairs them."""
    verdict = adjudicate(terms(doubleheader_number=1), terms(doubleheader_number=2))
    assert verdict.status is MatchStatus.MISMATCHED
    assert any("doubleheader_number" in r for r in verdict.reasons)


def test_missing_doubleheader_number_is_unverified():
    assert adjudicate(terms(), terms(doubleheader_number=None)).status is MatchStatus.UNVERIFIED


def test_different_game_dates_are_a_mismatch():
    verdict = adjudicate(terms(), terms(game_date="2026-07-28"))
    assert verdict.status is MatchStatus.MISMATCHED


def test_different_teams_are_a_mismatch():
    verdict = adjudicate(terms(), terms(home_team="Miami Marlins"))
    assert verdict.status is MatchStatus.MISMATCHED
    assert any("teams" in r for r in verdict.reasons)


def test_opposite_sides_of_the_same_game_are_not_the_same_contract():
    """Contracts on the two teams are the two sides of the event, not a matched pair."""
    verdict = adjudicate(terms(), terms(outcome_team="Boston Red Sox"))
    assert verdict.status is MatchStatus.MISMATCHED
    assert any("outcome_team" in r for r in verdict.reasons)


def test_small_start_time_disagreement_is_tolerated():
    later = terms(scheduled_start_utc=datetime(2026, 7, 27, 17, 15, tzinfo=timezone.utc))
    assert adjudicate(terms(), later).status is MatchStatus.MATCHED


def test_large_start_time_disagreement_implies_a_different_fixture():
    verdict = adjudicate(
        terms(), terms(scheduled_start_utc=datetime(2026, 7, 27, 23, 0, tzinfo=timezone.utc))
    )
    assert verdict.status is MatchStatus.MISMATCHED


def test_start_tolerance_is_configurable():
    later = terms(scheduled_start_utc=datetime(2026, 7, 27, 17, 10, tzinfo=timezone.utc))
    strict = MatchPolicy(start_tolerance=timedelta(minutes=1))
    assert adjudicate(terms(), later, strict).status is MatchStatus.MISMATCHED


# --- postponement window --------------------------------------------------


def test_postponement_window_must_agree_when_the_treatment_depends_on_it():
    verdict = adjudicate(terms(), terms(postponement_window_hours=48))
    assert verdict.status is MatchStatus.MISMATCHED
    assert any("postponement_window_hours" in r for r in verdict.reasons)


def test_postponement_window_is_ignored_when_the_treatment_does_not_use_it():
    """A void-immediately rule has no window, so a missing window is not a gap."""
    a = terms(postponement=PostponementTreatment.VOID_IMMEDIATELY, postponement_window_hours=None)
    b = terms(postponement=PostponementTreatment.VOID_IMMEDIATELY, postponement_window_hours=None)
    assert adjudicate(a, b).status is MatchStatus.MATCHED


def test_missing_window_is_unverified_when_the_treatment_needs_it():
    a = terms(postponement_window_hours=None)
    b = terms(postponement_window_hours=None)
    verdict = adjudicate(a, b)
    assert verdict.status is MatchStatus.UNVERIFIED
    assert any("postponement_window_hours" in r for r in verdict.reasons)


# --- settlement source ----------------------------------------------------


def test_distinct_settlement_sources_are_unverified_by_default():
    verdict = adjudicate(terms(), terms(settlement_source="ESPN box score"))
    assert verdict.status is MatchStatus.UNVERIFIED
    assert any("settlement_source" in r for r in verdict.reasons)


def test_settlement_sources_can_be_asserted_equivalent_explicitly():
    policy = MatchPolicy(
        trusted_equivalent_sources=frozenset(
            {frozenset({"mlb official final score", "espn box score"})}
        )
    )
    verdict = adjudicate(terms(), terms(settlement_source="ESPN box score"), policy)
    assert verdict.status is MatchStatus.MATCHED


def test_required_field_set_is_configurable_for_exploratory_scans():
    """Waiving a requirement widens the candidate set; it proves nothing.

    Exposed so an operator can count candidates before doing the manual rules work.
    """
    lenient = MatchPolicy(required_fields=("sport", "league", "market_type"))
    a = terms(listed_pitcher=None, suspended=None)
    b = terms(listed_pitcher=None, suspended=None)
    assert adjudicate(a, b, lenient).status is MatchStatus.MATCHED
    assert adjudicate(a, b).status is MatchStatus.UNVERIFIED


# --- name normalization ---------------------------------------------------


def test_league_is_compared_case_insensitively():
    assert adjudicate(terms(league="MLB"), terms(league="mlb")).status is MatchStatus.MATCHED


def test_normalize_name_strips_accents_punctuation_and_filler():
    assert normalize_name("Atlético Madrid") == "atletico madrid"
    assert normalize_name("FC Barcelona") == "barcelona"
    assert normalize_name("St. Louis Cardinals") == "st louis cardinals"
    assert normalize_name("  The   Lakers ") == "lakers"


def test_normalize_name_does_not_conflate_distinct_teams():
    assert normalize_name("New York Yankees") != normalize_name("New York Mets")
    assert normalize_name("Chicago Cubs") != normalize_name("Chicago White Sox")
