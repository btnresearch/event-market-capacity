"""Command line entry point.

    python -m emc.cli probe --snapshots data/example/snapshots.json
    python -m emc.cli probe --snapshots <path> --settlement <registry.json> --json

Read-only by construction: the CLI has no subcommand that transacts, and the
``capture`` subcommand only writes public quote data to a local file.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

from emc.depth import DEFAULT_THRESHOLDS
from emc.fees import BpsFee, FixedPerFillFee, KalshiStyleFee, VenueCosts, ZeroFee
from emc.probe import DEFAULT_MAX_SKEW, PairStatus, ProbeReport, run_probe
from emc.registry import SettlementRegistry
from emc.serde import load_snapshots

__all__ = ["build_default_costs", "format_report", "main"]


def build_default_costs() -> dict[str, VenueCosts]:
    """Cost models used when none are supplied.

    These are ASSUMPTIONS, not published schedules. They exist so the probe never
    runs with implicit zero cost, which would overstate every result. Replace them
    with verified numbers before treating output as a finding, and record the
    source in ``docs/METHOD.md``.
    """
    return {
        "kalshi": VenueCosts(venue="kalshi", taker_fee=KalshiStyleFee(rate=Decimal("0.07"))),
        "polymarket": VenueCosts(
            venue="polymarket",
            taker_fee=BpsFee(bps=Decimal("0")),
            per_fill=FixedPerFillFee(usd=Decimal("0.05")),
        ),
    }


def _fmt_usd(value: Decimal) -> str:
    return f"${value.quantize(Decimal('0.01')):>12,}"


def format_report(report: ProbeReport) -> str:
    lines: list[str] = []
    lines.append("event-market-capacity probe")
    lines.append(f"generated_at        {report.generated_at.isoformat()}")
    lines.append(f"snapshots           {report.snapshots_considered}")
    lines.append(f"cross-venue pairs   {report.candidates_considered}")
    lines.append(
        f"unpairable          {report.skipped_unpairable}  (no participants published)"
    )
    lines.append("")
    lines.append("pair adjudication")
    for status in PairStatus:
        lines.append(f"  {status.value:<24} {report.counts.get(status.value, 0)}")
    lines.append("")
    lines.append("capacity at net edge (per contract, $1 payout)")
    header = f"  {'min edge':>10} {'contracts':>10} {'capital':>14} {'profit':>14} {'ROC':>8}"
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))
    for point in report.aggregate:
        roc = point.return_on_capital
        roc_text = "n/a" if roc is None else f"{(roc * 100).quantize(Decimal('0.01'))}%"
        lines.append(
            f"  {point.min_net_edge:>10} {point.contracts:>10} "
            f"{_fmt_usd(point.capital_usd)} {_fmt_usd(point.profit_usd)} {roc_text:>8}"
        )
    lines.append("")
    if not report.has_capacity:
        lines.append(
            "No settlement-matched capacity measured. Check the adjudication counts above: "
            "a high 'unverified' count means settlement terms could not be proven, which is "
            "a different result from no edge existing."
        )
    else:
        lines.append(
            "Capacity shown is a single-instant measurement under the configured cost model. "
            "It is not an achievable return: it excludes execution latency between legs, "
            "queue position, capital transfer time between venues, and settlement risk on "
            "any term the adjudicator could not check."
        )
    return "\n".join(lines)


def _parse_thresholds(raw: str | None) -> tuple[Decimal, ...]:
    if not raw:
        return DEFAULT_THRESHOLDS
    return tuple(sorted(Decimal(part.strip()) for part in raw.split(",") if part.strip()))


def _cmd_probe(args: argparse.Namespace) -> int:
    snapshots = load_snapshots(args.snapshots)
    if args.settlement:
        registry = SettlementRegistry.load(args.settlement)
        snapshots = registry.apply_all(snapshots)

    report = run_probe(
        snapshots,
        costs=build_default_costs(),
        thresholds=_parse_thresholds(args.thresholds),
        max_skew=DEFAULT_MAX_SKEW if args.max_skew is None else _seconds(args.max_skew),
        default_costs=VenueCosts(venue="unknown", taker_fee=ZeroFee()) if args.allow_zero_cost else None,
    )

    if args.json:
        print(json.dumps(report.as_dict(), indent=2))
    else:
        print(format_report(report))
    return 0


def _seconds(value: float):
    from datetime import timedelta

    return timedelta(seconds=value)


def _cmd_capture(args: argparse.Namespace) -> int:
    """Fetch public books and write them to a local file for later replay."""
    from datetime import datetime, timezone

    from emc.serde import write_snapshots
    from emc.venues.base import FetchBlocked

    snapshots = []
    errors: list[str] = []
    for spec in args.market:
        venue_name, _, market_id = spec.partition(":")
        if not market_id:
            errors.append(f"{spec!r} is not in venue:market_id form")
            continue
        try:
            client = _client_for(venue_name)
            snapshots.append(client.fetch_snapshot(market_id))
        except FetchBlocked as exc:
            errors.append(f"{spec}: {exc}")
        except (KeyError, ValueError) as exc:
            errors.append(f"{spec}: {type(exc).__name__}: {exc}")

    for message in errors:
        print(f"error: {message}", file=sys.stderr)

    if snapshots:
        write_snapshots(args.out, snapshots)
        stamp = datetime.now(timezone.utc).isoformat()
        print(f"wrote {len(snapshots)} snapshot(s) to {args.out} at {stamp}")
    # A partial capture is not usable for a simultaneity-gated probe, so any
    # failure is a nonzero exit even when some legs succeeded.
    return 1 if errors else 0


def _client_for(venue: str):
    if venue == "kalshi":
        from emc.venues.kalshi import KalshiVenue

        return KalshiVenue()
    if venue == "polymarket":
        from emc.venues.polymarket import PolymarketVenue

        return PolymarketVenue()
    raise ValueError(f"unknown venue {venue!r}; expected 'kalshi' or 'polymarket'")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="emc", description="Read-only capacity probe for cross-venue sports event markets."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    probe = sub.add_parser("probe", help="measure capacity from a snapshot set")
    probe.add_argument("--snapshots", required=True, type=Path, help="JSON/JSONL file or directory")
    probe.add_argument("--settlement", type=Path, help="curated settlement registry JSON")
    probe.add_argument("--thresholds", help="comma-separated net-edge thresholds, e.g. 0,0.005,0.01")
    probe.add_argument("--max-skew", type=float, help="max capture skew in seconds (default 2)")
    probe.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    probe.add_argument(
        "--allow-zero-cost",
        action="store_true",
        help="treat unknown venues as costless (overstates capacity; for debugging only)",
    )
    probe.set_defaults(func=_cmd_probe)

    capture = sub.add_parser("capture", help="fetch public books to a local file")
    capture.add_argument("--market", action="append", required=True, help="venue:market_id")
    capture.add_argument("--out", required=True, type=Path)
    capture.set_defaults(func=_cmd_capture)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
