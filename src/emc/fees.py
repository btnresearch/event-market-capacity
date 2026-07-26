"""Venue fee schedules.

Both venues under investigation charge the same functional form:

    fee = multiplier * rate * contracts * price * (1 - price)

That shape peaks at price 0.50 and decays toward both tails, so the same gross
edge is far more expensive to capture on a coin-flip market than on a heavy
favorite. Because MLB game-winner markets for competitive games sit near 0.50,
this is where the fee is worst, and it is the single most important input to
every economic conclusion in this repository.

PROVENANCE
----------
Every rate below carries a ``source`` string and a ``verified`` status. Rates
marked ``CORROBORATED`` were taken from multiple independent secondary sources
that agree on both the formula and the constant; the primary documents
(kalshi.com/docs/kalshi-fee-schedule.pdf and docs.polymarket.com/trading/fees)
returned HTTP 403 to this environment's fetcher, so no primary reconciliation has
been performed. See STATUS.md.

Two things are explicitly NOT resolved and are flagged in the presets:

* Kalshi's ``M`` multiplier. The published formula is
  ``roundup(M * 0.07 * C * P * (1-P))``. What ``M`` equals for MLB series is
  unverified; the preset uses 1 and will be wrong if MLB carries a different M.
* Whether Kalshi's maker discount (25% of taker) is applied before or after the
  round-up to a cent. The preset applies it before.

ROUNDING
--------
Rounding is applied once per ORDER, over the sum of that order's fills, not per
book level. Charging a per-level ceiling makes the reported fee depend on how the
book happens to be sliced rather than on economics: 100 separate 1-contract
fills at 0.50 would be charged $2.00 against $1.75 for a single 100-contract
order, a 14% overstatement and 0.25c/contract, which is decisive at the edge
sizes that matter here.

REBATES ARE NOT MODELLED HERE
-----------------------------
Polymarket makers pay no fee and may additionally earn from a rebate pool. A
reward pool is not personally capturable revenue and must never be netted
against a cost. Maker fee is modelled as zero and nothing more; any incentive
analysis belongs in a separate, explicitly-argued calculation.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from enum import Enum

__all__ = [
    "KALSHI_MAKER",
    "KALSHI_TAKER",
    "POLYMARKET_SPORTS_MAKER",
    "POLYMARKET_SPORTS_TAKER",
    "Fill",
    "FeeSchedule",
    "QuadraticFee",
    "Role",
    "VenueCosts",
    "Verification",
    "ZeroFee",
    "preset_costs",
]

CENT = Decimal("0.01")
ONE = Decimal("1")


class Role(str, Enum):
    TAKER = "taker"
    MAKER = "maker"


class Verification(str, Enum):
    """How much trust a constant has earned."""

    PRIMARY = "primary"           # reconciled against the venue's own document or preview
    CORROBORATED = "corroborated" # multiple independent secondary sources agree
    UNVERIFIED = "unverified"     # placeholder


@dataclass(frozen=True, slots=True)
class Fill:
    """One executed slice: ``contracts`` at ``price``."""

    price: Decimal
    contracts: int

    def __post_init__(self) -> None:
        if not (Decimal(0) < self.price < ONE):
            raise ValueError(f"fill price out of range: {self.price}")
        if self.contracts <= 0:
            raise ValueError(f"fill contracts must be positive: {self.contracts}")

    @property
    def cash_usd(self) -> Decimal:
        return self.price * Decimal(self.contracts)


@dataclass(frozen=True, slots=True)
class QuadraticFee:
    """``multiplier * rate * contracts * price * (1 - price)``.

    ``role_discount`` implements a maker rate expressed as a fraction of the taker
    rate. It is applied to the rate before any rounding, which is an assumption
    (see module docstring).
    """

    rate: Decimal
    multiplier: Decimal = ONE
    role_discount: Decimal = ONE
    round_up_to_cent: bool = False
    source: str = ""
    verified: Verification = Verification.UNVERIFIED

    @property
    def effective_rate(self) -> Decimal:
        return self.rate * self.multiplier * self.role_discount

    def fee_usd(self, fills: Sequence[Fill]) -> Decimal:
        """Fee for one order, rounded once over all of that order's fills.

        Each fill is priced at its own level rather than at the order VWAP.
        ``price * (1 - price)`` is concave, so collapsing fills to a VWAP would
        overstate the fee; summing per fill is exact.
        """
        if not fills:
            return Decimal(0)
        raw = sum(
            (self.effective_rate * Decimal(f.contracts) * f.price * (ONE - f.price) for f in fills),
            Decimal(0),
        )
        if self.round_up_to_cent:
            return raw.quantize(CENT, rounding=ROUND_CEILING)
        return raw

    def fee_at(self, price: Decimal, contracts: int) -> Decimal:
        """Convenience for a single-price order."""
        return self.fee_usd([Fill(price=price, contracts=contracts)])

    def max_fee_per_contract(self) -> Decimal:
        """Cost per contract at the worst price (0.50). Useful for a fast screen."""
        return self.effective_rate * Decimal("0.25")


@dataclass(frozen=True, slots=True)
class ZeroFee:
    """Genuinely no explicit fee. Not a stand-in for 'unknown'."""

    source: str = ""
    verified: Verification = Verification.UNVERIFIED

    @property
    def effective_rate(self) -> Decimal:
        return Decimal(0)

    def fee_usd(self, fills: Sequence[Fill]) -> Decimal:
        return Decimal(0)

    def fee_at(self, price: Decimal, contracts: int) -> Decimal:
        return Decimal(0)

    def max_fee_per_contract(self) -> Decimal:
        return Decimal(0)


# --- Presets ---------------------------------------------------------------
#
# Kalshi: roundup(M * 0.07 * C * P * (1-P)); maker = 25% of taker.
# Max taker cost 1.75c/contract at P=0.50.
_KALSHI_SRC = (
    "Kalshi published trading-fee formula roundup(M*0.07*C*P*(1-P)); maker = 25% of taker. "
    "Corroborated across independent secondary sources; primary PDF returned HTTP 403. "
    "M for MLB series UNVERIFIED, assumed 1."
)

KALSHI_TAKER = QuadraticFee(
    rate=Decimal("0.07"),
    round_up_to_cent=True,
    source=_KALSHI_SRC,
    verified=Verification.CORROBORATED,
)
KALSHI_MAKER = QuadraticFee(
    rate=Decimal("0.07"),
    role_discount=Decimal("0.25"),
    round_up_to_cent=True,
    source=_KALSHI_SRC + " Discount applied before rounding (assumption).",
    verified=Verification.CORROBORATED,
)

# Polymarket sports: C * 0.05 * p * (1-p), rate raised 0.03 -> 0.05 in July 2026.
# Max taker cost $1.25 per 100 shares = 1.25c/contract at p=0.50. Makers pay 0.
_PM_SRC = (
    "Polymarket sports taker fee C*0.05*p*(1-p) (rate raised from 0.03 to 0.05 in July 2026; "
    "max $1.25/100 shares at p=0.50). Makers pay no fee. Corroborated across independent "
    "secondary sources; primary docs returned HTTP 403. No published rounding rule."
)

POLYMARKET_SPORTS_TAKER = QuadraticFee(
    rate=Decimal("0.05"),
    round_up_to_cent=False,
    source=_PM_SRC,
    verified=Verification.CORROBORATED,
)
POLYMARKET_SPORTS_MAKER = ZeroFee(
    source=_PM_SRC + " Rebate pool deliberately NOT modelled as a cost offset.",
    verified=Verification.CORROBORATED,
)


@dataclass(frozen=True, slots=True)
class FeeSchedule:
    """A venue's taker and maker fee models."""

    venue: str
    taker: QuadraticFee | ZeroFee
    maker: QuadraticFee | ZeroFee

    def for_role(self, role: Role) -> QuadraticFee | ZeroFee:
        return self.taker if role is Role.TAKER else self.maker


