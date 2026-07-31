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

from emc.fees import Role, preset_costs  # noqa: E402
from emc.models import (  # noqa: E402
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

DATA = Path(__file__).parent / "data"
EXAMPLE = REPO_ROOT / "data" / "example"
T0 = datetime(2026, 7, 26, 22, 0, tzinfo=timezone.utc)
START = datetime(2026, 7, 27, 17, 5, tzinfo=timezone.utc)


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
    """Fully-specified canonical terms, so a test can knock out exactly one field."""
    base = dict(
        sport="baseball",
        league="MLB",
        home_team="Boston Red Sox",
        away_team="New York Yankees",
        game_date="2026-07-27",
        doubleheader_number=0,
        scheduled_start_utc=START,
        market_type=MarketType.GAME_WINNER,
        outcome_team="New York Yankees",
        extra_innings=ExtraInnings.INCLUDED,
        tie_treatment=TieTreatment.IMPOSSIBLE,
        postponement=PostponementTreatment.VOID_IF_NOT_PLAYED_IN_WINDOW,
        postponement_window_hours=168,
        suspended=SuspendedTreatment.OFFICIAL_IF_REGULATION_COMPLETE,
        listed_pitcher=ListedPitcherRule.NOT_REQUIRED,
        venue_change=VenueChangeTreatment.NO_EFFECT,
        settlement_source="MLB official final score",
    )
    base.update(overrides)
    return SettlementTerms(**base)


def other_game(**overrides) -> SettlementTerms:
    """A different MLB game, for tests that need two independent events."""
    return terms(
        home_team="San Francisco Giants",
        away_team="Los Angeles Dodgers",
        outcome_team="Los Angeles Dodgers",
        **overrides,
    )


def snapshot(
    venue: str,
    *,
    bids: tuple[BookLevel, ...] = (),
    asks: tuple[BookLevel, ...] = (),
    captured_at: datetime | None = None,
    settlement: SettlementTerms | None = None,
    market_id: str | None = None,
    payout: Decimal = Decimal("1"),
) -> MarketSnapshot:
    return MarketSnapshot(
        venue=venue,
        market_id=market_id or f"{venue}-mkt",
        book=OrderBook(bids=bids, asks=asks),
        captured_at=captured_at or T0,
        settlement=settlement if settlement is not None else terms(),
        payout_usd=payout,
    )


@pytest.fixture
def taker_costs():
    return {
        "kalshi": preset_costs("kalshi", Role.TAKER),
        "polymarket": preset_costs("polymarket", Role.TAKER),
    }


@pytest.fixture
def maker_costs():
    return {
        "kalshi": preset_costs("kalshi", Role.MAKER),
        "polymarket": preset_costs("polymarket", Role.MAKER),
    }


@pytest.fixture
def ample_capital():
    return {"kalshi": Decimal("1000000"), "polymarket": Decimal("1000000")}
