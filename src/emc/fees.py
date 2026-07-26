"""Venue cost models.

IMPORTANT — the default rates in this module are *placeholders to be verified*,
not authoritative fee schedules. Venue fee tables change, differ by market
series, and differ by maker/taker role and volume tier. Before any capacity
number from this package is used for a decision, re-derive the numbers here from
the venue's current published fee schedule and record the source and date in
``docs/METHOD.md``. A capacity curve is only as honest as its cost model: at the
edge sizes that matter here (fractions of a cent per contract) fees are not a
rounding error, they are usually the whole answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal
from typing import Protocol, runtime_checkable

__all__ = [
    "BpsFee",
    "FeeModel",
    "FixedPerFillFee",
    "KalshiStyleFee",
    "VenueCosts",
    "ZeroFee",
]

CENT = Decimal("0.01")


@runtime_checkable
class FeeModel(Protocol):
    """Cost in USD to transact ``contracts`` at ``price``."""

    name: str

    def fee_usd(self, price: Decimal, contracts: int) -> Decimal: ...


@dataclass(frozen=True, slots=True)
class ZeroFee:
    """No explicit per-trade fee. Still leaves funding and transfer costs uncounted."""

    name: str = "zero"

    def fee_usd(self, price: Decimal, contracts: int) -> Decimal:
        return Decimal(0)


@dataclass(frozen=True, slots=True)
class BpsFee:
    """Fee as basis points of notional (``price * contracts``)."""

    bps: Decimal = Decimal("0")
    name: str = "bps"

    def fee_usd(self, price: Decimal, contracts: int) -> Decimal:
        return price * Decimal(contracts) * self.bps / Decimal(10_000)


@dataclass(frozen=True, slots=True)
class FixedPerFillFee:
    """A flat cost charged once per fill, regardless of size.

    Models gas, relayer, or transfer costs. Because it is size-independent it
    penalizes small slices hardest, which is exactly the effect that makes thin
    apparent edges uneconomic.
    """

    usd: Decimal = Decimal("0")
    name: str = "fixed-per-fill"

    def fee_usd(self, price: Decimal, contracts: int) -> Decimal:
        return self.usd if contracts > 0 else Decimal(0)


@dataclass(frozen=True, slots=True)
class KalshiStyleFee:
    """The ``rate * contracts * price * (1 - price)`` shape, rounded up to a cent.

    Worth modeling separately from a bps fee because it peaks at price 0.50 and
    decays toward both tails, so the same gross edge is far more expensive to
    capture on a coin-flip market than on a heavy favorite. At ``rate = 0.07`` the
    cost at 0.50 is 1.75 cents per contract, which is larger than most observed
    cross-venue gross spreads on its own.

    The ceiling to a whole cent is applied to the fee for the whole slice, not per
    contract, matching how such a fee is billed per order.

    ``rate`` is a configurable assumption. Verify it against the venue's current
    published schedule before use.
    """

    rate: Decimal = Decimal("0.07")
    name: str = "kalshi-style"

    def fee_usd(self, price: Decimal, contracts: int) -> Decimal:
        if contracts <= 0:
            return Decimal(0)
        raw = self.rate * Decimal(contracts) * price * (Decimal(1) - price)
        return raw.quantize(CENT, rounding=ROUND_CEILING)


@dataclass(frozen=True, slots=True)
class VenueCosts:
    """Everything that reduces edge on one venue's leg.

    ``max_contracts`` captures a binding position or size limit. It is part of
    the cost model because an edge you cannot scale into is not capacity.
    """

    venue: str
    taker_fee: FeeModel = ZeroFee()
    per_fill: FeeModel = FixedPerFillFee()
    max_contracts: int | None = None

    def cost_usd(self, price: Decimal, contracts: int) -> Decimal:
        return self.taker_fee.fee_usd(price, contracts) + self.per_fill.fee_usd(price, contracts)
