"""Phase-A objective — one transparent, versioned scoring function.

The whole formula lives in this file. Nothing elsewhere adjusts a score.

`phase_a_score_v2` rewards signal quality, not sizing, and not raw return:

    score = 100*log1p(max(net_return_pct,0)/100)     bounded return credit
          +  25*min(shrunk_pf, 3.0)                   edge per unit of loss
          +  15*clamp(shrunk_sharpe, -3, 3)           consistency
          -   drawdown_penalty(max_dd_pct)              progressive above 20%
          +  20*min(trades/min_trades, 2.0)           sample adequacy, capped

Return enters through log1p so a candidate cannot buy rank with one outlier run.

SAMPLE-SIZE SHRINKAGE (the v1 -> v2 fix)
----------------------------------------
v1 capped profit factor at 3.0 and weighted it at 40, which made a candidate
sitting exactly on the minimum trade gate with a fluke PF beat a candidate with
five times the sample and four times the return. Audited profiles under v1:

    thin: 22 trades, PF 4.0, Sharpe 3.0, +15%   -> 188.98   (ranked 1st)
    rare: 22 trades, PF 12.0, Sharpe 2.0, +20%  -> 178.23   (ranked 2nd)
    broad: 100 trades, PF 1.8, Sharpe 1.2, +80% -> 158.78   (ranked 4th)

PF and Sharpe are the two metrics whose sampling error explodes on small trade
counts, so both are now shrunk toward their null value by sample size:

    shrunk_pf     = 1 + (pf - 1) * n / (n + k)
    shrunk_sharpe = sharpe * n / (n + k)          with k = min_trades

At n = k the excess is halved; by n = 4k it is ~80% credited. A large PF on a
small sample can no longer outrank a real edge measured over many trades. PF
weight drops 40 -> 25 and the sample term rises 10 -> 20 so breadth is rewarded
directly rather than only through the cap.

REJECTED outright (score = REJECTED_SCORE, never shortlisted):
  * no trades at all, or fewer than the history-aware minimum
  * a backtest that errored or produced an empty equity curve
  * profit factor below 1.0 — the candidate loses money per unit risked
  * max drawdown at or above 60% — unrunnable regardless of return
  * negative net return

Minimum trade count is derived from the length of the partition rather than fixed,
because a flat threshold either makes a 180-day campaign impossible or is
meaninglessly weak for a 3-year one. The rule: `MIN_TRADES_PER_MONTH` trades per
month of evaluation, with an absolute floor and ceiling. The same rule is applied
to VALIDATION to flag statistically thin finalists — as a flag only, never as a
signal fed back into the search.
"""

import math
from typing import Any, Dict, Optional

SCORE_VERSION = "phase_a_score_v2"

REJECTED_SCORE = -1e9

# Rejection thresholds.
MIN_PROFIT_FACTOR = 1.0
MAX_DRAWDOWN_PCT = 60.0

# History-aware minimum trade count, expressed the way a human reads it: a
# trades-per-month expectation plus an absolute floor. Four per month is modest
# for a 15m breakout strategy — it does not force high-frequency trading — while
# the floor keeps short windows statistically meaningful and the ceiling stops a
# multi-year campaign from demanding an implausible count.
MIN_TRADES_PER_MONTH = 4.0
DAYS_PER_MONTH = 30.44
MIN_TRADES_FLOOR = 20
MIN_TRADES_CEILING = 200

# VALIDATION is under half the length of TRAIN under the 56/24/20 policy, so applying
# TRAIN's absolute floor to it would demand roughly five times the trade RATE and
# flag almost every finalist as thin. The out-of-sample flag therefore scales the
# TRAIN requirement by duration, with its own much smaller floor. This is a
# reporting flag only — it never rejects a candidate and never reaches the search.
THIN_VALIDATION_FLOOR = 8

# Score weights.
W_RETURN = 100.0
W_PROFIT_FACTOR = 25.0
PF_CAP = 3.0
W_SHARPE = 15.0
SHARPE_CLAMP = 3.0
DD_FREE_PCT = 20.0
W_DD_PENALTY = 2.0
DD_STEEP_PCT = 35.0          # beyond this, the penalty grows quadratically
W_DD_STEEP = 0.2
W_SAMPLE = 20.0
SAMPLE_CAP = 2.0


def minimum_trades(duration_days: float) -> int:
    """Trades a candidate must produce over `duration_days` to be taken seriously.

    Same deterministic rule for every candidate and for both partitions.
    """
    expected = (duration_days / DAYS_PER_MONTH) * MIN_TRADES_PER_MONTH
    return int(max(MIN_TRADES_FLOOR, min(MIN_TRADES_CEILING, round(expected))))


def thin_validation_threshold(min_train_trades: int, train_days: float,
                              valid_days: float) -> int:
    """Trade count below which a finalist's VALIDATION result is statistically thin."""
    if train_days <= 0:
        return THIN_VALIDATION_FLOOR
    scaled = min_train_trades * (valid_days / train_days)
    return int(max(THIN_VALIDATION_FLOOR, round(scaled)))


