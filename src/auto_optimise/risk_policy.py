"""Stage [4/6] risk policy — search space, gate and score.

Strategy parameters are frozen by the time this runs. Only the three policy inputs
the live `RiskConfig` actually exposes to sizing are searched:

    leverage, risk_per_trade_pct, max_position_allocation_pct

THE SIZING MECHANIC (verified against `BaselineRiskManager.calculate_position`)
-------------------------------------------------------------------------------
    raw_size   = equity * risk_per_trade_pct / |entry - sl|
    max_margin = equity * max_position_allocation_pct
    max_size   = (max_margin * leverage) / entry
    qty        = floor_to_step(min(raw_size, max_size))
    margin     = qty * entry / leverage        (must be <= equity and <= max_margin)

Leverage never enlarges `raw_size`. It only widens the allocation ceiling, and it
lowers the margin a position consumes. So the policy behaves in two regimes:

  * ALLOCATION-BOUND — the cap is smaller than the risk budget. More leverage
    (or more allocation) increases position size; risk_per_trade_pct is inert.
  * RISK-BOUND — the risk budget is smaller. More leverage changes nothing at
    all; only risk_per_trade_pct moves size.

Measured at equity 10,000 with a 2% stop: leverage 1.0 / risk 1.5% / allocation
50% is ALLOCATION-BOUND (cap $5,000 notional vs a $7,500 risk budget). The neutral
Phase-A policy was therefore capped, not risk-sized — which is exactly why this
stage exists. Because leverage saturates once the risk budget binds, the search
cannot manufacture return from leverage indefinitely.

RECOVERED FROM THE CANDIDATE #158 WORKFLOW (`deep_15m_optimizer.stage5_risk`)
-----------------------------------------------------------------------------
Retained: the three dimensions and their ranges — risk 0.5-3.0% step 0.1,
leverage 1.0-5.0 step 0.5, allocation 20-100% step 10 — and the idea of keeping
distinct conservative / balanced / aggressive profiles rather than one winner.

Changed, with reasons:
  * Legacy optimized on `self.df`, the FULL frame including the holdout. That is
    straightforward leakage: the risk policy was tuned on the data later used to
    validate it. Stage 4 searches TRAIN only and screens on VALIDATION.
  * Legacy maximised raw `net_return_pct` with a cliff at DD > 35%. Below the
    cliff it was pure return maximisation, which always drifts to the most
    aggressive sizing the cap allows. The score here is efficiency-based and the
    drawdown penalty is continuous.
  * Legacy filtered `dd < 50` after the fact and then took the highest-return row
    as "aggressive". Selection is now a gate plus a score, both fixed in advance.
  * Legacy ran `n_jobs=4` under a seeded TPE sampler, so its results were not
    reproducible. Stage 4 runs single-threaded.
"""

import math
from typing import Any, Dict, Optional

GATE_VERSION = "risk_gate_v1"
SCORE_VERSION = "risk_score_v1"
SPACE_VERSION = "risk_space_v1"

SEED = 42

# --- search space (recovered ranges) ---------------------------------------
LEVERAGE = (1.0, 5.0, 0.5)
RISK_PER_TRADE_PCT = (0.5, 3.0, 0.1)          # percent, converted to a fraction
MAX_ALLOCATION_PCT = (20.0, 100.0, 10.0)      # percent, converted to a fraction

PARAM_NAMES = ("leverage", "risk_per_trade_pct", "max_position_allocation_pct")

# Fields Stage 4 must never touch — the strategy is frozen.
FROZEN_STRATEGY_FIELDS = (
    "ema_period", "rsi_period", "rsi_overbought", "rsi_oversold", "atr_period",
    "consolidation_candles", "consolidation_atr_mult", "swing_lookback",
    "volume_sma_period", "volume_mult", "risk_reward_ratio",
    "long_enabled", "short_enabled",
)

# --- gate thresholds (risk_gate_v1) ----------------------------------------
# Stricter than the Phase-A strategy gate on purpose: Phase A was choosing an
# edge, this stage is choosing sizing someone would actually deploy.
MAX_DRAWDOWN_PCT = 40.0
MIN_PROFIT_FACTOR = 1.05
MIN_RETURN_PCT = 0.0
# A sizing policy that starts getting entries rejected (margin, allocation cap or
# minimum order size) is silently trading a different strategy. Losing more than
# a quarter of the neutral-policy entries is disqualifying.
MIN_TRADE_RETENTION = 0.75

