# PROJECT PLAYBOOK

## Experiment-03_Simple_AlgoTrade --- Human Guide

> This is the human-facing guide to the project: what it does, how to
> run it, how the execution modes differ, how the auto-optimiser works,
> and where development currently stands.
>
> **Rule:** if this guide ever disagrees with the live code, the live
> code wins and this file should be updated.

------------------------------------------------------------------------

# 1. The project in one picture

Current primary research market:

``` text
ETHUSDT / ETHUSDT.P
Binance Futures
15m
```

The project is config-driven:

``` text
CONFIG JSON
    │
    ▼
STRATEGY + INDICATORS
    │
    ├──────────────┬──────────────────┐
    ▼              ▼                  ▼
BACKTEST     HISTORICAL REPLAY    FORWARD PAPER
    │              │                  │
    └──────────────┴──────────────────┘
                   │
                   ▼
           TRADES + METRICS
```

The **BacktestEngine is essential**. The auto-optimiser sits on top of
it:

``` text
Optuna proposes parameters
        ↓
build StrategyConfig
        ↓
real BaselineStrategy
        ↓
real BacktestEngine
        ↓
real BacktestMetrics
        ↓
score candidate
        ↓
next trial
```

The optimiser does not replace the backtester. It runs the real
backtester repeatedly.

------------------------------------------------------------------------

# 2. Main launcher

The human entry point is:

``` bash
./pipeline.sh
```

See the live command list with:

``` bash
./pipeline.sh --help
```

Optimisation is a separate operating mode. It must not be combined with
execution/maintenance modes such as backtest, forward test, historical
replay, reset, hard reset, clear cache, or normal `--config` execution.

------------------------------------------------------------------------

# 3. Configs

There are two kinds of JSON.

## Trading configs

Runnable strategy configs include:

``` text
default.json
config1-ETHUSDTP15m-long.json
config2-ETHUSDTP15m-long.json
future optimiser-generated configs
```

The intended destination is:

``` text
configs/config/
```

If the live repository still has files directly under `configs/`, follow
the live tree until that migration is complete.

## Optimiser presets

Human-facing optimiser settings live under:

``` text
configs/optimize/
```

with the default preset:

``` text
odefault.json
```

Canonical optimiser command:

``` bash
./pipeline.sh --optimize --odefault.json --outputname.json
```

The eventual winner should be written as a runnable trading config. The
output name is mandatory and must not already exist.

The preset is intentionally simple: platform, symbol, timeframe,
history, starting balance, direction, trial count, optimisation mode,
and stage toggles. Optuna internals, seeds, warmup, partitions, search
ranges and storage should stay automatic unless an advanced mode is
added later.

------------------------------------------------------------------------

# 4. Candidate #158

Candidate #158 is the frozen benchmark stored in
`config1-ETHUSDTP15m-long.json`.

Recovered parameters:

``` text
EMA                         104
RSI                          20
RSI overbought               64
RSI oversold                 23
ATR                           7
Consolidation candles         7
Consolidation ATR mult       2.8
Swing lookback               17
Volume SMA                   12
Volume multiplier            1.8
Risk / Reward                3.6

Direction              LONG only

Leverage                    4.0x
Risk/trade                  2.6%
Max allocation               70%
```

Its Config1 Bollinger block is disabled.

Known Config1 regression reference:

``` text
Return        +274.67%
Trades             262
```

Candidate #158 is a **benchmark, not a seed**. The new optimiser should
not initialise around it, narrow ranges around it, enqueue it specially,
or use it to tune the objective.

Also, Candidate #158 was not necessarily the raw #1 model from its
original campaign. This project has deliberately preferred
robust/balanced candidates over blindly promoting the highest headline
score.

------------------------------------------------------------------------

# 5. Bollinger / Config2

Bollinger is the surviving production filter.

Rejected experiments such as MTF and KEMAD were removed. Experimental
ADX filtering was also rejected; do not silently resurrect rejected
filter stages.

Filter rule:

``` text
BaselineStrategy produces a candidate signal
              ↓
          filter gate
        ┌─────┴─────┐
      ALLOW        BLOCK
        │             │
 normal engine      no trade
```

A filter may remove a signal. It must never create one.

With a filter disabled, the underlying strategy should behave
identically.

