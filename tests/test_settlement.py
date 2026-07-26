from __future__ import annotations

from datetime import datetime, timedelta, timezone

from conftest import terms

from emc.models import MatchStatus
from emc.settlement import MatchPolicy, adjudicate, normalize_name


def test_identical_fully_specified_terms_match():
    assert adjudicate(terms(), terms()).status is MatchStatus.MATCHED


def test_participant_order_does_not_matter():
    a = terms(participants=frozenset({"Los Angeles Lakers", "Boston Celtics"}))
    b = terms(participants=frozenset({"Boston Celtics", "Los Angeles Lakers"}))
    assert adjudicate(a, b).status is MatchStatus.MATCHED


def test_conflicting_overtime_treatment_is_a_mismatch_not_an_edge():
    verdict = adjudicate(terms(includes_overtime=True), terms(includes_overtime=False))
    assert verdict.status is MatchStatus.MISMATCHED
    assert any("includes_overtime" in r for r in verdict.reasons)


def test_conflicting_void_rule_is_a_mismatch():
    verdict = adjudicate(terms(), terms(void_rule="no void; graded as played"))
    assert verdict.status is MatchStatus.MISMATCHED


def test_different_participants_are_a_mismatch():
    verdict = adjudicate(terms(), terms(participants=frozenset({"Miami Heat", "Chicago Bulls"})))
    assert verdict.status is MatchStatus.MISMATCHED
    assert any("participants" in r for r in verdict.reasons)


def test_missing_field_is_unverified_not_matched():
    verdict = adjudicate(terms(), terms(void_rule=None))
    assert verdict.status is MatchStatus.UNVERIFIED
    assert any("void_rule" in r for r in verdict.reasons)


def test_missing_on_both_sides_is_still_unverified():
    verdict = adjudicate(terms(includes_overtime=None), terms(includes_overtime=None))
    assert verdict.status is MatchStatus.UNVERIFIED


def test_absent_participants_are_unverified():
    verdict = adjudicate(terms(participants=frozenset()), terms())
    assert verdict.status is MatchStatus.UNVERIFIED


def test_a_conflict_outranks_a_missing_field():
    """A pair that both conflicts and lacks evidence is reported as the conflict.

    Mismatched is the more actionable verdict: it says the two contracts are known
    to be different, not merely unproven to be the same.
    """
    verdict = adjudicate(
        terms(includes_overtime=True, void_rule=None),
        terms(includes_overtime=False, void_rule=None),
    )
    assert verdict.status is MatchStatus.MISMATCHED


def test_small_start_time_disagreement_is_tolerated():
    later = terms(scheduled_start_utc=datetime(2026, 7, 27, 23, 10, tzinfo=timezone.utc))
    assert adjudicate(terms(), later).status is MatchStatus.MATCHED


def test_large_start_time_disagreement_implies_a_different_fixture():
    next_day = terms(scheduled_start_utc=datetime(2026, 7, 28, 23, 0, tzinfo=timezone.utc))
    verdict = adjudicate(terms(), next_day)
    assert verdict.status is MatchStatus.MISMATCHED
    assert any("scheduled_start_utc" in r for r in verdict.reasons)


def test_start_tolerance_is_configurable():
    later = terms(scheduled_start_utc=datetime(2026, 7, 27, 23, 10, tzinfo=timezone.utc))
    strict = MatchPolicy(start_tolerance=timedelta(minutes=1))
    assert adjudicate(terms(), later, strict).status is MatchStatus.MISMATCHED


def test_distinct_settlement_sources_are_unverified_by_default():
    verdict = adjudicate(terms(), terms(settlement_source="ESPN box score"))
    assert verdict.status is MatchStatus.UNVERIFIED
    assert any("settlement_source" in r for r in verdict.reasons)


def test_settlement_sources_can_be_asserted_equivalent_explicitly():
    policy = MatchPolicy(
        trusted_equivalent_sources=frozenset(
            {frozenset({"nba official final score", "espn box score"})}
        )
    )
    verdict = adjudicate(terms(), terms(settlement_source="ESPN box score"), policy)
    assert verdict.status is MatchStatus.MATCHED


def test_policy_can_waive_rule_fields_for_exploratory_scans():
    """Waiving a requirement widens the candidate set; it does not prove anything.

    Exposed so an operator can count candidates before doing the manual rules work,
    which is the realistic first step of the research.
    """
    lenient = MatchPolicy(require_void_rule=False, require_overtime_rule=False)
    a = terms(void_rule=None, includes_overtime=None)
    b = terms(void_rule=None, includes_overtime=None)
    assert adjudicate(a, b, lenient).status is MatchStatus.MATCHED
    assert adjudicate(a, b).status is MatchStatus.UNVERIFIED


def test_league_and_market_type_are_compared_case_insensitively():
    assert adjudicate(terms(league="NBA"), terms(league="nba")).status is MatchStatus.MATCHED


def test_normalize_name_strips_accents_punctuation_and_filler():
    assert normalize_name("Atlético Madrid") == "atletico madrid"
    assert normalize_name("FC Barcelona") == "barcelona"
    assert normalize_name("St. Louis Blues") == "st louis blues"
    assert normalize_name("  The   Lakers ") == "lakers"


def test_normalize_name_does_not_conflate_distinct_teams():
    assert normalize_name("New York Yankees") != normalize_name("New York Mets")
