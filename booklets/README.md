# Booklets — project index

Rule-based perpetual-futures trading research pipeline.
Python is the source of truth; a TradingView Pine port mirrors it for chart validation.

Symbol, timeframe and platform come from the active config — the engines, the Pine
exporter, the robustness suite and the optimizer all read them rather than assuming a
market. The frozen reference candidate happens to be **ETHUSDT perpetual, 15m, long-only**,
and every historical result in this project was produced on it, but that is the reference,
not a limitation of the app.

## What each file is for

| File | Audience | Purpose |
|---|---|---|
| `README.md` | human | this index |
| `WALKTHROUGH.md` | human | how the project works and how to run it |
| `TECH.md` | AI coding agent | invariants, call chains, safe-modification and testing protocol, recovery detail |
| `rebuild_project.sh` | ops | dated backup archive with manifests for reconstruction |

## Current frozen reference

| Preset | Pine | What it is |
|---|---|---|
| `configs/config/config1-ETHUSDTP15m-long.json` | `pine/config1-ETHUSDTP15m-long.pine` | **Frozen baseline** — Candidate #158, Bollinger OFF |
| `configs/config/config2-ETHUSDTP15m-long.json` | `pine/config2-ETHUSDTP15m-long.pine` | Same Candidate #158 values, Bollinger ON |
| `configs/config/default.json` | — | shared-risk-policy baseline preset |

