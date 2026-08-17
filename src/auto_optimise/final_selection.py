"""Stage [6/6] decision rules — gate, Pareto filter, MCDM rank, UNSEEN verdict.

Every threshold and weight in this file is declared before UNSEEN is ever opened,
and none of them reads an UNSEEN field. The champion is chosen from TRAIN,
VALIDATION and the Stage 3-5 evidence alone; UNSEEN only confirms or rejects it.

    final_gate_v1   hard safety/quality floor — a candidate that fails cannot win
    pareto_v1       dominance filter over five genuinely different properties
    final_rank_v1   TOPSIS over the non-dominated set
    unseen_confirmation_v1   CONFIRMED / DEGRADED / FAILED, plus fallback policy

WHY THESE FIVE PARETO DIMENSIONS
--------------------------------
Stages 2-5 produce dozens of columns, most of which are restatements of return.
Counting return five times would make Pareto meaningless, so each dimension
answers a different question:

    profitability     VALIDATION profit factor — is there an out-of-sample edge?
    generalization    VALID PF / TRAIN PF      — how much of the edge survives?
    robustness        stage-3 robustness score — does it hold under perturbation
                                                 and across market regimes?
    risk_quality      -VALIDATION max drawdown — what does holding it feel like?
    support           VALIDATION trades        — is any of this measurable?

All five are oriented so higher is better.

WHY TOPSIS
----------
It is deterministic, has no hyperparameters beyond the declared weights, needs no
third-party package, and produces an auditable closeness score per candidate:
each dimension is min-max normalised across the surviving set, weighted, and the
candidate closest to the ideal point (and furthest from the anti-ideal) wins.
Ties break on the pre-existing Stage-5 order, so the ranking is total.
"""

import math
from typing import Any, Dict, List, Optional, Sequence

GATE_VERSION = "final_gate_v1"
PARETO_VERSION = "pareto_v1"
RANK_VERSION = "final_rank_v1"
CONFIRMATION_VERSION = "unseen_confirmation_v1"

# --- final_gate_v1 ----------------------------------------------------------
MIN_VALID_PROFIT_FACTOR = 1.10
MIN_VALID_RETURN_PCT = 0.0
MAX_VALID_DRAWDOWN_PCT = 30.0
MIN_ROBUSTNESS_SCORE = 0.0        # > 0 means it passed stage 3's own gate
MIN_RISK_SCORE = 0.0              # > 0 means it passed stage 4's own gate

# --- pareto_v1 --------------------------------------------------------------
PARETO_DIMENSIONS = ("profitability", "generalization", "robustness",
                     "risk_quality", "support")

# --- final_rank_v1 (TOPSIS weights, must sum to 1) --------------------------
WEIGHTS = {
    "profitability": 0.25,
    "generalization": 0.20,
    "robustness": 0.25,
    "risk_quality": 0.15,
    "support": 0.15,
}

# --- unseen_confirmation_v1 -------------------------------------------------
# Compared against the candidate's own VALIDATION behaviour, not against a fixed
# return target: UNSEEN is a shorter window and a lower absolute return there is
# expected. Sample size is accounted for by scaling the trade floor.
CONFIRM_MIN_PF = 1.05
CONFIRM_MIN_RETURN_PCT = 0.0
CONFIRM_MAX_DD_PCT = 40.0
CONFIRM_MAX_DD_RATIO = 2.0        # vs its own VALIDATION drawdown
CONFIRM_MIN_TRADE_RATIO = 0.40    # vs VALIDATION trades, scaled by window length
FAIL_PF = 0.90
FAIL_RETURN_PCT = -10.0

# Predetermined fallback policy. Declared here, before UNSEEN is opened.
FALLBACK_POLICY = {
    "CONFIRMED": "accept",
    "DEGRADED": "accept_with_warning",
    "FAILED": "try_next_fallback",
}


