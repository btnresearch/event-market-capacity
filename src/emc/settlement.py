"""Settlement-equivalence adjudication over canonical coded fields.

The naive version of the mission's question has a misleading answer. Differencing
prices on two venues' "same game" markets produces a long list of apparent
spreads, most of which are not spreads: they are the market correctly pricing two
contracts that resolve differently. Different extra-innings treatment, different
postponement handling, a listed-pitcher requirement on one venue only, or a
different settlement source all show up as price divergence no position can
capture.

The adjudicator is deliberately pessimistic and its verdicts mean specific things:

* ``MATCHED``     — every required coded field is present on both sides and equal.
* ``MISMATCHED``  — two coded fields conflict. A positive claim of difference.
* ``UNVERIFIED``  — evidence is missing, or two distinct settlement sources have
  not been established as equivalent. Not a soft match.

Only ``MATCHED`` pairs are eligible for capacity. Excluding a real opportunity
costs nothing here; counting a fake one corrupts the measurement.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import timedelta

from emc.models import MatchStatus, MatchVerdict, SettlementTerms

__all__ = ["MatchPolicy", "adjudicate", "normalize_name"]

_PUNCT = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")
_NOISE_TOKENS = frozenset({"the", "fc", "cf", "sc", "club"})

# Required for a MATCHED verdict. Every one is a coded enum or a scalar, never
# free text, so equality is a real test rather than a string-similarity accident.
_REQUIRED_CODED_FIELDS = (
    "sport",
    "league",
    "market_type",
    "extra_innings",
    "tie_treatment",
    "postponement",
    "suspended",
    "listed_pitcher",
    "venue_change",
)


def normalize_name(value: str) -> str:
    """Casefold, strip accents and punctuation, drop low-signal tokens.

    Kept deliberately conservative: aggressive normalization manufactures
    agreement, which is the failure mode this module exists to prevent.
    """
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = _PUNCT.sub(" ", ascii_only.casefold())
    tokens = [t for t in _WS.sub(" ", lowered).strip().split(" ") if t and t not in _NOISE_TOKENS]
    return " ".join(tokens)


@dataclass(frozen=True, slots=True)
class MatchPolicy:
    """Tunables for adjudication.

    ``start_tolerance`` allows venues to publish scheduled starts differing by a
    few minutes for the same fixture. It is a tolerance on clerical disagreement,
    not on the event being different.

    ``trusted_equivalent_sources`` lets an operator assert in writing that two
    named settlement sources are equivalent. Empty by default so nothing is
    assumed on the reader's behalf.
    """

    start_tolerance: timedelta = timedelta(minutes=15)
    trusted_equivalent_sources: frozenset[frozenset[str]] = frozenset()
    required_fields: tuple[str, ...] = _REQUIRED_CODED_FIELDS


_DEFAULT_POLICY = MatchPolicy()


def adjudicate(
    a: SettlementTerms,
    b: SettlementTerms,
    policy: MatchPolicy | None = None,
) -> MatchVerdict:
    """Decide whether two contracts provably settle on the same outcome."""
    policy = policy or _DEFAULT_POLICY
    conflicts: list[str] = []
    unproven: list[str] = []

    for name in policy.required_fields:
        left, right = getattr(a, name), getattr(b, name)
        if left is None or right is None:
            missing = "both" if left is None and right is None else ("a" if left is None else "b")
            unproven.append(f"{name}: missing on {missing}")
        elif _coerce(left) != _coerce(right):
            conflicts.append(f"{name}: {_render(left)} != {_render(right)}")

    # Teams, compared as normalized sets so home/away ordering is irrelevant.
    if not a.participants or not b.participants:
        unproven.append("teams: missing on one or both sides")
    else:
        left_teams = {normalize_name(t) for t in a.participants}
        right_teams = {normalize_name(t) for t in b.participants}
        if left_teams != right_teams:
            conflicts.append(f"teams: {sorted(left_teams)} != {sorted(right_teams)}")

    # Which team the contract pays on. Two contracts on opposite teams of the same
    # game are not the same contract; they are the two sides of it.
    if a.outcome_team is None or b.outcome_team is None:
        unproven.append("outcome_team: missing on one or both sides")
    elif normalize_name(a.outcome_team) != normalize_name(b.outcome_team):
        conflicts.append(f"outcome_team: {a.outcome_team!r} != {b.outcome_team!r}")

    # Doubleheader number. Same teams, same date, different game is a different event.
    if a.doubleheader_number is None or b.doubleheader_number is None:
        unproven.append("doubleheader_number: missing on one or both sides")
    elif a.doubleheader_number != b.doubleheader_number:
        conflicts.append(
            f"doubleheader_number: {a.doubleheader_number} != {b.doubleheader_number}"
        )

    if a.game_date is None or b.game_date is None:
        unproven.append("game_date: missing on one or both sides")
    elif a.game_date != b.game_date:
        conflicts.append(f"game_date: {a.game_date!r} != {b.game_date!r}")

    # Scheduled start: clerical tolerance only.
    if a.scheduled_start_utc is None or b.scheduled_start_utc is None:
        unproven.append("scheduled_start_utc: missing on one or both sides")
    else:
        skew = abs(a.scheduled_start_utc - b.scheduled_start_utc)
        if skew > policy.start_tolerance:
            conflicts.append(f"scheduled_start_utc: differ by {skew}")

    # Postponement window only matters when the coded treatment depends on it.
    if _window_relevant(a) or _window_relevant(b):
        if a.postponement_window_hours is None or b.postponement_window_hours is None:
            unproven.append("postponement_window_hours: missing on one or both sides")
        elif a.postponement_window_hours != b.postponement_window_hours:
            conflicts.append(
                f"postponement_window_hours: {a.postponement_window_hours} != "
                f"{b.postponement_window_hours}"
            )

    # Settlement source: identical passes; distinct requires an explicit assertion.
    if a.settlement_source is None or b.settlement_source is None:
        unproven.append("settlement_source: missing on one or both sides")
    else:
        left_src = normalize_name(a.settlement_source)
        right_src = normalize_name(b.settlement_source)
        if left_src != right_src and frozenset({left_src, right_src}) not in policy.trusted_equivalent_sources:
            unproven.append(
                f"settlement_source: {a.settlement_source!r} vs {b.settlement_source!r} "
                "not established as equivalent"
            )

    if conflicts:
        return MatchVerdict(MatchStatus.MISMATCHED, tuple(conflicts))
    if unproven:
        return MatchVerdict(MatchStatus.UNVERIFIED, tuple(unproven))
    return MatchVerdict(MatchStatus.MATCHED, ())


def _window_relevant(terms: SettlementTerms) -> bool:
    from emc.models import PostponementTreatment

    return terms.postponement is PostponementTreatment.VOID_IF_NOT_PLAYED_IN_WINDOW


def _coerce(value: object) -> object:
    """Normalize strings for comparison; leave coded enums and scalars alone."""
    return normalize_name(value) if isinstance(value, str) else value


def _render(value: object) -> str:
    return repr(getattr(value, "value", value))
