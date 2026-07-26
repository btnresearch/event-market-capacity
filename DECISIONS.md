# Decisions

Dated records of decisions, measurements, and killed hypotheses. Newest first.

---

## 2026-07-26 — Hypothesis A killed as a $250k route at taker/taker

**Measurement.** Both venues charge a fee of the form
`rate × contracts × price × (1 − price)`, which peaks at price 0.50. Combined
two-leg taker cost:

| Price | Kalshi | Polymarket | Combined = required gross spread |
|---|---|---|---|
| 0.40 | 1.680c | 1.200c | **2.880c** |
| 0.45 | 1.732c | 1.238c | **2.970c** |
| 0.50 | 1.750c | 1.250c | **3.000c** |
| 0.60 | 1.680c | 1.200c | **2.880c** |

Competitive MLB game-winner markets trade in the 0.35–0.65 band, i.e. exactly where
the fee is worst.

**Consequence.** At a 4c persistent gross cross — already an extreme assumption for
two liquid venues on the same game — the requirement is:

| Target | Net edge | Contracts/slate | Capital/slate |
|---|---|---|---|
| $250k | 1.00c | 138,889 | ~$133,333 |
| $500k | 1.00c | 277,800 | ~$266,688 |
| $1M | 1.00c | 555,556 | ~$533,376 |

**Decision.** Taker/taker settlement-matched cross-venue arbitrage is dead as a
route to $250k/yr on MLB. The required matched depth is orders of magnitude beyond
what these books quote, and the conclusion follows from the fee schedules alone —
no depth data, latency measurement, or execution skill changes it.

**Why this was worth doing first.** It cost two web lookups and one arithmetic
script, and it falsified the top-ranked hypothesis before any collection
infrastructure was built. This is the cheap-falsification principle working as
intended.

**What is NOT concluded.** That no cross-venue spread ever exceeds 3c. That has not
been measured and remains the next binary gate. Only the *economic viability at
$250k scale* is settled.

**Encoded at** `emc/gates.py`, `tests/test_gates.py`, `python -m emc.cli screen`.

---

## 2026-07-26 — The fee structure redirects the investigation to Hypothesis B

**Measurement.** Same locked position, by execution role, at P=0.50:

| Roles | Required gross spread |
|---|---|
| taker / taker | 3.000c |
| taker / maker | 1.750c |
| maker / taker | 1.688c |
| maker / maker | 0.438c |

Maker/maker is ~6.9× cheaper, because Kalshi's maker rate is 25% of taker and
Polymarket makers pay nothing.

**Decision.** The only execution style that clears a plausible spread is resting
liquidity. But two resting maker orders cannot both be guaranteed to fill, and one
fill without the other is naked directional exposure, not a lock. So the fee
structure does not rescue arbitrage — it converts the problem into market making
with hedge risk, which is Hypothesis B.

This is elimination, not evidence. Hypothesis B leads because A is dead, and its own
economics are unmeasured. Note that even at maker fees, a 1c edge still needs
~247,000 contracts per slate for $250k: maker economics change the *sign* of the
edge, not the depth requirement.

---

## 2026-07-26 — Audit of the pre-existing implementation

Six defects found and reproduced before any changes. All were in code that passed
196 tests, which is why the tests were the wrong tests.

1. **Unit ambiguity in `net_edge`.** Reported dollars/contract while thresholds were
   documented in $1-payout probability units. A true 0.05 edge on a $10-payout
   contract reported `worst_net_edge = 0.50`. → `depth.py` deleted; `locked.py`
   works in explicit dollars with per-leg payouts.
2. **Shared venue capital double-counted.** Caps were per-pair, never per-venue.
   Two simultaneous pairs with a 1,000-contract kalshi cap reported 2,000 kalshi
   contracts. A test actively asserted the wrong behaviour. → `qlp.allocate_capital`
   with one shared per-venue budget.
3. **Fee ceiling applied per book level, not per order.** 100 one-contract fills at
   0.50 were charged $2.00 against $1.75 for one 100-contract order — a 14%
   overstatement, 0.25c/contract, driven purely by how the book was sliced. →
   `QuadraticFee.fee_usd` takes a fill list and rounds once.
4. **Settlement rules compared as prose.** Two venues never phrase a rule
   identically, so economically equivalent contracts were returned as `MISMATCHED`
   — a positive claim they settle differently, which was false. → canonical coded
   enums; prose→code mapping is a cited human judgement in the registry.
5. **No MLB event identity.** No doubleheader number, no postponement window, no
   listed-pitcher rule, no suspended-game treatment. Game 1 and game 2 of a
   doubleheader would pair. → full canonical settlement record; `canonical_event_key`
   includes the doubleheader number.
6. **No episode deduplication.** Every poll of a continuously stale price counted as
   a new opportunity, so the metric scaled with the polling rate. →
   `qlp.collapse_episodes`; an episode contributes its peak once.

Two further bugs were found *by* the new tests during this session:

7. **`optimize_locked` folded per-venue capital limits into a total capital cap.** A
   $200 kalshi balance acted as a $200 ceiling on both legs combined, understating
   the position by ~half (235 contracts instead of 500).
8. **Own analysis error, corrected.** An earlier docstring claimed a `P(1−P)` fee
   makes net edge non-monotone in depth. It does not — the 0.07 coefficient cannot
   outrun gross-edge decay. The genuine source of non-monotonicity is a *fixed
   per-fill* cost spread over slice size. Docstrings and the test now say the true
   reason.

**Preserved from the earlier work:** `models.py` book validation and crossed-book
rejection, Decimal-everywhere discipline, the venue parsers and their fixtures,
`serde.py`, `registry.py` provenance enforcement, and the read-only guardrail tests.

**Deleted:** `depth.py`, `test_depth.py`, `docs/METHOD.md` (folded into MISSION.md and
STATUS.md per the three-document rule).

---

## 2026-07-26 — Capital budget is a required argument

`run_probe` will not run without an explicit per-venue capital budget, and a venue
absent from the budget is treated as having none rather than unlimited. Capacity
without a capital constraint is a number with no economic meaning, and defaulting
to unlimited silently reproduces defect 2.

---

## 2026-07-26 — Rebate pools are not netted against costs

Polymarket makers pay no fee and may earn from a rebate pool. The maker fee model is
zero and nothing more. A reward pool is not personally capturable revenue — its
value depends on counterfactual share, which is unmeasured — so it must never appear
as a cost offset inside a locked-profit calculation. Any incentive analysis belongs
in a separate, explicitly-argued calculation under Hypothesis B.

---

## 2026-07-26 — Deferred: raw archive and hash-chain audit trail

The mission calls for immutable raw-response archival and a hash-chained audit
trail. Not built. Both are infrastructure whose only consumer is live data, and no
live data can be collected while egress is blocked. Building them now would be
architecture ahead of economic proof. Revisit when the venue hosts are allowlisted.
