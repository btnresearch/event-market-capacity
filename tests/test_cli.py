from __future__ import annotations

import json
import re
from decimal import Decimal

import pytest

from emc.cli import build_default_costs, main


def test_probe_on_the_example_exits_clean_and_reports_capacity(capsys, example_dir):
    code = main(["probe", "--snapshots", str(example_dir / "snapshots.json")])
    out = capsys.readouterr().out

    assert code == 0
    assert "capacity at net edge" in out
    assert re.search(r"matched_with_capacity\s+1", out)
    assert re.search(r"mismatched\s+1", out)
    assert re.search(r"unverified\s+1", out)


def test_probe_output_carries_the_caveat_that_capacity_is_not_a_return(capsys, example_dir):
    main(["probe", "--snapshots", str(example_dir / "snapshots.json")])
    out = capsys.readouterr().out
    assert "not an achievable return" in out
    assert "settlement risk" in out


def test_probe_json_output_is_valid_and_decimal_safe(capsys, example_dir):
    code = main(["probe", "--snapshots", str(example_dir / "snapshots.json"), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["candidates_considered"] == 3
    assert payload["aggregate"][0]["capital_usd"] == "778.00"
    assert isinstance(payload["aggregate"][0]["capital_usd"], str)


def test_probe_with_the_registry_verifies_the_extra_pair(capsys, example_dir):
    main(
        [
            "probe",
            "--snapshots",
            str(example_dir / "snapshots.json"),
            "--settlement",
            str(example_dir / "settlement.json"),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["counts"]["matched_with_capacity"] == 2
    assert payload["counts"]["unverified"] == 0


def test_custom_thresholds_are_honored(capsys, example_dir):
    main(
        [
            "probe",
            "--snapshots",
            str(example_dir / "snapshots.json"),
            "--thresholds",
            "0.01,0.03",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert [row["min_net_edge"] for row in payload["aggregate"]] == ["0.01", "0.03"]


def test_max_skew_flag_can_reject_the_whole_example(capsys, example_dir, tmp_path):
    """A zero-tolerance skew setting still passes here, since the example is simultaneous."""
    main(
        [
            "probe",
            "--snapshots",
            str(example_dir / "snapshots.json"),
            "--max-skew",
            "0",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["counts"]["stale_skew"] == 0


def test_unknown_venue_without_a_cost_model_is_an_error(tmp_path):
    from emc.serde import write_snapshots
    from tests_helpers import two_venue_snapshots  # type: ignore[import-not-found]

    path = tmp_path / "snaps.json"
    write_snapshots(path, two_venue_snapshots("mystery-a", "mystery-b"))
    with pytest.raises(ValueError, match="no cost model"):
        main(["probe", "--snapshots", str(path)])


def test_allow_zero_cost_overrides_the_missing_model(capsys, tmp_path):
    from emc.serde import write_snapshots
    from tests_helpers import two_venue_snapshots  # type: ignore[import-not-found]

    path = tmp_path / "snaps.json"
    write_snapshots(path, two_venue_snapshots("mystery-a", "mystery-b"))
    code = main(["probe", "--snapshots", str(path), "--allow-zero-cost", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["counts"]["matched_with_capacity"] == 1


def test_default_costs_are_never_silently_zero():
    """The shipped defaults must charge something, or every result is overstated."""
    costs = build_default_costs()
    assert set(costs) == {"kalshi", "polymarket"}
    for venue, model in costs.items():
        assert model.cost_usd(Decimal("0.50"), 1000) > 0, f"{venue} costs nothing"


def test_capture_rejects_a_malformed_market_spec(capsys, tmp_path):
    code = main(["capture", "--market", "kalshi-no-colon", "--out", str(tmp_path / "o.json")])
    err = capsys.readouterr().err
    assert code == 1
    assert "venue:market_id" in err


def test_capture_rejects_an_unknown_venue(capsys, tmp_path):
    code = main(["capture", "--market", "bookieco:ABC", "--out", str(tmp_path / "o.json")])
    err = capsys.readouterr().err
    assert code == 1
    assert "unknown venue" in err


def test_a_subcommand_is_required(capsys):
    with pytest.raises(SystemExit):
        main([])
