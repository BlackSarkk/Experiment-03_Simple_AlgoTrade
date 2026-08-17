"""Stage-4 diagnostic — is the 3.0% risk_per_trade_pct bound truncating the search?

Four of five Stage-4 winners selected exactly 3.0%, the top of the legacy range.
That is either the genuine optimum or a range artefact. This module answers the
question by walking `risk_per_trade_pct` past the bound while holding everything
else — strategy parameters, leverage, allocation — frozen at the values Stage 4
already selected, so the boundary is the only moving part.

It is read-only with respect to Stage 4: it loads `stage4_advancing.csv`, writes
its own two artifacts, and changes no winner. `risk_score_v1` and `risk_gate_v1`
are used exactly as they are.

SATURATION
----------
`BaselineRiskManager` sizes a position as
`min(equity*risk%/|entry-sl|, equity*alloc*leverage/entry)`. Once the allocation
term is the smaller of the two, raising `risk_per_trade_pct` changes nothing at
all. A ladder rung that reproduces the previous rung's metrics exactly is
therefore saturated, not improved, and is reported as such.
"""

import csv
import hashlib
import json
import os
from typing import Any, Dict, List, Optional

from . import evaluation, risk_policy, scoring, search_space

AUDIT_VERSION = "risk_boundary_audit_v1"

# `RiskConfig.risk_per_trade_pct` is a plain float — the RiskManager imposes no
# quantisation on it (only `quantity_step` quantises the resulting size), so any
# increment is mechanically valid. 0.25 steps straddle the existing bound and the
# ladder deliberately reaches well past it.
RISK_LADDER_PCT = (2.50, 2.75, 3.00, 3.25, 3.50, 3.75, 4.00, 4.50, 5.00)
EXISTING_BOUND_PCT = 3.00

# What "meaningfully better beyond the bound" has to look like, fixed in advance.
MIN_SCORE_GAIN = 2.0            # risk_score_v1 points over the best rung <= bound
MIN_STRATEGIES_IMPROVED = 2     # a single candidate improving is not evidence
MAX_DD_GROWTH_RATIO = 1.25      # >25% more drawdown is disproportionate

CSV_FIELDS = [
    "train_rank", "trial_id", "risk_per_trade_pct", "leverage",
    "max_position_allocation_pct", "beyond_bound", "saturated",
    "train_net_return_pct", "train_profit_factor", "train_sharpe",
    "train_max_dd_pct", "train_trades", "train_net_pnl",
    "valid_net_return_pct", "valid_profit_factor", "valid_sharpe",
    "valid_max_dd_pct", "valid_trades",
    "risk_score", "gate", "gate_failures",
]


def load_advancing(run_path: str) -> List[Dict[str, Any]]:
    path = os.path.join(run_path, "stage4_advancing.csv")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"stage-4 advancing set not found: {path}")
    out = []
    with open(path) as fh:
        for row in csv.DictReader(fh):
            cand = {
                "train_rank": int(row["train_rank"]),
                "trial_id": int(row["trial_id"]),
                "advance_rank": int(row["advance_rank"]),
                "leverage": float(row["leverage"]),
                "risk_per_trade_pct": float(row["risk_per_trade_pct"]),
                "max_position_allocation_pct": float(row["max_position_allocation_pct"]),
            }
            for name in search_space.PARAM_NAMES:
                cand[name] = (int(float(row[name]))
                              if name in search_space.INT_PARAMS
                              else float(row[name]))
            out.append(cand)
    out.sort(key=lambda c: c["advance_rank"])
    return out


def _same_sizing(a: Optional[Dict], b: Optional[Dict]) -> bool:
    """Identical TRAIN outcome means another cap bound the size, not the budget."""
    if not a or not b:
        return False
    return all(abs(float(a[k]) - float(b[k])) < 1e-9
               for k in ("net_pnl", "net_return_pct", "max_dd_pct")) \
        and int(a["trades"]) == int(b["trades"])


