# Method

## The question

Do settlement-matched cross-venue sports-market opportunities exist at
economically meaningful depth?

Three words in that question do the work, and each one is a gate in the code.

**Settlement-matched.** Two contracts form a pair only if they resolve on the
same event under the same rules. Not the same game — the same rules. Overtime
treatment, postponement and void handling, and the authoritative settlement
source all have to agree, or the two contracts are different instruments that are
*supposed* to trade at different prices.

**Cross-venue.** The measurement is the spread between two venues' books, not
within one.

**Economically meaningful depth.** Not the touch. The question is how much
capital fits, net of costs, so every level of both books is consumed and priced
separately.

## What is measured

For a matched pair, buy YES on the cheaper venue at its ask `a` and take the
opposite side on the richer venue at its bid `b`. Taking the opposite side is a
NO purchase at `1 - b`, so per contract:

```
gross edge = b - a
capital    = (1 - (b - a)) * payout
profit     = (b - a) * payout - fees
net edge   = profit / contracts
```

The position returns exactly the payout at settlement regardless of outcome,
*because* the legs resolve identically. That is the whole reason settlement
matching is the first-order concern rather than a footnote.

The output is a capacity curve: for each net-edge threshold, how many contracts
and how much capital clear it. Capacity is reported at 0, 0.25c, 0.5c, 1c, 2c,
and 5c of net edge per contract by default.

### Two things the walk gets right

**Capacity is not the touch.** The best quote says nothing about the size behind
it. Both books are consumed level by level.

**Net edge is not monotone in depth.** Gross edge decays monotonically as the
books are consumed, but per-contract cost does not: a fixed per-fill cost is
spread over the slice, so a thin slice at the touch can be uneconomic while a
thick slice one level deeper clears comfortably. One contract at a 3-cent gross
edge cannot carry a 5-cent fill cost; a thousand contracts at 1 cent can. So
every slice is priced, slices are ranked by realized net edge, and the qualifying
set is summed. Walking the book and stopping at the first failing slice would
report zero capacity where real capacity exists — `tests/test_depth.py` pins that
case.

## The three gates

Applied in this order, before a pair contributes a single contract.

| Gate | Rejects | Why |
|---|---|---|
| Simultaneity | Captures more than 2s apart | Two books read seconds apart show edges that never simultaneously existed |
| Settlement equivalence | Any pair not provably identical | Most apparent cross-venue spread is a settlement-rule difference, not an opportunity |
| Cost-adjusted depth | Slices below the net-edge threshold | Gross edge is not edge |

Everything rejected is counted and reported. A probe that says "no capacity" is
only informative if it also says how many candidates it rejected and why —
"we verified this pair and there was no edge" and "we could not verify anything"
are different results, and the report distinguishes them
(`matched_no_capacity` vs `unverified`).

The adjudicator is deliberately pessimistic. `MATCHED` requires every required
field present on both sides and in agreement. Any conflict is `MISMATCHED`. Any
missing field, or any two distinct settlement sources whose equivalence has not
been asserted in writing, is `UNVERIFIED` and excluded. Excluding a real
opportunity costs nothing here. Counting a fake one corrupts the measurement.

## Findings

### 1. Fees, not spreads, are usually the answer

The committed example carries a 3-cent gross cross on 600 contracts. Under a
`0.07 * P * (1-P)` fee at P=0.54, the fee is 1.74c per contract — 58% of the
gross edge. Net edge is 1.25c, and total profit on $582 of capital is $7.51, or
1.29%.

This is the general shape of the result, not an artifact of the example. That fee
form peaks at P=0.50, where it costs 1.75c per contract. Cross-venue gross
spreads on liquid sports markets are frequently smaller than that. A screen that
reports gross spreads will produce a long list of opportunities that a net screen
reduces to nearly nothing, and the closer a market is to a coin flip the more
severe the reduction.

### 2. Settlement terms are not in the market-data payloads

This is the structural finding and it bounds what any automated probe can do.

Venue market-list and order-book endpoints publish tickers, titles, prices, and
close times. They do not publish overtime treatment, void and postponement
rules, or the authoritative settlement source in machine-readable form. Those
live in prose rules pages that are not part of the market payload.

