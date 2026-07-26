# event-market-capacity

Capacity research for cross-venue sports event markets.

A read-only probe answering one question: for pairs of contracts that provably
settle on the same event under the same rules, how much **locked profit** is
quoted, net of exact fees, across real depth, within a shared capital budget?

**Current defensible revenue forecast: $0.** See [STATUS.md](STATUS.md).

## The headline result

```
$ python -m emc.cli screen

    price  taker/taker  taker/maker  maker/taker  maker/maker
     0.40      2.880c      1.680c      1.620c      0.420c
     0.50      3.000c      1.750c      1.688c      0.438c
     0.60      2.880c      1.680c      1.620c      0.420c
```

Both venues charge `rate × contracts × price × (1 − price)`, which peaks at 0.50 —
exactly where competitive MLB game-winner markets trade. A two-leg taker/taker
locked position therefore needs a **3-cent** gross cross-venue spread just to break
even. At an extreme 4c cross, $250k/yr requires 138,889 contracts and ~$133,000 of
capital *per slate*.

That kills settlement-matched cross-venue arbitrage as a $250k route, from the fee
schedules alone, with no live data. Details in [DECISIONS.md](DECISIONS.md).

## Scope

A measurement tool. It reads public quote data and computes a property of it.

It does **not** submit orders, trade, wager, forecast outcomes, or authenticate to
any venue. `tests/test_readonly_guardrails.py` parses every module and fails the
build on any credential construct, mutating HTTP verb, order-management path,
modelling dependency, or new CLI subcommand.

## Quickstart

```bash
pip install -e '.[dev]'
python -m pytest                       # 264 tests, no network required

python -m emc.cli screen               # fee floor + revenue gates, no data needed
python -m emc.cli probe --snapshots data/example/snapshots.json \
                        --capital kalshi=50000,polymarket=50000
```

`data/example/` is synthetic, hand-built to exercise every gate. No number derived
from it is a market observation.

## How it works

Gates, in order, before a pair contributes any measured capacity:

1. **Simultaneity** — captures >2s apart excluded.
2. **Settlement equivalence** — canonical *coded* rule fields, never prose.
   `MISMATCHED` is a positive claim of difference; `UNVERIFIED` means evidence is
   missing and is not a soft match.
3. **Exact locked profit** — `min(profit_if_yes, profit_if_no)` over integer
   quantity pairs, full-depth VWAP, fees rounded once per order.
4. **Shared capital** — simultaneous opportunities compete for one per-venue
   budget; the same capital is never counted twice.
5. **Episodes** — a continuously stale price is one opportunity, not one per poll.

Everything rejected is counted, so "verified, no profit" is never confused with
"could not verify".

## Layout

```
src/emc/
  models.py       Decimal prices, book validation, canonical settlement codes
  fees.py         venue fee schedules with provenance and verification status
  locked.py       exact locked profit over integer quantities and real depth
  gates.py        revenue gates and the fee-floor screen
  qlp.py          shared-capital allocation and episode collapsing
  settlement.py   settlement-equivalence adjudication over coded fields
  probe.py        orchestration and reporting
  registry.py     human-verified settlement terms, provenance required
  serde.py        exact-decimal serialization, coded-field validation
  venues/         read-only adapters: kalshi, polymarket, offline fixtures
  cli.py          screen / probe / capture
MISSION.md        mission, hypotheses, gates, constraints
STATUS.md         what is live, mocked, blocked; measured economics; next gate
DECISIONS.md      dated decisions, measurements, killed hypotheses
```

## Status summary

- **Working and tested:** locked-profit math, adjudication, fee models, capital
  allocation, episodes, registry, CLI, serialization, scope guardrails.
- **Blocked:** all three venue hosts are denied by the environment's egress policy.
  No live payload has ever been fetched. Parsers are written to documented shapes
  and tested against hand-written fixtures — self-consistent, not verified.
- **Corroborated, not primary:** fee formulas agree across independent secondary
  sources; both venues' primary docs returned HTTP 403. Kalshi's `M` multiplier for
  MLB is unresolved.

The rate limiter on any real measurement is not code. Neither venue publishes
overtime, void, postponement, or listed-pitcher rules in machine-readable form, so
an automated probe can only produce *candidate* pairs. Turning candidates into
measured capacity requires a human to read both rulebooks and record coded fields
with citations.
