"""Stage [3/6] — strategy robustness.

    Phase-A GENERALIZES candidates
      -> perturbation neighbourhood (test A)
      -> chronological regimes (test B)
      -> robustness_gate_v1     : deterministic PASS / FAIL
      -> robustness_score_v1    : deterministic ranking of survivors
      -> top N advance to stage [4/6]

Nothing in this module asks anyone anything. The advancing set is written to
`stage3_advancing.csv` so stage 4 reads a decision, not a recommendation.

TRAIN and VALIDATION only. `unlock()` is never called; regimes are evaluated
through `context_for_window`, which refuses any window reaching past VALIDATION.
"""

import csv
import json
import os
import time
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from . import artifacts, evaluation, robustness, search_space
from .dashboard import Stage3Dashboard, stage_status_from_preset

MAX_ADVANCING = 5
REQUIRED_STATUS = "GENERALIZES"


def _phase_a_dir(run_path: str) -> str:
    return run_path


def load_candidates(run_path: str, status: str = REQUIRED_STATUS) -> List[Dict[str, Any]]:
    """Read the Phase-A diverse shortlist and keep only qualifying candidates.

    Phase-A artifacts are opened read-only and never rewritten.
    """
    path = os.path.join(run_path, "phase_a_diverse_shortlist.csv")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Phase-A diverse shortlist not found: {path}")

    out = []
    with open(path) as fh:
        for row in csv.DictReader(fh):
            if row.get("valid_status") != status:
                continue
            cand = {
                "trial": int(row["trial"]),
                "train_rank": int(row["rank"]),
                "train_score": float(row["train_score"]),
            }
            for key in ("net_return_pct", "profit_factor", "sharpe",
                        "max_dd_pct", "trades"):
                for part in ("train", "valid"):
                    raw = row.get(f"{part}_{key}")
                    cand[f"{part}_{key}"] = float(raw) if raw not in (None, "") else None
            for name in search_space.PARAM_NAMES:
                value = row[name]
                cand[name] = (int(float(value)) if name in search_space.INT_PARAMS
                              else float(value))
            out.append(cand)
    out.sort(key=lambda c: c["train_rank"])
    return out


# ---------------------------------------------------------------------------
# Incremental persistence. Every evaluation is appended as it completes, so an
# interrupted run resumes instead of repeating work.
# ---------------------------------------------------------------------------

PERTURB_FILE = "stage3_perturbations.csv"
REGIME_FILE = "stage3_regimes.csv"

PERTURB_FIELDS = ["trial", "train_rank", "variant", "kind", "changed", "valid",
                  "net_return_pct", "net_pnl", "profit_factor", "sharpe",
                  "max_dd_pct", "trades"] + list(search_space.PARAM_NAMES)

REGIME_FIELDS = ["trial", "train_rank", "regime", "start", "end", "valid",
                 "net_return_pct", "net_pnl", "profit_factor", "sharpe",
                 "max_dd_pct", "trades"]


class _Ledger:
    """Append-only CSV that doubles as the resume index."""

    def __init__(self, path: str, fields: List[str], key_fields: List[str],
                 group_field: str = "trial"):
        self.path = path
        self.fields = fields
        self.key_fields = key_fields
        self.group_field = group_field
        self.rows: List[Dict[str, Any]] = []
        self.done = set()
        if os.path.isfile(path):
            with open(path) as fh:
                for row in csv.DictReader(fh):
                    self.rows.append(row)
                    self.done.add(tuple(row[k] for k in key_fields))
        else:
            with open(path, "w", newline="") as fh:
                csv.DictWriter(fh, fieldnames=fields).writeheader()

    def has(self, *key) -> bool:
        return tuple(str(k) for k in key) in self.done

    def append(self, row: Dict[str, Any]):
        with open(self.path, "a", newline="") as fh:
            csv.DictWriter(fh, fieldnames=self.fields,
                           extrasaction="ignore").writerow(row)
        self.rows.append({k: row.get(k) for k in self.fields})
        self.done.add(tuple(str(row[k]) for k in self.key_fields))

    def for_candidate(self, key) -> List[Dict[str, Any]]:
        return [r for r in self.rows if str(r[self.group_field]) == str(key)]


def _metrics_from_row(row) -> Optional[Dict[str, Any]]:
    if str(row.get("valid")).lower() not in ("true", "1"):
        return None
    return {
        "net_return_pct": float(row["net_return_pct"]),
        "net_pnl": float(row["net_pnl"]),
        "profit_factor": float(row["profit_factor"]),
        "sharpe": float(row["sharpe"]),
        "max_dd_pct": float(row["max_dd_pct"]),
        "trades": int(float(row["trades"])),
    }


