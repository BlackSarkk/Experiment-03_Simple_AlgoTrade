"""Stage [5/6] Bollinger filter — search space, score and ON/OFF gate.

Strategy and risk are frozen by the time this runs. The filter can only ever
REMOVE entry signals, so the question this stage answers is narrow and specific:

    does throwing away opportunities improve the frozen system enough to justify
    the opportunities thrown away?

OFF IS THE BASELINE, NOT THE FALLBACK
-------------------------------------
Every candidate's own Bollinger-OFF result is evaluated first, and every score is
expressed as a delta against it. `bollinger_score_v1` is constructed so OFF scores
exactly 0.0: a filter earns a positive score only by measurably improving PF,
Sharpe, drawdown or return, and loses points for blocking too much. A filter that
merely trades less is not rewarded. Concluding OFF for every candidate is a
legitimate outcome.

RECOVERED METHODOLOGY
---------------------
`src/filters/stage_1_bollinger/filter.py` is the live implementation and the only
source of truth. `compute_bollinger` attaches `bb_bandwidth` (band width as a
percentage of the middle band) and `bb_mid_dist` (distance from the middle band as
a fraction of full band width). `allow_mask` blocks a candle when bandwidth is
below `min_bandwidth_pct`, when bandwidth relative to `expansion_lookback` bars ago
is below `expansion_min_ratio`, or when mid-distance is below `min_mid_distance`.
Each condition is disabled by setting its threshold to 0.0, and warmup rows are
ALLOWED so the filter cannot manufacture a difference out of undefined indicators.

Neither `src/optimization/` nor its `backup/` ever searched Bollinger — the frozen
`config2` values (length 10, std 2.3, bandwidth 0.2, lookback 10, ratio 0.95,
mid-distance 0.15) were arrived at outside the recorded optimizer code. Ranges here
are therefore derived from the live semantics rather than inherited, and are
deliberately not centred on config2: at length 10 and mid-distance 0.15, config2
sits at the very bottom of two of the six ranges.
"""

from typing import Any, Dict, Optional

SPACE_VERSION = "bollinger_space_v1"
SCORE_VERSION = "bollinger_score_v1"
GATE_VERSION = "bollinger_gate_v1"

SEED = 42

# --- search space (live BollingerFilterConfig fields) -----------------------
# A threshold of 0.0 disables its condition, so the space contains genuine
# no-op filters; OFF is reachable from inside the search as well as outside it.
INT_PARAMS = {
    "length": (10, 50, 1),
    "expansion_lookback": (1, 20, 1),
}
FLOAT_PARAMS = {
    "std": (1.5, 3.0, 0.1),
    "min_bandwidth_pct": (0.0, 2.0, 0.05),
    "expansion_min_ratio": (0.0, 1.5, 0.05),
    "min_mid_distance": (0.0, 0.5, 0.05),
}
PARAM_NAMES = tuple(list(INT_PARAMS) + list(FLOAT_PARAMS))

# Never searched here — strategy and risk are frozen.
FORBIDDEN = (
    "ema_period", "rsi_period", "rsi_overbought", "rsi_oversold", "atr_period",
    "consolidation_candles", "consolidation_atr_mult", "swing_lookback",
    "volume_sma_period", "volume_mult", "risk_reward_ratio",
    "leverage", "risk_per_trade_pct", "max_position_allocation_pct",
)

# --- gate thresholds (bollinger_gate_v1) ------------------------------------
# Fixed before any candidate is evaluated.
MIN_SCORE = 5.0               # the filter must clear OFF by a real margin
MIN_TRADE_RETENTION = 0.50    # keeping under half the trades is not a filter
MAX_RETURN_SACRIFICE = 0.30   # may not give up more than 30% of OFF's return
MIN_VALID_PF_DELTA = -0.05    # VALIDATION PF must not deteriorate
MIN_VALID_RETURN_PCT = 0.0    # must still make money out of sample

# --- score weights (bollinger_score_v1) -------------------------------------
W_PF = 30.0
PF_DELTA_CAP = 1.0
W_SHARPE = 15.0
SHARPE_DELTA_CAP = 1.0
W_DD = 10.0
DD_DELTA_SCALE = 10.0         # a 10-percentage-point DD improvement = full credit
W_RETURN = 20.0               # relative to the OFF baseline's own return
W_RETENTION_PENALTY = 40.0
W_SAMPLE_PENALTY = 20.0


def suggest(trial) -> Dict[str, Any]:
    params = {}
    for name, (low, high, step) in INT_PARAMS.items():
        params[name] = trial.suggest_int(name, low, high, step=step)
    for name, (low, high, step) in FLOAT_PARAMS.items():
        params[name] = round(trial.suggest_float(name, low, high, step=step), 4)
    return params


def to_filter_dict(params: Dict[str, Any], enabled: bool = True) -> Dict[str, Any]:
    """Shape `BollingerFilterConfig.from_dict` expects."""
    return {"enabled": bool(enabled), **{k: params[k] for k in PARAM_NAMES}}


def is_noop(params: Dict[str, Any]) -> bool:
    """All three conditions disabled — mathematically identical to OFF."""
    return (float(params["min_bandwidth_pct"]) <= 0.0
            and float(params["expansion_min_ratio"]) <= 0.0
            and float(params["min_mid_distance"]) <= 0.0)


def _clamp(value, limit):
    return max(-limit, min(limit, value))


