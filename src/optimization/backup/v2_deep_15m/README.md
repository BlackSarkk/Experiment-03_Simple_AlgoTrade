# V2 — deep-15m / "new optimizer attempt" material

⚠️ **Naming collision — read this first.** Two distinct things carry a "V2"-ish label and
both are archived here, kept in separate folders:

1. **The deep-15m / multi-TF "new optimizer attempt"** — `deep_15m_optimizer.py` and the
   working-tree `multi_tf_optimizer.py`. This is what "V2 deep_15m" means.
2. **`new_optimizer_v2/`** — a *later, unrelated* package that self-identifies as
   "New Optimizer V2". It was written **after** the Phase-3A recovery to correct the
   recovered Scenario-4 objective. It is not a descendant of `deep_15m_optimizer.py`.

They are filed together only because the required archive layout has one V2 folder. Do not
treat them as one lineage.

## What it was
**(1) deep-15m attempt.** A 15m-only successor to the V1 multi-TF engine that got the
*shape* right — strategy searched first under neutral risk, then a separate stage-5 risk
search — but on leaking data.

**(2) new_optimizer_v2.** A 300-trial strategy+risk stage plus a 150-trial Bollinger stage,
whose entire purpose was a **selection rule fixed before any run** (`SELECTION_RULE.md`)
that caps the TRAIN return term so TRAIN cannot dominate VALID.

## Source / provenance
- `source/deep_15m_optimizer.py` — reorganized from the pre-existing `backup/` folder.
  Recorded there as a *working-tree copy, gitignored, never committed* (no git object exists).
- `source/worktree_copy_deep_15m_optimizer.py`, `source/worktree_copy_multi_tf_optimizer.py`
  — verified copies of the live files under `src/optimization/`; originals untouched.
- `source/new_optimizer_v2_package/` — verified copy of `src/optimization/new_optimizer_v2/`;
  original untouched.
- `source/inspection_lab/` — the `new_optimizer_lab` static-inspection dispatcher, which
  exists specifically to read these two engines **without importing them**.
- Assignment of (1) is proven by `COMPARISON.md`: *"**New optimizer attempt** =
  `src/optimization/multi_tf_optimizer.py` + `deep_15m_optimizer.py`"*.
  Assignment of (2) is proven by its own `SELECTION_RULE.md` title and body.

## Symbol / timeframe / direction
- deep-15m: **15m only**, `2024-01-01 → 2026-08-15`. Direction not searched.
- multi_tf (worktree): 10 timeframes; **`side_choice` was a search dimension**.
- new_optimizer_v2: ETHUSDT and BTCUSDT 15m, **strict long-only**, 14-dim space.

## Data dates and partitioning
- deep-15m and multi_tf: **50 / 25 / 25 by row index, no warmup** — indicators recomputed
  per slice, so every rolling window restarts at the partition edge.
- new_optimizer_v2 (as exercised in the bakeoff): DEV `2024-07-16 00:00 → 2026-07-15 23:45`,
  1,000-candle warmup, chronological **70/30** split, boundary `2025-12-09 00:00`,
  TRAIN 49,056 / VALID 21,024.

## Trial pipeline and budgets
- deep-15m: strategy stage under neutral risk `1.0x / 1.5% / 50%`, then stage3 regimes,
  stage4 selection, stage5 risk search (leverage 1.0–5.0 step 0.5, risk 0.5–3.0 step 0.1,
  alloc 20–100 **step 10**).
- multi_tf: single **joint** study — strategy + risk + `side_choice`.
- new_optimizer_v2: **Stage A** 300 trials (strategy+risk, TPE seed 42, `n_jobs=1`,
  unseeded) → **Stage B** 150 Bollinger trials with strategy+risk frozen.

## Locations
- Source: `v2_deep_15m/source/`
- Results: `v2_deep_15m/results/COMPARISON.md` — the three-way defect inventory
  (old optimizer · recovered C158 · new attempt).
- Configs / data: none archived. Neither engine has a config file, and both read the
  **production** `data/` cache, which is excluded by policy.
- Bakeoff ledgers that exercised `new_optimizer_v2` are **not** filed here — see
  *Unclassified* in `../README.md`, because those runs span two lineages.

## Known flaws / why retired
- **`deep_15m_optimizer.py` reads HOLDOUT during the process.** Stage-3 regimes and
  stage-5 risk both evaluate the full frame, and **stage 4 selects on holdout PF**. This
  is direct holdout leakage and is the reason the engine is quarantined rather than fixed.
- **`multi_tf_optimizer.py`** puts VALIDATION inside the objective and searches direction.
- Both use 50/25/25 with no warmup.
- Both are standalone `__main__` scripts with every setting hardcoded — no CLI, no symbol,
  date or trial overrides.
- `new_optimizer_v2` was not defective; it was **superseded** by V3, which reproduced the
  Candidate-#158 seed-then-config *structure* instead of a single flat stage.

## Verified candidate?
- deep-15m / multi_tf: **No.** Neither has ever been run in this checkout — neither
  `results/15m_deep_optimization/` nor `results/multi_tf_optimization/` exists.
- new_optimizer_v2: **Yes** — it selected ETHUSDT trial 267 (score 0.5097) in the 15m
  bakeoff, but it **lost** that bakeoff to the unseeded recovered recipe (trial 279,
  score 1.5884). Not promoted.

## Reproduction command
Only the **static inspection** path is valid and side-effect free:

```bash
PYTHONPATH=src python -m optimization.new_optimizer_lab --engine deep_15m --plan-only
```

`--plan-only` is the sole supported mode; the lab reads the engines with `ast` and never
imports them. Any other invocation exits 2.

## ⚠️ Warning — do not run
**`deep_15m_optimizer.py` and `multi_tf_optimizer.py` must never be executed.** Beyond
fetching into the production cache, `deep_15m_optimizer.py` **selects on holdout data**;
any number it produces is contaminated and must not be compared against, or promoted over,
any V3 result. Inspect statically via the lab only.
