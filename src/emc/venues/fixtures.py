"""Offline venue backed by recorded snapshots.

This is not only a test double. It is the replay path: a capture taken from a live
venue is written in the same format, so the analysis that produces a published
capacity number runs against a file that can be committed, reviewed, and
re-measured later. A capacity claim nobody else can reproduce is not a finding.
"""

from __future__ import annotations

from pathlib import Path

from emc.models import MarketRef, MarketSnapshot
from emc.serde import load_snapshots

__all__ = ["FixtureVenue"]


class FixtureVenue:
    """Serves snapshots from a file or directory. Performs no I/O to any venue."""

    def __init__(self, path: str | Path, venue: str | None = None) -> None:
        self._snapshots = load_snapshots(path)
        self.venue = venue or ""
        self._by_id = {(s.venue, s.market_id): s for s in self._snapshots}

    @property
    def snapshots(self) -> tuple[MarketSnapshot, ...]:
        """Every snapshot loaded, across all venues in the file."""
        return self._snapshots

    def list_sports_markets(self, limit: int = 100) -> tuple[MarketRef, ...]:
        refs = [s.ref for s in self._snapshots if not self.venue or s.venue == self.venue]
        return tuple(refs[:limit])

    def fetch_snapshot(self, market_id: str) -> MarketSnapshot:
        if self.venue:
            key = (self.venue, market_id)
            if key in self._by_id:
                return self._by_id[key]
        matches = [s for s in self._snapshots if s.market_id == market_id]
        if not matches:
            raise KeyError(f"no fixture snapshot for market_id {market_id!r}")
        if len(matches) > 1:
            raise KeyError(
                f"market_id {market_id!r} is ambiguous across venues "
                f"{sorted(m.venue for m in matches)}; construct FixtureVenue with venue=..."
            )
        return matches[0]
