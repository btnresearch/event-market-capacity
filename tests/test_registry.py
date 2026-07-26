from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from conftest import levels, snapshot, terms

from emc.registry import SettlementRegistry
from emc.serde import settlement_to_dict

VALID = {
    "venue": "polymarket",
    "market_id": "0xTOKEN",
    "source_url": "https://example.invalid/rules",
    "verified_on": "2026-07-26",
    "verified_by": "analyst",
    "terms": settlement_to_dict(terms()),
}


def test_entry_with_full_provenance_loads():
    registry = SettlementRegistry.from_dicts([VALID])
    assert len(registry) == 1
    assert registry.terms_for("polymarket", "0xTOKEN") == terms()


@pytest.mark.parametrize("field", ["source_url", "verified_on"])
def test_uncited_settlement_claim_is_refused(field):
    """Provenance is mandatory: an unsourced rules claim must not reach a capacity number."""
    record = dict(VALID)
    record.pop(field)
    with pytest.raises(ValueError, match="provenance"):
        SettlementRegistry.from_dicts([record])


@pytest.mark.parametrize("field", ["source_url", "verified_on"])
def test_blank_provenance_is_also_refused(field):
    record = dict(VALID) | {field: ""}
    with pytest.raises(ValueError, match="provenance"):
        SettlementRegistry.from_dicts([record])


@pytest.mark.parametrize("field", ["venue", "market_id"])
def test_entry_must_identify_a_market(field):
    record = dict(VALID)
    record.pop(field)
    with pytest.raises(ValueError, match=field):
        SettlementRegistry.from_dicts([record])


def test_lookup_misses_return_none_rather_than_empty_terms():
    registry = SettlementRegistry.from_dicts([VALID])
    assert registry.terms_for("kalshi", "0xTOKEN") is None
    assert registry.entry_for("kalshi", "0xTOKEN") is None


def test_apply_attaches_verified_terms():
    registry = SettlementRegistry.from_dicts([VALID])
    snap = snapshot(
        "polymarket",
        market_id="0xTOKEN",
        asks=levels(("0.54", 10)),
        settlement=terms(void_rule=None),
    )
    assert registry.apply(snap).settlement.void_rule == terms().void_rule


def test_apply_replaces_rather_than_merges():
    """Verified terms fully supersede inferred ones.

    Merging would leave a record in which some fields are sourced and some are
    guessed, with no way to tell them apart.
    """
    sparse = dict(VALID) | {
        "terms": settlement_to_dict(
            terms(
                event_key=None,
                includes_overtime=None,
                scheduled_start_utc=datetime(2026, 7, 27, 23, 0, tzinfo=timezone.utc),
            )
        )
    }
    registry = SettlementRegistry.from_dicts([sparse])
    snap = snapshot("polymarket", market_id="0xTOKEN", asks=levels(("0.54", 10)))
    applied = registry.apply(snap)

    assert snap.settlement.includes_overtime is True
    assert applied.settlement.includes_overtime is None


def test_apply_leaves_unlisted_snapshots_untouched():
    registry = SettlementRegistry.from_dicts([VALID])
    snap = snapshot("kalshi", asks=levels(("0.54", 10)))
    assert registry.apply(snap) is snap


def test_apply_all_preserves_order_and_count():
    registry = SettlementRegistry.from_dicts([VALID])
    snaps = (
        snapshot("kalshi", market_id="k", asks=levels(("0.54", 10))),
        snapshot("polymarket", market_id="0xTOKEN", asks=levels(("0.54", 10))),
    )
    applied = registry.apply_all(snaps)
    assert [s.market_id for s in applied] == ["k", "0xTOKEN"]


def test_load_accepts_wrapped_and_bare_forms(tmp_path):
    wrapped = tmp_path / "w.json"
    wrapped.write_text(json.dumps({"entries": [VALID]}), encoding="utf-8")
    bare = tmp_path / "b.json"
    bare.write_text(json.dumps([VALID]), encoding="utf-8")
    assert len(SettlementRegistry.load(wrapped)) == 1
    assert len(SettlementRegistry.load(bare)) == 1


def test_committed_example_registry_is_valid(example_dir):
    registry = SettlementRegistry.load(example_dir / "settlement.json")
    entry = registry.entry_for("polymarket", "0xNYYBOS-NYY-YES")
    assert entry is not None
    assert entry.source_url
    assert entry.verified_on
    # The committed example is a placeholder and says so, so nobody cites it.
    assert "PLACEHOLDER" in (entry.note or "")


def test_empty_registry_is_usable():
    registry = SettlementRegistry()
    assert len(registry) == 0
    snap = snapshot("kalshi", asks=levels(("0.54", 10)))
    assert registry.apply_all([snap]) == (snap,)
