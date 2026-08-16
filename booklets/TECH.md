# TECH.md — operations guide for an AI coding agent

Authoritative technical reference. Read fully before modifying anything.
No secrets, credentials or machine-specific data are recorded here.

---

## 1. Project invariants

| Invariant | Value |
|---|---|
| Symbol | `ETHUSDT` (Binance USD-M perpetual; TradingView `BINANCE:ETHUSDT.P`) |
| Timeframe | `15m` |
| Active config | `configs/config1-ETHUSDTP15m-long.json` |
| Active Pine | `pine/config1-ETHUSDTP15m-long.pine` |
| Direction | `long_enabled: true`, `short_enabled: false` |
| Active risk policy | `src/risk_management/riskmanager.json` |
| RiskManager code | `src/risk_management/baseline.py` (`BaselineRiskManager`) |
| Initial capital | 10,000 |
| Commission | 0.05% taker, charged on entry and exit notional |
| Slippage | `slippage_ticks × tick_size`, always adverse |
| Tick size | 0.01 · **Quantity step** 0.001 (floor, never round up) |

**Execution semantics (do not change casually):**
- Entry fills at the **next** candle's open ± slippage (`use_next_candle_open`).
- SL/TP are first evaluated on the bar **after** the entry bar.
- Same-bar SL+TP collision resolves to **SL**.
- Gap handling: LONG SL `min(open, sl)`, LONG TP `max(open, tp)`; mirrored for SHORT.
- Open position at the final candle is force-closed as `END_OF_DATA` (backtest only).

**Sizing formulas (`BaselineRiskManager.calculate_position`):**
```
risk_budget  = equity × risk_per_trade_pct          # gross price risk, fees excluded
raw_size     = risk_budget / |entry − sl|
max_margin   = equity × max_position_allocation_pct
max_notional = max_margin × leverage
qty          = floor_to_step(min(raw_size, max_notional/entry), quantity_step)
reject if: equity<=0, entry<=0, inverted/zero stop, qty<min_position_size,
           margin_required > equity, margin_required > max_margin
```
Inverted stops are **rejected**, never replaced. SL/TP rounded to `tick_size`.

**Date slicing / warmup:** indicators are computed on the **full** cached frame, then
`slice_evaluation_window()` clamps rows to `start_date..end_date` (bare `end_date` =
inclusive end-of-day). Warmup candles seed indicators and can never trade.

---

## 2. Call chains

```
pipeline.sh
 ├─ parse flags; --config <file> → CONFIG_ARG
 ├─ resolve CONFIG_ARG → CONFIG_PATH  (<arg> | <arg>.json | configs/<arg> | configs/<arg>.json)
 ├─ validate: file exists → JSON parses → required schema present (else error + exit 1)
 └─ build CMD → .venv/bin/python3 src/main.py --config-preset "$CONFIG_PATH" …

src/main.py:main()
 ├─ resolve + load the config path (same 4-candidate order), re-validate JSON/schema
 ├─ risk policy resolution           (§3)
 ├─ set PlatformConfig start/end     (preset start_date/end_date, else 2024-01-01..2026-08-15)
 └─ run_pipeline()
     ├─ hard_reset / reset / clear_cache  → exit if maintenance_only
     ├─ MarketDataLoader.load_ohlcv()     → data/candles_futures_*.csv
     ├─ compute_all_indicators()          → ema_51, ema_200, rsi, atr, vol_sma_20,
     │                                       is_consolidating, cons_range, swing_high/low
     ├─ slice_evaluation_window()
     └─ stage:
         backtest        → BacktestEngine.run()      → results/backtest/
         robustness      → RobustnessEvaluator
         forward PAPER   → PaperForwardEngine.run_forward_session()
         HISTORICAL_REPLAY → HistoricalReplayEngine.run_replay() → results/replay/

BacktestEngine.run(df)
 ├─ self.strategy.generate_signals(df)        # BaselineStrategy
 └─ per bar: manage open position (SL/TP/END_OF_DATA)
             → open new position via BaselineRiskManager.calculate_position()
             → AccountingEngine.update_account_on_trade_close()
```

Injection point for research: `engine.strategy = <subclass of BaselineStrategy>` after
constructing `BacktestEngine`. This is how the Bollinger gate is applied without editing
engine or strategy source.

---

## 3. Config precedence (verified against `src/main.py`)

1. **Preset-owned risk** — if the preset contains `"_risk_policy": "preset"`, its `risk`
   block is used verbatim and `riskmanager.json` is bypassed entirely.
   (`config1-ETHUSDTP15m-long.json` sets this.)
2. Otherwise **`src/risk_management/riskmanager.json`** overrides the preset's `risk`
   block field-by-field, and overridden keys are printed at startup.
3. Otherwise the preset's own `risk` block.
4. Otherwise `RiskConfig` dataclass defaults (`src/common/config.py`).

