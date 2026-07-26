"""Settlement-equivalence adjudication.

This module exists because the naive version of the mission's question has a
misleading answer. Scanning two venues for the same game and differencing the
prices produces a long list of apparent cross-venue spreads, and most of them
are not spreads at all: they are the market correctly pricing two contracts that
resolve differently. Different overtime treatment, different postponement and
void rules, a different settlement source, or a subtly different outcome
definition all show up as price divergence that no position can capture.

So the adjudicator is deliberately pessimistic. A pair is ``MATCHED`` only when
every field required for settlement equivalence is present on both sides and
agrees. Any conflict is ``MISMATCHED``. Anything absent, or any pair of distinct
settlement sources whose equivalence has not been established, is ``UNVERIFIED``
and is excluded from capacity. Excluding a real opportunity costs nothing here;
counting a fake one corrupts the measurement.
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

# Dropped before comparison so that "Los Angeles Lakers" and "LA Lakers" do not
# fail on decoration alone. Kept deliberately short: aggressive normalization
# manufactures agreement, which is the failure mode this module guards against.
_NOISE_TOKENS = frozenset({"the", "fc", "cf", "sc", "club"})


def normalize_name(value: str) -> str:
    """Casefold, strip accents and punctuation, and drop low-signal tokens."""
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = _PUNCT.sub(" ", ascii_only.casefold())
    tokens = [t for t in _WS.sub(" ", lowered).strip().split(" ") if t and t not in _NOISE_TOKENS]
    return " ".join(tokens)


@dataclass(frozen=True, slots=True)
class MatchPolicy:
    """Tunables for adjudication.

    ``start_tolerance`` allows for venues publishing scheduled start times that
    differ by a few minutes for the same fixture. It is a tolerance on clerical
    disagreement, not on the event being different: beyond it, two start times
    imply two different fixtures.

    ``trusted_equivalent_sources`` lets an operator assert, explicitly and in
    writing, that two named settlement sources are equivalent for a league. It
    defaults to empty so that nothing is assumed on the reader's behalf.
    """

    start_tolerance: timedelta = timedelta(minutes=15)
    trusted_equivalent_sources: frozenset[frozenset[str]] = frozenset()
    require_void_rule: bool = True
    require_overtime_rule: bool = True


_DEFAULT_POLICY = MatchPolicy()


def _both_present(a: object | None, b: object | None) -> bool:
    return a is not None and b is not None


def adjudicate(
    a: SettlementTerms,
    b: SettlementTerms,
    policy: MatchPolicy | None = None,
) -> MatchVerdict:
    """Decide whether two contracts provably settle on the same outcome.

    Returns ``MISMATCHED`` if any comparable field conflicts, ``UNVERIFIED`` if
    any required field is missing or unproven, and ``MATCHED`` only when every
    required field is present and agrees.
    """
    policy = policy or _DEFAULT_POLICY
    conflicts: list[str] = []
    unproven: list[str] = []

    def compare(label: str, left: object | None, right: object | None) -> None:
        if not _both_present(left, right):
            missing = "both" if left is None and right is None else ("a" if left is None else "b")
            unproven.append(f"{label}: missing on {missing}")
        elif left != right:
            conflicts.append(f"{label}: {left!r} != {right!r}")

    compare("league", _norm_or_none(a.league), _norm_or_none(b.league))
    compare("market_type", _norm_or_none(a.market_type), _norm_or_none(b.market_type))
    compare("outcome", _norm_or_none(a.outcome), _norm_or_none(b.outcome))

    if policy.require_overtime_rule:
        compare("includes_overtime", a.includes_overtime, b.includes_overtime)
    if policy.require_void_rule:
        compare("void_rule", _norm_or_none(a.void_rule), _norm_or_none(b.void_rule))

    # Participants: compared as normalized sets so home/away ordering is irrelevant.
    if not a.participants or not b.participants:
        unproven.append("participants: missing on one or both sides")
    else:
        left = {normalize_name(p) for p in a.participants}
        right = {normalize_name(p) for p in b.participants}
        if left != right:
            conflicts.append(f"participants: {sorted(left)} != {sorted(right)}")

    # Scheduled start: clerical tolerance only.
    if not _both_present(a.scheduled_start_utc, b.scheduled_start_utc):
        unproven.append("scheduled_start_utc: missing on one or both sides")
    else:
        skew = abs(a.scheduled_start_utc - b.scheduled_start_utc)  # type: ignore[operator]
        if skew > policy.start_tolerance:
            conflicts.append(f"scheduled_start_utc: differ by {skew}")

    # Settlement source: identical passes; distinct requires an explicit assertion.
    if not _both_present(a.settlement_source, b.settlement_source):
        unproven.append("settlement_source: missing on one or both sides")
    else:
        left_src = _norm_or_none(a.settlement_source)
        right_src = _norm_or_none(b.settlement_source)
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


def _norm_or_none(value: str | None) -> str | None:
    return None if value is None else normalize_name(value)
