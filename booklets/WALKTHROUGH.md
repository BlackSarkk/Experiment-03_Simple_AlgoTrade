# Project walkthrough

Rule-based **ETHUSDT perpetual futures, 15m, long-only** research pipeline.
Backtest, historical replay, and paper-forward all share one strategy, one risk manager
and one execution engine, so results reconcile trade-for-trade.

---

## 1. Directory structure

```
configs/          runnable presets (JSON)
  default.json                    baseline preset, uses the shared risk policy
  config1-ETHUSDTP15m-long.json   FROZEN Candidate #158, filters OFF
  config2-ETHUSDTP15m-long.json   same Candidate #158 values, Bollinger ON
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
`<arg>` → `<arg>.json` → `configs/<arg>` → `configs/<arg>.json`.
So all of these are equivalent:

```bash
./pipeline.sh --config config1-ETHUSDTP15m-long.json      --backtest
./pipeline.sh --config configs/config1-ETHUSDTP15m-long.json --backtest
./pipeline.sh --config config1-ETHUSDTP15m-long           --backtest
```

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

| | `default.json` | `config1-ETHUSDTP15m-long.json` |
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
indicator-attached, window-sliced frame the backtest uses, so the two cannot drift.
It replays candle-by-candle (open tick → intrabar ticks → close tick → candle-close
callback), writing to `results/replay/`. Its state lives in `logs/replay_state.json`,
fully isolated from live paper state.

Backtest and replay agree trade-for-trade on entry/exit timestamps, prices, quantity,
SL/TP, fees and PnL. The backtest reports one extra trade: it force-closes an open
position at the final candle with `exit_reason = END_OF_DATA`; replay leaves it open.

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
via `engine.strategy`. `MaskedStrategy` is filter-agnostic: it applies any precomputed
boolean mask, so a future filter reuses it without depending on another filter's package.
With Bollinger disabled no mask is built at all and the run is byte-identical to the
unfiltered baseline.

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
