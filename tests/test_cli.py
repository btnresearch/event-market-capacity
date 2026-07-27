from __future__ import annotations

import json
import re
from decimal import Decimal

import pytest

from emc.cli import format_screen, main, parse_capital

CAPITAL = "kalshi=1000000,polymarket=1000000"


# --- the screen -----------------------------------------------------------


def test_screen_runs_without_any_data(capsys):
    """The falsification must be reachable before a single quote is collected."""
    assert main(["screen"]) == 0
    out = capsys.readouterr().out
    assert "FEE FLOOR" in out
    assert "3.250c" in out  # taker/taker at P=0.50, Polymarket US theta 0.06


def test_screen_reports_the_revenue_gates_as_contract_counts(capsys):
    main(["screen"])
    out = capsys.readouterr().out
    assert "185,186" in out
    assert "$250k/yr" in out and "$1M/yr" in out


def test_screen_shows_maker_economics_are_an_order_of_magnitude_cheaper(capsys):
    main(["screen"])
    out = capsys.readouterr().out
    assert "0.438c" in out  # maker/maker at P=0.50 (0.004375 rounded for display)


# --- capital parsing ------------------------------------------------------


def test_parse_capital_reads_per_venue_budgets():
    assert parse_capital("kalshi=50000,polymarket=25000") == {
        "kalshi": Decimal("50000"),
        "polymarket": Decimal("25000"),
    }


def test_parse_capital_tolerates_whitespace_and_trailing_commas():
    assert parse_capital(" kalshi = 100 , ") == {"kalshi": Decimal("100")}


@pytest.mark.parametrize("bad", ["kalshi", "kalshi=0", "kalshi=-5", ""])
def test_parse_capital_rejects_malformed_or_non_positive_budgets(bad):
    with pytest.raises(ValueError):
        parse_capital(bad)


# --- probe ----------------------------------------------------------------


def test_probe_on_the_example_exits_clean(capsys, example_dir):
    code = main(
        ["probe", "--snapshots", str(example_dir / "snapshots.json"), "--capital", CAPITAL]
    )
    out = capsys.readouterr().out

    assert code == 0
    assert re.search(r"locked_profit\s+1", out)
    assert re.search(r"matched_no_profit\s+1", out)
    assert re.search(r"mismatched\s+1", out)
    assert re.search(r"unverified\s+1", out)


def test_probe_output_states_that_qlp_is_not_revenue(capsys, example_dir):
    main(["probe", "--snapshots", str(example_dir / "snapshots.json"), "--capital", CAPITAL])
    out = capsys.readouterr().out
    assert "QUOTED, NOT REALIZED" in out
    assert "settlement risk" in out


def test_probe_reports_uncapped_and_capital_constrained_separately(capsys, example_dir):
    main(
        [
            "probe",
            "--snapshots",
            str(example_dir / "snapshots.json"),
            "--capital",
            "kalshi=100,polymarket=100",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    uncapped = Decimal(payload["uncapped_locked_profit_usd"])
    capped = Decimal(payload["capital_constrained_locked_profit_usd"])
    assert uncapped > capped > 0


def test_probe_json_is_decimal_safe(capsys, example_dir):
    main(
        ["probe", "--snapshots", str(example_dir / "snapshots.json"), "--capital", CAPITAL, "--json"]
    )
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload["uncapped_locked_profit_usd"], str)
    assert payload["candidates_considered"] == 4


def test_probe_maker_role_changes_the_measured_result(capsys, example_dir):
    for role, key in (("taker", "taker"), ("maker", "maker")):
        main(
            [
                "probe",
                "--snapshots",
                str(example_dir / "snapshots.json"),
                "--capital",
                CAPITAL,
                "--role",
                role,
                "--json",
            ]
        )
        payload = json.loads(capsys.readouterr().out)
        if key == "taker":
            taker_profit = Decimal(payload["uncapped_locked_profit_usd"])
        else:
            maker_profit = Decimal(payload["uncapped_locked_profit_usd"])
    assert maker_profit > taker_profit


def test_probe_with_the_registry_verifies_the_extra_pair(capsys, example_dir):
    main(
        [
            "probe",
            "--snapshots",
            str(example_dir / "snapshots.json"),
            "--settlement",
            str(example_dir / "settlement.json"),
            "--capital",
            CAPITAL,
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["counts"]["unverified"] == 0
    assert payload["counts"]["locked_profit"] == 2


def test_probe_requires_an_explicit_capital_budget(example_dir):
    """Capacity without a capital budget has no economic meaning."""
    with pytest.raises(SystemExit):
        main(["probe", "--snapshots", str(example_dir / "snapshots.json")])


# --- capture (blocked in this environment) --------------------------------


def test_capture_rejects_a_malformed_market_spec(capsys, tmp_path):
    code = main(["capture", "--market", "kalshi-no-colon", "--out", str(tmp_path / "o.json")])
    assert code == 1
    assert "venue:market_id" in capsys.readouterr().err


def test_capture_rejects_an_unknown_venue(capsys, tmp_path):
    code = main(["capture", "--market", "bookieco:ABC", "--out", str(tmp_path / "o.json")])
    assert code == 1
    assert "unknown venue" in capsys.readouterr().err


def test_a_subcommand_is_required():
    with pytest.raises(SystemExit):
        main([])
