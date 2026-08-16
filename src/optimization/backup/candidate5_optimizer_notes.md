# Candidate #5 — legacy optimizer notes

Parameters, recovered verbatim from `run_candidate5_robustness.py:33-50`:

```
ema_period 51 | rsi_period 21 | rsi_overbought 65.0 | rsi_oversold 45.0
atr_period 21 | consolidation_candles 8 | consolidation_atr_mult 2.8
swing_lookback 12 | volume_sma_period 20 | use_volume_filter True
volume_mult 1.6 | risk_reward_ratio 3.0
use_ema_slope_filter False | use_trend_filter False
long_enabled True | short_enabled False
```

Risk policy hardcoded at `run_candidate5_robustness.py:65-66`:
`leverage = 3.5`, `risk_per_trade_pct = 0.015`.

## Why this workflow is not comparable to Phase 3 without a rerun

The legacy optimizer differs from the current one in ways that change results:

- `multi_tf_optimizer_old.py` SAMPLED `leverage`, `risk_per_trade_pct` and
  `max_position_allocation_pct` as search dimensions (`suggest_params`), and also
  searched `side_choice` (both / long_only / short_only) in the same study.
- Ranking used `robust_score()`, a hand-weighted dollar-return objective
  (`ret*0.3 + PF*15 - dd*1.5 + sharpe*10 + wr*20 + ...`), not the sizing-neutral
  expectancy_R / PF / Sharpe score used in Phase 3.
- Split was 50 / 25 / 25; Phase 3 uses 60 / 20 / 20.
- It ran against the PRE-Phase-2 RiskManager (notional-based allocation cap,
  silent 1% SL substitution, `round(qty, 4)`, no margin guard) — see
  `src/risk_management/backup/`.
- No evaluation-window slicing existed, so the effective date range was whatever
  the cache happened to contain.

Candidate #5 has since been re-benchmarked on the CURRENT engine over the Phase-3
partitions; those figures live in the session record, not here.