@dataclass(frozen=True, slots=True)
class VenueCosts:
    """Everything that reduces edge on one venue's leg.

    ``max_capital_usd`` is the binding constraint for this project, not a contract
    count: capital is what is actually scarce and what is shared across
    simultaneous opportunities.
    """

    venue: str
    schedule: FeeSchedule
    role: Role = Role.TAKER
    max_capital_usd: Decimal | None = None
    max_contracts: int | None = None

    @property
    def fee_model(self) -> QuadraticFee | ZeroFee:
        return self.schedule.for_role(self.role)

    def fee_usd(self, fills: Sequence[Fill]) -> Decimal:
        return self.fee_model.fee_usd(fills)


KALSHI_SCHEDULE = FeeSchedule(venue="kalshi", taker=KALSHI_TAKER, maker=KALSHI_MAKER)
POLYMARKET_SCHEDULE = FeeSchedule(
    venue="polymarket", taker=POLYMARKET_SPORTS_TAKER, maker=POLYMARKET_SPORTS_MAKER
)

_SCHEDULES = {"kalshi": KALSHI_SCHEDULE, "polymarket": POLYMARKET_SCHEDULE}


def preset_costs(
    venue: str,
    role: Role = Role.TAKER,
    max_capital_usd: Decimal | None = None,
) -> VenueCosts:
    """Cost model for a known venue. Raises for unknown venues.

    Unknown venues raise rather than defaulting to zero cost, because an implicit
    zero would overstate every downstream number.
    """
    if venue not in _SCHEDULES:
        raise ValueError(
            f"no fee schedule for venue {venue!r}; known: {sorted(_SCHEDULES)}. "
            "Add a schedule with a cited source rather than assuming zero cost."
        )
    return VenueCosts(
        venue=venue, schedule=_SCHEDULES[venue], role=role, max_capital_usd=max_capital_usd
    )