# --- score weights (risk_score_v1) -----------------------------------------
# Capital efficiency dominates: return per unit of drawdown, capped. Raw return
# carries a deliberately small, log-scaled weight so it breaks ties between
# similarly efficient policies without being able to buy rank with leverage.
DD_FLOOR_PCT = 2.0        # keeps the ratio finite for near-zero-drawdown runs
EFFICIENCY_CAP = 6.0
W_EFFICIENCY = 15.0       # <= 90
W_PROFIT_FACTOR = 20.0    # <= 20
PF_EXCESS_CAP = 1.0
W_SHARPE = 10.0           # <= 30
SHARPE_CLAMP = 3.0
W_RETURN = 15.0           # ~10 at +100%
DD_FREE_PCT = 20.0
W_DD_LINEAR = 1.5
DD_STEEP_PCT = 30.0
W_DD_STEEP = 0.15


def suggest(trial) -> Dict[str, float]:
    """Draw one risk policy. Identical space for every strategy candidate."""
    lo, hi, step = LEVERAGE
    leverage = trial.suggest_float("leverage", lo, hi, step=step)
    lo, hi, step = RISK_PER_TRADE_PCT
    risk_pct = trial.suggest_float("risk_per_trade_pct", lo, hi, step=step)
    lo, hi, step = MAX_ALLOCATION_PCT
    alloc = trial.suggest_float("max_position_allocation_pct", lo, hi, step=step)
    return {
        "leverage": round(leverage, 4),
        "risk_per_trade_pct": round(risk_pct, 4),
        "max_position_allocation_pct": round(alloc, 4),
    }


def as_fractions(policy: Dict[str, float]) -> Dict[str, float]:
    """Percent inputs -> the fractions `RiskConfig` expects."""
    return {
        "leverage": float(policy["leverage"]),
        "risk_per_trade_pct": float(policy["risk_per_trade_pct"]) / 100.0,
        "max_position_allocation_pct": float(policy["max_position_allocation_pct"]) / 100.0,
    }


def _finite(*values) -> bool:
    return all(v is not None and isinstance(v, (int, float))
               and math.isfinite(float(v)) for v in values)


def gate_failures(metrics: Optional[Dict[str, Any]], min_trades: int,
                  neutral_trades: Optional[int] = None) -> list:
    """Every reason this risk policy is unusable. Empty list means PASS."""
    if not metrics:
        return ["invalid_backtest"]

    if not _finite(metrics.get("net_return_pct"), metrics.get("profit_factor"),
                   metrics.get("max_dd_pct"), metrics.get("sharpe")):
        return ["non_finite_metrics"]

    out = []
    trades = int(metrics.get("trades", 0))
    if trades == 0:
        out.append("no_trades")
    elif trades < min_trades:
        out.append(f"too_few_trades({trades}<{min_trades})")

    if neutral_trades:
        retention = trades / float(neutral_trades)
        if retention < MIN_TRADE_RETENTION:
            out.append(f"entries_rejected_by_sizing({retention:.0%}"
                       f"<{MIN_TRADE_RETENTION:.0%})")

    dd = float(metrics.get("max_dd_pct", 0.0))
    if dd >= MAX_DRAWDOWN_PCT:
        out.append(f"drawdown({dd:.1f}%>={MAX_DRAWDOWN_PCT}%)")
    if float(metrics.get("net_return_pct", 0.0)) <= MIN_RETURN_PCT:
        out.append("no_capital_growth")
    if float(metrics.get("profit_factor", 0.0)) < MIN_PROFIT_FACTOR:
        out.append(f"profit_factor({metrics.get('profit_factor'):.2f}"
                   f"<{MIN_PROFIT_FACTOR})")
    return out


def _clamp(value, limit):
    return max(-limit, min(limit, value))


