"""Read-only capacity probe for settlement-matched cross-venue event markets.

Scope guardrails (enforced by tests/test_readonly_guardrails.py):

* Read-only. Every venue adapter issues unauthenticated HTTP GET requests against
  public market-data endpoints. Nothing places, modifies, or cancels an order, and
  nothing signs a request.
* No wagering, no automated trading, no forecasting. This package measures a
  property of public quote data. It does not predict outcomes and does not decide
  to transact.

The question it answers: for pairs of contracts that provably settle on the same
event under the same rules, how much locked profit is quoted, net of exact fees,
across real depth, within a shared capital budget?
"""

from emc.fees import Role, VenueCosts, Verification, preset_costs
from emc.gates import RevenueGate, breakeven_gross_edge, required_contracts, screen
from emc.locked import (
    InsufficientDepth,
    LockedQuote,
    consume_depth,
    evaluate_locked,
    no_side_ladder,
    optimize_locked,
)
from emc.models import (
    BookLevel,
    CrossedBookError,
    MarketSnapshot,
    MatchStatus,
    MatchVerdict,
    OrderBook,
    SettlementTerms,
)
from emc.probe import PairResult, PairStatus, ProbeReport, run_probe
from emc.qlp import Allocation, Episode, Observation, allocate_capital, collapse_episodes
from emc.settlement import adjudicate

__all__ = [
    "Allocation",
    "BookLevel",
    "CrossedBookError",
    "Episode",
    "InsufficientDepth",
    "LockedQuote",
    "MarketSnapshot",
    "MatchStatus",
    "MatchVerdict",
    "Observation",
    "OrderBook",
    "PairResult",
    "PairStatus",
    "ProbeReport",
    "RevenueGate",
    "Role",
    "SettlementTerms",
    "VenueCosts",
    "Verification",
    "adjudicate",
    "allocate_capital",
    "breakeven_gross_edge",
    "collapse_episodes",
    "consume_depth",
    "evaluate_locked",
    "no_side_ladder",
    "optimize_locked",
    "preset_costs",
    "required_contracts",
    "run_probe",
    "screen",
]

__version__ = "0.2.0"
