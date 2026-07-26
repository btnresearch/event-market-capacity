# Status

**As of 2026-07-26.**

## Current measured economics

**$0.** No live data has been collected. No fill, no settlement, no reconciled
cash. Every number below is either arithmetic on published fee schedules or a
measurement over synthetic fixtures.

The one economically decisive number established this session:

> A two-leg settlement-matched taker/taker locked position on Kalshi + Polymarket
> costs **2.88c–3.00c per contract in fees** across the 0.40–0.60 price band where
> competitive MLB game-winner markets trade.

At an already-extreme 4c persistent gross cross, $250k/yr needs **138,889
contracts and ~$133,000 of capital per slate**. That is orders of magnitude above
what MLB game-winner books quote. Hypothesis A is killed at taker/taker without
needing any live data — see DECISIONS.md.

## Tests

```
264 passed in 7.54s
```

`python -m pytest`. No network required. Coverage by area:

| Area | Module | Tests |
|---|---|---|
| Fee formulas, rounding, maker/taker, provenance | `test_fees.py` | 8 |
| Locked profit, depth, integer optimization, caps | `test_locked.py` | 25 |
| Revenue gates and the fee floor | `test_gates.py` | 21 |
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
| Polymarket sports taker | `C × 0.05 × p × (1−p)`, max $1.25/100 shares | **CORROBORATED**. Rate raised 0.03 → 0.05 in July 2026. |
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

**Does any Kalshi ↔ Polymarket MLB game-winner pair ever quote a gross cross-venue
spread above 3.00c, simultaneously, at depth, with provably identical settlement?**

Yes → measure QLP at 0, 5, 30, 90s and test against $1,389/slate.
No → Hypothesis A is dead outright, not just at $250k, and the work moves to
Hypothesis B where the binding question is inventory risk rather than locked profit.

The fee floor makes the expected answer "no". The gate exists to falsify that
expectation with data rather than assume it.

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