Dates: `preset["start_date"] / ["end_date"]`, else `2024-01-01 / 2026-08-15`.
PAPER forward instead uses a rolling window (`days=60`, dates `None`).

**Fields main.py reads:** `symbol, platform, timeframe, strategy.*, risk.*, execution.*,
start_date, end_date, _risk_policy`.
**Fields main.py IGNORES:** `filters.*`, `strategy.use_trend_filter`,
`strategy.trend_ema_period`, `strategy.use_ema_slope_filter`, `bollinger` (legacy key).
Adding such a key to a preset changes nothing until `main.py` is wired.

---

## 4. Engine parity rules

Must remain identical between Backtest and Historical Replay:
entry timestamp, entry price, quantity, SL, TP, exit timestamp, exit price, exit reason,
fees, net PnL. The only permitted difference is the terminal `END_OF_DATA` trade.

Both must receive the **same indicator-attached, window-sliced frame** — `main.py`
passes `df_indicators` to both. Never let replay recompute indicators from a sliced
frame (that silently discards warmup).

Pine parity: identical signal logic, SL rule, RR, sizing formulas, fee/slippage model,
next-bar entry, SL priority.
**Known unavoidable Pine limitation:** quantity is sized from the signal bar's `close`
because Pine cannot observe `open[N+1]` before submitting. Entry timing, entry price,
SL and TP match Python exactly; quantity differs marginally. Also `strategy.equity`
includes open-trade P&L whereas Python sizes off closed balance, and TradingView's feed
differs slightly from Binance REST klines.

---

## 5. Safe modification rules

**May be edited for strategy research** (prefer subclassing over editing):
`src/filters/**`, `tools/generate_pine.py`, `tests/**`, `booklets/**`, new files.

**Do not change casually** (any edit invalidates every recorded result):
`src/strategy/baseline_strategy.py`, `src/strategy/indicators.py`,
`src/risk_management/baseline.py`, `src/backtest/engine.py`,
`src/common/accounting.py`, `src/forward_test/replay_engine.py`,
`configs/config1-ETHUSDTP15m-long.json`, `pine/config1-ETHUSDTP15m-long.pine`.

**Never touch:** `src/risk_management/backup/**`, `src/optimization/backup/**`
(historical reference; never imported by production — verify with
`grep -rn "backup" src/ --include=*.py | grep -v "^src/.*/backup/"`).

**Preserving the baseline:** commit or stash before experimenting; keep experiments in
a separate directory and inject behaviour via `engine.strategy`; never overwrite the
frozen config/Pine — create a new `configN-*` pair instead.

**Adding a filter:** create `src/filters/<stage>/filter.py` exposing a
`BaselineStrategy` subclass that calls `super().generate_signals()` and only *removes*
signals. A filter must never create a signal or alter prices/sizing. Add its parameters
under `filters.<name>` in the preset **and** wire them in `main.py`, otherwise they are
inert.

**Adding a config:** drop a new JSON into `configs/` with the full schema
(`platform, symbol, timeframe, strategy, risk, execution, filters`) and add
`"_risk_policy": "preset"` if it owns its risk block. **No `pipeline.sh` change is
needed** — there are no aliases or filename mappings. Run it with
`./pipeline.sh --config <newfile>.json --backtest`.

**Detecting data leakage / protecting unseen data:** compute indicators on the full
frame but evaluate only the partition; assert an explicit upper row bound in every
optimization call and unlock it only after selection is final; never rank candidates
using the final partition; print partition boundaries before optimizing.

---

## 6. Testing protocol (in order)

