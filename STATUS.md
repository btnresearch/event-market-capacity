# Status

**As of 2026-07-26.**

## Current measured economics

**$0.** No live data has been collected. No fill, no settlement, no reconciled
cash. Every number below is either arithmetic on published fee schedules or a
measurement over synthetic fixtures.

The one economically decisive number established this session:

> A two-leg settlement-matched taker/taker locked position on Kalshi + Polymarket
> costs **3.12c–3.25c per contract in fees** across the 0.40–0.60 price band where
> competitive MLB game-winner markets trade.

Restated 2026-07-27 from 2.88c–3.00c after correcting Polymarket to the US uniform
theta of 0.06 (0.05 is the international sports rate). All four role combinations
are now bounded and tested; three are dead on fees alone. See DECISIONS.md
2026-07-27.

At an already-extreme 4c persistent gross cross, $250k/yr needs **185,186
contracts and ~$177,779 of capital per slate**. That is orders of magnitude above
what MLB game-winner books quote. Hypothesis A is killed at **every** role
combination without needing any live data — see DECISIONS.md.

## Tests

```
327 passed in 7.67s
```

`python -m pytest`. No network required. Coverage by area:

| Area | Module | Tests |
|---|---|---|
| Fee formulas, rounding, maker/taker, provenance | `test_fees.py` | 8 |
| Locked profit, depth, integer optimization, caps | `test_locked.py` | 25 |
| Revenue gates and the fee floor, all four role combinations | `test_gates.py` | 84 |
| Shared capital, episode collapsing | `test_qlp.py` | 16 |
| Settlement adjudication over coded fields | `test_settlement.py` | 30 |
| Probe orchestration end to end | `test_probe.py` | 22 |
| Book/price validation | `test_models.py` | 21 |
| Serialization, coded-field rejection | `test_serde.py` | 24 |
| Registry provenance enforcement | `test_registry.py` | 15 |
| CLI | `test_cli.py` | 17 |
| Venue payload parsers | `test_venue_parsers.py` | 41 |
| Read-only scope guardrails | `test_readonly_guardrails.py` | 24 |

## Venue adapters

| Venue | Market data | Status |
|---|---|---|
| Kalshi | `api.elections.kalshi.com/trade-api/v2` | **BLOCKED** — network policy |
| Polymarket | `clob.polymarket.com`, `gamma-api.polymarket.com` | **BLOCKED** — network policy |
| ProphetX | — | **BLOCKED** — no credentials, no adapter written |

Live acceptance test attempted this session:

```
$ python -m emc.cli capture --market kalshi:MLB-TEST --market polymarket:0xTEST --out live.json
error: kalshi:MLB-TEST: GET https://api.elections.kalshi.com/trade-api/v2/markets/MLB-TEST/orderbook
       failed: ProxyError: 403 Forbidden
error: polymarket:0xTEST: GET https://clob.polymarket.com/book failed: ProxyError: 403 Forbidden
exit=1
```

The environment's egress policy denies CONNECT to all three venue hosts. The
adapters fail loudly rather than returning empty books, so a blocked fetch can
never be mistaken for "we looked and found nothing".

Parsers are written to the *documented* payload shapes and tested against
hand-written fixtures. **That proves the normalization is self-consistent; it does
not prove the shape matches the live API.** The riskiest untested surface is the
YES/NO normalization: Kalshi's NO bid ladder reflects to the YES ask ladder
through 100, and a Polymarket NO token's book reflects through 1 *and swaps sides*.
Backwards either way invents a large fake spread.

## Fee schedules

| Venue | Formula | Verification |
|---|---|---|
| Kalshi taker | `roundup(M × 0.07 × C × P × (1−P))`, max 1.75c/contract | **CORROBORATED** — multiple independent secondary sources agree on formula and constant. Primary PDF returned HTTP 403. |
| Kalshi maker | 25% of taker → max 0.4375c/contract | **CORROBORATED**. Whether the discount applies before or after the cent round-up is **UNRESOLVED**; code applies it before. |
| Polymarket US taker | `C × 0.06 × p × (1−p)`, max $1.50/100 shares | **CORROBORATED**. Uniform theta across categories. **This is the rate used.** |
| Polymarket international sports taker | `C × 0.05 × p × (1−p)`, max $1.25/100 shares | **CORROBORATED**. Recorded for provenance only; NOT used. Raised 0.03 → 0.05 in July 2026. |
| Polymarket sports maker | no fee | **CORROBORATED**. Rebate pool deliberately NOT modelled as a cost offset. |

