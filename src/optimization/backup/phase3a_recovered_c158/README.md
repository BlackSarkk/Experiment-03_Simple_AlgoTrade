# Phase-3A — recovered Candidate #158 workflow

## What it was
The historical **Candidate #158** discovery architecture: a strategy-first, risk-second,
multi-stage Optuna/TPE lineage that produced the C158 configuration. It is the direct
ancestor of the active V3 optimizer — V3 reproduces this *structure* (discover a seed,
then search a final configuration around it) with the Phase-9/Phase-10 audit defects fixed.

## Source / provenance
The original source was **deleted** and is absent from every commit, every dangling git
object, and both copies of the project tree. It was recovered **byte-for-byte** by
replaying the original `Write`/`Edit` tool calls found in Claude session transcripts
(`session 08facafa-…`, de-duplicated by `tool_use` id).

Original location:
`…/algo-research/Experiment-03_Simple_AlgoTrade/src/optimization/`

| archived source | bytes | lines | first written (UTC) |
|---|---|---|---|
| `source/core_15m_long_optimizer.py` | 19339 | 447 | 2026-08-16T03:00:56Z |
| `source/stage3_neighbourhood.py` | 5605 | 153 | 2026-08-16T06:53:07Z |
| `source/stage3_stable_region.py` | 8844 | 218 | 2026-08-16T07:02:14Z |
| `source/risk_policy_search_t53.py` | 5060 | 120 | 2026-08-16T07:30:00Z |
| `source/robustness_showdown.py` | 9316 | 209 | 2026-08-16T07:45:08Z |
| `source/campaign_2y_15m.py` | 17993 | 363 | 2026-08-16T10:07:28Z |

`evidence/recovery_ledger.json` holds the full chronological edit ledger (tool, timestamp,
`tool_use` id, byte deltas, running sha256 after each step). Only
`core_15m_long_optimizer.py` was edited after its `Write` (two edits adding `STAGE2_SPACE`);
every other file is a single `Write`. No `Bash`/`sed`/`tee` mutation of these paths appears
anywhere in the transcripts. Per-file sha256 values are in
`evidence/recovered_phase3a_README.md`.

## Symbol / timeframe / direction
**ETHUSDT, 15m, long-only** (`core_15m_long_optimizer` — long-only is in the name and the
frozen vector). The campaign arm (`campaign_2y_15m.py`) is likewise long-only.

## Data dates and partitioning
- **Phase-3A proper**: `2022-01-01 → 2026-08-15`, **161,953 candles**, **60/20/20** by row
  index with a **300-bar warmup prefix per partition** dropped before evaluation
  (train `0..97171`, validation `97171..129562`, holdout `129562..161953`).
- **Campaign arm** (`campaign_2y_15m.py`): DEV `2024-07-16 → 2026-07-15` with
  `2026-07-16 → 2026-08-15` **locked**; **70/30** of DEV by date, warmup taken from the
  preceding frame.
- HOLDOUT was **never read** by any search stage (asserted in the historical logs). The
  campaign asserts `eval_hi <= DEV_HI` on every run and unlocks the final month once, at
  the very end.

## Trial pipeline and budgets
Stage 1 (11 fine-grained strategy dims, TRAIN only) → Stage 2 (narrowed to the union of
the two best regimes, e.g. EMA 82–108 / RR 1.9–3.1; TRAIN+VALID with an explicit
consistency-gap penalty) → Stage 3 neighbourhood / stable-region helpers → dedicated
**risk-only** search on the frozen strategy (leverage 1.0–5.0 **step 0.1**, risk 0.5–3.0
step 0.1, alloc 20–80 **step 1** — the only space that can emit 28%) → robustness showdown.

Objective was **sizing-neutral**:
`0.5*clip(expR) + 0.3*clip(PF−1) + 0.2*clip(Sharpe/2) − 0.5*max(0, DD−20%)`, combined as
`0.6*TRAIN + 0.4*VALID − consistency_gap`.

## Locations
- Source: `phase3a_recovered_c158/source/`
- Configs: `configs/recovered_presets/` (+ `PROVENANCE.md`), `configs/config4_candidate158_balanced.json`
- Results: `results/stage/` (scenario-4 strategy + Bollinger trial ledgers),
  `results/bollinger_transfer_results.*`, `results/scenario4_result.json`,
  `results/preflight.json`, `results/dev_gap_probe.json`, `results/bounded_fetch.json`
- Data: `data/candles_futures_binance_futures_ETHUSDT_15m.csv` (the **quarantine** copy —
  the production cache was not touched)
- Evidence: `evidence/recovery_ledger.json`, `evidence/recovered_phase3a_README.md`,
  `evidence/frozen_challengers/` (trials 52/125/156/189/285 + manifests and forward
  protocol), `evidence/PHASE4_CHALLENGER_ANALYSIS.md`, `evidence/BOLLINGER_TRANSFER.md`,
  `evidence/pine/auto5000-c158rematch.pine`

## Known flaws / why retired
1. **Scenario-4 objective defect.** The campaign arm used
   `0.70*TRAIN_ret + 0.30*VALID_ret + …`, **uncapped**, which selected the member of its
   region with the **lowest VALID profit factor (1.157)** and the **deepest VALID drawdown
   (29.68%)**. This is the specific defect `new_optimizer_v2` and then V3 were built to fix.
2. `RISK_POLICY_PATH = "configs/riskmanager.json"` no longer resolves — the file was
   relocated to `src/risk_management/riskmanager.json` by commit `e03b220` (content
   byte-identical). An unmodified run raises `FileNotFoundError`.
3. `END_DATE = "2026-08-15"` **reaches into the locked window**; an unmodified run triggers
   a re-fetch that pulls locked candles into the production `data/` cache.
4. The current cache is short by exactly **2,976 rows** (the 31-day locked month, which
   lives inside HOLDOUT), so index-identical stages 1–3 cannot be reproduced without
   restoring them.

Retired in favour of V3, which keeps the seed-then-config structure and replaces the
objective, the evaluation-window handling and the Bollinger selection.

## Verified candidate?
**Yes — reproduced and fingerprint-matched.** A fixed-parameter (no Optuna) reproduction of
strategy Trial #53 at the frozen risk policy, TRAIN and VALIDATION only, locked window
guarded and never loaded:

    TRAIN  323 trades (historical 323) · return 49.3291% (49.33) · PF 1.2892 (1.289) · DD 11.0576% (11.06)
    VALID  101 trades (historical 101) · return 11.3840% (11.38) · PF 1.2341 (1.234) · DD 12.4464% (12.45)
    score  0.03516 (historical 0.03516)

## Reproduction command
**None valid as-is.** A rerun requires all three of: supplying `riskmanager.json` at the
historical path, capping the data load below the locked window, and restoring the 2,976
missing candles to recover the 60/20/20 index boundaries. Decide that deliberately.

The only routinely exercised path is as an **imported comparison arm**
(`import campaign_2y_15m as REC`) inside the phase harnesses, where the caller supplies a
pre-bounded frame and sets `REC.DEV_HI` explicitly.

## ⚠️ Warning — do not run unmodified
Executing `core_15m_long_optimizer.py` or `campaign_2y_15m.py` unmodified will **fetch the
locked window into the production `data/` cache** (`END_DATE = 2026-08-15`). That silently
destroys the holdout guarantee for every other phase. Any verification must cap the load
itself. Do not use the historical C158 seed, Strategy #53 or Risk #158 in new V3 work.
