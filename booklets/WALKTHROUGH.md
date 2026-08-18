# Project walkthrough

Rule-based **ETHUSDT perpetual futures, 15m, long-only** research pipeline.
Backtest, historical replay, and paper-forward all share one strategy, one risk manager
and one execution engine, so results reconcile trade-for-trade.

---

## 1. Directory structure

```
configs/
  config/       runnable strategy presets (JSON) + optimizer output
    default.json                    baseline preset, uses the shared risk policy
    config1-ETHUSDTP15m-long.json   FROZEN Candidate #158, filters OFF
    config2-ETHUSDTP15m-long.json   same Candidate #158 values, Bollinger ON
  optimize/     optimizer input presets
    odefault.json                   human-facing optimizer inputs
pine/
  config1/2-ETHUSDTP15m-long.pine     TradingView ports (both generated from tools/generate_pine.py;
                                      config1 is PROTECTED and never regenerated)
src/
  main.py                         CLI entrypoint, config loading, stage routing
  common/       config.py (dataclasses), market_data.py (fetch+cache), accounting.py, utils.py
  strategy/     baseline_strategy.py (signals), indicators.py (EMA/RSI/ATR/consolidation/swing/volume)
  risk_management/  baseline.py (sizing), riskmanager.json (ACTIVE policy), backup/ (reference)
  backtest/     engine.py, metrics.py, reports.py, robustness.py
  forward_test/ paper_engine.py, replay_engine.py, feed.py, state.py, dashboard.py
  filters/      stage_1_bollinger/filter.py   Bollinger chop gate (the only signal filter)
                masked_strategy.py            MaskedStrategy — generic precomputed-mask gate
  optimization/ multi_tf_optimizer.py, deep_15m_optimizer.py, fetch_data.py, backup/
                (legacy, AI-operated; left untouched)
  auto_optimise/ human-operable optimizer — cli.py, preset.py, history.py,
                output_guard.py, trials.py, runplan.py, ui.py
tests/          unit / integration / parity / regression
tools/          generate_pine.py
data/           cached OHLCV CSV (regenerated on demand)
results/, logs/ generated output
booklets/       this documentation set
```

---

## 2. `pipeline.sh` usage

An explicit action flag is always required; a bare invocation errors with usage and exit 1.
`./pipeline.sh --help` (or `-h`) prints the full usage and exits **0**.

All usage text comes from a single `usage()` function, so the help output, the unknown-flag
error and the no-action error can never drift apart. Unknown flags get a "did you mean"
suggestion when they prefix-match a known flag (`--backtes` → `--backtest`), and
`--config --foo.json` explains that the value must not start with `-` and suggests
`--config foo.json`.

If `--config` is omitted the script falls back to `configs/default.json` and prints an
explicit `NOTE:` saying so, rather than using it silently.

### Execution stages

Every run takes `--config <config-file>` plus one action:

```bash
./pipeline.sh --config config1-ETHUSDTP15m-long.json --backtest
./pipeline.sh --config config2-ETHUSDTP15m-long.json --backtest
./pipeline.sh --config config2-ETHUSDTP15m-long.json --historical-replay
./pipeline.sh --config default.json --forward-test
./pipeline.sh --config default.json --robustness
```

**Config resolution** — the argument is tried in this order, first hit wins:
`<arg>` → `<arg>.json` → `configs/config/<arg>` → `configs/config/<arg>.json`.
So all of these are equivalent:

```bash
./pipeline.sh --config config1-ETHUSDTP15m-long.json             --backtest
./pipeline.sh --config configs/config/config1-ETHUSDTP15m-long.json --backtest
./pipeline.sh --config config1-ETHUSDTP15m-long                  --backtest
```

The old flat `configs/<name>.json` location is gone; it no longer resolves.

`--config=<file>` also works. There are no `--config1`/`--config2` aliases and no
`--default` shortcut — pass the filename.

