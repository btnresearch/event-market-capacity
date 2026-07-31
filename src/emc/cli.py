"""Command line entry point.

    python -m emc.cli screen                          # fee-floor falsification, no data needed
    python -m emc.cli probe --snapshots <path> --capital kalshi=50000,polymarket=50000
    python -m emc.cli capture --market kalshi:TICKER --out book.json

Read-only by construction: no subcommand transacts. ``capture`` only writes public
quote data to a local file.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from emc.fees import Role, preset_costs
from emc.gates import GATE_250K, GATE_500K, GATE_1M, breakeven_gross_edge, screen
from emc.probe import DEFAULT_MAX_SKEW, PairStatus, ProbeReport, run_probe
from emc.registry import SettlementRegistry
from emc.serde import load_snapshots

__all__ = ["format_report", "format_screen", "main", "parse_capital"]

PRICE_BAND = [Decimal(p) for p in ("0.10", "0.20", "0.30", "0.40", "0.50", "0.60", "0.70", "0.80", "0.90")]
ROLE_PAIRS = [
    (Role.TAKER, Role.TAKER),
    (Role.TAKER, Role.MAKER),
    (Role.MAKER, Role.TAKER),
    (Role.MAKER, Role.MAKER),
]


def parse_capital(raw: str) -> dict[str, Decimal]:
    """Parse ``kalshi=50000,polymarket=25000`` into a per-venue budget."""
    out: dict[str, Decimal] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        venue, sep, amount = part.partition("=")
        if not sep:
            raise ValueError(f"expected venue=amount, got {part!r}")
        value = Decimal(amount)
        if value <= 0:
            raise ValueError(f"capital for {venue!r} must be positive, got {value}")
        out[venue.strip()] = value
    if not out:
        raise ValueError("no venue capital supplied")
    return out


def format_screen() -> str:
    """The fee-floor screen. Kills or clears a hypothesis before any data exists."""
    lines: list[str] = []
    lines.append("FEE FLOOR: minimum gross cross-venue spread that can break even")
    lines.append("two-leg settlement-matched locked position, kalshi + polymarket, $1 payout")
    lines.append("")
    header = f"  {'price':>7}" + "".join(
        f"{k.value[:5] + '/' + p.value[:5]:>13}" for k, p in ROLE_PAIRS
    )
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))
    for price in PRICE_BAND:
        row = f"  {price:>7}"
        for k_role, p_role in ROLE_PAIRS:
            floor = breakeven_gross_edge(
                price, preset_costs("kalshi", k_role), preset_costs("polymarket", p_role)
            )
            row += f"{floor * 100:>11.3f}c"
        lines.append(row)
    lines.append("")
    lines.append("MLB game-winner markets for competitive games sit near 0.50, where the")
    lines.append("fee is worst. This floor is independent of depth, latency and skill.")
    lines.append("")
    lines.append("REVENUE GATES at a 4.00c gross spread and P=0.50 (both legs taker):")
    kt, pt = preset_costs("kalshi", Role.TAKER), preset_costs("polymarket", Role.TAKER)
    for gate in (GATE_250K, GATE_500K, GATE_1M):
        verdict = screen(gate, Decimal("0.50"), Decimal("0.04"), kt, pt)
        lines.append(f"  {verdict.summary()}")
    lines.append("")
    lines.append("A 4c persistent gross cross between two liquid venues on the same MLB")
    lines.append("game-winner market is already an extreme assumption. The contract counts")
    lines.append("above are what would be required at that spread.")
    return "\n".join(lines)


def _fmt(value: Decimal) -> str:
    return f"${value.quantize(Decimal('0.01')):>14,}"


def format_report(report: ProbeReport, capital: dict[str, Decimal]) -> str:
    lines: list[str] = []
    lines.append("event-market-capacity probe")
    lines.append(f"generated_at         {report.generated_at.isoformat()}")
    lines.append(f"snapshots            {report.snapshots_considered}")
    lines.append(f"cross-venue pairs    {report.candidates_considered}")
    lines.append(f"unidentifiable       {report.skipped_unidentifiable}")
    lines.append(f"capital budget       {', '.join(f'{v}=${a:,}' for v, a in sorted(capital.items()))}")
    lines.append("")
    lines.append("pair adjudication")
    for status in PairStatus:
        lines.append(f"  {status.value:<22} {report.counts.get(status.value, 0)}")
    lines.append("")
    lines.append("quoted locked profit (QLP)")
    lines.append(f"  uncapped (theoretical)   {_fmt(report.uncapped_locked_profit_usd)}")
    lines.append(f"  capital-constrained      {_fmt(report.capital_constrained_locked_profit_usd)}")
    for venue, amount in sorted(report.allocation.capital_used.items()):
        lines.append(f"  capital used on {venue:<8} {_fmt(amount)}")
    roc = report.allocation.return_on_capital
    if roc is not None:
        lines.append(f"  return on capital        {(roc * 100).quantize(Decimal('0.001'))}%")
    if report.allocation.skipped:
        lines.append("")
        lines.append("skipped by the capital allocator")
        for obs, reason in report.allocation.skipped:
            lines.append(f"  {obs.pair_key}: {reason}")
    lines.append("")
    if report.capital_constrained_locked_profit_usd <= 0:
        lines.append(
            "No locked profit. Check the adjudication counts: a high 'unverified' count "
            "means settlement terms could not be proven, which is a different result "
            "from no opportunity existing."
        )
    else:
        lines.append(
            "QUOTED, NOT REALIZED. This is what the book showed at one instant. It is an "
            "upper bound: it excludes latency between legs, queue position, whether resting "
            "size is available to a taker, capital transfer time between venues, and "
            "settlement risk on any term the adjudicator could not check. Nothing here is "
            "revenue until it has been filled, settled and reconciled."
        )
    return "\n".join(lines)


def _cmd_screen(args: argparse.Namespace) -> int:
    print(format_screen())
    return 0


def _cmd_probe(args: argparse.Namespace) -> int:
    capital = parse_capital(args.capital)
    role = Role(args.role)
    snapshots = load_snapshots(args.snapshots)
    if args.settlement:
        snapshots = SettlementRegistry.load(args.settlement).apply_all(snapshots)

    venues = {s.venue for s in snapshots}
    costs = {v: preset_costs(v, role) for v in venues}

    report = run_probe(
        snapshots,
        costs=costs,
        venue_capital=capital,
        max_skew=DEFAULT_MAX_SKEW if args.max_skew is None else timedelta(seconds=args.max_skew),
    )
    print(json.dumps(report.as_dict(), indent=2) if args.json else format_report(report, capital))
    return 0


def _cmd_capture(args: argparse.Namespace) -> int:
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
            snapshots.append(_client_for(venue_name).fetch_snapshot(market_id))
        except FetchBlocked as exc:
            errors.append(f"{spec}: {exc}")
        except (KeyError, ValueError) as exc:
            errors.append(f"{spec}: {type(exc).__name__}: {exc}")

    for message in errors:
        print(f"error: {message}", file=sys.stderr)
    if snapshots:
        write_snapshots(args.out, snapshots)
        print(f"wrote {len(snapshots)} snapshot(s) to {args.out}")
    # A partial capture is unusable for a simultaneity-gated probe, so any failure
    # is a nonzero exit even when some legs succeeded.
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
        prog="emc", description="Read-only capacity probe for cross-venue event markets."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    scr = sub.add_parser("screen", help="fee-floor and revenue-gate screen (no data required)")
    scr.set_defaults(func=_cmd_screen)

    probe = sub.add_parser("probe", help="measure locked-profit capacity from a snapshot set")
    probe.add_argument("--snapshots", required=True, type=Path)
    probe.add_argument("--capital", required=True, help="e.g. kalshi=50000,polymarket=25000")
    probe.add_argument("--settlement", type=Path, help="curated settlement registry JSON")
    probe.add_argument("--role", default="taker", choices=[r.value for r in Role])
    probe.add_argument("--max-skew", type=float, help="max capture skew in seconds (default 2)")
    probe.add_argument("--json", action="store_true")
    probe.set_defaults(func=_cmd_probe)

    capture = sub.add_parser("capture", help="fetch public books to a local file")
    capture.add_argument("--market", action="append", required=True, help="venue:market_id")
    capture.add_argument("--out", required=True, type=Path)
    capture.set_defaults(func=_cmd_capture)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