def deltas(on: Optional[Dict[str, Any]],
           off: Optional[Dict[str, Any]]) -> Dict[str, float]:
    """Filtered result minus its own OFF baseline. Drawdown delta is signed so
    positive always means 'better'."""
    if not on or not off:
        return {}
    off_trades = max(1, int(off.get("trades", 0)))
    off_return = float(off.get("net_return_pct", 0.0))
    return {
        "d_return_pct": float(on["net_return_pct"]) - off_return,
        "d_return_rel": ((float(on["net_return_pct"]) - off_return) / abs(off_return)
                         if abs(off_return) > 1e-9 else 0.0),
        "d_profit_factor": float(on["profit_factor"]) - float(off["profit_factor"]),
        "d_sharpe": float(on.get("sharpe", 0.0)) - float(off.get("sharpe", 0.0)),
        "d_max_dd_pct": float(off["max_dd_pct"]) - float(on["max_dd_pct"]),
        "d_trades": int(on["trades"]) - int(off["trades"]),
        "trade_retention": int(on["trades"]) / off_trades,
    }


def bollinger_score_v1(on: Optional[Dict[str, Any]], off: Optional[Dict[str, Any]],
                       min_trades: int) -> float:
    """Score a filter against its own OFF baseline. OFF itself scores exactly 0.0."""
    if not on or not off:
        return -1e9
    d = deltas(on, off)
    if not d:
        return -1e9

    pf = W_PF * _clamp(d["d_profit_factor"], PF_DELTA_CAP)
    sharpe = W_SHARPE * _clamp(d["d_sharpe"], SHARPE_DELTA_CAP)
    dd = W_DD * _clamp(d["d_max_dd_pct"] / DD_DELTA_SCALE, 1.0)
    ret = W_RETURN * _clamp(d["d_return_rel"], 1.0)

    # Blocking is only ever a cost. Retention at or above the floor costs nothing;
    # below it the penalty ramps to the full weight at zero retention.
    shortfall = max(0.0, MIN_TRADE_RETENTION - d["trade_retention"])
    retention_penalty = W_RETENTION_PENALTY * (shortfall / MIN_TRADE_RETENTION)

    # A filter whose quality comes from almost never trading is worthless.
    trades = int(on["trades"])
    sample_penalty = (W_SAMPLE_PENALTY * (1.0 - trades / float(min_trades))
                      if trades < min_trades else 0.0)

    return pf + sharpe + dd + ret - retention_penalty - sample_penalty


def gate_failures(on_train, off_train, on_valid, off_valid,
                  score: float, min_trades: int) -> list:
    """Reasons this filter must not replace OFF. Empty list means Bollinger ON."""
    if not on_train or not off_train:
        return ["invalid_backtest"]

    out = []
    d_train = deltas(on_train, off_train)

    if score < MIN_SCORE:
        out.append(f"improvement_too_small({score:.2f}<{MIN_SCORE})")
    if d_train["trade_retention"] < MIN_TRADE_RETENTION:
        out.append(f"blocks_too_many_trades(retention "
                   f"{d_train['trade_retention']:.0%}<{MIN_TRADE_RETENTION:.0%})")
    if int(on_train["trades"]) < min_trades:
        out.append(f"filtered_sample_too_small({on_train['trades']}<{min_trades})")
    if d_train["d_return_rel"] < -MAX_RETURN_SACRIFICE:
        out.append(f"return_sacrifice({d_train['d_return_rel']:.0%}"
                   f"<-{MAX_RETURN_SACRIFICE:.0%})")

    if not on_valid or not off_valid:
        out.append("no_validation_result")
        return out

    d_valid = deltas(on_valid, off_valid)
    if d_valid["d_profit_factor"] < MIN_VALID_PF_DELTA:
        out.append(f"validation_pf_deteriorates({d_valid['d_profit_factor']:+.2f})")
    if float(on_valid["net_return_pct"]) <= MIN_VALID_RETURN_PCT:
        out.append(f"validation_return({on_valid['net_return_pct']:.2f}%<=0)")
    return out


def describe() -> Dict[str, Any]:
    return {
        "space_version": SPACE_VERSION,
        "score_version": SCORE_VERSION,
        "gate_version": GATE_VERSION,
        "seed": SEED,
        "dimensions": list(PARAM_NAMES),
        "ranges": {
            **{k: {"low": v[0], "high": v[1], "step": v[2], "type": "int"}
               for k, v in INT_PARAMS.items()},
            **{k: {"low": v[0], "high": v[1], "step": v[2], "type": "float"}
               for k, v in FLOAT_PARAMS.items()},
        },
        "off_baseline_scores": 0.0,
        "forbidden_fields": list(FORBIDDEN),
        "gate_thresholds": {
            "min_score": MIN_SCORE,
            "min_trade_retention": MIN_TRADE_RETENTION,
            "max_return_sacrifice": MAX_RETURN_SACRIFICE,
            "min_valid_pf_delta": MIN_VALID_PF_DELTA,
            "min_valid_return_pct": MIN_VALID_RETURN_PCT,
        },
        "score_weights": {
            "W_PF": W_PF, "PF_DELTA_CAP": PF_DELTA_CAP, "W_SHARPE": W_SHARPE,
            "SHARPE_DELTA_CAP": SHARPE_DELTA_CAP, "W_DD": W_DD,
            "DD_DELTA_SCALE": DD_DELTA_SCALE, "W_RETURN": W_RETURN,
            "W_RETENTION_PENALTY": W_RETENTION_PENALTY,
            "W_SAMPLE_PENALTY": W_SAMPLE_PENALTY,
        },
        "uses_unseen": False,
    }