**Validation** — before anything runs, `pipeline.sh` checks the file exists, parses as
JSON, and contains the required schema (`symbol`, `timeframe`, `strategy`, `risk`,
`execution` plus their required keys). Any failure prints a specific error and exits 1.

### Maintenance actions (terminal — they run and exit 0)
```bash
./pipeline.sh --clear-cache          # delete market-data cache only
./pipeline.sh --reset                # delete current runtime results/logs only
./pipeline.sh --reset --clear-cache  # both
./pipeline.sh --hard-reset           # delete ALL generated results/logs/cache (cannot combine with a stage)
```

Maintenance flags may also precede a stage, in which case maintenance runs first:
```bash
./pipeline.sh --config default.json --backtest --reset --clear-cache
```

Other flags: `--resume`, `--clear-cache-only`. `--historical-replay` selects
`FORWARD_MODE=HISTORICAL_REPLAY` for you.

---

## 3. `default.json` vs the frozen config

| | `config/default.json` | `config/config1-ETHUSDTP15m-long.json` |
|---|---|---|
| Purpose | baseline / regression reference | frozen Candidate #158 |
| Risk source | `src/risk_management/riskmanager.json` (authoritative) | **its own `risk` block** (`"_risk_policy": "preset"`) |
| Leverage / risk / allocation | 1.0x / 1.5% / 50% | 4.0x / 2.6% / 70% |
| Strategy | EMA 51, RR 1.5, short off | EMA 104 / RSI 20 / OB 64 / OS 23 / ATR 7 / cons 7@2.8 / swing 17 / volSMA 12@1.8 / RR 3.6 |
| Bollinger | disabled | values stored, `enabled: false` |

A preset containing `"_risk_policy": "preset"` bypasses the shared risk policy entirely.
Without that marker, `riskmanager.json` overrides the preset's `risk` block field-by-field.

---

## 4. Backtest flow

```
pipeline.sh → src/main.py
  resolve + validate --config path, load JSON
  resolve risk policy (preset-owned vs riskmanager.json)
  MarketDataLoader.load_ohlcv()      fetch or reuse cache, validate coverage
  compute_all_indicators()            on the FULL cached frame (warmup included)
  slice_evaluation_window()           clamp to start_date..end_date
  BacktestEngine.run()                bar-by-bar; BaselineStrategy → BaselineRiskManager
  BacktestMetrics + BacktestExporter → results/backtest/{trades,equity_curve}.csv
```

Warmup candles before `start_date` seed indicators only — they can never produce a
trade, PnL or drawdown.

---

## 5. Historical replay flow

`HistoricalReplayEngine` subclasses `PaperForwardEngine` but is fed the same
indicator-attached, window-sliced frame the backtest uses, **and the same filtered strategy
object** (`main.py` puts the replay engine through the same `_apply_filters(...)`), so the
two cannot drift. It replays candle-by-candle (open tick → intrabar ticks → close tick →
candle-close callback), writing to `<results_dir>/replay/`. Its state and file log live in
`<logs_dir>/replay_state.json` and `<logs_dir>/replay_debug.log`, isolated from live paper
state — the replay repoints the `PaperEngine` file handler at construction, because
`paper_engine` binds its log path at import time before any config exists.

**Signals are resolved once over the whole frame.** `WholeFrameSignals` runs the real
strategy — `MaskedStrategy` with a Bollinger mask included — exactly as the backtest does,
then serves back the signal for each closed candle. The per-bar rolling slice the replay
loop uses is far too short to carry a frame-wide filter mask, so anything resolved per slice
would ignore filters entirely.

Replay also writes a **per-bar equity curve** to `<results_dir>/replay/equity_curve.csv`,
using the same mark-to-market helper as the live snapshot.

