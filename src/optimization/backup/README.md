# Optimizer archive — research evidence only

Historical optimizer material, organized by lineage. **Nothing here is production code.**
Nothing in this tree is imported by `pipeline.sh`, `src/main.py`, `src/auto_optimise/` or
any test. Several folders contain engines that are actively dangerous to run — each
version README carries its own warning.

## Lineage

```
V1 Candidate-5  →  V2 deep/multi-TF attempts  →  recovered Phase-3A / C158  →  active V3 research
```

- **V1 Candidate-5** — one joint TPE study per timeframe: strategy + risk + trade direction
  searched together, 50/25/25 with no warmup, dollar-return objective. Produced Candidate #5.
- **V2 deep/multi-TF attempts** — 15m-only successor that got the *shape* right
  (strategy first under neutral risk, then a separate risk stage) but **selected on
  holdout**. Never run in this checkout. Filed alongside it, under a name collision, is the
  later `new_optimizer_v2` package, which fixed Phase-3A's objective and lost the 15m
  bakeoff to the unseeded recovered recipe.
- **Recovered Phase-3A / C158** — the deleted Candidate-#158 workflow, recovered
  byte-for-byte from session transcripts. Strategy-first / risk-second, 60/20/20 with a
  300-bar warmup prefix, sizing-neutral objective, holdout never read by a search stage.
  Its campaign arm carried the uncapped `0.70*TRAIN + 0.30*VALID` defect.
- **Active V3 research** — reproduces the C158 seed-then-config *structure* with that
  defect and the Phase-9/10 audit findings fixed. 1,850 trials, seed 42, `n_jobs=1`.
  Verified candidates exist for ETHUSDT (Phase 16) and BTCUSDT (Phase 17B).

## Layout

| folder | lineage | contains the runnable engine? |
|---|---|---|
| `v1_candidate5/` | V1 | yes — **do not run** |
| `v2_deep_15m/` | V2 (two distinct lineages, see its README) | yes — **do not run** |
| `phase3a_recovered_c158/` | Phase-3A | yes — **do not run unmodified** |
| `v3_research_evidence/` | V3 | lab harnesses, ledgers and datasets — not the V3 engine |
| `pre_v3_auto_optimise/` | pre-V3 human optimizer | outputs only |
| `unclassified_legacy_utilities/` | none — unattributable | yes — **do not run** |

## Retired from the active tree

`src/optimization/` was cleaned so that only `v3/`, `backup/` and `README.md` remain
active. Everything that used to sit beside them was **moved** here, internal tree
preserved, nothing deleted or overwritten:

| archived path | original path |
|---|---|
| `v2_deep_15m/retired_active_source/new_optimizer_v2/` | `src/optimization/new_optimizer_v2/` |
| `phase3a_recovered_c158/retired_active_source/recovered_phase3a/` | `src/optimization/recovered_phase3a/` |
| `v3_research_evidence/retired_active_lab/new_optimizer_lab/` | `src/optimization/new_optimizer_lab/` |
| `unclassified_legacy_utilities/source/fetch_data.py` | `src/optimization/fetch_data.py` |
| `unclassified_legacy_utilities/source/fetch_delta.py` | `src/optimization/fetch_delta.py` |
| `v3_research_evidence/configs/retired_active/` | `configs/config/phase16_repro_winner.json`, `configs/optimize/ophase16_repro.json` |
| `v3_research_evidence/retired_active_pine/` | the four `pine/v3_*.pine` exports |
| `pre_v3_auto_optimise/` | pre-V3 winners, presets and Pine from `configs/` and `pine/` |
| `unclassified_legacy_utilities/pine/` | the three parity / bakeoff Pine exports |

These `retired_active_*` folders are deliberately separate from the earlier
copy-based archive folders beside them: the copies were taken while the originals were
still live, and these are the originals themselves. No archive file was overwritten —
the move refused to proceed on any collision.

`configs/config/` now holds only `config1-ETHUSDTP15m-long.json`,
`config2-ETHUSDTP15m-long.json` and `default.json`; `configs/optimize/` only
`odefault.json`; `pine/` only the `config1`/`config2` scripts.

The active V3 source stays at `src/optimization/v3/` and is **not** archived.

## How this archive was built

- Files already inside `backup/` were **reorganized** (moved) into their proven folder: 6 files.
- Everything outside `backup/` was **copied**; no original was moved, renamed or deleted: 92 files.
- Every copy was verified by sha256 against its source at write time.
- Assignments were proven from `backup/README.md`'s git-provenance table, each file's own
  docstring, `new_optimizer_lab/COMPARISON.md` (which names the old / recovered / new-attempt
  engines explicitly), `new_optimizer_v2/SELECTION_RULE.md`, `recovered_phase3a/README.md`
  and its `recovery_ledger.json` — not from filenames.

## Excluded by policy — never read, copied, moved or modified

`src/auto_optimise/` · `pipeline.sh` · `src/main.py` · production engine / RiskManager /
filter code · production `data/` and `results/` caches · `configs/config1*` ·
`configs/config2*` · all auto-config test caches.

The archive builder enforced this with a path guard that aborts on any excluded prefix.
Consequently **no production market-data cache was duplicated**: Phase-3A's dataset is the
quarantine copy, and V3's datasets are the isolated lab copies.
`pine/config1-ETHUSDTP15m-long.pine` and `pine/config2-ETHUSDTP15m-long.pine` were left
untouched under the same rule.

## Unclassified — archived rather than assigned

These span more than one lineage, or have no provenance evidence strong enough to
attribute. They are archived under clearly named folders rather than filed into a
lineage they may not belong to:

| item | where it now lives | why unresolved |
|---|---|---|
| `bakeoff_15m/`, `phase12_parity/` | `v3_research_evidence/retired_active_lab/new_optimizer_lab/` | Two-arm runs (recovered Scenario-4 recipe **vs** `new_optimizer_v2`). Their ledgers, configs and datasets belong jointly to Phase-3A and V2; splitting them would break the comparison they document. |
| the three parity / bakeoff Pine exports | `unclassified_legacy_utilities/pine/` | Outputs of those same two-arm runs. |
| `fetch_data.py`, `fetch_delta.py` | `unclassified_legacy_utilities/source/` | Not attributable from their contents to any lineage; both write into the production `data/` cache. |

## Verification performed

**Archive build.** Inventory before: 131 files under `src/optimization/`. 6 moved,
92 copied, 0 missing, every copy sha256-verified.

**Active-tree cleanup.** 20 entries / 135 files moved out of the active tree. Every
move was sha256-verified after the fact, and the mover aborts on any destination that
already exists, so no archive file was overwritten. Nothing was deleted.

Excluded paths were re-checked afterwards and are byte-identical. Nothing staged or
committed.