def run(preset, prepared, run_path: str, progress=None) -> Dict[str, Any]:
    def say(msg):
        if progress is not None:
            progress(msg)

    candidates = load_advancing(run_path)
    train_days = (prepared.train.end - prepared.train.start).total_seconds() / 86400.0
    valid_days = (prepared.validation.end - prepared.validation.start).total_seconds() / 86400.0
    min_trades = scoring.minimum_trades(train_days)
    min_valid = scoring.thin_validation_threshold(min_trades, train_days, valid_days)

    rows: List[Dict[str, Any]] = []
    for cand in candidates:
        params = {k: cand[k] for k in search_space.PARAM_NAMES}
        rank = cand["train_rank"]
        say(f"rank {rank}: risk ladder, leverage {cand['leverage']}x / "
            f"allocation {cand['max_position_allocation_pct']:.0f}% frozen")

        neutral = evaluation.run_backtest(prepared, "train", params, preset)
        neutral_trades = int((neutral or {}).get("trades", 0)) or None

        previous = None
        for risk_pct in RISK_LADDER_PCT:
            # Only risk_per_trade_pct moves. Leverage and allocation are the
            # values Stage 4 selected for this candidate.
            policy = {"leverage": cand["leverage"],
                      "risk_per_trade_pct": risk_pct,
                      "max_position_allocation_pct": cand["max_position_allocation_pct"]}
            fractions = risk_policy.as_fractions(policy)

            train = evaluation.run_backtest(prepared, "train", params, preset, fractions)
            valid = evaluation.run_backtest(prepared, "validation", params, preset,
                                            fractions)
            failures = risk_policy.gate_failures(train, min_trades, neutral_trades)
            score = risk_policy.risk_score_v1(train, min_trades, neutral_trades)

            row = {
                "train_rank": rank, "trial_id": cand["trial_id"],
                "risk_per_trade_pct": risk_pct,
                "leverage": cand["leverage"],
                "max_position_allocation_pct": cand["max_position_allocation_pct"],
                "beyond_bound": risk_pct > EXISTING_BOUND_PCT,
                "saturated": _same_sizing(previous, train),
                "risk_score": round(score, 4),
                "gate": "PASS" if not failures else "FAIL",
                "gate_failures": "; ".join(failures),
            }
            for key in ("net_return_pct", "profit_factor", "sharpe",
                        "max_dd_pct", "trades", "net_pnl"):
                row[f"train_{key}"] = (train or {}).get(key)
            for key in ("net_return_pct", "profit_factor", "sharpe",
                        "max_dd_pct", "trades"):
                row[f"valid_{key}"] = (valid or {}).get(key)
            rows.append(row)
            previous = train

    verdict = decide(rows)
    _write(run_path, rows, verdict, preset, prepared, min_trades, min_valid)
    return {"rows": rows, "verdict": verdict, "candidates": candidates}


