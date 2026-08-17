# New Optimizer V3 — two-stage seed → config

Isolated. Nothing outside this directory was created or modified: the recovered recipe, V2,
`src/auto_optimise/`, the legacy optimizers, `pipeline.sh`, `src/main.py`, production configs
and the production engine / risk / filter code are all untouched. V3 imports the production
`BacktestEngine`, `BaselineStrategy`, `compute_all_indicators`, `BaselineRiskManager` and the
Stage-1 Bollinger filter, and modifies none of them.

V3 reproduces the *structure* the recovered Candidate-#158 workflow actually used — discover a
seed, then search a final configuration around it — while correcting the defects found in the
Phase-9 and Phase-10 audits.

```
STAGE 1 — seed discovery                     STAGE 2 — seeded final configuration
  1a broad    400 trials, 11 dims              2a final  300 trials, 14 dims jointly
              neutral risk 1.0x/1.5%/50%                 SEED enqueued as trial 0
  1b narrow   800 trials, 11 dims              2b boll   150 trials, 6 dims
              ranges derived from 1a                     strategy + risk frozen
  1c risk     200 trials, 3 dims
              strategy frozen
  -> exactly ONE 14-dimension seed
```

## Files

| file | role | imports |
|---|---|---|
| `spec.py` | every budget, range, gate threshold and score weight | stdlib only |
| `scoring.py` | gate and score functions, pure and deterministic | stdlib only |
| `optimizer.py` | `Campaign` — the five stages, production engine calls | pandas / optuna / production |
| `__main__.py` | CLI; `--plan-only` imports only `spec` + `scoring` | stdlib only |
| `SCORING_AND_SELECTION.md` | the rules, fixed before any run | — |

## Plan-only

```bash
PYTHONPATH=src python -m optimization.new_optimizer_v3 --plan-only
```

Prints budgets, long-only status, all 20 ranges, the neutral-risk policy, both stage flows, the
data contract and every gate/score rule. It **cannot** import pandas, numpy, optuna, the data
loader or the engine — the CLI reaches only the two stdlib-only modules — and it creates no
directory. The plan run self-reports which heavy modules were imported; the expected answer is
`none`. Any other invocation is refused with exit code 2.

## Data contract

The caller supplies one frame holding exactly `[warmup rows][DEV rows]` for one symbol and one
timeframe. `Campaign` splits DEV 70/30 into TRAIN/VALID and can address nothing else — there is
no holdout partition in the frame, no unlock path, and therefore nothing to leak. Indicators are
computed once per candidate on the **full** warmup+DEV frame and sliced by index, so every
evaluated partition carries complete warmup.

## Running a campaign later

Not implemented in this CLI by design. Drive it from a harness that prepares the frame and calls
`Campaign.stage1()` → `Campaign.stage2_config(seed)` → `Campaign.stage2_bollinger(winner)`, so
that preparing data and authorising a run stay separate, explicit decisions.
