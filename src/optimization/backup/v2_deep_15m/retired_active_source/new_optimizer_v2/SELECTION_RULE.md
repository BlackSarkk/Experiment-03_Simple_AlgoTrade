# New Optimizer V2 — selection rules, fixed BEFORE any run

Recorded ahead of execution. Not tuned against results; not changed afterwards.

## Stage A — strategy + risk (300 trials, TPE seed 42, n_jobs=1, unseeded)

**Hard gate** (fails → score `-10.0`, trial cannot be selected):

    tr_trades >= 100          va_trades >= 40
    tr_return_pct > 0         va_return_pct > 0
    va_profit_factor >= 1.10  va_max_dd_pct <= 35.0

**Score** (only for trials passing the gate):

    consistency_gap = clip(|tr_ret - va_ret| / max(|tr_ret|, 1e-9), 0, 2)

    score = 0.55 * clip(va_ret / 100, -1.0, 1.5)      # VALID return, credit caps at +150%
          + 0.20 * clip(va_pf - 1.0,   0.0, 1.0)      # VALID edge,   credit caps at PF 2.0
          + 0.15 * clip(tr_ret / 100, -1.0, 1.0)      # TRAIN return, credit CAPS AT +100%
          + 0.10 * clip(tr_pf - 1.0,   0.0, 1.0)
          - 0.50 * max(0, va_max_dd/100 - 0.20)       # VALID drawdown above 20%
          - 0.30 * max(0, tr_max_dd/100 - 0.25)       # TRAIN drawdown above 25%
          - 0.40 * consistency_gap

**Why TRAIN return cannot dominate.** The TRAIN return term is clipped at 1.0, so its maximum
contribution is **0.15** no matter how large TRAIN return grows — +193% and +1000% score
identically on that term. VALID terms contribute up to **0.825 + 0.20 = 1.025**, and VALID
drawdown/consistency can subtract without bound. A candidate with enormous TRAIN return and a weak
VALID profit factor or a deep VALID drawdown therefore cannot out-rank a candidate that generalises,
and one with `va_pf < 1.10` or `va_max_dd > 35%` is rejected outright regardless of TRAIN.

This is the specific defect being corrected: the recovered Scenario-4 objective was
`0.70*TRAIN_ret + 0.30*VALID_ret + …`, uncapped, which selected the member of its region with the
**lowest VALID profit factor (1.157)** and the **deepest VALID drawdown (29.68%)**.

**Winner** = highest score among gated trials. Deterministic; ties broken by lower trial number.

## Stage B — Bollinger (150 trials, TPE seed 42, n_jobs=1, strategy + risk frozen)

Search space (identical to the recovered recipe, so the arms differ only in selection):
`length 10-50 · std 1.5-3.0 s0.1 · min_bandwidth_pct 0.0-6.0 s0.1 · expansion_lookback 2-20 ·
expansion_min_ratio 0.0-1.6 s0.05 · min_mid_distance 0.0-0.45 s0.01`

**Hard gate** (fails → `-10.0`): `va_on.trades >= 25` and `va_on.trades / va_off.trades >= 0.40`
and `tr_on.trades >= 50`.

**Score:**

    score = 0.45 * clip(va_pf_on - va_pf_off, -0.5, 0.8) / 0.8
          + 0.25 * clip(1 - va_gross_loss_on / va_gross_loss_off, -0.5, 0.8) / 0.8
          + 0.15 * clip((va_netpnl_on - va_netpnl_off) / max(|va_netpnl_off|, 1.0), -1, 1)
          + 0.10 * clip(tr_pf_on - tr_pf_off, -0.5, 0.8) / 0.8
          + 0.05 * clip((va_dd_off - va_dd_on) / max(va_dd_off, 1e-9), -1, 1)
          - 0.30 * max(0, 0.60 - va_trades_on/va_trades_off) / 0.60

Scored on TRAIN and VALID **separately** — the recovered recipe scored its Bollinger stage on the
whole DEV span at once, which cannot tell an in-sample filter from a generalising one.

**Winner** = highest score. **If no trial passes the gate, V2 ships Bollinger DISABLED** and reports
that outcome rather than forcing a filter.

## Fixed architecture guarantees

Single symbol, single timeframe per campaign · long-only, direction not searched · indicators
computed ONCE on the full warmup+DEV frame per trial and sliced by index, never recomputed on an
already-sliced partition · TPE seed 42, `n_jobs=1` · no incumbent enqueued or seeded · no holdout or
unseen partition exists in the frame at all (the data stops at 2026-07-15 23:45) · production
`BacktestEngine`, `BaselineRiskManager`, `compute_all_indicators`, fees 0.05%, slippage 1 tick,
quantity step 0.001, tick size 0.01 — identical to the recovered arm · 14-dimensional search space
identical to Scenario 4.