Backtest and replay agree trade-for-trade on signal/entry/exit timestamps, prices, quantity,
SL/TP, fees, gross and net PnL, and on the equity curve row for row. The backtest reports one
extra trade: it force-closes an open position at the final candle with
`exit_reason = END_OF_DATA`; replay correctly leaves it open (its entry fields still match).
Verified on config2 (Bollinger ON) against fresh Binance 15m candles: 16 common closed
trades, all fields exact.

---

## 6. Forward / paper flow

`PaperForwardEngine` warms up from REST, then consumes a live websocket feed,
evaluating SL/TP on every tick and strategy signals on candle close. State is
checkpointed atomically to `logs/forward_state.json` (resume by default, `--reset`
archives the previous experiment). A Rich terminal dashboard renders account,
position, performance and feed health.

---

## 7. Market data / cache flow

`MarketDataLoader` fetches Binance USD-M futures klines and caches to
`data/candles_futures_<platform>_<symbol>_<resolution>.csv`. On reuse it validates that
the cached span covers the requested range and re-fetches when it does not. The cache
may be **wider** than the evaluation window; the evaluation window is never wider than
requested.

**Optimizer data preparation validates the cache twice.** For the bar-count-driven history
modes (`auto`, `candles`), `auto_optimise/dataprep._load_raw` requires both:

1. **depth** — at least `warmup + evaluable` bars (AUTO: 1,000 + 43,200 = 44,200);
2. **recency** — the last cached candle no more than one bar behind the newest candle the
   exchange can have closed.