def shrink(value: float, null_value: float, n: int, k: int) -> float:
    """Pull a small-sample statistic toward its null value.

    `k` is the sample size at which half the excess over `null_value` is credited.
    """
    if n <= 0:
        return null_value
    weight = n / float(n + max(1, k))
    return null_value + (value - null_value) * weight


def _clamp(value: float, limit: float) -> float:
    return max(-limit, min(limit, value))


def drawdown_penalty(dd_pct: float) -> float:
    """Linear above DD_FREE_PCT, quadratic above DD_STEEP_PCT."""
    linear = W_DD_PENALTY * max(0.0, dd_pct - DD_FREE_PCT)
    steep = W_DD_STEEP * max(0.0, dd_pct - DD_STEEP_PCT) ** 2
    return linear + steep


def rejection_reason(metrics: Optional[Dict[str, Any]], min_trades: int) -> Optional[str]:
    """Return why this candidate is unusable, or None if it is admissible."""
    if not metrics or metrics.get("error"):
        return "invalid_backtest"

    trades = int(metrics.get("trades", 0))
    if trades == 0:
        return "no_trades"
    if trades < min_trades:
        return f"too_few_trades({trades}<{min_trades})"

    if float(metrics.get("net_return_pct", 0.0)) < 0.0:
        return "negative_return"
    if float(metrics.get("profit_factor", 0.0)) < MIN_PROFIT_FACTOR:
        return "profit_factor_below_1"
    if float(metrics.get("max_dd_pct", 0.0)) >= MAX_DRAWDOWN_PCT:
        return "catastrophic_drawdown"
    return None


def phase_a_score_v2(metrics: Optional[Dict[str, Any]], min_trades: int) -> float:
    """Score a completed TRAIN backtest. Rejected candidates score REJECTED_SCORE."""
    if rejection_reason(metrics, min_trades) is not None:
        return REJECTED_SCORE

    ret = float(metrics["net_return_pct"])
    pf = float(metrics["profit_factor"])
    sharpe = float(metrics.get("sharpe", 0.0))
    dd = float(metrics.get("max_dd_pct", 0.0))
    trades = int(metrics["trades"])

    # PF and Sharpe are the small-sample-fragile terms; shrink both toward null.
    pf_eff = shrink(pf, 1.0, trades, min_trades)
    sharpe_eff = shrink(sharpe, 0.0, trades, min_trades)

    return_credit = W_RETURN * math.log1p(max(ret, 0.0) / 100.0)
    pf_credit = W_PROFIT_FACTOR * min(pf_eff, PF_CAP)
    sharpe_credit = W_SHARPE * _clamp(sharpe_eff, SHARPE_CLAMP)
    dd_penalty = drawdown_penalty(dd)
    sample_credit = W_SAMPLE * min(trades / float(min_trades), SAMPLE_CAP)

    return return_credit + pf_credit + sharpe_credit - dd_penalty + sample_credit


# Current scorer. Callers use this name; the version string identifies the rule.
phase_a_score = phase_a_score_v2


def describe(min_trades: int) -> Dict[str, Any]:
    """Serializable snapshot for the run manifest."""
    return {
        "version": SCORE_VERSION,
        "formula": ("W_RETURN*log1p(max(ret,0)/100) + W_PF*min(shrink(pf,1),3) "
                    "+ W_SHARPE*clamp(shrink(sharpe,0),3) "
                    "- [2.0*max(0,dd-20) + 0.2*max(0,dd-35)^2] "
                    "+ W_SAMPLE*min(trades/min_trades,2)"),
        "shrinkage": {"applied_to": ["profit_factor", "sharpe"],
                      "half_credit_at_n": "min_trades"},
        "weights": {
            "W_RETURN": W_RETURN, "W_PROFIT_FACTOR": W_PROFIT_FACTOR,
            "PF_CAP": PF_CAP, "W_SHARPE": W_SHARPE, "SHARPE_CLAMP": SHARPE_CLAMP,
            "DD_FREE_PCT": DD_FREE_PCT, "W_DD_PENALTY": W_DD_PENALTY,
            "DD_STEEP_PCT": DD_STEEP_PCT, "W_DD_STEEP": W_DD_STEEP,
            "W_SAMPLE": W_SAMPLE, "SAMPLE_CAP": SAMPLE_CAP,
        },
        "rejections": {
            "min_trades": min_trades,
            "min_profit_factor": MIN_PROFIT_FACTOR,
            "max_drawdown_pct": MAX_DRAWDOWN_PCT,
            "negative_return": True,
        },
        "min_trades_rule": {
            "trades_per_month": MIN_TRADES_PER_MONTH,
            "floor": MIN_TRADES_FLOOR,
            "ceiling": MIN_TRADES_CEILING,
        },
    }
