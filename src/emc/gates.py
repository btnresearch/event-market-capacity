"""Revenue gates and the break-even screen.

Assumptions are parameters here, not constants buried in code, so that changing
"180 slates" or "25% capture" changes every downstream number at once and no
stale figure survives in a report.

THE FAST SCREEN
---------------
Before any live data is collected, the fee schedules alone bound what is
possible. A two-leg locked position pays fees on both legs, and both venues
charge ``rate * C * P * (1-P)``, so the minimum gross cross-venue spread that can
possibly break even is:

    required_gross = kalshi_rate_eff * P*(1-P) + polymarket_rate_eff * P*(1-P)

At P = 0.50 with both legs taker that is 3.00c per contract. MLB game-winner
markets for competitive games sit near 0.50, which is exactly where the fee is
worst. This is a hard floor: it does not depend on depth, latency, or execution
skill, and no amount of engineering moves it.

Screening on this before collecting data is the cheapest possible falsification,
which is why it is a first-class function rather than a comment.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from emc.fees import Role, VenueCosts

__all__ = [
    "GateVerdict",
    "RevenueGate",
    "breakeven_gross_edge",
    "required_capital_usd",
    "required_contracts",
]

ONE = Decimal("1")


@dataclass(frozen=True, slots=True)
class RevenueGate:
    """A revenue target expressed as a per-slate requirement.

    ``capture_rate`` converts a realized-profit requirement into a
    quoted-opportunity requirement. Quoted opportunity is not revenue: if only a
    quarter of quoted locked profit is ever actually filled and settled, the
    quoted bar is four times the realized bar. Default is 1 (no discount) so that
    an unmeasured capture rate cannot flatter a result by accident; supply a
    measured value once one exists.
    """

    annual_target_usd: Decimal
    slates_per_year: int = 180
    capture_rate: Decimal = ONE
    label: str = ""

    def __post_init__(self) -> None:
        if self.slates_per_year <= 0:
            raise ValueError("slates_per_year must be positive")
        if not (Decimal(0) < self.capture_rate <= ONE):
            raise ValueError(f"capture_rate must be in (0, 1], got {self.capture_rate}")

    @property
    def realized_net_per_slate_usd(self) -> Decimal:
        """Net profit per slate required, after fees and after settlement."""
        return (self.annual_target_usd / Decimal(self.slates_per_year)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    @property
    def quoted_per_slate_usd(self) -> Decimal:
        """Quoted locked profit per slate required, given the capture rate."""
        return (self.realized_net_per_slate_usd / self.capture_rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    def passes(self, measured_per_slate_usd: Decimal) -> bool:
        return measured_per_slate_usd >= self.quoted_per_slate_usd


# The three targets named in the mission, at 180 MLB slates.
GATE_250K = RevenueGate(Decimal("250000"), label="$250k/yr")
GATE_500K = RevenueGate(Decimal("500000"), label="$500k/yr")
GATE_1M = RevenueGate(Decimal("1000000"), label="$1M/yr")


def breakeven_gross_edge(
    price: Decimal,
    yes_costs: VenueCosts,
    no_costs: VenueCosts,
) -> Decimal:
    """Minimum gross cross-venue spread, per contract, that can break even at ``price``.

    Both legs are evaluated at ``price``. ``P*(1-P)`` is symmetric about 0.50, so
    buying NO at ``1-P`` costs the same as buying YES at ``P`` under this fee form,
    and one price parameterizes both legs.

    Ignores the round-up-to-a-cent term, which makes this a slight UNDERSTATEMENT
    of the true floor. Understating the floor is the safe direction for a screen
    designed to kill hypotheses.
    """
    if not (Decimal(0) < price < ONE):
        raise ValueError(f"price must be in (0,1), got {price}")
    q = price * (ONE - price)
    return yes_costs.fee_model.effective_rate * q + no_costs.fee_model.effective_rate * q


def required_contracts(net_per_slate_usd: Decimal, net_edge_per_contract_usd: Decimal) -> int:
    """Contracts per slate needed to earn ``net_per_slate_usd`` at a given net edge.

    Raises if the net edge is not positive: there is no quantity that makes a
    negative edge profitable, and returning a large number would imply otherwise.
    """
    if net_edge_per_contract_usd <= 0:
        raise ValueError(
            f"net edge per contract must be positive, got {net_edge_per_contract_usd}; "
            "no position size makes a non-positive edge reach a revenue target"
        )
    return int(
        (net_per_slate_usd / net_edge_per_contract_usd).to_integral_value(rounding=ROUND_CEILING)
    )


def required_capital_usd(contracts: int, gross_edge_per_contract_usd: Decimal) -> Decimal:
    """Capital to hold ``contracts`` of a locked pair.

    A locked pair costs ``1 - gross_edge`` per contract, since the two legs
    together cost the payout minus the spread captured.
    """
    return (Decimal(contracts) * (ONE - gross_edge_per_contract_usd)).quantize(Decimal("0.01"))


@dataclass(frozen=True, slots=True)
class GateVerdict:
    """The outcome of screening one price point against one revenue gate."""

    gate: RevenueGate
    price: Decimal
    assumed_gross_edge: Decimal
    breakeven_gross_edge: Decimal
    yes_role: Role
    no_role: Role

    @property
    def net_edge_per_contract(self) -> Decimal:
        return self.assumed_gross_edge - self.breakeven_gross_edge

    @property
    def feasible(self) -> bool:
        return self.net_edge_per_contract > 0

    @property
    def contracts_needed(self) -> int | None:
        if not self.feasible:
            return None
        return required_contracts(self.gate.quoted_per_slate_usd, self.net_edge_per_contract)

    @property
    def capital_needed_usd(self) -> Decimal | None:
        contracts = self.contracts_needed
        if contracts is None:
            return None
        return required_capital_usd(contracts, self.assumed_gross_edge)

    def summary(self) -> str:
        if not self.feasible:
            return (
                f"{self.gate.label} @ P={self.price}: INFEASIBLE — assumed gross "
                f"{self.assumed_gross_edge * 100:.2f}c does not cover the "
                f"{self.breakeven_gross_edge * 100:.2f}c fee floor "
                f"({self.yes_role.value}/{self.no_role.value})"
            )
        return (
            f"{self.gate.label} @ P={self.price}: needs {self.contracts_needed:,} contracts/slate "
            f"and ${self.capital_needed_usd:,} capital at "
            f"{self.net_edge_per_contract * 100:.2f}c net "
            f"({self.yes_role.value}/{self.no_role.value})"
        )


def screen(
    gate: RevenueGate,
    price: Decimal,
    assumed_gross_edge: Decimal,
    yes_costs: VenueCosts,
    no_costs: VenueCosts,
) -> GateVerdict:
    """Screen one assumed gross spread against a revenue gate."""
    return GateVerdict(
        gate=gate,
        price=price,
        assumed_gross_edge=assumed_gross_edge,
        breakeven_gross_edge=breakeven_gross_edge(price, yes_costs, no_costs),
        yes_role=yes_costs.role,
        no_role=no_costs.role,
    )