Config2 has been used for Candidate #158 + Bollinger. Check the current
JSON before quoting exact Config2 metrics because experiments changed
during development.

------------------------------------------------------------------------

# 6. Backtest --- what it actually does

A backtest asks:

> If this exact config had run over these historical candles, what
> trades would the production simulation have produced?

``` text
historical candles
      ↓
indicators
      ↓
strategy evaluates
      ↓
optional filters block/allow
      ↓
RiskManager sizes position
      ↓
BacktestEngine simulates entry
      ↓
SL / TP / exit
      ↓
fees + slippage + accounting
      ↓
trade log
      ↓
equity curve + metrics
```

Typical command:

``` bash
./pipeline.sh --config <config>.json --backtest
```

Use `./pipeline.sh --help` if config-path resolution changes.

Important metrics:

``` text
Return %
Net PnL
Gross profit
Gross loss
Profit Factor
Sharpe
Max Drawdown
Trades
Wins / losses
Win rate
Fees
signals/trades blocked by filters
```

Do not judge a strategy from Return% alone.

------------------------------------------------------------------------

# 7. Historical replay

Historical replay feeds historical candles through a forward-style
sequential workflow.

``` text
candle closes
    ↓
engine receives only information available then
    ↓
strategy / risk / position handling
    ↓
next candle
    ↓
repeat
```

Its parity target is:

``` text
same candles + same config

BACKTEST                  HISTORICAL REPLAY
signals       ==          signals
entries       ==          entries
exits         ==          exits
SL/TP         ==          SL/TP
quantity      ==          quantity
PnL           ==          PnL
```

This is useful for detecting lookahead and differences between research
and forward execution.

Use the current `--historical-replay` mode shown by
`./pipeline.sh --help`.

Historical replay should reuse strategy/business logic rather than
contain a duplicate strategy implementation.

------------------------------------------------------------------------

# 8. Forward paper test

Forward testing asks:

> What does the strategy do from now onward as new market data arrives,
> without risking real money?

``` text
historical warmup
      ↓
indicators ready
      ↓
live market feed
      ↓
new candle / market information
      ↓
strategy decision
      ↓
paper position + accounting
      ↓
persistent state + dashboard
      ↓
repeat
```

The forward system uses concepts such as `MarketFeed`, `PaperEngine`, a
Rich terminal dashboard, persistent forward state, and forward-result
files.

Typical command:

``` bash
./pipeline.sh --config <config>.json --forward-test
```

The dashboard displays engine state. It must never drive trading
decisions.

A 15m strategy does not need to generate a decision every dashboard
refresh. Trading logic follows market/candle events, not UI refresh
speed.

------------------------------------------------------------------------

# 9. Backtest vs replay vs forward

  ----------------------------------------------------------------------------
  Mode              Data              Purpose                Time
  ----------------- ----------------- ---------------------- -----------------
  Backtest          Historical        Fast                   Fast
                                      strategy/performance   
                                      research               

  Historical replay Historical        Parity + forward-style Slower
                    sequential        validation             

  Forward test      Incoming/live     Paper validation in    Real time
                                      real time              
  ----------------------------------------------------------------------------

Mental model:

``` text
BACKTEST
Does it work historically?
      ↓
HISTORICAL REPLAY
Does forward-style execution reproduce the historical logic?
      ↓
FORWARD PAPER
Does it behave correctly as new data actually arrives?
```

None replaces the others.

------------------------------------------------------------------------

# 10. Auto-optimiser roadmap

New implementation:

``` text
src/auto_optimise/
```

The six-stage roadmap:

``` text
[1/6] DATA PREPARATION
        │
        ▼
[2/6] STRATEGY OPTIMIZATION
        │
        ▼
[3/6] STRATEGY ROBUSTNESS
        │
        ▼
[4/6] RISK MANAGEMENT
        │
        ▼
[5/6] BOLLINGER
        │
        ▼
[6/6] FINAL TOP 10 + UNSEEN
        │
        ▼
   WINNER CONFIG
```

Current status when this guide was created:

``` text
[1/6] Data Preparation       IMPLEMENTED
[2/6] Strategy Optimization  IMPLEMENTED + smoke-tested
      Full campaign          NEXT after score/trade-gate audit
[3/6] Strategy Robustness    PLANNED
[4/6] Risk Management        PLANNED
[5/6] Bollinger              PLANNED
[6/6] Final Selection        PLANNED
```