def _row_from_metrics(base: Dict[str, Any], m: Optional[Dict[str, Any]]):
    row = dict(base, valid=bool(m))
    if m:
        row.update(m)
    return row


def run(preset, prepared, run_path: str, progress=None,
        max_advancing: int = MAX_ADVANCING, show_dashboard: bool = False,
        campaign_started: Optional[float] = None) -> Dict[str, Any]:
    """Execute stage 3 against a completed Phase-A run directory."""

    def say(msg):
        if progress is not None:
            progress(msg)

    started = time.time()
    candidates = load_candidates(run_path)
    if not candidates:
        raise RuntimeError(
            f"no Phase-A candidates with valid_status == {REQUIRED_STATUS}; "
            "stage 3 has nothing to test"
        )

    regimes = robustness.regime_boundaries(prepared.train.start,
                                           prepared.validation.end)

    perturb_ledger = _Ledger(os.path.join(run_path, PERTURB_FILE),
                             PERTURB_FIELDS, ["trial", "variant"])
    regime_ledger = _Ledger(os.path.join(run_path, REGIME_FILE),
                            REGIME_FIELDS, ["trial", "regime"])

    # The whole in-sample span, warmup-backed, reused for every perturbation.
    insample = prepared.context_for_window(prepared.train.start,
                                           prepared.validation.end)

    total_evals = sum(
        len(robustness.perturbations(c)) + len(regimes) for c in candidates)

    status = stage_status_from_preset(preset.stages, running=3)
    summaries = []

    with Stage3Dashboard(total_evals, len(candidates), status,
                         enabled=show_dashboard,
                         campaign_started=campaign_started) as dash:
        for idx, cand in enumerate(candidates, start=1):
            variants = robustness.perturbations(cand)

            dash.set_candidate(idx, cand["train_rank"], "Perturbation")
            say(f"candidate {idx}/{len(candidates)} (rank {cand['train_rank']}) "
                f"- perturbation, {len(variants)} variants")
            for vi, variant in enumerate(variants):
                if not perturb_ledger.has(cand["trial"], vi):
                    params = {k: variant[k] for k in search_space.PARAM_NAMES}
                    metrics = evaluation.run_on_context(insample, params, preset)
                    perturb_ledger.append(_row_from_metrics({
                        "trial": cand["trial"], "train_rank": cand["train_rank"],
                        "variant": vi, "kind": variant["kind"],
                        "changed": variant["changed"], **params}, metrics))
                dash.evaluation_done()

            dash.set_candidate(idx, cand["train_rank"], "Regime")
            say(f"candidate {idx}/{len(candidates)} (rank {cand['train_rank']}) "
                f"- regimes, {len(regimes)} periods")
            parent_params = {k: cand[k] for k in search_space.PARAM_NAMES}
            for name, lo, hi in regimes:
                if not regime_ledger.has(cand["trial"], name):
                    metrics = evaluation.run_backtest_window(prepared, lo, hi,
                                                             parent_params, preset)
                    regime_ledger.append(_row_from_metrics({
                        "trial": cand["trial"], "train_rank": cand["train_rank"],
                        "regime": name, "start": lo, "end": hi}, metrics))
                dash.evaluation_done()

            # Verdict as soon as this candidate's evidence is complete.
            summary = robustness.summarise(
                cand["trial"], cand["train_rank"], cand,
                [_metrics_from_row(r)
                 for r in perturb_ledger.for_candidate(cand["trial"])],
                [_metrics_from_row(r)
                 for r in regime_ledger.for_candidate(cand["trial"])])
            summaries.append(summary)
            if summary.passed:
                dash.set_best(summary)

        dash.finish_stage(3, "PASS" if any(s.passed for s in summaries) else "FAILED")

    survivors = [s for s in summaries if s.passed]
    # Rank by robustness score; ties break on TRAIN rank so the order is total.
    survivors.sort(key=lambda s: (-s.score, s.train_rank))
    advancing = survivors[:max_advancing]

    _write_candidates(run_path, summaries, candidates)
    _write_advancing(run_path, advancing, candidates)
    manifest = _manifest(preset, prepared, run_path, candidates, regimes,
                         summaries, survivors, advancing,
                         len(perturb_ledger.rows) + len(regime_ledger.rows),
                         time.time() - started)
    with open(os.path.join(run_path, "stage3_manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2, default=str)

    return {
        "candidates": candidates,
        "summaries": summaries,
        "survivors": survivors,
        "advancing": advancing,
        "regimes": regimes,
        "evaluations": len(perturb_ledger.rows) + len(regime_ledger.rows),
        "seconds": time.time() - started,
        "manifest": manifest,
        "failed": not survivors,
    }


CANDIDATE_FIELDS = [
    "train_rank", "trial", "passed", "score", "failures",
    "perturbations_tested", "perturb_valid", "perturb_profitable_rate",
    "perturb_pf_rate", "median_perturb_return", "median_perturb_pf",
    "median_perturb_sharpe", "worst_perturb_dd", "perturb_return_iqr",
    "perturb_dispersion", "catastrophic_failures", "catastrophic_rate",
    "regimes_tested", "profitable_regimes", "profitable_regime_rate",
    "pf_ge_1_regimes", "regime_pf_rate", "median_regime_return",
    "median_regime_pf", "worst_regime_return", "worst_regime_pf",
    "worst_regime_dd", "median_regime_trades", "min_regime_trades",
    "regime_concentration",
]


def _summary_row(s, cand):
    row = {k: v for k, v in asdict(s).items() if k in CANDIDATE_FIELDS}
    row["failures"] = "; ".join(s.failures)
    row["passed"] = s.passed
    row["score"] = round(s.score, 4)
    row.update({k: cand[k] for k in search_space.PARAM_NAMES})
    return row


def _write_candidates(run_path, summaries, candidates):
    by_trial = {c["trial"]: c for c in candidates}
    rows = [_summary_row(s, by_trial[s.trial]) for s in summaries]
    rows.sort(key=lambda r: (not r["passed"], -r["score"], r["train_rank"]))
    fields = CANDIDATE_FIELDS + list(search_space.PARAM_NAMES)
    with open(os.path.join(run_path, "stage3_candidates.csv"), "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_advancing(run_path, advancing, candidates):
    """The stage-4 input. A decision, not a suggestion."""
    by_trial = {c["trial"]: c for c in candidates}
    fields = (["advance_rank", "train_rank", "trial", "robustness_score",
               "perturb_profitable_rate", "profitable_regime_rate",
               "median_regime_pf", "worst_regime_dd",
               "train_net_return_pct", "train_profit_factor",
               "valid_net_return_pct", "valid_profit_factor"]
              + list(search_space.PARAM_NAMES))
    with open(os.path.join(run_path, "stage3_advancing.csv"), "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for i, s in enumerate(advancing, start=1):
            cand = by_trial[s.trial]
            row = {
                "advance_rank": i, "train_rank": s.train_rank, "trial": s.trial,
                "robustness_score": round(s.score, 4),
                "perturb_profitable_rate": round(s.perturb_profitable_rate, 4),
                "profitable_regime_rate": round(s.profitable_regime_rate, 4),
                "median_regime_pf": round(s.median_regime_pf, 4),
                "worst_regime_dd": round(s.worst_regime_dd, 4),
            }
            for key in ("net_return_pct", "profit_factor"):
                row[f"train_{key}"] = cand.get(f"train_{key}")
                row[f"valid_{key}"] = cand.get(f"valid_{key}")
            row.update({k: cand[k] for k in search_space.PARAM_NAMES})
            writer.writerow(row)


def _manifest(preset, prepared, run_path, candidates, regimes, summaries,
              survivors, advancing, evaluations, seconds) -> Dict[str, Any]:
    phase_a = artifacts.read_manifest(run_path)
    return {
        "stage": "stage_3_strategy_robustness",
        "phase_a_run_id": phase_a.get("run_id"),
        "phase_a_data_checksum": phase_a.get("data_checksum"),
        "phase_a_scoring_version": phase_a.get("scoring", {}).get("version"),
        "input_status_filter": REQUIRED_STATUS,
        "candidates_in": len(candidates),
        "evaluations": evaluations,
        "regimes": [{"name": n, "start": str(lo), "end": str(hi)}
                    for n, lo, hi in regimes],
        "partitions_used": ["TRAIN", "VALIDATION"],
        "robustness": robustness.describe(),
        "neutral_risk": evaluation.NEUTRAL_RISK.as_dict(),
        "seed": robustness.SEED,
        "max_advancing": MAX_ADVANCING,
        "survivors": [s.trial for s in survivors],
        "survivor_train_ranks": [s.train_rank for s in survivors],
        "advancing_trials": [s.trial for s in advancing],
        "advancing_train_ranks": [s.train_rank for s in advancing],
        "stage_failed": not survivors,
        "selection_required_from_human": False,
        "runtime_seconds": round(seconds, 2),
        "unseen_accessed": False,
    }