An adapter that reads only public market data therefore leaves those fields
empty, the adjudicator returns `UNVERIFIED`, and the pair is excluded. That is
correct behavior, and it means **the honest ceiling on a fully automated probe is
a list of candidate pairs, not measured capacity.** Crossing that gap requires a
human to read both venues' rules.

`emc/registry.py` is where that human work is recorded, and it requires
provenance — a source URL and a verification date — on every entry. Uncited
settlement claims are refused at load time rather than propagated into a capacity
number. `tests/test_probe.py` shows a pair moving from `UNVERIFIED` to measured
capacity once the missing term is supplied with a citation.

### 3. The cost model is the weakest input

Fee rates in `emc/fees.py` and `emc.cli.build_default_costs` are **configurable
placeholders, not verified schedules.** They are deliberately non-zero so the
probe never runs at an implicit zero cost, which would overstate every number.
At the edge sizes that matter — fractions of a cent per contract — the fee
assumption usually determines the sign of the answer.

Before any output is treated as a finding, re-derive these from each venue's
current published schedule and record the source and date in the table below.

| Venue | Model | Value | Source | Verified |
|---|---|---|---|---|
| kalshi | `KalshiStyleFee` | `rate = 0.07` | **unverified placeholder** | — |
| polymarket | `BpsFee` + `FixedPerFillFee` | `0 bps` + `$0.05/fill` | **unverified placeholder** | — |

Maker/taker role, volume tier, and per-series variation are not modeled at all.

## What is verified and what is not

**Verified by the test suite (196 tests):** the capacity math, threshold
filtering, slice ranking under non-proportional costs, position-cap truncation
and re-pricing, payout scaling, the settlement adjudicator's full verdict matrix,
book validation including crossed-book rejection, exact-decimal serialization
round trips, registry provenance enforcement, the CLI, and the read-only scope
guardrails.

**Not verified:** the venue payload shapes. The development environment's network
policy denies outbound access to `api.elections.kalshi.com`,
`gamma-api.polymarket.com`, and `clob.polymarket.com` (the proxy returns 403 to
CONNECT), so no live payload was ever fetched. The parsers in `emc/venues/` were
written against the documented shapes and are tested against hand-written
fixtures in that shape. That proves the normalization logic is self-consistent.
It does not prove the shape matches the live API.

The riskiest piece of that untested surface is the YES/NO normalization. Kalshi
publishes resting bids on both sides, so the YES ask ladder is the NO bid ladder
reflected through 100; Polymarket quotes each outcome as its own token, so a NO
token's book reflects through 1 *and swaps sides*. Getting either backwards
invents a large fake spread. Both are pinned by tests, but against assumed
payload shapes.

## Running it

```bash
pip install -e '.[dev]'
python -m pytest

# Measure capacity from a snapshot set
python -m emc.cli probe --snapshots data/example/snapshots.json

# With curated settlement terms
python -m emc.cli probe --snapshots data/example/snapshots.json \
                        --settlement data/example/settlement.json --json
```

`data/example/` is synthetic and hand-written to exercise all three gates. No
number derived from it is a market observation.

## Next steps, in dependency order

1. **Allowlist the three venue hosts** in the environment's network policy. Until
   then no live measurement is possible.
2. **Capture one real payload per endpoint**, diff against `tests/data/*.json`,
   and correct the parsers. Do this before anything else downstream.
3. **Verify the fee schedules** and fill in the table above.
4. **Curate settlement terms** for a small set of candidate pairs — one league,
   one market type — with citations. This is manual and is the real bottleneck.
5. **Then** measure, over repeated simultaneous captures rather than one instant.

## What this does not measure

Capacity here is a single-instant upper bound, not an achievable return. It
excludes execution latency between the two legs, queue position and whether
resting size is actually available to a taker, capital transfer time between
venues, funding costs while capital is committed until settlement, and settlement
risk on any term the adjudicator could not check.

Capacity is summed across pairs as independent positions. That reading holds only
if the pairs do not share a settlement event; a portfolio built across correlated
events carries correlation the sum does not show.