Stage 2 code working does **not** mean a new champion has already been
found.

------------------------------------------------------------------------

# 11. Stage \[1/6\] --- Data Preparation

History can be requested as latest N days:

``` json
"history": {
  "days": 180,
  "start_date": null,
  "end_date": null
}
```

or explicit dates:

``` json
"history": {
  "days": null,
  "start_date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD"
}
```

Canonical chronological split:

``` text
TRAIN       60%
VALIDATION  20%
UNSEEN      20%
```

Never shuffled.

``` text
earlier                                                later

[ WARMUP ][──────── TRAIN 60% ────────][ VALID 20% ][ UNSEEN 20% ]
                                                       LOCKED
```

Warmup belongs to no metrics partition.

## The warmup bug this fixes

Wrong:

``` text
slice TRAIN
→ start EMA/RSI/ATR from the partition edge
```

Correct:

``` text
load historical context
→ compute trial indicators with warmup
→ evaluate only the requested partition rows
```

Phase A changes indicator periods per trial, so partition context must
include the required historical lead-in before trial-specific indicators
are recomputed.

The still-forming candle is dropped for deterministic results.

A completely empty cache is valid: Stage 1 should download the required
history + warmup.

------------------------------------------------------------------------

# 12. UNSEEN is the final exam

UNSEEN is structurally locked.

During:

``` text
strategy search
validation
robustness
risk optimisation
Bollinger optimisation
ranking
```

UNSEEN must remain inaccessible.

Mental model:

``` text
TRAIN       = learn
VALIDATION  = check generalisation
UNSEEN      = final exam
```

Final process:

``` text
TRAIN + VALIDATION
      ↓
freeze finalists/ranking
      ↓
no more tuning
      ↓
unlock UNSEEN once
      ↓
confirmation only
```

UNSEEN must not reorder the Top 10 or cause retuning.

------------------------------------------------------------------------

# 13. The Five V3 Stages --- Strategy, Risk and Filter Optimization

Goal:

> Find signal edge before leverage/risk sizing can distort candidate
> ranking.

Current neutral Phase-A sizing:

``` text
Leverage       1.0x
Risk/trade     1.5%
Allocation      50%
```

Every Phase-A trial uses the same policy.

## 11 search dimensions

``` text
 1. EMA period
 2. RSI period
 3. RSI overbought
 4. RSI oversold
 5. ATR period
 6. Consolidation candles
 7. Consolidation ATR multiplier
 8. Swing lookback
 9. Volume SMA period
10. Volume multiplier
11. Risk / Reward ratio
```

Current broad ranges:

``` text
EMA                         10 – 200
RSI period                   7 – 35
RSI overbought              55 – 80
RSI oversold                20 – 45
ATR                           7 – 35
Consolidation candles        4 – 15
Consolidation ATR mult     1.0 – 4.0
Swing lookback               3 – 20
Volume SMA                  10 – 50
Volume multiplier          0.5 – 2.5
Risk / Reward              1.0 – 5.0
```

Not searched in Phase A:

``` text
leverage
risk_per_trade_pct
max_position_allocation_pct
```

Those belong to Stage 4.

------------------------------------------------------------------------

# 14. Trials vs dimensions

These are different ideas.

``` json
"trials": 5000
```

means:

``` text
5,000 proposed candidate combinations
in an 11-dimensional strategy space
```

Each trial runs a real backtest.

`"trials": "auto"` lets the optimiser choose the Phase-A budget. The
current provisional mapping uses:

``` text
15m → 750 Phase-A trials
```

An explicit integer such as 5000 overrides auto.

------------------------------------------------------------------------

# 15. One Phase-A trial

``` text
Optuna/TPE proposes:
EMA / RSI / OB / OS / ATR / consolidation /
swing / volume / RR
          ↓
trial-specific indicators
with correct historical context
          ↓
BaselineStrategy
          ↓
BacktestEngine on TRAIN
          ↓
BacktestMetrics
          ↓
Phase-A score
          ↓
Optuna learns from TRAIN result
```

Repeat hundreds or thousands of times.

The study is seeded/persisted so campaigns can be reproduced and
resumed.