There is no Config3. A Multi-Timeframe (2h EMA300) gate was trialled as Config2/Config3 and
**rejected**; its Python filter, configs and Pine block have been removed. See
[§ Rejected experiments](#rejected-experiments).

Active risk policy: `src/risk_management/riskmanager.json`

Frozen candidate: **Candidate #158** — EMA 104 / RSI 20 / OB 64 / OS 23 / ATR 7 /
consolidation 7 @ 2.8 ATR / swing 17 / vol SMA 12 @ 1.8x / RR 3.6,
leverage 4.0x, risk 2.6%, allocation 70%.
Config1 and Config2 share these values exactly; they differ **only** in
`filters.bollinger.enabled`.

UNSEEN reference (2025-12-01 → 2026-08-15, $10,000 start):

| | Return | PF | Max DD | Trades |
|---|---|---|---|---|
| Config1 (Bollinger OFF) | +38.57% | 1.257 | 29.67% | 73 |
| **Config2** (Bollinger ON) | +80.50% | 1.681 | 16.33% | 53 |

## Config layout

```
configs/
├── config/     runnable strategy configs (and optimizer output)
└── optimize/   optimizer input presets
```

This is a hard rule — `configs/*.json` no longer resolves. Trading configs live in
`configs/config/`, optimizer presets in `configs/optimize/`.

## Running it

One generic interface — no aliases, no numbered presets:

```bash
./pipeline.sh --config <config-file> --backtest
./pipeline.sh --config <config-file> --historical-replay
./pipeline.sh --config <config-file> --forward-test
```

`<config-file>` may be `config1-ETHUSDTP15m-long.json`,
`configs/config/config1-ETHUSDTP15m-long.json`, or the bare name
`config1-ETHUSDTP15m-long`.

## Optimizing

```bash
./pipeline.sh --optimize --odefault.json --mywinner.json
```

Reads `configs/optimize/odefault.json`, writes `configs/config/mywinner.json`.

The optimizer drives the canonical V3 package (`src/optimization/v3/`) and is
**fully implemented end to end** — all five V3 stages run and a runnable config is
written:

| stage | trials at the 1,850 total | what it searches |
|---|---|---|
| 1a broad strategy | 400 | 11 strategy dims at neutral risk |
| 1b narrowed strategy | 800 | same dims, ranges derived from 1a survivors |
| 1c risk-only | 200 | leverage / risk / allocation on the frozen strategy |
| 2a final joint | 300 | all 14 dims, discovered seed enqueued as trial 0 |
| 2b Bollinger | 150 | 6 filter dims, strategy + risk frozen |

`trials` is a single TOTAL. `"auto"` resolves a documented total from timeframe and
history length; any explicit integer is allocated deterministically across the five
stages. The run plan prints the five budgets before anything runs.

`execution.tick_size` is `"auto"` by default and is resolved once from Binance
Futures `PRICE_FILTER.tickSize` for the symbol; the quantity step comes from
`LOT_SIZE.stepSize`. Both resolved values are recorded in the manifest and the
emitted config, **and are applied to every trial** — a campaign is not limited to the
symbols listed in `optimization/v3/spec.py`. Tick size is never derived from the
timeframe and there is no per-symbol map.

`history` (preset `_schema_version: 3`) requires an explicit `mode`: `auto`, `days`, `date_range`, or `candles`.
Only fields required by the chosen mode may be non-null. `auto` targets 43,200 evaluable candles plus
1,000 warmup (e.g., 1m -> ~30d, 15m -> ~450d). For `auto` and `candles`, data preparation checks the local
cache for both **depth** and **recency** and fetches or extends it when either fails, so
"availability-limited" means the exchange genuinely has no more history — not a stale cache.
Custom short history runs emit an experimental note without stopping execution.

UNSEEN is carved off the end of the window, locked, and physically removed from the
frame the optimizer receives, so no search, narrowing, seed, risk or Bollinger stage
can address it. It is opened once, after the winner is frozen, and its metrics are
recorded as confirmation only — they never change the selection. The reservation
defaults to 20% (effective split TRAIN 56% / VALID 24% / UNSEEN 20%); the optional
`partition` block sets a different `unseen_pct` or pins `unseen_start` to an exact date
for reproducing a historical campaign.

The output name is mandatory, is never auto-generated, and an existing config is
never overwritten — the check runs before any data is loaded. `--optimize` cannot be
combined with any execution or maintenance action. See `WALKTHROUGH.md` § 15.

## Backups and reference code

| Path | Contents |
|---|---|
| `src/risk_management/backup/` | pre-Phase-2 RiskManager (reference only, never imported) |
| `src/optimization/backup/` | legacy Candidate #5-era optimizer (reference only) |
| `backups/` | archives produced by `booklets/rebuild_project.sh` (created on first run) |

Nothing under a `backup/` directory is ever imported by production code. Verify with:

```bash
grep -rn "backup" src/ --include=*.py | grep -v "^src/.*/backup/"
```

<a name="rejected-experiments"></a>
## Rejected experiments (historical — NOT production)

Kept so the same ground is not re-broken. None of this is active functionality.

| Experiment | Status | Trace left in the repo |
|---|---|---|
| **MTF 2h EMA300 gate** (Config2 strict / Config3 relaxed 0.50%) | **Rejected and removed** — did not work properly in practice | none in production code, configs or Pine |
| **ADX filter** | Never ported to Python | inert `input.bool(false, …)` toggle in both Pine files only |
| **Candidate #5** | Superseded by Candidate #158 | `src/optimization/backup/` |

The MTF removal deleted `src/filters/stage_2_mtf/`, `configs/config3-*` and `pine/config3-*`.
Because the shared `MaskedStrategy` wrapper had been placed *inside* that MTF package, its
deletion also broke the Bollinger path and every `main.py` import; the wrapper now lives at
`src/filters/masked_strategy.py`, independent of any single filter.

Historical MTF-era measurements (Config2 +72.12% PF 2.534, Config3 +83.87% PF 2.530 on
2025-12-01 → 2026-08-15) describe code that no longer exists and must not be compared
against current Config1/Config2 results.

## How to start reading

1. This file.
2. `WALKTHROUGH.md` — architecture, flows, commands.
3. `TECH.md` — if you are (or are directing) an AI agent making changes.
4. `configs/config/config1-ETHUSDTP15m-long.json` — the numbers that define current behaviour.
5. `configs/optimize/odefault.json` — the optimizer's human-facing inputs.
