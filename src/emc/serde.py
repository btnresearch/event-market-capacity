"""Snapshot and settlement serialization.

One format is shared by fixtures, recorded captures, and the CLI, so a capture
taken from a live venue replays through the exact code path the tests exercise.

Prices are written as decimal *strings*, never JSON numbers: a round trip through
a float perturbs quotes in the fourth decimal place, the same order of magnitude
as the edges being measured.

Coded settlement fields are serialized by their enum value and are rejected on
load if unrecognized. A typo in a rule code must fail loudly rather than silently
becoming ``None``, because ``None`` reads downstream as "unverified" and would
quietly turn a data-entry error into a settlement claim nobody checked.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, TypeVar

from emc.models import (
    BookLevel,
    ExtraInnings,
    ListedPitcherRule,
    MarketSnapshot,
    MarketType,
    OrderBook,
    PostponementTreatment,
    SettlementTerms,
    SuspendedTreatment,
    TieTreatment,
    VenueChangeTreatment,
)

__all__ = [
    "load_snapshots",
    "settlement_from_dict",
    "settlement_to_dict",
    "snapshot_from_dict",
    "snapshot_to_dict",
    "write_snapshots",
]

E = TypeVar("E", bound=Enum)

_CODED_FIELDS: dict[str, type[Enum]] = {
    "market_type": MarketType,
    "extra_innings": ExtraInnings,
    "tie_treatment": TieTreatment,
    "postponement": PostponementTreatment,
    "suspended": SuspendedTreatment,
    "listed_pitcher": ListedPitcherRule,
    "venue_change": VenueChangeTreatment,
}

_PLAIN_FIELDS = (
    "sport",
    "league",
    "home_team",
    "away_team",
    "game_date",
    "outcome_team",
    "settlement_source",
)


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp must include a timezone offset: {value!r}")
    return parsed.astimezone(timezone.utc)


def _coded(field: str, enum_cls: type[E], value: Any) -> E | None:
    if value is None:
        return None
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(value)
    except ValueError as exc:
        valid = sorted(m.value for m in enum_cls)
        raise ValueError(
            f"unrecognized code for {field!r}: {value!r}; valid values are {valid}"
        ) from exc


def settlement_from_dict(data: dict[str, Any] | None) -> SettlementTerms:
    if not data:
        return SettlementTerms()
    kwargs: dict[str, Any] = {name: data.get(name) for name in _PLAIN_FIELDS}
    for name, enum_cls in _CODED_FIELDS.items():
        kwargs[name] = _coded(name, enum_cls, data.get(name))

    dh = data.get("doubleheader_number")
    kwargs["doubleheader_number"] = None if dh is None else int(dh)
    window = data.get("postponement_window_hours")
    kwargs["postponement_window_hours"] = None if window is None else int(window)
    innings = data.get("minimum_innings")
    kwargs["minimum_innings"] = None if innings is None else Decimal(str(innings))
    for name in ("scheduled_start_utc", "settlement_deadline_utc"):
        raw = data.get(name)
        kwargs[name] = None if raw is None else _parse_dt(raw)
    return SettlementTerms(**kwargs)


def settlement_to_dict(terms: SettlementTerms) -> dict[str, Any]:
    out: dict[str, Any] = {name: getattr(terms, name) for name in _PLAIN_FIELDS}
    for name in _CODED_FIELDS:
        value = getattr(terms, name)
        out[name] = None if value is None else value.value
    out["doubleheader_number"] = terms.doubleheader_number
    out["postponement_window_hours"] = terms.postponement_window_hours
    out["minimum_innings"] = (
        None if terms.minimum_innings is None else str(terms.minimum_innings)
    )
    for name in ("scheduled_start_utc", "settlement_deadline_utc"):
        value = getattr(terms, name)
        out[name] = None if value is None else value.isoformat()
    return out


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
    """Load snapshots from a JSON file, a JSONL file, or a directory of either."""
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