def drawdown_penalty(dd_pct: float) -> float:
    return (W_DD_LINEAR * max(0.0, dd_pct - DD_FREE_PCT)
            + W_DD_STEEP * max(0.0, dd_pct - DD_STEEP_PCT) ** 2)


def efficiency(ret_pct: float, dd_pct: float) -> float:
    """Return per unit of drawdown, capped. The core of the score."""
    return min(max(ret_pct, 0.0) / max(dd_pct, DD_FLOOR_PCT), EFFICIENCY_CAP)


def risk_score_v1(metrics: Optional[Dict[str, Any]], min_trades: int,
                  neutral_trades: Optional[int] = None) -> float:
    """Score a risk policy on TRAIN. Gated-out policies score 0."""
    if gate_failures(metrics, min_trades, neutral_trades):
        return 0.0

    ret = float(metrics["net_return_pct"])
    dd = float(metrics["max_dd_pct"])
    pf = float(metrics["profit_factor"])
    sharpe = float(metrics.get("sharpe", 0.0))

    eff = W_EFFICIENCY * efficiency(ret, dd)
    pf_credit = W_PROFIT_FACTOR * max(0.0, min(pf - 1.0, PF_EXCESS_CAP))
    sharpe_credit = W_SHARPE * _clamp(sharpe, SHARPE_CLAMP)
    return_credit = W_RETURN * math.log1p(max(ret, 0.0) / 100.0)

    return eff + pf_credit + sharpe_credit + return_credit - drawdown_penalty(dd)


def classify(train_metrics, valid_metrics, min_valid_trades: int) -> str:
    """Deterministic out-of-sample verdict for a risk policy."""
    if not valid_metrics or valid_metrics.get("trades", 0) == 0:
        return "NO_TRADES"
    if valid_metrics["trades"] < min_valid_trades:
        return "THIN"
    if valid_metrics["net_return_pct"] <= 0 or valid_metrics["profit_factor"] < 1.0:
        return "COLLAPSES"
    train_pf = float((train_metrics or {}).get("profit_factor") or 0.0)
    if train_pf > 0 and valid_metrics["profit_factor"] < train_pf * 0.6:
        return "DEGRADES"
    return "GENERALIZES"


def describe() -> Dict[str, Any]:
    return {
        "space_version": SPACE_VERSION,
        "gate_version": GATE_VERSION,
        "score_version": SCORE_VERSION,
        "seed": SEED,
        "dimensions": list(PARAM_NAMES),
        "ranges": {
            "leverage": {"low": LEVERAGE[0], "high": LEVERAGE[1], "step": LEVERAGE[2]},
            "risk_per_trade_pct": {"low": RISK_PER_TRADE_PCT[0],
                                   "high": RISK_PER_TRADE_PCT[1],
                                   "step": RISK_PER_TRADE_PCT[2]},
            "max_position_allocation_pct": {"low": MAX_ALLOCATION_PCT[0],
                                            "high": MAX_ALLOCATION_PCT[1],
                                            "step": MAX_ALLOCATION_PCT[2]},
        },
        "frozen_strategy_fields": list(FROZEN_STRATEGY_FIELDS),
        "gate_thresholds": {
            "max_drawdown_pct": MAX_DRAWDOWN_PCT,
            "min_profit_factor": MIN_PROFIT_FACTOR,
            "min_return_pct": MIN_RETURN_PCT,
            "min_trade_retention": MIN_TRADE_RETENTION,
        },
        "score_weights": {
            "W_EFFICIENCY": W_EFFICIENCY, "EFFICIENCY_CAP": EFFICIENCY_CAP,
            "DD_FLOOR_PCT": DD_FLOOR_PCT, "W_PROFIT_FACTOR": W_PROFIT_FACTOR,
            "PF_EXCESS_CAP": PF_EXCESS_CAP, "W_SHARPE": W_SHARPE,
            "SHARPE_CLAMP": SHARPE_CLAMP, "W_RETURN": W_RETURN,
            "DD_FREE_PCT": DD_FREE_PCT, "W_DD_LINEAR": W_DD_LINEAR,
            "DD_STEEP_PCT": DD_STEEP_PCT, "W_DD_STEEP": W_DD_STEEP,
        },
        "uses_unseen": False,
    }