------------------------------------------------------------------------

# 16. TRAIN and VALIDATION discipline

``` text
all Optuna proposals
       ↓
TRAIN only
       ↓
freeze TRAIN ranking
       ↓
shortlist
       ↓
VALIDATION
```

VALIDATION should not feed back into TPE proposals.

Otherwise it becomes another training set.

The 30-trial smoke campaign demonstrated why this matters:

``` text
TRAIN
Return   +17.44%
PF         2.380
Sharpe     1.05
DD         3.74%
Trades       33

VALIDATION
Return    -2.54%
PF         0.570
Sharpe    -0.79
DD         5.15%
Trades       10
```

That candidate was a plumbing/smoke result, not a new champion.

------------------------------------------------------------------------

# 17. Phase-A score and trade-count gate

The optimiser should not blindly maximize raw return.

The score should consider:

``` text
Return / Net PnL
Profit Factor
Sharpe
Max Drawdown
Trade count
```

and reject/penalize:

``` text
no-trade candidates
tiny lucky samples
catastrophic drawdown
strongly negative systems
invalid simulations
huge PF caused by barely trading
```

**Current immediate task:** audit the exact score and minimum-trade gate
before launching the full Phase-A campaign.

Questions the audit must answer:

``` text
Can one metric dominate?
Can a low-trade candidate cheat the score?
Does minimum trade count scale with history duration?
Does huge return with huge DD rank too highly?
```

------------------------------------------------------------------------

# 18. Optimiser terminal UI

Long campaigns show an in-place colored dashboard:

``` text
AUTO OPTIMISER

[1/6] Data Preparation       PASS
[2/6] Strategy Optimization  RUNNING
[3/6] Strategy Robustness    WAITING
[4/6] Risk Management        WAITING
[5/6] Bollinger              WAITING / SKIPPED
[6/6] Final Selection        WAITING

Trials:        438 / 750
Progress:      ███████████░░░
Stage elapsed: ...
Stage ETA:     ...
Overall time:  ...
Overall ETA:   ...
Trials/sec:    ...

Current best
Score:
Return:
PF:
Sharpe:
Max DD:
Trades:

Parameters
EMA:
RSI:
OB / OS:
ATR:
Consolidation:
Swing:
Volume SMA:
Volume Mult:
RR:
```

It updates in place instead of printing a permanent line per trial.

UI failure must never change optimiser mathematics or kill a valid
study.

------------------------------------------------------------------------

# 19. Stage \[3/6\] --- Strategy Robustness \[PLANNED\]

Phase A finds promising coordinates.

Stage 3 asks:

> Is this genuinely stable, or just one lucky Optuna point?

The final winner should **not automatically be Phase-A rank #1**.

``` text
strong Phase-A candidates
       ↓
local parameter perturbation
       ↓
regime / sequential-chunk testing
       ↓
consistency checks
       ↓
reject fragile peaks
       ↓
robust finalists
```

A Phase-A rank #7 can legitimately beat rank #1 after robustness.

Local stability means testing nearby parameter values. Regime testing
asks whether the strategy survives different market periods rather than
only one favourable environment.

UNSEEN stays locked.

------------------------------------------------------------------------

# 20. Stage \[4/6\] --- Phase B Risk Management \[PLANNED\]

Only after strategy logic is frozen do we optimise deployment risk.

RiskManager mathematics remain unchanged.

Search policy inputs only:

``` text
leverage
risk_per_trade_pct
max_position_allocation_pct
```

## B1 --- Broad risk search

Explore the return/risk landscape using:

``` text
Return
Net PnL
PF
Sharpe
Max DD
Fees
margin safety
```

Do not automatically choose the highest leverage, highest raw return, or
lowest DD.

## B2 --- Refinement / stability

``` text
promising B1 regions
      ↓
finer search
      ↓
small leverage/risk/allocation perturbations
      ↓
reject fragile policies
```

The optimiser may internally preserve:

``` text
PROFIT
BALANCED
DEFENSIVE
```

profiles even if one final config is emitted.

After B2:

``` text
strategy = FROZEN
risk     = FROZEN
```

------------------------------------------------------------------------

# 21. Stage \[5/6\] --- Bollinger \[PLANNED\]

Run only when enabled in the optimiser preset.