def decide(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Deterministic recommendation. Thresholds fixed above, before any run."""
    per_candidate = {}
    for rank in sorted({r["train_rank"] for r in rows}):
        mine = [r for r in rows if r["train_rank"] == rank]
        within = [r for r in mine if not r["beyond_bound"] and r["gate"] == "PASS"]
        beyond = [r for r in mine if r["beyond_bound"] and r["gate"] == "PASS"]

        best_within = max(within, key=lambda r: r["risk_score"]) if within else None
        best_beyond = max(beyond, key=lambda r: r["risk_score"]) if beyond else None

        saturated_beyond = [r for r in mine if r["beyond_bound"] and r["saturated"]]
        failed_beyond = [r for r in mine if r["beyond_bound"] and r["gate"] == "FAIL"]

        gain = (best_beyond["risk_score"] - best_within["risk_score"]
                if best_within and best_beyond else 0.0)
        dd_ratio = None
        if best_within and best_beyond and best_within["train_max_dd_pct"]:
            dd_ratio = (best_beyond["train_max_dd_pct"]
                        / best_within["train_max_dd_pct"])

        improved = bool(
            best_beyond and best_within
            and gain >= MIN_SCORE_GAIN
            and not best_beyond["saturated"]
            and (dd_ratio is None or dd_ratio <= MAX_DD_GROWTH_RATIO)
        )
        per_candidate[rank] = {
            "best_within_bound_pct": best_within["risk_per_trade_pct"] if best_within else None,
            "best_within_score": best_within["risk_score"] if best_within else None,
            "best_beyond_bound_pct": best_beyond["risk_per_trade_pct"] if best_beyond else None,
            "best_beyond_score": best_beyond["risk_score"] if best_beyond else None,
            "score_gain": round(gain, 4),
            "dd_growth_ratio": round(dd_ratio, 4) if dd_ratio else None,
            "saturated_rungs_beyond": len(saturated_beyond),
            "gate_failures_beyond": len(failed_beyond),
            "improved": improved,
        }

    improved_count = sum(1 for v in per_candidate.values() if v["improved"])
    truncating = improved_count >= MIN_STRATEGIES_IMPROVED

    if truncating:
        proposed = max(v["best_beyond_bound_pct"] for v in per_candidate.values()
                       if v["improved"])
        reason = (f"{improved_count} of {len(per_candidate)} advancing strategies gained "
                  f">= {MIN_SCORE_GAIN} risk-score points beyond {EXISTING_BOUND_PCT}% "
                  f"without saturation or disproportionate drawdown")
    else:
        proposed = None
        reason = _keep_reason(per_candidate)

    return {
        "audit_version": AUDIT_VERSION,
        "decision": "BOUND_IS_TRUNCATING_SEARCH" if truncating else "KEEP_3.0_BOUND",
        "existing_bound_pct": EXISTING_BOUND_PCT,
        "proposed_bound_pct": proposed,
        "strategies_improved": improved_count,
        "strategies_tested": len(per_candidate),
        "reason": reason,
        "thresholds": {
            "min_score_gain": MIN_SCORE_GAIN,
            "min_strategies_improved": MIN_STRATEGIES_IMPROVED,
            "max_dd_growth_ratio": MAX_DD_GROWTH_RATIO,
        },
        "per_candidate": per_candidate,
    }


def _keep_reason(per_candidate) -> str:
    saturated = sum(v["saturated_rungs_beyond"] for v in per_candidate.values())
    failures = sum(v["gate_failures_beyond"] for v in per_candidate.values())
    bits = []
    if saturated:
        bits.append(f"{saturated} rungs beyond the bound were saturated "
                    "(allocation cap binds, so extra risk budget does nothing)")
    if failures:
        bits.append(f"{failures} rungs beyond the bound failed risk_gate_v1")
    if not bits:
        bits.append("no strategy gained enough risk-adjusted score beyond the bound")
    return "; ".join(bits)


def _write(run_path, rows, verdict, preset, prepared, min_trades, min_valid):
    with open(os.path.join(run_path, "stage4_risk_boundary_audit.csv"),
              "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    payload = dict(verdict)
    payload.update({
        "ladder_pct": list(RISK_LADDER_PCT),
        "diagnostic_maximum_pct": max(RISK_LADDER_PCT),
        "evaluations": len(rows) * 2,          # TRAIN + VALIDATION per rung
        "frozen": ["strategy parameters", "leverage", "max_position_allocation_pct"],
        "varied": ["risk_per_trade_pct"],
        "score_version": risk_policy.SCORE_VERSION,
        "gate_version": risk_policy.GATE_VERSION,
        "min_train_trades": min_trades,
        "min_validation_trades": min_valid,
        "train": {"start": str(prepared.train.start), "end": str(prepared.train.end)},
        "validation": {"start": str(prepared.validation.start),
                       "end": str(prepared.validation.end)},
        "stage4_winners_changed": False,
        "unseen_accessed": False,
    })
    with open(os.path.join(run_path, "stage4_risk_boundary_audit.json"), "w") as fh:
        json.dump(payload, fh, indent=2, default=str)


def artifact_digest(run_path: str, names) -> Dict[str, str]:
    """SHA-256 of named files — used to prove Stage-4 artifacts stay byte-identical."""
    out = {}
    for name in names:
        path = os.path.join(run_path, name)
        if os.path.isfile(path):
            with open(path, "rb") as fh:
                out[name] = hashlib.sha256(fh.read()).hexdigest()
    return out