```bash
# 1 syntax / import
.venv/bin/python3 -c "import ast;[ast.parse(open(f).read()) for f in __import__('glob').glob('src/**/*.py',recursive=True)]"
PYTHONPATH=src .venv/bin/python3 -c "import main, risk_management.baseline, filters.stage_1_bollinger.filter"
bash -n pipeline.sh

# 2 tiny backtest (short window preset)
./pipeline.sh --config config1-ETHUSDTP15m-long.json --backtest

# 3 baseline reproduction — MUST match before any optimization
#    2024-01-01..2026-08-15 → +274.67%, 262 trades

# 4 backtest ↔ replay parity
./pipeline.sh --config config1-ETHUSDTP15m-long.json --historical-replay
#    compare results/backtest/trades.csv vs results/replay/trades.csv on
#    entry/exit timestamp+price, quantity, sl, tp, exit_reason, fees, net_pnl

# 5 Pine ↔ config parameter parity
#    regex every `input.*(` default out of the .pine and compare to the JSON

# 6 recent TradingView sanity (small window, expect close but not identical)

# 7 unit tests
PYTHONPATH=src .venv/bin/pytest tests/unit -q
```
Note: `tests/unit/test_engine.py::test_leverage_1_vs_3_5_risk_budget` fails at
`149.99 != 150.00` — quantity-step flooring makes realized risk land just under budget.
Pre-existing; the test's tolerance is too tight, not an engine defect.
Running `tests/unit` triggers a 3h market-data download; delete
`data/candles_futures_binance_ETHUSDT_3h.csv` afterwards if unwanted.

---

## 7. Recovery information

**Module responsibilities**
| Module | Responsibility |
|---|---|
| `common/config.py` | `StrategyConfig`, `RiskConfig`, `ExecutionConfig`, `PlatformConfig`, `PipelineConfig` |
| `common/market_data.py` | `MarketDataLoader`: fetch, resample, cache, coverage validation |
| `common/accounting.py` | `AccountState`, `Position`, `AccountingEngine` (balance reconciliation) |
| `strategy/indicators.py` | `compute_all_indicators` |
| `strategy/baseline_strategy.py` | `BaselineStrategy`, `Signal` |
| `risk_management/baseline.py` | `BaselineRiskManager`, `PositionSizingResult`, `floor_to_step`, `round_to_tick` |
| `backtest/engine.py` | `BacktestEngine`, `TradeRecord` |
| `forward_test/replay_engine.py` | `HistoricalReplayEngine` |
| `forward_test/paper_engine.py` | `PaperForwardEngine` |
| `filters/stage_1_bollinger/filter.py` | `BollingerFilterConfig`, `BollingerFilteredStrategy` |

**Preset schema**
```json
{ "_name": "...", "_risk_policy": "preset",
  "platform": "BINANCE_FUTURES", "symbol": "ETHUSDT", "timeframe": "15m",
  "strategy": { "ema_period","rsi_period","rsi_overbought","rsi_oversold","atr_period",
                "consolidation_candles","consolidation_atr_mult","swing_lookback",
                "volume_sma_period","volume_mult","long_enabled","short_enabled",
                "risk_reward_ratio" },
  "risk": { "sizing_mode":"RISK_BASED","initial_capital","leverage",
            "risk_per_trade_pct","max_position_allocation_pct","quantity_step" },
  "execution": { "commission_pct","slippage_ticks","tick_size" },
  "filters": { "bollinger": { "enabled","length","std","min_bandwidth_pct",
                              "expansion_lookback","expansion_min_ratio","min_mid_distance" } } }
```
`risk_per_trade_pct` and `max_position_allocation_pct` are **percent** in JSON and
converted to fractions in `main.py`.

**Active config values (Candidate #158)**
EMA 104 · RSI 20 · OB 64 · OS 23 · ATR 7 · consolidation 7 @ 2.8 · swing 17 ·
volSMA 12 @ 1.8 · RR 3.6 · leverage 4.0 · risk 2.6% · allocation 70% ·
qty step 0.001 · tick 0.01 · commission 0.05% · slippage 1 tick · Bollinger
(10 / 2.3 / 0.2 / 10-0.95 / 0.15) stored with `enabled: false`.

**Result schemas**
`results/backtest/trades.csv` — `trade_id, side, signal_timestamp, entry_timestamp,
entry_price, exit_timestamp, exit_price, quantity, position_notional, margin, leverage,
holding_duration, sl, tp, gross_pnl, fees, slippage, net_pnl, return_pct, r_multiple,
balance_before, balance_after, exit_reason, ema_51, rsi, atr, consolidation_range,
volume, vol_sma_20, swing_high, swing_low`.
`equity_curve.csv` — `bar_idx, timestamp, datetime, balance, equity, open_pnl,
drawdown_pct, in_position, current_price`.
Replay/forward `trades.csv` uses `sl_price`/`tp_price`/`commission` instead of
`sl`/`tp`/`fees` — account for this when diffing.

**CLI behaviour — canonical workflow for agents:**
```bash
./pipeline.sh --config <config-file> <action>
```
`<action>` ∈ `--backtest | --historical-replay | --forward-test | --robustness`.
Always pass `--config` explicitly; never assume a default preset.
Resolution order: `<arg>` → `<arg>.json` → `configs/<arg>` → `configs/<arg>.json`.
Exit codes: bare invocation → usage + 1; unknown flag → 1; `--config` without a value → 1;
missing file → 1 (lists available configs); malformed JSON → 1; schema-invalid → 1
(lists every missing field); `--hard-reset` + stage → 1; maintenance-only actions → 0.

---

## 8. Open defects (verified)

1. `main.py` ignores `filters.*` and `use_trend_filter` / `trend_ema_period`, so the
   Bollinger filter and any regime filter are inert via the CLI.
2. `--reset` deletes forward artifacts **before** `archive_previous_experiment()` runs,
   so `--forward-test --reset` discards instead of archiving.
3. `tests/unit/test_engine.py::test_leverage_1_vs_3_5_risk_budget` tolerance too tight.
