"""Read-only capacity probe for settlement-matched cross-venue sports event markets.

Scope guardrails (enforced by tests in tests/test_readonly_guardrails.py):

* Read-only. Every venue adapter issues unauthenticated HTTP GET requests
  against public market-data endpoints. Nothing in this package places,
  modifies, or cancels an order, and nothing signs a request.
* No wagering, no automated trading, no forecasting. The package measures a
  property of public quote data. It does not predict outcomes and it does not
  decide to transact.

The single question this package answers: for pairs of contracts that provably
settle on the same event under the same rules, how much capital could be
deployed at a given net edge, given the depth actually resting on both books at
one instant?
"""

from emc.depth import capacity_curve
from emc.fees import BpsFee, KalshiStyleFee, VenueCosts, ZeroFee
from emc.models import (
    BookLevel,
    CapacityCurve,
    CapacityPoint,
    CrossedBookError,
    MarketSnapshot,
    MatchStatus,
    MatchVerdict,
    OrderBook,
    SettlementTerms,
)
from emc.probe import PairResult, ProbeReport, run_probe
from emc.settlement import adjudicate

__all__ = [
    "BookLevel",
    "BpsFee",
    "CapacityCurve",
    "CapacityPoint",
    "CrossedBookError",
    "KalshiStyleFee",
    "MarketSnapshot",
    "MatchStatus",
    "MatchVerdict",
    "OrderBook",
    "PairResult",
    "ProbeReport",
    "SettlementTerms",
    "VenueCosts",
    "ZeroFee",
    "adjudicate",
    "capacity_curve",
    "run_probe",
]

__version__ = "0.1.0"
