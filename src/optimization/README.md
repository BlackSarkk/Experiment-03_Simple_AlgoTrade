# Optimization Module

Research optimization code. Nothing in this directory is production code, and nothing here
is imported by `pipeline.sh`, `src/main.py`, `src/auto_optimise/` or any test.

This directory now contains exactly three entries:

| path | status |
|---|---|
| `v3/` | **The only active optimizer.** |
| `backup/` | All retired and research evidence. Archival only — nothing here is active. |
| `README.md` | This file. |

**No legacy optimizer, recovered source, lab harness or fetch helper remains active at
this root.** They were not deleted — every one was moved into `backup/` with its
internal tree preserved and its original path recorded:

| what | now lives at |
|---|---|
| `new_optimizer_v2/` | `backup/v2_deep_15m/retired_active_source/` |
| `recovered_phase3a/` | `backup/phase3a_recovered_c158/retired_active_source/` |
| `new_optimizer_lab/` | `backup/v3_research_evidence/retired_active_lab/` |
| `fetch_data.py`, `fetch_delta.py` | `backup/unclassified_legacy_utilities/source/` |

V2, recovered Phase-3A and V3 lab evidence stay in separate lineage folders and are
never merged. See `backup/README.md` for the full map and the per-lineage READMEs for
provenance, known flaws and run warnings.

## `v3/` — canonical

The active optimizer package: Stage 1 discovers a single 14-dimension seed, Stage 2
enqueues that seed as trial 0 and searches a final configuration around it, then selects a
Bollinger filter. 1,850 trials total (400 / 800 / 200 / 300 / 150), TPE seed 42,
`n_jobs=1`. Direction comes from the caller's preset and is never searched.

Promoted from `new_optimizer_v3/` by a move only — every file is byte-identical to its
pre-move state and no V3 logic was changed.

Only the plan-only mode is reachable from the CLI:

```bash
PYTHONPATH=src ./.venv/bin/python -m optimization.v3 --plan-only
```

It prints declarations and nothing else — no market data, no Optuna, no pandas, no
directory creation. Running an actual campaign requires driving `optimizer.Campaign` from a
harness with a prepared frame.

**Campaigns must be launched with `./.venv/bin/python`** (NumPy 2.5.2). Bare `python3`
resolves to NumPy 1.26.4, which changes TPE's quantization of stepped suggestions and
silently forks the search tree — see `backup/v3_research_evidence/README.md`.

## `backup/` — archival only

Lineage-organized archive: `v1_candidate5/`, `v2_deep_15m/`, `phase3a_recovered_c158/`,
`v3_research_evidence/`, `pre_v3_auto_optimise/`, `unclassified_legacy_utilities/`. Several archived engines are dangerous to execute (production-cache
fetches, holdout leakage) — see the per-folder READMEs. The retired root scripts
`multi_tf_optimizer.py` and `deep_15m_optimizer.py` now live in
`backup/v2_deep_15m/source/retired_active_root/`.