def gate_failures(c: Dict[str, Any], min_valid_trades: int) -> List[str]:
    """Hard floor. A candidate failing any of these cannot be champion."""
    out = []
    if _f(c.get("valid_profit_factor")) < MIN_VALID_PROFIT_FACTOR:
        out.append(f"validation_pf({_f(c.get('valid_profit_factor')):.2f}"
                   f"<{MIN_VALID_PROFIT_FACTOR})")
    if _f(c.get("valid_net_return_pct")) <= MIN_VALID_RETURN_PCT:
        out.append("validation_return_not_positive")
    if _f(c.get("valid_max_dd_pct")) > MAX_VALID_DRAWDOWN_PCT:
        out.append(f"validation_drawdown({_f(c.get('valid_max_dd_pct')):.1f}%"
                   f">{MAX_VALID_DRAWDOWN_PCT}%)")
    if int(_f(c.get("valid_trades"))) < min_valid_trades:
        out.append(f"validation_sample({int(_f(c.get('valid_trades')))}"
                   f"<{min_valid_trades})")
    if _f(c.get("robustness_score")) <= MIN_ROBUSTNESS_SCORE:
        out.append("failed_stage3_robustness")
    if _f(c.get("risk_score")) <= MIN_RISK_SCORE:
        out.append("failed_stage4_risk")
    return out


def _f(value, default=0.0) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError):
        return default


def dimensions(c: Dict[str, Any]) -> Dict[str, float]:
    """Project a candidate onto the five Pareto dimensions. Higher is better."""
    train_pf = _f(c.get("train_profit_factor"))
    valid_pf = _f(c.get("valid_profit_factor"))
    return {
        "profitability": valid_pf,
        "generalization": (valid_pf / train_pf) if train_pf > 0 else 0.0,
        "robustness": _f(c.get("robustness_score")),
        "risk_quality": -_f(c.get("valid_max_dd_pct")),
        "support": _f(c.get("valid_trades")),
    }


def dominates(a: Dict[str, float], b: Dict[str, float]) -> bool:
    """`a` dominates `b`: at least as good everywhere, strictly better somewhere."""
    at_least = all(a[d] >= b[d] for d in PARETO_DIMENSIONS)
    strictly = any(a[d] > b[d] for d in PARETO_DIMENSIONS)
    return at_least and strictly


def pareto_front(candidates: Sequence[Dict[str, Any]]) -> List[int]:
    """Indices of the non-dominated candidates, in input order."""
    points = [dimensions(c) for c in candidates]
    front = []
    for i, pi in enumerate(points):
        if not any(dominates(pj, pi) for j, pj in enumerate(points) if j != i):
            front.append(i)
    return front


def topsis(candidates: Sequence[Dict[str, Any]],
           weights: Optional[Dict[str, float]] = None) -> List[float]:
    """Closeness-to-ideal score per candidate, in [0, 1]. Deterministic."""
    weights = weights or WEIGHTS
    if not candidates:
        return []
    points = [dimensions(c) for c in candidates]
    if len(points) == 1:
        return [1.0]

    # Min-max normalisation per dimension. A dimension with no spread carries no
    # information, so every candidate scores the same on it rather than 0 or 1.
    norm = []
    for d in PARETO_DIMENSIONS:
        values = [p[d] for p in points]
        lo, hi = min(values), max(values)
        span = hi - lo
        norm.append([0.5 if span <= 1e-12 else (v - lo) / span for v in values])

    scores = []
    for i in range(len(points)):
        best = math.sqrt(sum(weights[d] * (1.0 - norm[k][i]) ** 2
                             for k, d in enumerate(PARETO_DIMENSIONS)))
        worst = math.sqrt(sum(weights[d] * (norm[k][i] - 0.0) ** 2
                              for k, d in enumerate(PARETO_DIMENSIONS)))
        scores.append(0.0 if (best + worst) <= 1e-12 else worst / (best + worst))
    return scores


