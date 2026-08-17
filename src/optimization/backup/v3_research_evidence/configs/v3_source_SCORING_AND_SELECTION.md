# V3 scoring and selection — fixed before any run

Version `v3-seed-then-config-1.0`; score `v3_score_v1`; Bollinger score `v3_boll_score_v1`.
Recorded ahead of execution, not tuned against results.

## Gate — graded, never a flat sentinel

Minimum trades scale with partition length: `max(30, partition_rows // 500)` per partition.
For a 46,032 / 19,728 DEV split that is **92 TRAIN / 39 VALID**.

Six requirements: `tr_trades >= min_tr`, `va_trades >= min_va`, `va_return_pct > 0`,
`tr_return_pct > 0`, `va_pf >= 1.05`, `va_max_dd <= 35%`.

Each violated requirement contributes up to 1.0 of *shortfall*, scaled by how badly it misses.
A failing trial scores `-1.0 - 1.0 × shortfall/6`, i.e. inside `[-2.0, -1.0]`, so failures stay
**ordered** and TPE always has a gradient. Verified: a total failure scores −2.0000, a trial
missing only VALID PF scores −1.0048, and every passing trial lands above −1.0 (measured
passing range −0.0684 … +0.7700). This is the direct fix for V2's flat `-10.0`, which left 95%
of the Phase-8 BTC surface constant.

## Score `v3_score_v1` — selection for stages 1a, 1b, 1c and 2a

```
+0.30  clip(va_return_pct / 100, -1, 1)          VALID return, credit caps at +100%
+0.25  clip(va_pf - 1, 0, 1)                     VALID edge, caps at PF 2.0
-0.20  clip(relu(va_dd - 15%) / 35%, 0, 1)       VALID drawdown above 15%
+0.10  clip(va_trades / (3 × min_va), 0, 1)      VALID sample adequacy
+0.10  clip(tr_return_pct / 100, -1, 1)          TRAIN return, credit caps at +100%
+0.05  clip(tr_pf - 1, 0, 1)
-0.15  clip(|tr_ret - va_ret| / (|tr_ret| + |va_ret|), 0, 1)   symmetric consistency penalty
```

VALID terms carry **0.85** of the weight against TRAIN's 0.15, and VALID *return* alone carries
only **0.30** — so VALID profit factor, drawdown and sample size (0.55 combined) outweigh VALID
return. Verified: holding TRAIN identical, a candidate with strong VALID scores +0.4307 against
+0.0000-ish (−0.0508) for a weak-VALID one, so VALID decides.

Winner = highest score among gated trials; ties break to the lower trial number.

## Bollinger score `v3_boll_score_v1` — stage 2b

Gate: `va_on_trades >= min_va` (**the same VALID credibility floor as stage 1**),
`tr_on_trades >= min_tr`, and VALID trade retention `>= 40%` of unfiltered. Graded on failure,
same band as above.

```
+0.25  Δ VALID profit factor   (clipped ±0.8, normalised)
+0.20  Δ VALID net P&L         (normalised by |off|, clipped ±1)
+0.15  Δ VALID max drawdown    (relative improvement, clipped ±1)
+0.15  Δ TRAIN profit factor
+0.15  Δ TRAIN net P&L
+0.10  Δ TRAIN max drawdown
```

Both partitions contribute profit, net P&L, PF and drawdown. Net P&L carries 0.35 against PF's
0.40, and TRAIN carries 0.40 in total. If no trial clears the gate, V3 ships Bollinger
**disabled** and reports that rather than forcing a filter.

## Corrections versus the recovered recipe

| defect (Phase-9 finding) | recovered behaviour | V3 |
|---|---|---|
| VALID PF and VALID DD absent from the objective | `profit_first` = 0.70·TRAIN_ret + 0.30·VALID_ret; DD penalty only above 40% | VALID PF 0.25, VALID DD penalised above 15%, VALID sample 0.10 |
| unbounded consistency gap | `|ts-vs| / max(|ts|, 1e-9)`, could reach −299 | symmetric denominator `|tr|+|va|`, clipped, max penalty 0.15 |
| TRAIN partition got zero warmup | `warm_lo = max(0, 0-300)` → no prefix for TRAIN | indicators computed once on the full warmup+DEV frame, every partition fully warm |
| evaluable window moved with `ema_period` | strategy skips `max(ema+10, 60)` of the passed slice | fixed 170-bar skip via `SkipHeadStrategy`, above the 160-bar maximum, identical for all candidates |
| Bollinger fitted on all of DEV at once | one combined TRAIN+VALID evaluation | TRAIN and VALID scored separately, both in the objective |
| incumbent enqueued in the only search | `enqueue_trial` in the single strategy+risk stage | no enqueue in stage 1; the seed enters stage 2 only, and it is V3's own seed, never C158 |
| flat gate sentinel (V2) | `-10.0` constant | graded `[-2, -1]` band |
| single fixed tick size | 0.01 for every symbol | per symbol: ETHUSDT 0.01, BTCUSDT 0.1; an undeclared symbol raises |
| exceptions swallowed into empty metrics | `try/except` → `empty_metrics()` | no blanket catch; a failure surfaces |

Preserved deliberately: production `BacktestEngine`, `BaselineRiskManager`,
`compute_all_indicators` and the Bollinger mathematics are untouched, so results stay comparable
to every earlier arm. LONG-only is hardcoded in `build_cfg` and is not a search dimension.
TPE seed 42, `n_jobs=1` throughout.