Freeze strategy, direction, risk and execution. Search only real
production Bollinger fields.

The experiment is fundamentally:

``` text
FILTER OFF
vs
FILTER ON
```

on TRAIN + VALIDATION.

A useful filter should ideally:

``` text
preserve/increase useful profit
reduce gross loss
improve PF
improve Sharpe
reduce DD
retain enough trades
```

Do not reward a filter merely because it removes almost every trade.

If Bollinger does not convincingly help, the final config may keep it
disabled even though the stage was tested.

------------------------------------------------------------------------

# 22. Stage \[6/6\] --- Final Top 10 + UNSEEN \[PLANNED\]

After enabled stages:

``` text
strategy
+ risk
+ optional Bollinger
```

build the final Top 10 using TRAIN + VALIDATION only.

Then:

``` text
freeze Top 10
freeze order
      ↓
unlock UNSEEN once
      ↓
run each finalist
      ↓
CONFIRMED / DEGRADED / FAILED
```

UNSEEN does not reorder the Top 10 and does not trigger retuning.

Only after this should the final runnable output config be emitted.

------------------------------------------------------------------------

# 23. Candidate #158 showdown

Candidate #158 stays out of the new optimiser's learning process.

``` text
new optimiser
   ↓
strategy search
   ↓
robustness
   ↓
risk
   ↓
Bollinger if enabled
   ↓
Top 10 frozen
   ↓
UNSEEN confirmation
   ↓
new winner frozen
```

Only then compare:

``` text
Candidate #158
      VS
new winner
```

using:

``` text
same current BacktestEngine
same symbol
same timeframe
same exact dates
same starting balance
same fees/slippage
same execution assumptions
```

Compare Return, Net PnL, gross profit/loss, PF, Sharpe, Max DD, trades,
win rate and fees.

That answers the real question:

> Did the new auto-optimiser independently discover something better
> than Candidate #158?

------------------------------------------------------------------------

# 24. LONG / SHORT semantics

V1 uses one shared strategy parameter set.

``` text
LONG=true, SHORT=false
→ LONG only
```

``` text
LONG=false, SHORT=true
→ SHORT only
```

``` text
LONG=true, SHORT=true
→ both active
→ one shared parameter set
→ one BacktestEngine simulation
→ one combined score
```

V1 does not invent separate `long_ema`, `short_ema`, etc.

Direction itself should not be a random Optuna dimension.

------------------------------------------------------------------------

# 25. Clean-cache behaviour

A hard reset may leave no market-data cache.

Expected behaviour:

``` text
empty cache
    ↓
Stage 1 resolves history
    ↓
downloads history + warmup
    ↓
drops forming candle
    ↓
prepares deterministic dataset
    ↓
Phase A starts
```

Measured 30-trial smoke example:

``` text
data prep/download      8.1 sec
30 trials              13.9 sec
per trial             ~0.465 sec

estimated 150         ~70 sec
estimated 750         ~5.8 min
```

Those numbers apply only to that workload. Longer history means slower
trials.

------------------------------------------------------------------------

# 26. Persistence / resume

Phase-A artifacts should preserve enough state that later stages do not
rerun the whole search:

``` text
run ID
preset snapshot
data checksum
partition boundaries
warmup details
seed
sampler
trial budget
score version
search-space definition
all completed trial parameters
TRAIN metrics
shortlist
VALIDATION metrics
runtime
persistent study database
```

The optimizer is implemented end to end. All five canonical V3 stages run:

    1a broad strategy       400 trials    11 dims, neutral risk
    1b narrowed strategy    800 trials    11 dims, ranges derived from 1a
    1c risk-only            200 trials    3 dims, strategy frozen
    2a final joint          300 trials    14 dims, seed enqueued as trial 0
    2b Bollinger            150 trials    6 dims, strategy + risk frozen

(the allocation shown is for the canonical 1,850 total; any other total is scaled
deterministically and printed in the run plan before anything runs).

After 2b the winner is frozen, UNSEEN is opened exactly once for confirmation, and
the requested config is written to `configs/config/`. A required stage that produces
no result ends the run with no config written.

------------------------------------------------------------------------

# 27. Quick command map

## Backtest

``` bash
./pipeline.sh --config <config>.json --backtest
```

## Historical replay

Use `--historical-replay` with the exact current arguments shown by:

``` bash
./pipeline.sh --help
```

## Forward paper test

``` bash
./pipeline.sh --config <config>.json --forward-test
```

## Auto-optimiser

``` bash
./pipeline.sh --optimize --odefault.json --mywinner.json
```

## 5,000 Phase-A trials

In `odefault.json` or another optimiser preset:

``` json
"trials": 5000
```

## Automatic Phase-A budget

``` json
"trials": "auto"
```

Current provisional 15m value:

``` text
750 trials
```

------------------------------------------------------------------------

# 28. Things not to do

1.  Do not tune against UNSEEN.
2.  Do not mix leverage/risk search into Phase A.
3.  Do not select only by raw return.
4.  Do not select only by PF.
5.  Do not select only by lowest DD.
6.  Do not automatically promote Phase-A rank #1.
7.  Do not let filters create trades.
8.  Do not duplicate strategy logic in replay/forward engines.
9.  Do not let the dashboard drive trading.
10. Do not tune the new optimiser around Candidate #158.

------------------------------------------------------------------------

# 29. Current checkpoint

``` text
DONE
✓ pipeline CLI repaired/conflict protected
✓ backtest path working
✓ forward-test path tested
✓ Candidate #158 preserved
✓ Bollinger preserved
✓ rejected MTF/KEMAD experiments removed
✓ auto-optimiser shell
✓ Stage 1 data preparation
✓ warmup-before-slice protection
✓ chronological UNSEEN-first split: 20% sealed UNSEEN, then V3's 70/30 within
  DEV — effective full-history TRAIN 56% / VALID 24% / UNSEEN 20%
✓ structural UNSEEN lock
✓ clean-cache bootstrap
✓ Phase-A real BacktestEngine trials
✓ persistent/resumable study
✓ V1 direction semantics
✓ terminal progress dashboard
✓ 30-trial smoke campaign

NEXT
→ audit Phase-A score
→ audit minimum-trade gate
→ controlled ranking sanity test
→ full Phase-A campaign
→ inspect TRAIN + VALIDATION finalists

LATER
○ Stage 3 robustness
○ Stage 4 risk B1/B2
○ Stage 5 Bollinger optimisation
○ Stage 6 Top 10 / UNSEEN
○ Candidate #158 final showdown
```

------------------------------------------------------------------------

# 30. Full roadmap

``` text
                    OPTIMISER PRESET
                          │
                          ▼
                [1/6] DATA PREPARATION
                history + warmup
                TRAIN / VALID / UNSEEN🔒
                          │
                          ▼
                [2/6] STRATEGY SEARCH
                11 dimensions
                neutral risk
                Optuna/TPE on TRAIN
                          │
                          ▼
                shortlist → VALIDATION
                          │
                          ▼
                [3/6] ROBUSTNESS
                perturbations + regimes
                          │
                          ▼
                [4/6] RISK
                B1 broad → B2 refine
                          │
                          ▼
                [5/6] BOLLINGER
                optional OFF vs ON
                          │
                          ▼
                FINAL TOP 10
                TRAIN + VALID only
                          │
                    FREEZE ORDER
                          │
                          ▼
                [6/6] UNSEEN 🔓 ONCE
                confirmation only
                          │
                          ▼
                   WINNER CONFIG
                          │
                          ▼
              #158 vs NEW WINNER
              same dates / engine
```

------------------------------------------------------------------------

# 31. Five things to remember

1.  **BacktestEngine is the engine; the optimiser is a search layer on
    top.**
2.  **TRAIN teaches, VALIDATION checks, UNSEEN is the final exam.**
3.  **Strategy edge first, risk sizing second, filters third.**
4.  **Top score is not automatically the best candidate; robustness
    matters.**
5.  **Candidate #158 is the benchmark to beat independently, not the
    answer fed into the new optimiser.**

------------------------------------------------------------------------

# 32. Maintaining this guide

Update this file whenever these change:

``` text
pipeline syntax
config folder layout
active production configs
strategy search dimensions
auto trial policy
partition policy
optimizer stages
risk-stage design
filter-stage design
UNSEEN policy
winner-selection policy
Candidate #158 benchmark status
```

This file should always answer:

> **"I opened the project after three months away. What does everything
> do, what do I run, and where are we now?"**