Unresolved: Kalshi's `M` multiplier for MLB series (code assumes 1, and is wrong if
MLB differs). Volume tiers and per-series variation are not modelled.

## What is mocked

- All snapshot data. `data/example/` is synthetic, hand-built to exercise every
  gate, and labelled as such in the file. No number derived from it is a market
  observation.
- `data/example/settlement.json` cites a placeholder URL and says so.

## Matched markets

**0 real.** 5 synthetic candidate pairs, of which the probe measures 1 as having
locked profit, 1 as matched-with-no-profit, 1 mismatched, 1 unverified, and 1 never
paired (doubleheader game 1 vs game 2).

## Not built, deliberately

No order submission, no market-making execution, no predictive model, no dashboard,
no agent supervisor, no live trading. No raw-response archive or hash-chain audit
trail yet — that is infrastructure ahead of economic proof and is deferred until a
route survives.

## Next binary gate

**This gate changed on 2026-07-27 and is no longer a question about a spread.**

The old gate was *"does any pair ever quote a gross cross above 3.00c?"* That gate
assumed taker/taker was the route worth measuring. The four-role extension killed
taker/taker (needs >3.2500c), maker/taker (>1.9375c) and taker/maker (>1.7500c) on
fees alone. The only combination that clears a plausible cross is maker/maker at
0.4375c — **and maker/maker is not arbitrage, because neither resting leg is
guaranteed to fill.** A spread question cannot settle a fill question, so measuring
crosses would no longer decide anything.

The gate is therefore:

> **When a resting two-sided quote is posted on both venues on the same
> settlement-matched MLB game-winner market, does the joint two-sided fill arrive
> often enough, and does the one-sided fill cost little enough to unwind, that
> expected net per contract stays positive after the 0.4375c maker/maker floor and
> after the cost of flattening unmatched inventory?**

Yes → Hypothesis B has an economic basis and QLP gets replaced by an
inventory-aware measure, because quoted locked profit is the wrong instrument for a
position that is not locked.
No → Hypothesis B dies on the same fee-and-fill arithmetic that killed A, and the
work moves to Hypothesis C, where the asset is the dataset rather than the trade.

**This gate needs different data than the old one.** Book snapshots alone cannot
answer it. It requires fill and print data, the queue position of a resting order,
and the realized cost of unwinding a one-sided fill. None of that is collected, and
none of it is modelled anywhere in this repository — `emc.locked` and `emc.qlp`
contain no fill-probability parameter by design.

**What must NOT happen next.** Building a market-making simulator to answer this
from assumptions. A simulator would produce a fill rate chosen by whoever wrote it,
and that number would then propagate into a revenue forecast. The fill rate has to
be measured or the gate stays open.

## Blocking the gate

1. **Egress policy denies all three venue hosts.** Nothing can be measured until
   `api.elections.kalshi.com`, `clob.polymarket.com`, and
   `gamma-api.polymarket.com` are allowlisted. This is the only hard blocker.
2. **Payload shapes unverified.** Needs one real response per endpoint diffed
   against `tests/data/*.json` before any live output is trustworthy.
3. **Fee primary sources return 403.** Formulas are corroborated but not
   reconciled against a venue document or an actual order preview. The `M`
   multiplier is unresolved.
4. **Settlement terms are not in the market-data payloads.** Neither venue
   publishes overtime, void, postponement, or listed-pitcher rules in
   machine-readable form, so an adapter reading only public data yields
   `UNVERIFIED` and the pair is excluded. Crossing this gap is manual: a human
   reads both rulebooks and records coded fields with citations in
   `emc/registry.py`. **This caps a fully automated probe at producing candidate
   pairs, not measured capacity**, and it is the rate limiter on any real
   measurement.
5. **Jurisdiction, venue automation permission, and account eligibility are
   unverified.** Not blocking read-only collection. Blocking any execution phase.
