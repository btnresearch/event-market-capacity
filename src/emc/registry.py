"""Human-curated settlement terms, with provenance.

This module exists because of the most important negative result the probe
produces: **venue market-list APIs do not publish the fields that settlement
equivalence depends on.** Titles, tickers, and close times are available;
overtime treatment, postponement and void handling, and the authoritative
settlement source generally are not, or are only stated in prose rules pages that
are not part of the market payload.

The consequence is structural, not incidental. An adapter that reads only public
market metadata leaves those fields ``None``, so :func:`emc.settlement.adjudicate`
returns ``UNVERIFIED`` and the pair is excluded. That is the correct behavior, and
it means the honest ceiling on a fully automated probe is a list of *candidate*
pairs, not measured capacity.

To cross that gap a human has to read both venues' rules and record what they
found. This registry is where that goes, and it requires provenance on every
entry: a source URL and a verification date. An uncited settlement claim is
refused at load time rather than accepted and quietly propagated into a capacity
number.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from emc.models import MarketSnapshot, SettlementTerms
from emc.serde import settlement_from_dict, settlement_to_dict

__all__ = ["RegistryEntry", "SettlementRegistry"]

_REQUIRED_PROVENANCE = ("source_url", "verified_on")


@dataclass(frozen=True, slots=True)
class RegistryEntry:
    venue: str
    market_id: str
    terms: SettlementTerms
    source_url: str
    verified_on: str
    verified_by: str | None = None
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "venue": self.venue,
            "market_id": self.market_id,
            "source_url": self.source_url,
            "verified_on": self.verified_on,
            "verified_by": self.verified_by,
            "note": self.note,
            "terms": settlement_to_dict(self.terms),
        }


class SettlementRegistry:
    """Lookup of verified settlement terms by ``(venue, market_id)``."""

    def __init__(self, entries: Iterable[RegistryEntry] = ()) -> None:
        self._entries: dict[tuple[str, str], RegistryEntry] = {}
        for entry in entries:
            self._entries[(entry.venue, entry.market_id)] = entry

    def __len__(self) -> int:
        return len(self._entries)

    @classmethod
    def from_dicts(cls, records: Iterable[dict[str, Any]]) -> SettlementRegistry:
        entries = []
        for record in records:
            missing = [f for f in _REQUIRED_PROVENANCE if not record.get(f)]
            if missing:
                raise ValueError(
                    f"settlement entry {record.get('venue')}:{record.get('market_id')} "
                    f"is missing provenance {missing}; settlement claims must cite a source"
                )
            for field_name in ("venue", "market_id"):
                if not record.get(field_name):
                    raise ValueError(f"settlement entry missing {field_name!r}")
            entries.append(
                RegistryEntry(
                    venue=str(record["venue"]),
                    market_id=str(record["market_id"]),
                    terms=settlement_from_dict(record.get("terms")),
                    source_url=str(record["source_url"]),
                    verified_on=str(record["verified_on"]),
                    verified_by=record.get("verified_by"),
                    note=record.get("note"),
                )
            )
        return cls(entries)

    @classmethod
    def load(cls, path: str | Path) -> SettlementRegistry:
        parsed = json.loads(Path(path).read_text(encoding="utf-8"))
        records = parsed["entries"] if isinstance(parsed, dict) else parsed
        return cls.from_dicts(records)

    def terms_for(self, venue: str, market_id: str) -> SettlementTerms | None:
        entry = self._entries.get((venue, market_id))
        return None if entry is None else entry.terms

    def entry_for(self, venue: str, market_id: str) -> RegistryEntry | None:
        return self._entries.get((venue, market_id))

    def apply(self, snapshot: MarketSnapshot) -> MarketSnapshot:
        """Attach verified terms to a snapshot, leaving it untouched if none exist.

        Registry terms replace whatever the adapter inferred rather than merging
        with it. Merging would blend a verified field with an inferred one and
        leave no way to tell which is which.
        """
        terms = self.terms_for(snapshot.venue, snapshot.market_id)
        if terms is None:
            return snapshot
        return replace(snapshot, settlement=terms)

    def apply_all(self, snapshots: Iterable[MarketSnapshot]) -> tuple[MarketSnapshot, ...]:
        return tuple(self.apply(s) for s in snapshots)