def rank(candidates: Sequence[Dict[str, Any]], min_valid_trades: int
         ) -> Dict[str, Any]:
    """Gate -> Pareto -> TOPSIS. Returns the frozen ordering. No UNSEEN input."""
    evaluated = []
    for i, c in enumerate(candidates):
        failures = gate_failures(c, min_valid_trades)
        evaluated.append({"index": i, "candidate": c, "gate_failures": failures,
                          "passed_gate": not failures,
                          "dimensions": dimensions(c)})

    survivors = [e for e in evaluated if e["passed_gate"]]
    if not survivors:
        return {"ordering": [], "evaluated": evaluated, "pareto_indices": [],
                "gate_version": GATE_VERSION, "pareto_version": PARETO_VERSION,
                "rank_version": RANK_VERSION, "weights": dict(WEIGHTS)}

    front_local = pareto_front([e["candidate"] for e in survivors])
    front = [survivors[i] for i in front_local]
    for e in evaluated:
        e["on_pareto_front"] = e in front

    scores = topsis([e["candidate"] for e in front])
    for e, score in zip(front, scores):
        e["topsis_score"] = score

    # Non-dominated candidates rank by TOPSIS; dominated survivors follow, in
    # their original Stage-5 order. Ties break on input order, so this is total.
    dominated = [e for e in survivors if e not in front]
    ordering = sorted(front, key=lambda e: (-e["topsis_score"], e["index"])) \
        + sorted(dominated, key=lambda e: e["index"])

    return {"ordering": ordering, "evaluated": evaluated,
            "pareto_indices": [e["index"] for e in front],
            "gate_version": GATE_VERSION, "pareto_version": PARETO_VERSION,
            "rank_version": RANK_VERSION, "weights": dict(WEIGHTS)}


def confirm(unseen: Optional[Dict[str, Any]], candidate: Dict[str, Any],
            min_unseen_trades: int) -> Dict[str, Any]:
    """unseen_confirmation_v1 — CONFIRMED / DEGRADED / FAILED, with reasons."""
    if not unseen:
        return {"status": "FAILED", "reasons": ["unseen_backtest_unavailable"]}

    ret = _f(unseen.get("net_return_pct"))
    pf = _f(unseen.get("profit_factor"))
    dd = _f(unseen.get("max_dd_pct"))
    trades = int(_f(unseen.get("trades")))
    valid_dd = _f(candidate.get("valid_max_dd_pct"))

    fail, warn = [], []

    if ret <= FAIL_RETURN_PCT:
        fail.append(f"return({ret:.2f}%<={FAIL_RETURN_PCT}%)")
    if pf < FAIL_PF:
        fail.append(f"profit_factor({pf:.2f}<{FAIL_PF})")
    if dd > CONFIRM_MAX_DD_PCT:
        fail.append(f"drawdown({dd:.1f}%>{CONFIRM_MAX_DD_PCT}%)")
    if fail:
        return {"status": "FAILED", "reasons": fail}

    if ret <= CONFIRM_MIN_RETURN_PCT:
        warn.append(f"return_not_positive({ret:.2f}%)")
    if pf < CONFIRM_MIN_PF:
        warn.append(f"profit_factor({pf:.2f}<{CONFIRM_MIN_PF})")
    if valid_dd > 0 and dd > valid_dd * CONFIRM_MAX_DD_RATIO:
        warn.append(f"drawdown_{dd / valid_dd:.1f}x_validation")
    if trades < min_unseen_trades:
        warn.append(f"thin_sample({trades}<{min_unseen_trades})")

    return {"status": "DEGRADED" if warn else "CONFIRMED", "reasons": warn}


def describe() -> Dict[str, Any]:
    return {
        "gate_version": GATE_VERSION,
        "pareto_version": PARETO_VERSION,
        "rank_version": RANK_VERSION,
        "confirmation_version": CONFIRMATION_VERSION,
        "gate_thresholds": {
            "min_valid_profit_factor": MIN_VALID_PROFIT_FACTOR,
            "min_valid_return_pct": MIN_VALID_RETURN_PCT,
            "max_valid_drawdown_pct": MAX_VALID_DRAWDOWN_PCT,
            "min_robustness_score": MIN_ROBUSTNESS_SCORE,
            "min_risk_score": MIN_RISK_SCORE,
        },
        "pareto_dimensions": list(PARETO_DIMENSIONS),
        "mcdm_method": "TOPSIS",
        "weights": dict(WEIGHTS),
        "confirmation_thresholds": {
            "confirm_min_pf": CONFIRM_MIN_PF,
            "confirm_min_return_pct": CONFIRM_MIN_RETURN_PCT,
            "confirm_max_dd_pct": CONFIRM_MAX_DD_PCT,
            "confirm_max_dd_ratio": CONFIRM_MAX_DD_RATIO,
            "confirm_min_trade_ratio": CONFIRM_MIN_TRADE_RATIO,
            "fail_pf": FAIL_PF,
            "fail_return_pct": FAIL_RETURN_PCT,
        },
        "fallback_policy": dict(FALLBACK_POLICY),
        "uses_unseen_for_selection": False,
    }
