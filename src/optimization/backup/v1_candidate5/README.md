# V1 — Candidate #5 (legacy multi-timeframe optimizer)

## What it was
The first-generation optimizer: a single Optuna/TPE study that searched strategy
parameters **and** risk parameters **and** trade direction jointly, per timeframe,
across many timeframes. It produced the legacy **Candidate #5** parameter set.

## Source / provenance
Proven from `src/optimization/backup/README.md` (the byte-exactness table it carried),
each file's own docstring, and `new_optimizer_lab/COMPARISON.md`, which names
`backup/run_multi_tf_optimization.py` as "**Old optimizer** … (Candidate-#5-era flow)".

| archived file | origin | byte-exact from git |
|---|---|---|
| `source/multi_tf_optimizer_old.py` | `git show HEAD:src/optimization/multi_tf_optimizer.py` | YES |
| `source/run_candidate5_robustness.py` | `git show 0d70ae7^:run_candidate5_robustness.py` | YES |
| `source/run_multi_tf_optimization.py` | `git show 0d70ae7^:run_multi_tf_optimization.py` | YES |
| `source/fetch_data_old.py` | `git show HEAD:src/optimization/fetch_data.py` | YES |

The two root-level scripts were deleted in commit `0d70ae7` ("freeze neutral validated
baseline") and recovered from its parent. `fetch_data_old.py` is assigned here on the
evidence of its own docstring: *"Pre-download script for multi-timeframe optimization
data … Binance Futures 2024-01-01 → 2026-08-13"* — the exact window and role this
optimizer consumed.

## Symbol / timeframe / direction
Multi-symbol, **8–10 timeframes** (2m resampled from 1m). Direction was a **search
dimension** — `side_choice ∈ {both, long_only, short_only}` was sampled inside the same
study. This is **not** a long-only optimizer.

The frozen Candidate #5 vector itself is long-only
(`long_enabled True`, `short_enabled False`) — see `configs/candidate5_optimizer_notes.md`
for the verbatim 12-parameter vector and the hardcoded `leverage 3.5 / risk 1.5%`.

## Data dates and partitioning
- Dates: `2024-01-01 → 2026-08-13`, whatever the cache happened to contain.
- Partitioning: **50 / 25 / 25** by row index on the raw frame.
- **No warmup prefix and no evaluation-window slicing** — indicators restart at each
  partition edge.

## Trial pipeline and budgets
Single-stage joint study per timeframe: 11 coarse strategy dimensions (EMA/RSI/ATR
categorical, not continuous) + 3 risk dimensions (leverage 1.0–**10.0**) + `side_choice`,
plus `use_volume_filter` / `use_ema_slope_filter` / `use_trend_filter`. Ranking used
`robust_score()`, a hand-weighted dollar-return objective
(`ret*0.3 + PF*15 − dd*1.5 + sharpe*10 + wr*20 + …`).
`run_candidate5_robustness.py` then re-measured the chosen vector on VAL/HOLDOUT.

## Locations
- Source: `v1_candidate5/source/`
- Config/notes: `v1_candidate5/configs/candidate5_optimizer_notes.md`
- Data: none archived — this engine read the **production** `data/` cache, which is
  excluded from this archive by policy and was not copied.
- Results: none — `results/multi_tf_optimization/` was never produced in this checkout.

## Known flaws / why retired
1. **VALIDATION sat inside the objective** (`0.6*TRAIN + 0.4*VALID`), making it a second
   training set rather than a held-out check.
2. Risk parameters searched jointly with strategy, so sizing inflated the score directly;
   the objective was dollar-return-driven and not sizing-neutral.
3. `side_choice` in the same study — direction was fitted, not decided.
4. 50/25/25 with **no warmup**, so every rolling indicator restarts at the partition edge.
5. Ran against the **pre-Phase-2 RiskManager**: notional-based allocation cap, silent 1%
   SL substitution, `round(qty, 4)`, no margin guard.
6. Effective date range was undefined — whatever was cached.

Retired in favour of the Phase-3A strategy-first / risk-second lineage.

## Verified candidate?
**Yes, with a caveat.** It produced Candidate #5 (vector recorded in the notes file).
Those numbers are **not comparable** to any later phase without a rerun, because the
engine, RiskManager, partitioning and objective all changed afterwards. The notes record
that Candidate #5 was later re-benchmarked on the current engine over the Phase-3
partitions; those figures live in the session record, not in this archive.

## Reproduction command
**None valid.** These are archived copies with a changed directory depth; their
`PROJECT_ROOT`/`sys.path` bootstrapping resolves relative to their original locations.
The historical invocation was:

    TQDM_DISABLE=1 PYTHONPATH=src python src/optimization/multi_tf_optimizer.py

## ⚠️ Warning — do not run
Do not execute anything in this folder. `fetch_data_old.py` and the optimizers call
`MarketDataLoader` against the **production** `data/` cache and will fetch and overwrite
it, including dates inside the currently locked window. Read as evidence only.
