# event-market-capacity

Capacity research for cross-venue sports event markets.

A read-only probe that measures one thing: for pairs of contracts that provably
settle on the same event under the same rules, how much capital fits at a given
net edge, given the depth actually resting on both books at one instant.

## Scope

This is a measurement tool. It reads public quote data and computes a property of
it.

It does **not** submit orders, trade, wager, forecast outcomes, or authenticate to
any venue. Every venue adapter issues unauthenticated HTTP GET requests against
public market-data endpoints. Those constraints are enforced by
`tests/test_readonly_guardrails.py`, which parses every module and fails the build
on any credential construct, mutating HTTP verb, order-management path, modelling
dependency, or new CLI subcommand.

## Quickstart

```bash
pip install -e '.[dev]'
python -m pytest                     # 196 tests, no network required

python -m emc.cli probe --snapshots data/example/snapshots.json
```

```
pair adjudication
  matched_with_capacity    1
  matched_no_capacity      0
  mismatched               1
  unverified               1
  stale_skew               0

capacity at net edge (per contract, $1 payout)
    min edge  contracts        capital         profit      ROC
  ------------------------------------------------------------
           0        800 $      778.00 $        7.98    1.03%
        0.01        600 $      582.00 $        7.51    1.29%
        0.02          0 $        0.00 $        0.00      n/a
```

That example carries a 3-cent gross cross. Fees take 1.74c of it. What is left is
600 contracts and $7.51 on $582 of capital, and the other two candidate pairs are
rejected outright — one for conflicting overtime rules, one for unverifiable
settlement terms.

`data/example/` is synthetic and hand-written to exercise all three gates. No
number derived from it is a market observation.

## How it works

Three gates, applied in order, before a pair contributes any measured capacity:

1. **Simultaneity** — captures more than 2s apart are excluded. Books read
   seconds apart show edges that never simultaneously existed.
2. **Settlement equivalence** — a pair counts only if every required resolution
   term is present on both sides and agrees. Conflicts are `MISMATCHED`; missing
   or unproven terms are `UNVERIFIED`. Both are excluded and both are reported.
3. **Cost-adjusted depth** — both books are consumed level by level under an
   explicit cost model, and slices are ranked by realized net edge.

Everything rejected is counted, so "we verified this pair and found no edge" is
never confused with "we could not verify anything".

## Layout

```
src/emc/
  models.py       value types; Decimal prices, book validation, crossed-book rejection
  fees.py         venue cost models (placeholder rates — see docs/METHOD.md)
  settlement.py   settlement-equivalence adjudication, deliberately pessimistic
  depth.py        the measurement: capacity curve over book depth
  probe.py        orchestration and reporting
  registry.py     human-verified settlement terms, provenance required
  serde.py        exact-decimal snapshot serialization
  venues/         read-only adapters: kalshi, polymarket, offline fixtures
  cli.py          `probe` and `capture`
data/example/     synthetic snapshots and a placeholder settlement registry
docs/METHOD.md    measurement definition, findings, and limitations
```

## Status

The measurement engine is complete and tested. The ingest layer is not verified
against live endpoints.

- **Working and tested:** capacity math, adjudication, cost models, registry, CLI,
  serialization, scope guardrails. 196 tests, no network required.
- **Unverified:** venue payload shapes. The development environment's network
  policy denies outbound access to the Kalshi and Polymarket hosts, so no live
  payload was ever fetched. Parsers were written against documented shapes and
  tested against hand-written fixtures. Capture one real payload per endpoint and
  reconcile before trusting live output.
- **Placeholder:** fee rates. Non-zero by design so nothing runs at an implicit
  zero cost, but not verified against any published schedule.

Two findings are worth reading before using this: fees usually exceed the gross
spread on near-coin-flip markets, and settlement terms are not published in
machine-readable form by either venue — which caps a fully automated probe at
producing *candidate* pairs rather than measured capacity. Both are documented
with their consequences in [docs/METHOD.md](docs/METHOD.md).
