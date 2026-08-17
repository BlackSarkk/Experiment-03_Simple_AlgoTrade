# V3 — research evidence (campaign artifacts only, NOT the optimizer source)

⚠️ **The active V3 source is deliberately not archived here.** It lives at
`src/optimization/new_optimizer_v3/` and must stay unchanged. Only its two documentation
files are copied, into `configs/`, as a record of what the campaigns below ran against.

## What it was
V3 is a repaired implementation of the Phase-3A Candidate-#158 discovery architecture:
Stage 1 discovers a single 14-dimension seed, Stage 2 enqueues that seed as trial 0 and
searches a final configuration around it, then selects a Bollinger filter.

Corrections over the recovered recipe: full warmup+DEV indicators computed before
partition slicing; a **fixed 170-bar** evaluation skip so every candidate is scored on
identical rows; graded failed-gate scores instead of a flat sentinel; VALID PF/DD/sample
quality materially weighted; per-symbol tick size; **no hardcoded C158 / #53 / Risk-158
seed**; Bollinger selected on separate TRAIN and VALID metrics.

## Source / provenance
Campaign harnesses live in `src/optimization/new_optimizer_lab/phase*/` and were copied
here verbatim with their ledgers. Each `phase*_results.json` carries its own preflight
block recording the boundaries that run actually asserted.

## Symbol / timeframe / direction
**Strict long-only** (`long_enabled=True`, `short_enabled=False`, hardcoded in `spec.py`,
never a search dimension).

| phase | symbol | tick | note |
|---|---|---|---|
| 14 | ETHUSDT 15m | 0.01 | earlier DEV boundary (DEV ends 2026-05-31, separate TEST) |
| 16 | ETHUSDT 15m | 0.01 | full historical; the reference fingerprint run |
| 17 | BTCUSDT 15m | 0.1 | two runs — see the environment warning below |
| 18 | ETHUSDT 15m | 0.01 | reproduction check of Phase 16 |

## Data dates and partitioning
Phases 16 / 17 / 18 share one contract:
- warmup **1,000** candles
- DEV `2024-07-16 00:00 → 2026-07-15 23:45 UTC` = **70,080** rows
- chronological **70/30**: TRAIN **49,056** / VALID **21,024**, VALID begins `2025-12-09 00:00 UTC`
- comparison window `2026-07-16 → 2026-08-15`, **physically excluded** from every
  optimization frame (`df.iloc[:dev_hi]`) and evaluated once, after all 1,850 selections froze

Phase 14 used an earlier split (DEV ends `2026-05-31 23:45`, 65,760 rows; separate 7,392-row TEST).

## Trial pipeline and budgets
Fixed sequence, **1,850 trials total**, TPE seed **42**, `n_jobs=1`:

    1a broad   400   11 strategy dims, neutral risk 1.0x / 1.5% / 50%
    1b narrow  800   11 strategy dims, ranges derived from 1a survivors
    1c risk    200   strategy frozen, leverage / risk / allocation only
    2a final   300   14 dims jointly, discovered seed enqueued as trial 0
    2b boll    150   6 Bollinger dims, strategy + risk frozen

## Locations
- Ledgers + results: `results/phase14_v3_eth/`, `results/phase16_v3_full_historical/`,
  `results/phase17_v3_btc_full_historical/` (includes the `venv_numpy_2_5_2/` subrun),
  `results/phase18_v3_eth_reprocheck/`
- Pine parity evidence: `results/pine/` — `v3_eth15m_bb_off.pine`, `v3_eth15m_bb_on.pine`,
  `v3_fullhistorical_eth15m.pine`, `v3_fullhistorical_btc15m.pine`
- Data: `data/ETHUSDT_15m_warmup_dev_test.csv` + manifest — the isolated Phase-12 lab
  dataset that Phases 16 and 18 read. The BTC datasets travel inside the Phase-17 folder.
- V3 doc snapshot: `configs/v3_source_README.md`, `configs/v3_source_SCORING_AND_SELECTION.md`

## Known flaws / why retired
**Not retired — V3 is the active line.** Two findings are recorded here because they
affect how this evidence may be compared:

1. **Environment forks the search.** Phase 16 ran under `.venv/bin/python`
   (**NumPy 2.5.2**); Phase 17's first run and Phase 18 ran under system `python3`
   (**NumPy 1.26.4**). With byte-identical data and source, Stage-1a trials 0–28 match
   exactly and then diverge at **trial 29** (`swing_lookback` 19 vs 20) — NumPy's version
   changes how TPE quantizes a stepped suggestion at a rounding boundary, forking the whole
   1,850-trial tree. Proven by replaying each ledger's recorded objective values into a
   fresh `TPESampler(seed=42)` under both interpreters.
2. **Phase 18 therefore did not reproduce Phase 16** — every stage winner differs. This is
   an environment artifact, not a V3 defect. Phase 18's own results are valid *for*
   NumPy 1.26.4 and simply not comparable to Phase 16.

Consequence: `results/phase17_v3_btc_full_historical/v3_stage*.csv` (the top-level ones)
are the **superseded** NumPy 1.26.4 run. The authoritative BTC result is
`results/phase17_v3_btc_full_historical/venv_numpy_2_5_2/`.

Also note Phase 17's original data file had **zero comparison-window rows** — its fetch
loop discarded every row at or after the lock while its validator *required* their absence,
making the mandatory final comparison impossible. Fixed in the `venv_numpy_2_5_2` run.

## Verified candidate?
**Yes — one per symbol, both evaluated once on the held-out comparison window.**

**ETHUSDT (Phase 16):** stage winners 1a t324/0.3820 · 1b t457/0.4179 · 1c t47/0.6736
(`3.5x / 3.0% / 65%`) · 2a t0 (seed retained)/0.6736 · BB t143/0.1693
(`11/3.0/1.1/17/0.1/0.05`). Comparison window — BB OFF `−0.19%`, PF 0.985, DD 10.99%,
10 trades, `−$18.55`; BB ON `+2.64%`, PF 1.412, DD 7.36%, 5 trades, `+$264.46`.

**BTCUSDT (Phase 17B, canonical env):** stage winners 1a t280/0.1816 · 1b t422/0.1755 ·
1c t103/0.2434 · 2a **t289**/0.3027 (seed *not* retained) · BB t45/0.2820
(`12/3.0/0.0/16/0.5/0.16`). DEV VALID — OFF `+52.61%` PF 1.854; ON `+58.77%` PF 2.283.
Comparison window — BB OFF `+0.65%`, PF 1.175, DD 3.75%, 8 trades, `+$64.95`; BB ON
`+0.62%`, PF 1.166, DD 4.33%, 7 trades, `+$61.89`. Bollinger adds nothing out of sample
on BTC despite helping materially on DEV.

## Reproduction command
Valid, and it **must** use the project venv:

```bash
./.venv/bin/python src/optimization/new_optimizer_lab/phase17_v3_btc_full_historical/run_phase17.py
```

The harness asserts Python 3.12.3 · NumPy 2.5.2 · Optuna 4.9.0 · pandas 3.0.5 · seed 42 ·
`n_jobs=1` and exits 1 rather than falling back. Runtime ≈ 1,011 s for 1,850 trials.

## ⚠️ Warning
Never launch a V3 campaign with bare `python` or `python3` — NumPy 1.26.4 silently produces
a different search tree and the result cannot be compared to any other phase. Re-running a
phase **overwrites its ledgers**; write new runs to a fresh subdirectory, as
`venv_numpy_2_5_2/` does. The files in this archive are copies — reproduce from the live
harnesses, not from here.
