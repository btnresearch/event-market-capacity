"""Snapshot serialization.

One format is shared by fixtures, recorded captures, and the CLI, so that a
capture taken from a live venue can be replayed through the exact code path the
tests exercise. Prices are written as decimal *strings*, never JSON numbers: a
round trip through a float would perturb quotes at the fourth decimal place,
which is the same order of magnitude as the edges being measured.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from emc.models import BookLevel, MarketSnapshot, OrderBook, SettlementTerms

__all__ = [
    "load_snapshots",
    "settlement_from_dict",
    "settlement_to_dict",
    "snapshot_from_dict",
    "snapshot_to_dict",
    "write_snapshots",
]


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must include a timezone offset: {value!r}")
    return parsed.astimezone(timezone.utc)


def settlement_from_dict(data: dict[str, Any] | None) -> SettlementTerms:
    if not data:
        return SettlementTerms()
    start = data.get("scheduled_start_utc")
    return SettlementTerms(
        event_key=data.get("event_key"),
        league=data.get("league"),
        participants=frozenset(data.get("participants") or ()),
        scheduled_start_utc=None if start is None else _parse_dt(start),
        market_type=data.get("market_type"),
        outcome=data.get("outcome"),
        settlement_source=data.get("settlement_source"),
        includes_overtime=data.get("includes_overtime"),
        void_rule=data.get("void_rule"),
    )


def settlement_to_dict(terms: SettlementTerms) -> dict[str, Any]:
    return {
        "event_key": terms.event_key,
        "league": terms.league,
        "participants": sorted(terms.participants),
        "scheduled_start_utc": (
            None if terms.scheduled_start_utc is None else terms.scheduled_start_utc.isoformat()
        ),
        "market_type": terms.market_type,
        "outcome": terms.outcome,
        "settlement_source": terms.settlement_source,
        "includes_overtime": terms.includes_overtime,
        "void_rule": terms.void_rule,
    }


def _levels_from(raw: Sequence[Any]) -> tuple[BookLevel, ...]:
    out = []
    for entry in raw:
        if isinstance(entry, dict):
            price, size = entry["price"], entry["size"]
        elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
            price, size = entry[0], entry[1]
        else:
            raise ValueError(f"unrecognized level: {entry!r}")
        out.append(BookLevel(price=Decimal(str(price)), size=int(size)))
    return tuple(out)


def snapshot_from_dict(data: dict[str, Any]) -> MarketSnapshot:
    """Build a snapshot, letting model validation reject malformed books."""
    for required in ("venue", "market_id", "captured_at", "book"):
        if required not in data:
            raise ValueError(f"snapshot missing required field {required!r}")
    book = data["book"]
    return MarketSnapshot(
        venue=str(data["venue"]),
        market_id=str(data["market_id"]),
        book=OrderBook(
            bids=_levels_from(book.get("bids") or ()),
            asks=_levels_from(book.get("asks") or ()),
        ),
        captured_at=_parse_dt(data["captured_at"]),
        settlement=settlement_from_dict(data.get("settlement")),
        payout_usd=Decimal(str(data.get("payout_usd", "1"))),
        title=data.get("title"),
    )


def snapshot_to_dict(snapshot: MarketSnapshot) -> dict[str, Any]:
    return {
        "venue": snapshot.venue,
        "market_id": snapshot.market_id,
        "title": snapshot.title,
        "captured_at": snapshot.captured_at.isoformat(),
        "payout_usd": str(snapshot.payout_usd),
        "book": {
            "bids": [[str(lvl.price), lvl.size] for lvl in snapshot.book.bids],
            "asks": [[str(lvl.price), lvl.size] for lvl in snapshot.book.asks],
        },
        "settlement": settlement_to_dict(snapshot.settlement),
    }


def load_snapshots(path: str | Path) -> tuple[MarketSnapshot, ...]:
    """Load snapshots from a JSON file, a JSONL file, or a directory of either.

    Accepts a bare list, a ``{"snapshots": [...]}`` wrapper, or one object per
    line, so a hand-written fixture and a streamed capture both work.
    """
    target = Path(path)
    if target.is_dir():
        out: list[MarketSnapshot] = []
        for child in sorted(target.iterdir()):
            if child.suffix in {".json", ".jsonl"}:
                out.extend(load_snapshots(child))
        return tuple(out)

    text = target.read_text(encoding="utf-8")
    if target.suffix == ".jsonl":
        records: list[Any] = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and "snapshots" in parsed:
            records = list(parsed["snapshots"])
        elif isinstance(parsed, list):
            records = list(parsed)
        else:
            records = [parsed]
    return tuple(snapshot_from_dict(record) for record in records)


def write_snapshots(path: str | Path, snapshots: Iterable[MarketSnapshot]) -> None:
    payload = {"snapshots": [snapshot_to_dict(s) for s in snapshots]}
    Path(path).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
