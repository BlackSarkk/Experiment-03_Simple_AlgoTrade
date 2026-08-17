"""Stage [3/6] robustness — perturbation and regime definitions, gate and score.

Everything that decides which candidates survive lives in this one file, and every
decision is deterministic. There is no human or AI judgement anywhere in the path:
a candidate passes or fails on fixed thresholds, is scored by a fixed formula, and
advances by rank. Running the same inputs twice produces the same survivors.

RECOVERED FROM THE CANDIDATE #158 WORKFLOW
------------------------------------------
`src/optimization/deep_15m_optimizer.py` contributed the two ideas kept here:

  * `stage2_stability` perturbed a handful of parameters one at a time
    (EMA +/-5, RSI period +/-2, RSI bounds +/-2, RR +/-0.2) and required
    `avg_pert_pf > 1.05 and min_pert_pf > 0.95`.
  * `stage3_regimes` scored five fixed calendar windows (H1/H2 2024, H1/H2 2025,
    2026 YTD) and required `positive_regimes >= 3` of 5.
  * `src/optimization/backup/` added sequential chunk testing (`n_splits = 4`).

INTENTIONALLY CHANGED
---------------------
  * The legacy perturbation set covered 4 of 11 parameters. Fragility in
    `consolidation_atr_mult` or `volume_mult` was invisible. All 11 are perturbed.
  * Legacy step sizes were hand-picked per parameter and unrelated to the search
    range. Steps here are a fixed fraction of each parameter's own range, so every
    dimension is stressed comparably.
  * Legacy gates used the MEAN perturbation PF. One outlier variant could carry a
    fragile parent. Medians and pass RATES are used instead.
  * Legacy regimes were hard-coded calendar dates that only fit one dataset. Here
    they are derived from the actual TRAIN+VALIDATION span by a documented rule.
  * Legacy regime evaluation sliced the frame and then computed indicators,
    restarting every rolling window at the regime boundary. Regimes here are
    evaluated through `context_for_window`, which prepends the Stage-1 warmup.
  * Concentration is new: the legacy rule counted profitable regimes but never
    checked whether one regime produced nearly all of the profit.
"""

import math
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from . import search_space

GATE_VERSION = "robustness_gate_v1"
SCORE_VERSION = "robustness_score_v1"
PERTURBATION_VERSION = "perturbation_v1"
REGIME_VERSION = "sequential_regimes_v1"

SEED = 42

# ---------------------------------------------------------------------------
# Perturbation definition
# ---------------------------------------------------------------------------
# Each parameter is nudged by a fixed fraction of its own search range, so an EMA
# and a volume multiplier are stressed by comparable amounts. Two magnitudes are
# used: a small step (is this a knife edge?) and a larger one (how wide is the
# plateau?). Single-parameter moves find axis-aligned fragility; the joint jitters
# find fragility that only appears when several parameters move together, which a
# one-at-a-time sweep cannot see. The full Cartesian product is never taken.
SMALL_STEP_FRACTION = 0.05     # 5% of each parameter's range
LARGE_STEP_FRACTION = 0.10     # 10%
JOINT_JITTERS = 8              # deterministic multi-parameter variants
JOINT_JITTER_FRACTION = 0.05

# 11 params x 2 directions x 2 magnitudes = 44, plus 8 joint = 52 per candidate.
VARIANTS_PER_CANDIDATE = len(search_space.PARAM_NAMES) * 4 + JOINT_JITTERS

# ---------------------------------------------------------------------------
# Regime definition
# ---------------------------------------------------------------------------
# Chunks must be long enough that a regime's metrics mean something and short
# enough that several exist. ~120 days at 15m is roughly a market quarter and, at
# the trade density this strategy shows, yields a usable per-regime sample.
REGIME_TARGET_DAYS = 120.0
MIN_REGIMES = 4
MAX_REGIMES = 8

