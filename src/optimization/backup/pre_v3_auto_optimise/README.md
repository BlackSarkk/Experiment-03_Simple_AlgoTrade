# Pre-V3 auto-optimiser outputs

Configs, presets and Pine produced by `src/auto_optimise/` **before** it was rebuilt
around canonical V3. The optimizer package itself is not archived here — it is still
the live human entry point, now driving `src/optimization/v3/`.

These artifacts came from the earlier six-stage pipeline (Phase-A strategy search,
robustness, risk, Bollinger, Top-10 + UNSEEN), whose selection rules differ from V3's.
They are kept as a record of what that pipeline produced.

## Original paths and role

| archived path | original path | role |
|---|---|---|
| `configs/auto5000-c158rematch.json` | `configs/config/` | pre-V3 auto-optimiser winner, TRAIN rank 1 — the Candidate-#158 rematch run |
| `configs/autowinner.json` | `configs/config/` | pre-V3 auto-optimiser winner, TRAIN rank 62 |
| `presets/ocandidate158_rematch.json` | `configs/optimize/` | preset that drove the C158 rematch |
| `presets/olong3y.json` | `configs/optimize/` | 3-year long-only preset |
| `presets/omanual_eth15m.json` | `configs/optimize/` | manual ETH 15m preset |
| `pine/auto5000-c158rematch.pine` | `pine/` | Pine export of the C158 rematch winner |

## Not comparable to V3

Produced by a different search and a different selection rule. Do not rank these
against any V3 result without rerunning them under V3. The presets use the OLD preset
schema (per-phase trial semantics, no `execution.tick_size`, no `history.candles`) and
will not load in the current optimizer.