Too shallow → re-fetch with an explicit start derived from the requirement (not the
loader's generic one-year fallback, which is short of 43,200 bars on every timeframe of 1h
and above). Stale but deep → fetch only the missing tail and union it onto the cache, so a
long history (the 15m file feeds other stages' warmup) is extended, never truncated.
Either way `auto` reports "availability-limited" only when Binance genuinely has no more
history. `days` and `date_range` are date-driven and keep their previous load behaviour.

---

## 8. Strategy and RiskManager flow

**Signal** (`baseline_strategy.py`) — long entry requires an EMA cross-up, RSI validity,
recent-or-current consolidation, and a volume breakout. SL is `min(swing_low, low)`,
widened to `close − 0.85×ATR` if closer than `0.4×ATR`. Optional `use_trend_filter`
and `use_ema_slope_filter` exist in code but are **not** read from preset JSON.

**Sizing** (`risk_management/baseline.py`) —
```
risk budget   = equity × risk_per_trade_pct        (gross price risk; fees excluded)
max_margin    = equity × max_position_allocation_pct
max_notional  = max_margin × leverage
qty           = floor_to_step(min(risk_qty, alloc_qty), quantity_step)
```
Invalid/inverted stops are **rejected**, never substituted. SL/TP round to `tick_size`.
Margin is validated against equity and the allocation cap.

**Execution** — entry fills at the next candle's open ± `slippage_ticks × tick_size`;
SL takes priority when both SL and TP are touched in the same bar; taker fee 0.05%.

---

## 9. Filter flow

Filters are **signal gates**: they subclass `BaselineStrategy`, call it unchanged, and only
*remove* signals. They can never create a trade or alter prices, SL/TP, sizing or fees.

**Stage 1 — Bollinger** (`src/filters/stage_1_bollinger/filter.py`) is the **only** signal
filter in the project. It blocks on minimum bandwidth, band-expansion ratio, and middle-band
distance. Each condition is disabled when its threshold is `0`.

**Warmup.** Filter masks are computed on the **full** indicator frame and then sliced with
the same evaluation-window mask as the bars. Computing a filter on the sliced frame would
restart its rolling windows at the window edge and silently change results.

**Wiring.** `main.py` reads `filters.bollinger` from the preset, computes the allow mask on
the full frame, slices it, and injects `MaskedStrategy` (`src/filters/masked_strategy.py`)
via `engine.strategy` — into **both** the backtest engine and the historical replay engine.
`MaskedStrategy` is filter-agnostic: it applies any precomputed boolean mask, so a future
filter reuses it without depending on another filter's package. With Bollinger disabled no
mask is built at all and the run is byte-identical to the unfiltered baseline.

Historical replay previously received no mask and silently ran the unfiltered strategy —
a filtered config could not be replay-validated. It now applies the identical mask; see §5.

## 10. Optimizer folders

`src/optimization/` holds `multi_tf_optimizer.py` (Optuna TPE, seeded, SQLite-resumable)
and `deep_15m_optimizer.py`. `src/optimization/backup/` preserves the legacy
Candidate #5-era optimizer verbatim for reference — it sampled leverage/risk/allocation
and ranked with a dollar-weighted score, neither of which reflects current practice.

---

## 11. Pine relationship

`tools/generate_pine.py` emits both Pine ports from their configs, injecting every
parameter. Config1 and Config2 come from one shared `TEMPLATE`; the Bollinger toggle is
injected from `filters.bollinger.enabled`, which is the only thing that differs between
them. The Pine mirrors the corrected sizing (margin-based allocation cap, plain-equity risk
budget, quantity-step flooring, tick rounding) and the pending-order handshake (submit on
the signal bar; compute SL/TP from the realized fill on the entry bar).

`config1-ETHUSDTP15m-long.pine` is listed in `PROTECTED` and is never overwritten — it is
the frozen baseline. Config2's Pine is regenerated freely, and regeneration is idempotent.

The Pine also carries an **ADX toggle** that has no Python counterpart. It defaults to
`false` and is inert; it exists only as a chart-side experiment hook and is not part of the
strategy. Do not treat it as production functionality.

Known unavoidable Pine limitation: quantity is sized from the signal bar's `close`
because Pine cannot see the next bar's open before submitting. Timing, prices, SL and TP
match Python; quantity differs marginally.

---

## 12. Results and logs

```
results/backtest/   trades.csv, equity_curve.csv
results/replay/     trades.csv, events.csv, equity_curve.csv, tracker.csv
results/forward/    trades.csv, events.csv, equity_curve.csv, archive/
logs/               forward_state.json, replay_state.json, run logs
```

---

## 13. Reset semantics

| Command | Deletes | Preserves |
|---|---|---|
| `--clear-cache` | market-data cache for the active symbol | results, logs, configs, source |
| `--reset` | current runtime results/logs (explicit allowlist) | cache, archives, optimization output, configs, source |
| `--hard-reset` | all generated results/logs/cache/archives | configs, source, tests, README, pipeline.sh, `.git` |

`--hard-reset` cannot be combined with an execution stage.

> Caveat: `--reset` deletes `results/forward/*.csv` and `logs/forward_state.json`
> **before** the forward engine's archive step runs, so `--forward-test --reset`
> discards rather than archives the previous experiment.

---

## 14. Current frozen candidate

**Candidate #158**, ETHUSDT.P 15m, long-only. Config1 = Bollinger OFF;
Config2 = the same values with Bollinger ON. There is no Config3.
EMA 104 / RSI 20 / OB 64 / OS 23 / ATR 7 / consolidation 7 @ 2.8 ATR / swing 17 /
volume SMA 12 @ 1.8x / RR 3.6 — leverage 4.0x, risk 2.6%, allocation 70%,
quantity step 0.001, tick size 0.01, commission 0.05%, slippage 1 tick.

Reference (current engine, 2024-01-01 → 2026-08-15): **+274.67%**, 262 trades.
2026 YTD (2026-01-01 → 2026-08-15): **+24.69%**, PF 1.205, DD 29.68%, 64 trades.


---

## 15. Auto-optimizer (`src/auto_optimise/`)

Human-operable optimizer. Orchestration only — every trial runs the production
`BacktestEngine` / `BaselineStrategy` / `BaselineRiskManager` unchanged.

```bash
./pipeline.sh --optimize --odefault.json --mywinner.json
```

This is the only optimizer syntax: preset first, output second, both as
`--<name>.json`.

`<preset>` resolves under `configs/optimize/`; `<output>` is created in
`configs/config/`. The output name is **mandatory**, is never auto-generated, and
an existing strategy config is **never** overwritten — existing configs are
recorded experiments. Names must be plain `.json` file names: no subdirectories,
no `..`, no absolute paths.

`--optimize` is mutually exclusive with `--backtest`, `--forward-test`,
`--historical-replay`, `--robustness`, `--config` and every maintenance flag, in
either order on the command line.

### Preset (`configs/optimize/odefault.json`)

Only human-facing inputs. Search ranges, objective weights, Optuna internals,
warmup, partition ratios, seeds and storage paths stay automatic.

| Field | Meaning |
|---|---|
| `_schema_version` | must be `3` |
| `platform`, `symbol`, `timeframe` | what to optimize on (`1m…4h`) |
| `history.mode` | `auto` \| `days` \| `date_range` \| `candles`; only that mode's fields may be non-null |
| `initial_balance` | starting equity |
| `execution.tick_size` *(optional)* | `"auto"` (exchange `PRICE_FILTER`) or a positive multiple of it |
| `partition.unseen_pct` **or** `partition.unseen_start` *(optional)* | UNSEEN share (5–40, default 20) or an exact boundary date; mutually exclusive |
| `direction.long_enabled` / `short_enabled` | at least one must be true |
| `trials` | `"auto"` or a whole number |
| `optimization_mode` | `balanced` / `conservative` / `aggressive` |
| `stages.{strategy_optimization,risk_management,bollinger}` | per-stage on/off |

**Direction semantics.** `StrategyConfig` holds one shared parameter set, so both
directions use the same indicator values. With LONG and SHORT both enabled the
optimizer runs **one mixed campaign** — one backtest per trial with both sides
active and one combined score, not two separate campaigns. Independent per-side
parameters would need a new `DualSideStrategy`; that is deliberately not faked.

**Trials.** `"auto"` resolves by timeframe and sizes the **Phase-A strategy search
only**; the risk and Bollinger stages derive their own smaller budgets. The
mapping and its rationale live in `src/auto_optimise/trials.py` and is currently
provisional pending a timed measurement.

**History `auto`.** Targets 43,200 evaluable bars plus 1,000 warmup, resolved against real
availability during data preparation (§7). It reports "availability-limited" only when the
exchange genuinely has less history — a short or stale cache triggers a fetch instead.

**Partitions.** UNSEEN is carved off the end first, then canonical V3 splits the remaining
DEV 70/30 — so the default effective split is **TRAIN 56% / VALID 24% / UNSEEN 20%**.
`partition.unseen_pct` changes the reservation; `partition.unseen_start` pins the boundary
to an exact date for reproducing a historical campaign, and the emitted config records which
of the two set the boundary.

**Market rules.** Tick size and quantity step are resolved from the exchange per run and
applied to every trial, so a campaign is not restricted to symbols hardcoded in
`optimization/v3/spec.py`.

### Campaign stages

```
[1/6] Data preparation
[2/6] Strategy optimization           (neutral risk: leverage 1.0, fixed risk/allocation)
[3/6] Strategy robustness / validation
[4/6] Risk optimization               (leverage, risk_per_trade_pct, max_position_allocation_pct)
[5/6] Bollinger optimization          (length, std, min_bandwidth_pct,
                                       expansion_lookback, expansion_min_ratio, min_mid_distance)
[6/6] Final Top-10 + UNSEEN confirmation
```

Disabled stages print as `SKIPPED` and are excluded from ETA estimation.
Partitioning is policy, not a search input: UNSEEN is reserved off the end **first**
(default 20%, or `partition.unseen_pct` / `partition.unseen_start`), then canonical V3
splits the remaining DEV 70/30 **by row count** — effective **TRAIN 56 / VALID 24 /
UNSEEN 20** — plus a separate warmup block before TRAIN. Ranking uses TRAIN +
VALIDATION only; UNSEEN unlocks once after the Top-10 is frozen and never
reorders it.

### Stage [1/6] — Data preparation (implemented)

```
resolve history -> load/download -> compute indicators on the FULL warmup+window
frame -> slice the evaluation window -> reserve the final 20% as sealed UNSEEN ->
split the remaining DEV 70/30 -> expose TRAIN + VALIDATION,
seal UNSEEN
```

Indicators are computed **before** any slice is taken, so no rolling window ever
restarts at a partition boundary. The warmup lead-in is 1000 candles
(`src/auto_optimise/lookback.py`: largest supported lookback 200 x 5 safety, floor
500) and is trimmed to exactly that size so results do not depend on how wide the
data cache happens to be. The still-forming candle is dropped — its close and
volume mutate on every fetch. Warmup candles belong to no partition and can never
trade.

Assertions run every time and raise rather than warn: partition counts must sum to
the evaluation window, partitions must not overlap, concatenating them must
reproduce the window exactly (no gaps), and the first TRAIN and VALIDATION candles
must carry real indicator history — a restarted EMA equals its first close, so that
equality is treated as a failure.

`UnseenVault` (`src/auto_optimise/unseen.py`) is a structural barrier, not a
convention: the frame lives in a closure with no attribute holding it, the object
has `__slots__` and no `__dict__`, and pickling, copying and iteration all raise.
`get()` raises `UnseenLockedError` until a final-selection stage calls
`unlock(reason)`, which is one-way and recorded. Row count and date bounds stay
readable while locked so the run plan can report them.

### The five V3 stages (implemented)

The optimizer does not contain its own search mathematics. It imports the canonical
package `src/optimization/v3/` and drives it; every range, gate, score weight,
sampler and selection rule lives there. If a number in `auto_optimise` ever
disagrees with V3, V3 wins.

Optuna/TPE (seed 42, `n_jobs=1`) proposes parameters; each trial is simulated by the
**production** `BacktestEngine` driving the production `BaselineStrategy` and
`BaselineRiskManager`. There is no research backtester.

| stage | dims | risk policy | notes |
|---|---|---|---|
| 1a broad strategy | 11 | neutral 1.0x / 1.5% / 50% | sizing-neutral, so no candidate out-ranks another by sizing harder |
| 1b narrowed strategy | 11 | neutral | ranges derived from the top gated 1a trials, widened one step, clipped to the 1a bounds |
| 1c risk-only | 3 | searched | strategy frozen; leverage / risk / allocation only |
| 2a final joint | 14 | searched | the discovered seed is enqueued as trial 0, then the space is re-opened jointly |
| 2b Bollinger | 6 | frozen | strategy and risk frozen; ships the filter OFF if nothing clears the gate |

Stage 1 emits exactly one 14-dimension seed. Stage 2a may or may not keep it — the
seed competes as trial 0 and is beaten on its merits or not at all.

**Trial budget.** The human writes one TOTAL. `1850` allocates exactly
400 / 800 / 200 / 300 / 150. Any other integer scales that reference allocation
deterministically (largest-remainder, then documented per-stage floors), and always
sums to the requested total exactly. `"auto"` resolves a documented total from the
timeframe and history length. The five resolved budgets are printed in the run plan
before anything runs. Per-stage budgets are never preset fields.

**Stage toggles.** `strategy_optimization` is required — a run cannot start without
it, because 1a/1b produce the seed the later stages search around. `risk_management`
controls 1c and the risk half of 2a; when it is off the seed keeps the neutral risk
policy and the emitted config records `_risk_optimized: false`, so a skipped risk
stage is never described as risk-optimized. `bollinger` controls 2b only. Every
skipped stage appears as SKIPPED in the run plan, the manifest, the config metadata
and the terminal result.

**Market rules.** `execution.tick_size` is `"auto"` by default and resolves once,
during data preparation, from Binance Futures `PRICE_FILTER.tickSize` for the
symbol. A numeric override is validated against the same metadata and rejected
unless it is a positive whole multiple of the exchange tick. The quantity step comes
from `LOT_SIZE.stepSize` and is not a preset field. Both resolved values are written
into the manifest and the emitted config. Tick size is a property of the symbol and
is never derived from the timeframe; there is no per-symbol map in this repo.

**TRAIN / VALID / UNSEEN discipline.** UNSEEN is carved off the end of the window,
locked in the vault, and **physically removed** from the frame handed to V3 — the
DEV frame is sliced at the UNSEEN boundary before `Campaign` is constructed, so no
stage can address those rows even by index. V3's own data contract asserts the same
thing and has no unlock path. The vault is asserted locked before and after the
search.

UNSEEN is opened exactly once, by `v3_confirm.confirm`, after the winner and the
Bollinger filter are frozen. Its metrics are recorded as `CONFIRMATION_ONLY` and
there is no path from that result back into any search, narrowing, seed, risk or
Bollinger stage. If UNSEEN disappoints, the honest outcome is a config whose
manifest says so — not a retune.

**Partition policy (canonical).**

    full requested history
      -> reserve the final 20% chronologically as sealed UNSEEN
      -> the remaining 80% is DEV
      -> V3's existing 70/30 split applies WITHIN DEV

    Effective full-history split:  TRAIN 56% / VALID 24% / UNSEEN 20%

UNSEEN is reserved **first**, before DEV is divided at all, and is physically removed
from the frame V3 receives. It stays inaccessible until the single final
confirmation, after the winner is frozen.

Generic V3 runs are never described with a single three-way ratio taken from an
older policy. Both views are always displayed —
the DEV-local 70/30 and the effective full-history 56/24/20 — because quoting only
one of them is what makes partition documentation misleading. The DEV-local ratio is
read from `optimization.v3.spec.TRAIN_FRAC`, never hardcoded, so this layer cannot
drift from the search that actually runs, and the split is applied BY ROW COUNT
exactly as `Campaign.__init__` applies it.

`partition.unseen_start` remains available as an **explicit reproduction override
only** — it pins the UNSEEN boundary to an exact date so a historical campaign such
as Phase-16 can be reproduced. It is not a tuning knob, and the emitted config
records which of the two sources set the boundary.

Actual partition dates, row counts and both ratio views are written into every
manifest and every emitted config.

**Direction.** One shared strategy/risk parameter vector, always. With
`long_enabled` and `short_enabled` both true, each trial runs a SINGLE
`BacktestEngine` simulation with both directions enabled and the combined result is
scored — one study, one winner, no side-specific parameters and no parallel
campaigns. With one side enabled, only that side is evaluated. The emitted config
preserves the preset's direction flags exactly.

**Artifacts** land in `results/auto_optimise/<run_id>/`: `v3_manifest.json` (preset
snapshot, environment including NumPy and Optuna versions, market-rule snapshot,
data checksum, partition boundaries, total and per-stage budgets), the five trial
ledgers `v3_stage1a_broad.csv` … `v3_stage2b_bollinger.csv`, `v3_seed.json`,
`v3_unseen_confirmation.json` and `v3_final_config.json`.

**The output config** is written only after every enabled stage succeeded. It carries
the frozen strategy / risk / execution / Bollinger values, the resolved tick size and
quantity step, the direction from the preset, the source preset and run metadata, the
total and per-stage budgets, the exact warmup and partition dates and row counts, the
pre-UNSEEN winner, and the UNSEEN result clearly marked as confirmation only. It is
runnable by the ordinary backtest and forward-test commands.

`--plan-only` validates and prints the run plan — budgets, market-rule resolution
policy and partition policy — without a network call, an Optuna study, a data load or
any file write.

### Current status

Implemented end to end. `./pipeline.sh --optimize --<preset>.json --<output>.json`
runs all five V3 stages, opens UNSEEN once for confirmation, and writes the requested
config.
