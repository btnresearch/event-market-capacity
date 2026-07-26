# Mission

Determine whether an internet-connected Mac mini can operate a legally compliant,
auditable event-market system with a credible path to $250,000–$1,000,000 per year.

**The defensible revenue forecast is $0** and stays $0 until money has been
filled, settled, and reconciled.

The first question is not how to build the business. It is: *does enough fee-net,
settlement-matched, delay-surviving economic capacity exist to justify building
anything else?*

## Hypotheses

| # | Hypothesis | Status |
|---|---|---|
| A | Settlement-matched cross-venue locked arbitrage | **KILLED as a $250k route at taker/taker.** See DECISIONS.md 2026-07-26. |
| B | Incentive-aware market making with cross-venue hedging | Open. Now the leading candidate, by elimination, not by evidence. |
| C | Proprietary settlement / book / fill / capacity dataset | Open, unevaluated. No customer, distribution, or rights analysis done. |
| D | Predictive sports models | Not started. Only if pricing and market-structure routes fail. |

None of these is an identity. MLB, arbitrage, market making, and any particular
venue are hypotheses to be falsified cheaply.

## The measurement

For a settlement-matched pair, buy YES on the cheaper venue and the NO side on the
other. Exactly one side pays out, so:

```
cash_out      = yes_cash + no_cash + yes_fee + no_fee
profit_if_yes = q_yes * payout_yes - cash_out
profit_if_no  = q_no  * payout_no  - cash_out
locked_profit = min(profit_if_yes, profit_if_no)
```

`min` is the point: a position is locked only to the extent of its worst
settlement outcome. No probability appears anywhere — the outcome set is
enumerable and the P&L in each state is exactly computable.

**QLP** is fee-net, full-depth, settlement-matched, delay-surviving quoted locked
profit per slate, subject to a capital cap. It is reported both uncapped
(theoretical) and capital-constrained. It is *quoted*, never realized.

### Gates, applied in order

1. **Simultaneity.** Captures more than 2s apart are excluded. Books read seconds
   apart show edges that never simultaneously existed.
2. **Settlement equivalence.** Only pairs proven to settle identically are
   eligible. Canonical coded rule fields, never prose. `MISMATCHED` is a positive
   claim of difference; `UNVERIFIED` means evidence is missing and is not a soft
   match.
3. **Exact locked profit** over integer quantity pairs, full-depth VWAP, exact
   fees rounded once per order.
4. **Shared capital.** Simultaneous opportunities compete for one per-venue
   budget. The same venue capital is never counted twice.
5. **Episodes.** A continuously stale price is one opportunity, not one per poll.

## The fee floor

Both venues charge `rate × contracts × price × (1 − price)`. That peaks at 0.50,
which is exactly where competitive MLB game-winner markets sit. Minimum gross
cross-venue spread that can break even, per contract:

| Roles | Floor at P=0.50 |
|---|---|
| taker / taker | **3.000c** |
| taker / maker | 1.750c |
| maker / taker | 1.688c |
| maker / maker | 0.438c |

Run `python -m emc.cli screen` to reproduce. This floor depends on nothing but the
two fee schedules — not depth, latency, or execution skill.

## Revenue gates

180 MLB slates per year. These are **realized net profit**, not quoted opportunity.

| Target | Net per slate |
|---|---|
| $250,000 | $1,388.89 |
| $500,000 | $2,777.78 |
| $1,000,000 | $5,555.56 |

Capture rate defaults to 1.0 so an unmeasured discount cannot flatter a result. At
a measured 25% capture, the quoted bar for $250k becomes $5,555.56 per slate.

### Decision logic

- Zero-delay QLP below $1,389/slate → kill MLB arbitrage as a $250k path.
- Zero-delay passes but 90s fails → manual execution is dead.
- 90s QLP only slightly above $1,389 → fragile; do not annualize.
- 90s QLP materially above $5,556 across diverse independent slates → recommend a
  tiny legally approved fill experiment.
- Opportunity concentrated in one game, one venue error, or one rule mismatch →
  do not extrapolate.
- Counterfactual market-making contribution below $1,389/slate at intended
  capital → kill MLB-only market making for $250k.
- Never project a temporary incentive beyond its verified expiration.
- Never project $500k or $1M from fewer than several months of reconciled fills.

## Constraints

- Decimal or integer arithmetic only. Never float for prices, fees, quantities, or
  settlement P&L.
- Never confuse displayed price with executable value, or venue volume with
  capturable volume.
- Never treat a reward pool as personally capturable revenue.
- Never infer contract equivalence from titles.
- No automated execution before a read-only probe demonstrates capacity.
- Invalid inputs fail loudly. No silent normalization or fallback in financial
  calculations.
- Read-only data collection and live trading are separate phases. Before any live
  execution, verify venue availability from the operator's actual physical
  jurisdiction, that the venue permits the proposed automation, contract
  availability, and account eligibility. No VPN, remote access, or cloud routing
  to evade geolocation. Fail closed when legal or venue status is unclear. These
  are unresolved and are not legal advice.

## AI boundary

A language model may audit formulas, compare rule language, propose canonical
mappings, find anomalies, generate tests, and maintain code.

It may not override fee calculations or risk limits, approve an unverified
contract match, invent missing order-book data, make a directional prediction in
the execution path, or promote anything to live capital. All financial decisions
are deterministic and reproducible. Enforced by
`tests/test_readonly_guardrails.py`.