# ---------------------------------------------------------------------------
# Gate thresholds (robustness_gate_v1)
# ---------------------------------------------------------------------------
# Chosen from the principle that a real edge must survive a *majority* of nearby
# coordinates and a *majority* of market periods, and must not depend on a single
# window. They are set here, before any candidate is evaluated, and are not tuned
# afterwards.
MIN_PERTURB_PROFITABLE_RATE = 0.60   # >60% of neighbours profitable, not a coin flip
MIN_PERTURB_PF_RATE = 0.60           # >60% of neighbours keep PF >= 1
MIN_MEDIAN_PERTURB_PF = 1.15         # the median neighbour still has an edge
MAX_CATASTROPHIC_RATE = 0.05         # <=5% of neighbours may blow up
CATASTROPHIC_DD_PCT = 50.0
CATASTROPHIC_PF = 0.5

MIN_PROFITABLE_REGIME_RATE = 0.50    # profitable in more than half of all periods
MIN_REGIME_PF_RATE = 0.60
MIN_WORST_REGIME_RETURN_PCT = -20.0  # a losing period must still be survivable
MAX_REGIME_CONCENTRATION = 0.80      # one period may not be ~all of the profit
MIN_MEDIAN_REGIME_TRADES = 5         # the per-regime sample must exist

# ---------------------------------------------------------------------------
# Score weights (robustness_score_v1)
# ---------------------------------------------------------------------------
# Consistency, not headline return. Every term is bounded, so no single metric can
# dominate: rates are already in [0,1], PF credit is capped at +1.0 over breakeven,
# and dispersion is normalised. Return itself is deliberately absent — Phase A
# already ranked on it, and rewarding it again here would reintroduce exactly the
# fragile-high-return preference this stage exists to remove.
W_PERTURB_RATE = 25.0
W_PERTURB_PF = 20.0
W_REGIME_RATE = 25.0
W_REGIME_PF = 15.0
W_STABILITY = 10.0        # low dispersion across the neighbourhood
W_SPREAD = 10.0           # profit spread across regimes, not concentrated
W_SAMPLE = 5.0
PF_EXCESS_CAP = 1.0
W_DD_PENALTY = 20.0
DD_FREE_FRACTION = 0.25   # worst-regime drawdown above 25% starts costing


# ---------------------------------------------------------------------------
# Perturbation generation
# ---------------------------------------------------------------------------

def _bounds(name):
    if name in search_space.INT_PARAMS:
        low, high, step = search_space.INT_PARAMS[name]
        return float(low), float(high), float(step), True
    low, high, step = search_space.FLOAT_PARAMS[name]
    return float(low), float(high), float(step), False


def _nudge(name: str, value: float, fraction: float, direction: int):
    """Move `value` by `fraction` of its range, snapped to the parameter's step."""
    low, high, step, is_int = _bounds(name)
    delta = (high - low) * fraction
    steps = max(1, round(delta / step))
    moved = value + direction * steps * step
    moved = min(high, max(low, moved))
    if is_int:
        return int(round(moved))
    return round(moved / step) * step


