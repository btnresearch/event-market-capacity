from __future__ import annotations

import json
from decimal import Decimal

import pytest
from conftest import T0, levels, snapshot

from emc.serde import load_snapshots, snapshot_from_dict, snapshot_to_dict, write_snapshots


def test_round_trip_preserves_prices_exactly():
    """Prices must survive serialization bit for bit.

    A float round trip perturbs quotes in the fourth decimal place, which is the
    same magnitude as the edges being measured.
    """
    original = snapshot(
        "kalshi", bids=levels(("0.5401", 300)), asks=levels(("0.5403", 700))
    )
    restored = snapshot_from_dict(snapshot_to_dict(original))

    assert restored.book.bids[0].price == Decimal("0.5401")
    assert restored.book.asks[0].price == Decimal("0.5403")
    assert restored == original


def test_prices_are_written_as_strings_not_json_numbers():
    payload = json.dumps(snapshot_to_dict(snapshot("kalshi", asks=levels(("0.54", 10)))))
    assert '"0.54"' in payload
    assert ": 0.54" not in payload


def test_round_trip_preserves_settlement_terms():
    original = snapshot("kalshi", asks=levels(("0.54", 10)))
    restored = snapshot_from_dict(snapshot_to_dict(original))
    assert restored.settlement == original.settlement


def test_naive_timestamps_are_rejected_on_load():
    data = snapshot_to_dict(snapshot("kalshi", asks=levels(("0.54", 10))))
    data["captured_at"] = "2026-07-26T22:00:00"
    with pytest.raises(ValueError, match="timezone"):
        snapshot_from_dict(data)


def test_z_suffix_timestamps_are_accepted():
    data = snapshot_to_dict(snapshot("kalshi", asks=levels(("0.54", 10))))
    data["captured_at"] = "2026-07-26T22:00:00Z"
    assert snapshot_from_dict(data).captured_at == T0


@pytest.mark.parametrize("field", ["venue", "market_id", "captured_at", "book"])
def test_missing_required_field_is_an_error(field):
    data = snapshot_to_dict(snapshot("kalshi", asks=levels(("0.54", 10))))
    del data[field]
    with pytest.raises(ValueError, match=field):
        snapshot_from_dict(data)


def test_levels_accept_both_pair_and_object_forms():
    data = snapshot_to_dict(snapshot("kalshi", asks=levels(("0.54", 10))))
    data["book"]["asks"] = [{"price": "0.54", "size": 10}]
    assert snapshot_from_dict(data).book.asks[0].price == Decimal("0.54")


def test_malformed_level_is_rejected():
    data = snapshot_to_dict(snapshot("kalshi", asks=levels(("0.54", 10))))
    data["book"]["asks"] = ["0.54"]
    with pytest.raises(ValueError, match="unrecognized level"):
        snapshot_from_dict(data)


def test_load_accepts_a_wrapped_object_a_bare_list_and_jsonl(tmp_path):
    one = snapshot_to_dict(snapshot("kalshi", asks=levels(("0.54", 10))))

    wrapped = tmp_path / "wrapped.json"
    wrapped.write_text(json.dumps({"snapshots": [one]}), encoding="utf-8")
    bare = tmp_path / "bare.json"
    bare.write_text(json.dumps([one]), encoding="utf-8")
    lines = tmp_path / "stream.jsonl"
    lines.write_text(json.dumps(one) + "\n" + json.dumps(one) + "\n", encoding="utf-8")

    assert len(load_snapshots(wrapped)) == 1
    assert len(load_snapshots(bare)) == 1
    assert len(load_snapshots(lines)) == 2


def test_load_from_a_directory_reads_every_snapshot_file(tmp_path):
    for name in ("a.json", "b.jsonl", "ignored.txt"):
        (tmp_path / name).write_text(
            json.dumps(snapshot_to_dict(snapshot("kalshi", asks=levels(("0.54", 10))))),
            encoding="utf-8",
        )
    assert len(load_snapshots(tmp_path)) == 2


def test_write_then_load_is_stable(tmp_path):
    original = snapshot("kalshi", bids=levels(("0.52", 300)), asks=levels(("0.54", 700)))
    path = tmp_path / "out.json"
    write_snapshots(path, [original])
    assert load_snapshots(path) == (original,)


def test_example_file_loads_and_is_internally_consistent(example_dir):
    snapshots = load_snapshots(example_dir / "snapshots.json")
    assert len(snapshots) == 6
    for snap in snapshots:
        # Every example book must be a legal uncrossed book.
        if snap.book.bids and snap.book.asks:
            assert snap.book.best_bid < snap.book.best_ask
