"""Phase-A search space — the single definition of what the strategy search may vary.

Every name below is a real field on `common.config.StrategyConfig` and is read by
`BaselineStrategy` / `compute_all_indicators`. Nothing here is invented, and
nothing outside this module proposes a parameter.

Ranges are inherited from the historical optimizers
(`src/optimization/deep_15m_optimizer.py` and `multi_tf_optimizer.py`), which are
the only measured precedent this project has. Where the two disagree the wider
bound is taken, so the new search is never narrower than the one that found
Candidate #158:

    ema_period              10..200   (deep: 10..100, multi_tf: 10..200)
    rsi_period               7..35    (deep: 7..35,   multi_tf: 7..21)
    rsi_overbought          55..80    (deep: 60..80,  multi_tf: 55..80)
    rsi_oversold            20..45    (deep: 20..40,  multi_tf: 20..45)
    atr_period               7..35    (deep: 7..35,   multi_tf: 7..21)
    consolidation_candles    4..15
    consolidation_atr_mult   1.0..4.0
    swing_lookback           3..20
    volume_sma_period       10..50
    volume_mult              0.5..2.5 (deep: 0.5..2.5, multi_tf: 0.5..2.0)
    risk_reward_ratio        1.0..5.0 (deep: 1.0..5.0, multi_tf: 1.0..4.0)

Deliberately ABSENT: leverage, risk_per_trade_pct, max_position_allocation_pct.
The historical optimizers sampled those in the same study as the strategy
parameters, which let a candidate out-rank a better signal purely by sizing more
aggressively. They belong to stage [4/6]; Phase A is sizing-neutral.

Also absent: `use_trend_filter`, `trend_ema_period`, `use_ema_slope_filter`,
`use_breakeven_at_1r`, `pyramiding`. These exist on `StrategyConfig` but `main.py`
does not read them from preset JSON, so a value found for them could not be
expressed in a runnable config.
"""

from typing import Any, Dict

# name -> (kind, low, high, step)
INT_PARAMS = {
    "ema_period":            (10, 200, 1),
    "rsi_period":            (7, 35, 1),
    "atr_period":            (7, 35, 1),
    "consolidation_candles": (4, 15, 1),
    "swing_lookback":        (3, 20, 1),
    "volume_sma_period":     (10, 50, 1),
}

FLOAT_PARAMS = {
    "rsi_overbought":         (55.0, 80.0, 1.0),
    "rsi_oversold":           (20.0, 45.0, 1.0),
    "consolidation_atr_mult": (1.0, 4.0, 0.1),
    "volume_mult":            (0.5, 2.5, 0.1),
    "risk_reward_ratio":      (1.0, 5.0, 0.1),
}

PARAM_NAMES = tuple(list(INT_PARAMS) + list(FLOAT_PARAMS))

# Never searched in Phase A — asserted by tests.
FORBIDDEN_IN_PHASE_A = (
    "leverage", "risk_per_trade_pct", "max_position_allocation_pct",
    "initial_capital", "quantity_step", "commission_pct", "slippage_ticks",
)


def suggest(trial) -> Dict[str, Any]:
    """Draw one strategy parameter set from an Optuna trial."""
    params = {}
    for name, (low, high, step) in INT_PARAMS.items():
        params[name] = trial.suggest_int(name, low, high, step=step)
    for name, (low, high, step) in FLOAT_PARAMS.items():
        params[name] = trial.suggest_float(name, low, high, step=step)
    return params


def is_coherent(params: Dict[str, Any]) -> bool:
    """Cheap structural rejection before a backtest is ever run.

    An oversold level at or above the overbought level is not a strategy, it is a
    contradiction; running the engine on it wastes a trial.
    """
    return float(params["rsi_oversold"]) < float(params["rsi_overbought"])


def describe() -> Dict[str, Any]:
    """Serializable snapshot for the run manifest."""
    return {
        "int_params": {k: {"low": v[0], "high": v[1], "step": v[2]}
                       for k, v in INT_PARAMS.items()},
        "float_params": {k: {"low": v[0], "high": v[1], "step": v[2]}
                         for k, v in FLOAT_PARAMS.items()},
        "excluded_risk_params": list(FORBIDDEN_IN_PHASE_A),
    }
