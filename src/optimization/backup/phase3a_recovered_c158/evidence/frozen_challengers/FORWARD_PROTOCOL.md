# FORWARD PROTOCOL — clean out-of-sample comparison of the five frozen candidates

Prepared, **not started**. Nothing in this document has been executed: no forward test, no replay,
no backtest, no data fetch.

## Why forward-only

Every historical window through 2026-08-15 has already informed the selection of these five
candidates, and the DEV window 2024-07-16 .. 2026-07-15 has been searched twice. A further backtest
over those dates cannot discriminate between them — it can only restate the ledger. The only
uncontaminated evidence left is data that did not exist when they were chosen.

## Start

```
Forward evaluation begins   2026-08-18 00:00:00 UTC
First evaluated candle      the first 15m candle closing at or after that instant
```

No candle before 2026-08-18 00:00 UTC contributes a trade, PnL, drawdown or exposure figure to this
comparison. Historical candles are used **only** as indicator warmup (a fixed 1,000-candle lead-in,
computed on the full warmup+forward frame before slicing, per the project's standing warmup rule),
and warmup candles can never open a position.

The window 2026-07-16 .. 2026-08-17 is **not** part of this protocol. It is neither a forward
period nor a selection input here; it stays untouched.

## Candidates — fixed at t=0

| slot | config file | role |
|---|---|---|
| 1 | `trial285_candidate158_benchmark.json` | historical benchmark (Candidate #158) |
| 2 | `trial189_primary_challenger.json` | primary generalisation challenger |
| 3 | `trial156_low_dd_alternate.json` | low-drawdown alternate |
| 4 | `trial125_risk_boundary_hypothesis.json` | risk-boundary hypothesis |
| 5 | `trial52_defensive_high_sample.json` | defensive / high-sample hypothesis |

Plus one **shadow slot, outside the ranking pack**:

| slot | config file | role |
|---|---|---|
| S | `trial285_candidate158_bollinger_on_shadow.json` | deployed historical system: C158 + Bollinger ON |

The original five are the only fair Bollinger-OFF ranking pack; slot S is a shadow / deployed
benchmark only and **is not eligible to win the five-way raw-strategy ranking**. It is observed
from the same start instant under identical conditions, from its own independent $10,000 account,
and may be compared descriptively with the five — never used to rank them. Slot S differs from
slot 1 in exactly the six Bollinger values plus `enabled`; `strategy`, `risk` and `execution` are
identical. Its descriptive metadata also differs, intentionally, so the file states its shadow role
accurately. It remains excluded from ranking.

Identity is pinned by the sha256 values in `MANIFEST.md`. Verify all five hashes before the first
candle and again at every report; a changed hash invalidates the run.

## Identical conditions

Each candidate runs its own isolated paper account, all five starting from:

```
initial_capital        $10,000.00   (independent, no shared equity, no cross-netting)
symbol / timeframe     ETHUSDT perpetual (Binance USD-M), 15m
feed                   one shared candle stream — the same closes drive all five engines
commission             0.05% taker, charged on entry and exit notional
slippage               1 tick (0.01), always adverse
sizing                 BaselineRiskManager, RISK_BASED, quantity_step 0.001, floor never round up
execution              entry at the next candle's open ± slippage; SL/TP first evaluated on the bar
                       after entry; same-bar SL+TP collision resolves to SL; gap handling per engine
direction              long_enabled true, short_enabled false
filters                Bollinger DISABLED for all five
```

Only the 11 strategy and 3 risk values differ between slots. Any divergence in feed, fee, slippage,
sizing or execution assumption between candidates voids the comparison.

## Frozen for the duration

- No parameter may change — not one of the 11 strategy or 3 risk values, in any slot, including S.
- No Bollinger tuning anywhere. Slots 1-5 keep the filter disabled for the whole window; slot S
  keeps its six frozen Bollinger values exactly as shipped. Neither may be toggled or retuned.
- No candidate added, removed, restarted, re-funded or re-seeded.
- No objective, gate, threshold or ranking rule introduced after the start.
- If a process dies it resumes from its checkpointed state; it does not restart with fresh equity.
  An unrecoverable gap is recorded as a data-quality event against **all five** slots, not patched.

## Recorded per candidate

Per closed trade: side, signal timestamp, entry/exit timestamp and price, quantity, notional,
margin, leverage, SL, TP, holding duration, gross PnL, fees, slippage, **net PnL**, exit reason.

Per reporting interval, always reported **after all fees and slippage**, and always with profit and
loss stated separately rather than only netted:

```
net profit          sum of winning trades' net PnL
net loss            sum of losing trades' net PnL
net P&L             net profit − net loss
fees paid           total commission (already inside the three figures above)
return %            net P&L / 10,000
profit factor       net profit / net loss
max drawdown %      peak-to-trough on the equity curve
trade count         closed trades, plus wins / losses and win rate
exposure            % of elapsed candles holding a position, and mean holding duration
open position       marked-to-market separately; never mixed into closed-trade figures
```

## Selection embargo

Slot S is excluded from every ranking, ordering and winner claim at all times, before and after the
gate. Its figures are reported in a separate row marked *shadow / not ranked*.

**No ranking, promotion, elimination or winner claim before BOTH of:**

```
>= 90 calendar days elapsed since 2026-08-18 00:00 UTC   (i.e. not before 2026-11-16)
>= 30 closed trades for EVERY one of the five candidates
```

Until both hold, output is **interim data only** — tables and curves, no ordering, no language
implying one candidate leads. A candidate that has not reached 30 closed trades blocks the gate for
the whole pack; it is not dropped to unblock it.

Rationale for the trade floor: trial 285's DEV VALID conclusions rest on 59 trades, which is thin
for a profit-factor claim. Trial 52 trades roughly 4× more often than trial 189, so a fixed
calendar window alone would compare a large sample against a small one.

When the gate opens, decide in advance of seeing the data which measure ranks the pack, and record
that choice in this file before unsealing. Ranking on a metric chosen after the fact reintroduces
exactly the selection bias this protocol exists to avoid.

## Interim reporting cadence

Weekly interim snapshot covering slots 1-5 plus slot S in a separate *shadow / not ranked* row, and
an event note whenever a candidate crosses 10 / 20 / 30 closed trades. Slot S's trade count does not
count toward the embargo gate — the gate depends on slots 1-5 only.
Interim snapshots carry the fixed disclaimer: *sample below the embargo threshold, no ordering
implied.*
