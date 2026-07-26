from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from emc.fees import BpsFee, FixedPerFillFee, KalshiStyleFee, VenueCosts, ZeroFee  # noqa: E402
from emc.models import BookLevel, MarketSnapshot, OrderBook, SettlementTerms  # noqa: E402

DATA = Path(__file__).parent / "data"
EXAMPLE = REPO_ROOT / "data" / "example"
T0 = datetime(2026, 7, 26, 22, 0, tzinfo=timezone.utc)


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def example_dir() -> Path:
    return EXAMPLE


def load_json(name: str):
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def levels(*pairs: tuple[str, int]) -> tuple[BookLevel, ...]:
    return tuple(BookLevel(price=Decimal(p), size=s) for p, s in pairs)


def terms(**overrides) -> SettlementTerms:
    """Fully-specified settlement terms, so a test can knock out exactly one field."""
    base = {
        "event_key": "nba-2026-07-27-lal-bos",
        "league": "NBA",
        "participants": frozenset({"Los Angeles Lakers", "Boston Celtics"}),
        "scheduled_start_utc": datetime(2026, 7, 27, 23, 0, tzinfo=timezone.utc),
        "market_type": "moneyline",
        "outcome": "Los Angeles Lakers",
        "settlement_source": "NBA official final score",
        "includes_overtime": True,
        "void_rule": "void if not completed within 7 days of scheduled start",
    }
    base.update(overrides)
    return SettlementTerms(**base)


def snapshot(
    venue: str,
    *,
    bids: tuple[BookLevel, ...] = (),
    asks: tuple[BookLevel, ...] = (),
    captured_at: datetime | None = None,
    settlement: SettlementTerms | None = None,
    market_id: str | None = None,
) -> MarketSnapshot:
    return MarketSnapshot(
        venue=venue,
        market_id=market_id or f"{venue}-mkt",
        book=OrderBook(bids=bids, asks=asks),
        captured_at=captured_at or T0,
        settlement=settlement if settlement is not None else terms(),
    )


@pytest.fixture
def zero_costs() -> dict[str, VenueCosts]:
    return {
        "kalshi": VenueCosts(venue="kalshi", taker_fee=ZeroFee()),
        "polymarket": VenueCosts(venue="polymarket", taker_fee=ZeroFee()),
    }


@pytest.fixture
def realistic_costs() -> dict[str, VenueCosts]:
    """Mirrors emc.cli.build_default_costs so report assertions stay in one place."""
    return {
        "kalshi": VenueCosts(venue="kalshi", taker_fee=KalshiStyleFee(rate=Decimal("0.07"))),
        "polymarket": VenueCosts(
            venue="polymarket",
            taker_fee=BpsFee(bps=Decimal("0")),
            per_fill=FixedPerFillFee(usd=Decimal("0.05")),
        ),
    }