def _repair(params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Keep a variant legal: RSI oversold must stay below overbought."""
    if params["rsi_oversold"] >= params["rsi_overbought"]:
        return None
    return params


def perturbations(parent: Dict[str, Any], seed: int = SEED) -> List[Dict[str, Any]]:
    """Deterministic neighbourhood of a parent candidate.

    Risk parameters are never present here — the search space contains strategy
    parameters only, so a perturbation cannot change sizing.
    """
    base = {k: parent[k] for k in search_space.PARAM_NAMES}
    out: List[Dict[str, Any]] = []
    seen = set()

    def add(params, kind, changed):
        params = _repair(params)
        if params is None:
            return
        key = tuple(round(float(params[k]), 6) for k in search_space.PARAM_NAMES)
        if key in seen or key == tuple(round(float(base[k]), 6)
                                       for k in search_space.PARAM_NAMES):
            return
        seen.add(key)
        out.append({"kind": kind, "changed": changed, **params})

    for fraction, label in ((SMALL_STEP_FRACTION, "small"),
                            (LARGE_STEP_FRACTION, "large")):
        for name in search_space.PARAM_NAMES:
            for direction in (-1, 1):
                variant = dict(base)
                variant[name] = _nudge(name, float(base[name]), fraction, direction)
                if variant[name] == base[name]:
                    continue
                add(variant, f"single_{label}", f"{name}{'+' if direction > 0 else '-'}")

    rng = random.Random(seed)
    for i in range(JOINT_JITTERS):
        variant = dict(base)
        for name in search_space.PARAM_NAMES:
            direction = rng.choice((-1, 0, 1))
            if direction:
                variant[name] = _nudge(name, float(base[name]),
                                       JOINT_JITTER_FRACTION, direction)
        add(variant, "joint", f"jitter_{i}")

    return out


# ---------------------------------------------------------------------------
# Regime boundaries
# ---------------------------------------------------------------------------

def regime_boundaries(start, end):
    """Split an in-sample span into equal sequential regimes.

    Deterministic: the count follows from the span, and the cut points are equal
    time slices. Identical for every candidate.
    """
    import pandas as pd

    start = pd.Timestamp(start)
    end = pd.Timestamp(end)
    total_days = (end - start).total_seconds() / 86400.0
    n = int(max(MIN_REGIMES, min(MAX_REGIMES, round(total_days / REGIME_TARGET_DAYS))))
    span = (end - start) / n
    out = []
    for i in range(n):
        lo = start + span * i
        hi = end if i == n - 1 else start + span * (i + 1)
        out.append((f"R{i + 1}", lo, hi))
    return out


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _median(values: Sequence[float]) -> float:
    vals = sorted(v for v in values if v is not None and v == v)
    if not vals:
        return float("nan")
    mid = len(vals) // 2
    if len(vals) % 2:
        return float(vals[mid])
    return float((vals[mid - 1] + vals[mid]) / 2.0)


def _iqr(values: Sequence[float]) -> float:
    vals = sorted(v for v in values if v is not None and v == v)
    if len(vals) < 4:
        return 0.0
    q1 = vals[len(vals) // 4]
    q3 = vals[(3 * len(vals)) // 4]
    return float(q3 - q1)


@dataclass
class RobustnessSummary:
    trial: int
    train_rank: int
    # perturbation
    perturbations_tested: int = 0
    perturb_valid: int = 0
    perturb_profitable_rate: float = 0.0
    perturb_pf_rate: float = 0.0
    median_perturb_return: float = float("nan")
    median_perturb_pf: float = float("nan")
    median_perturb_sharpe: float = float("nan")
    worst_perturb_dd: float = float("nan")
    perturb_return_iqr: float = float("nan")
    perturb_dispersion: float = float("nan")
    catastrophic_failures: int = 0
    catastrophic_rate: float = 0.0
    # regimes
    regimes_tested: int = 0
    profitable_regimes: int = 0
    profitable_regime_rate: float = 0.0
    pf_ge_1_regimes: int = 0
    regime_pf_rate: float = 0.0
    median_regime_return: float = float("nan")
    median_regime_pf: float = float("nan")
    worst_regime_return: float = float("nan")
    worst_regime_pf: float = float("nan")
    worst_regime_dd: float = float("nan")
    median_regime_trades: float = 0.0
    min_regime_trades: int = 0
    regime_concentration: float = float("nan")
    # verdict
    passed: bool = False
    failures: List[str] = field(default_factory=list)
    score: float = 0.0


def summarise(trial: int, train_rank: int, parent: Dict[str, Any],
              perturb_results: List[Optional[Dict[str, Any]]],
              regime_results: List[Optional[Dict[str, Any]]]) -> RobustnessSummary:
    s = RobustnessSummary(trial=trial, train_rank=train_rank)

    s.perturbations_tested = len(perturb_results)
    valid = [m for m in perturb_results if m]
    s.perturb_valid = len(valid)
    if valid:
        rets = [m["net_return_pct"] for m in valid]
        pfs = [m["profit_factor"] for m in valid]
        s.perturb_profitable_rate = sum(1 for r in rets if r > 0) / len(valid)
        s.perturb_pf_rate = sum(1 for p in pfs if p >= 1.0) / len(valid)
        s.median_perturb_return = _median(rets)
        s.median_perturb_pf = _median(pfs)
        s.median_perturb_sharpe = _median([m["sharpe"] for m in valid])
        s.worst_perturb_dd = max(m["max_dd_pct"] for m in valid)
        s.perturb_return_iqr = _iqr(rets)
        parent_ret = abs(float(parent.get("train_net_return_pct") or 0.0)) or 1.0
        s.perturb_dispersion = s.perturb_return_iqr / parent_ret
        s.catastrophic_failures = sum(
            1 for m in valid
            if m["max_dd_pct"] >= CATASTROPHIC_DD_PCT or m["profit_factor"] < CATASTROPHIC_PF)
    # A variant that failed to run at all counts against the parent.
    s.catastrophic_failures += len(perturb_results) - len(valid)
    s.catastrophic_rate = (s.catastrophic_failures / len(perturb_results)
                           if perturb_results else 1.0)

    s.regimes_tested = len(regime_results)
    reg = [m for m in regime_results if m]
    if reg:
        rets = [m["net_return_pct"] for m in reg]
        pfs = [m["profit_factor"] for m in reg]
        pnls = [m["net_pnl"] for m in reg]
        s.profitable_regimes = sum(1 for r in rets if r > 0)
        s.profitable_regime_rate = s.profitable_regimes / len(regime_results)
        s.pf_ge_1_regimes = sum(1 for p in pfs if p >= 1.0)
        s.regime_pf_rate = s.pf_ge_1_regimes / len(regime_results)
        s.median_regime_return = _median(rets)
        s.median_regime_pf = _median(pfs)
        s.worst_regime_return = min(rets)
        s.worst_regime_pf = min(pfs)
        s.worst_regime_dd = max(m["max_dd_pct"] for m in reg)
        s.median_regime_trades = _median([m["trades"] for m in reg])
        s.min_regime_trades = min(m["trades"] for m in reg)
        positive = [p for p in pnls if p > 0]
        s.regime_concentration = (max(positive) / sum(positive)) if positive else 1.0
    else:
        s.regime_concentration = 1.0

    s.failures = gate_failures(s)
    s.passed = not s.failures
    s.score = robustness_score_v1(s) if s.passed else 0.0
    return s


# ---------------------------------------------------------------------------
# Gate
# ---------------------------------------------------------------------------

def gate_failures(s: RobustnessSummary) -> List[str]:
    """Every reason this candidate is not robust. Empty list means PASS."""
    out = []
    if s.perturb_valid == 0:
        out.append("no_valid_perturbations")
        return out

    if s.perturb_profitable_rate < MIN_PERTURB_PROFITABLE_RATE:
        out.append(f"neighbourhood_mostly_unprofitable({s.perturb_profitable_rate:.0%}"
                   f"<{MIN_PERTURB_PROFITABLE_RATE:.0%})")
    if s.perturb_pf_rate < MIN_PERTURB_PF_RATE:
        out.append(f"neighbourhood_pf_rate({s.perturb_pf_rate:.0%}<{MIN_PERTURB_PF_RATE:.0%})")
    if not (s.median_perturb_pf >= MIN_MEDIAN_PERTURB_PF):
        out.append(f"median_neighbour_pf({s.median_perturb_pf:.2f}<{MIN_MEDIAN_PERTURB_PF})")
    if s.catastrophic_rate > MAX_CATASTROPHIC_RATE:
        out.append(f"catastrophic_neighbours({s.catastrophic_rate:.0%}>{MAX_CATASTROPHIC_RATE:.0%})")

    if s.regimes_tested == 0:
        out.append("no_regimes_evaluated")
        return out

    if s.profitable_regime_rate < MIN_PROFITABLE_REGIME_RATE:
        out.append(f"loses_in_most_regimes({s.profitable_regimes}/{s.regimes_tested})")
    if s.regime_pf_rate < MIN_REGIME_PF_RATE:
        out.append(f"regime_pf_rate({s.pf_ge_1_regimes}/{s.regimes_tested})")
    if s.worst_regime_return < MIN_WORST_REGIME_RETURN_PCT:
        out.append(f"worst_regime_return({s.worst_regime_return:.1f}%"
                   f"<{MIN_WORST_REGIME_RETURN_PCT}%)")
    if s.regime_concentration > MAX_REGIME_CONCENTRATION:
        out.append(f"one_regime_wonder({s.regime_concentration:.0%}"
                   f">{MAX_REGIME_CONCENTRATION:.0%})")
    if s.median_regime_trades < MIN_MEDIAN_REGIME_TRADES:
        out.append(f"regime_sample_too_small({s.median_regime_trades:.0f}"
                   f"<{MIN_MEDIAN_REGIME_TRADES})")
    return out


# ---------------------------------------------------------------------------
# Score
# ---------------------------------------------------------------------------

def robustness_score_v1(s: RobustnessSummary) -> float:
    """Bounded consistency score. Headline return is deliberately not a term."""
    def pf_credit(pf):
        if pf != pf:
            return 0.0
        return max(0.0, min(pf - 1.0, PF_EXCESS_CAP))

    perturb_rate = W_PERTURB_RATE * s.perturb_profitable_rate
    perturb_pf = W_PERTURB_PF * pf_credit(s.median_perturb_pf)
    regime_rate = W_REGIME_RATE * s.profitable_regime_rate
    regime_pf = W_REGIME_PF * pf_credit(s.median_regime_pf)

    dispersion = s.perturb_dispersion if s.perturb_dispersion == s.perturb_dispersion else 1.0
    stability = W_STABILITY * max(0.0, 1.0 - min(dispersion, 1.0))

    concentration = s.regime_concentration if s.regime_concentration == s.regime_concentration else 1.0
    even = 1.0 / max(1, s.regimes_tested)
    # 1.0 when profit is spread perfectly evenly, 0.0 when one regime holds it all.
    spread = W_SPREAD * max(0.0, (1.0 - concentration) / max(1e-9, 1.0 - even))

    sample = W_SAMPLE * min(1.0, s.median_regime_trades / (2.0 * MIN_MEDIAN_REGIME_TRADES))

    worst_dd = s.worst_regime_dd if s.worst_regime_dd == s.worst_regime_dd else 0.0
    dd_penalty = W_DD_PENALTY * max(0.0, worst_dd / 100.0 - DD_FREE_FRACTION)

    return (perturb_rate + perturb_pf + regime_rate + regime_pf
            + stability + spread + sample - dd_penalty)


def describe() -> Dict[str, Any]:
    return {
        "gate_version": GATE_VERSION,
        "score_version": SCORE_VERSION,
        "perturbation_version": PERTURBATION_VERSION,
        "regime_version": REGIME_VERSION,
        "seed": SEED,
        "perturbation": {
            "small_step_fraction": SMALL_STEP_FRACTION,
            "large_step_fraction": LARGE_STEP_FRACTION,
            "joint_jitters": JOINT_JITTERS,
            "joint_jitter_fraction": JOINT_JITTER_FRACTION,
            "max_variants_per_candidate": VARIANTS_PER_CANDIDATE,
            "dimensions": list(search_space.PARAM_NAMES),
        },
        "regimes": {
            "target_days": REGIME_TARGET_DAYS,
            "min_regimes": MIN_REGIMES,
            "max_regimes": MAX_REGIMES,
        },
        "gate_thresholds": {
            "min_perturb_profitable_rate": MIN_PERTURB_PROFITABLE_RATE,
            "min_perturb_pf_rate": MIN_PERTURB_PF_RATE,
            "min_median_perturb_pf": MIN_MEDIAN_PERTURB_PF,
            "max_catastrophic_rate": MAX_CATASTROPHIC_RATE,
            "catastrophic_dd_pct": CATASTROPHIC_DD_PCT,
            "catastrophic_pf": CATASTROPHIC_PF,
            "min_profitable_regime_rate": MIN_PROFITABLE_REGIME_RATE,
            "min_regime_pf_rate": MIN_REGIME_PF_RATE,
            "min_worst_regime_return_pct": MIN_WORST_REGIME_RETURN_PCT,
            "max_regime_concentration": MAX_REGIME_CONCENTRATION,
            "min_median_regime_trades": MIN_MEDIAN_REGIME_TRADES,
        },
        "score_weights": {
            "W_PERTURB_RATE": W_PERTURB_RATE, "W_PERTURB_PF": W_PERTURB_PF,
            "W_REGIME_RATE": W_REGIME_RATE, "W_REGIME_PF": W_REGIME_PF,
            "W_STABILITY": W_STABILITY, "W_SPREAD": W_SPREAD,
            "W_SAMPLE": W_SAMPLE, "PF_EXCESS_CAP": PF_EXCESS_CAP,
            "W_DD_PENALTY": W_DD_PENALTY, "DD_FREE_FRACTION": DD_FREE_FRACTION,
        },
        "headline_return_is_a_term": False,
        "uses_unseen": False,
    }
