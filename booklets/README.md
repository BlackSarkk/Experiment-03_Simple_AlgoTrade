# Booklets — project index

Rule-based **ETHUSDT perpetual, 15m, long-only** trading research pipeline.
Python is the source of truth; a TradingView Pine port mirrors it for chart validation.

## What each file is for

| File | Audience | Purpose |
|---|---|---|
| `README.md` | human | this index |
| `WALKTHROUGH.md` | human | how the project works and how to run it |
| `TECH.md` | AI coding agent | invariants, call chains, safe-modification and testing protocol, recovery detail |
| `rebuild_project.sh` | ops | dated backup archive with manifests for reconstruction |

## Current frozen reference

| | Path |
|---|---|
| Active config | `configs/config1-ETHUSDTP15m-long.json` |
| Active Pine | `pine/config1-ETHUSDTP15m-long.pine` |
| Active risk policy | `src/risk_management/riskmanager.json` |
| Baseline preset | `configs/default.json` |

Frozen candidate: **Candidate #158** — EMA 104 / RSI 20 / OB 64 / OS 23 / ATR 7 /
consolidation 7 @ 2.8 ATR / swing 17 / vol SMA 12 @ 1.8x / RR 3.6,
leverage 4.0x, risk 2.6%, allocation 70%, Bollinger stored but `enabled: false`.

## Running it

One generic interface — no aliases, no numbered presets:

```bash
./pipeline.sh --config <config-file> --backtest
./pipeline.sh --config <config-file> --historical-replay
./pipeline.sh --config <config-file> --forward-test
```

`<config-file>` may be `config1-ETHUSDTP15m-long.json`,
`configs/config1-ETHUSDTP15m-long.json`, or the bare name `config1-ETHUSDTP15m-long`.

## Backups and reference code

| Path | Contents |
|---|---|
| `src/risk_management/backup/` | pre-Phase-2 RiskManager (reference only, never imported) |
| `src/optimization/backup/` | legacy Candidate #5-era optimizer (reference only) |
| `backups/` | archives produced by `booklets/rebuild_project.sh` |

## How to start reading

1. This file.
2. `WALKTHROUGH.md` — architecture, flows, commands.
3. `TECH.md` — if you are (or are directing) an AI agent making changes.
4. `configs/config1-ETHUSDTP15m-long.json` — the numbers that define current behaviour.
