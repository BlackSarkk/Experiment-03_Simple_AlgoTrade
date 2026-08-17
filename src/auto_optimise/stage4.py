"""Stage [4/6] — risk management optimization.

    Stage-3 advancing strategies (frozen)
      -> per-strategy TPE search over leverage / risk% / allocation% on TRAIN
      -> TRAIN shortlist, de-duplicated across policies
      -> VALIDATION screening (never reaches TPE)
      -> deterministic risk winner for that strategy
      -> risk_gate_v1 + risk_score_v1 across all strategies
      -> top N strategy+risk combinations advance to stage [5/6]

Every strategy gets the identical search space, objective, gate and budget.
Nothing here asks anyone anything, and `unlock()` is never called.
"""

import csv
import json
import os
import time
from typing import Any, Dict, List, Optional

import optuna
from optuna.samplers import TPESampler

from . import (artifacts, evaluation, risk_policy, scoring, search_space,
               stage3)
from .dashboard import Stage4Dashboard, stage_status_from_preset

optuna.logging.set_verbosity(optuna.logging.WARNING)

MAX_ADVANCING = 3

# Stage 4 searches 3 dimensions, not 11, and its grid is only ~2,100 points.
# Phase A's budget is meaningless here, so `trials: "auto"` maps to a fixed
# per-strategy figure rather than anything derived from the strategy search.
# 150 matches the only measured precedent in this repo
# (`deep_15m_optimizer.stage5_risk`) and gives TPE ~7% coverage of the grid.
AUTO_TRIALS_PER_STRATEGY = 150
MIN_TRIALS_PER_STRATEGY = 60
MAX_TRIALS_PER_STRATEGY = 300

SHORTLIST_SIZE = 10
# Two policies whose three parameters are all within this fraction of their range
# are the same policy for practical purposes; the shortlist keeps the better one.
SHORTLIST_MIN_DISTANCE = 0.12

TRIAL_FILE = "stage4_risk_trials.csv"
SHORTLIST_FILE = "stage4_risk_shortlist.csv"

TRIAL_FIELDS = ["train_rank", "trial_id", "risk_trial", "leverage",
                "risk_per_trade_pct", "max_position_allocation_pct", "valid",
                "score", "gate_failures", "net_return_pct", "net_pnl",
                "profit_factor", "sharpe", "max_dd_pct", "trades"]

SHORTLIST_FIELDS = ["train_rank", "trial_id", "rank", "leverage",
                    "risk_per_trade_pct", "max_position_allocation_pct",
                    "train_score", "train_net_return_pct", "train_profit_factor",
                    "train_sharpe", "train_max_dd_pct", "train_trades",
                    "valid_net_return_pct", "valid_profit_factor", "valid_sharpe",
                    "valid_max_dd_pct", "valid_trades", "valid_status", "selected"]


def resolve_trials(preset) -> int:
    """Stage-4 budget. Independent of the Phase-A strategy budget."""
    raw = preset.trials
    if isinstance(raw, str):                      # "auto"
        return AUTO_TRIALS_PER_STRATEGY
    # An explicit Phase-A integer is a statement about search effort, not about
    # this 3-D space; scale it down and clamp into a sane band.
    return int(max(MIN_TRIALS_PER_STRATEGY,
                   min(MAX_TRIALS_PER_STRATEGY, round(int(raw) / 5))))


def load_advancing(run_path: str) -> List[Dict[str, Any]]:
    """Read stage [3/6]'s decision. Stage-3 artifacts are never rewritten."""
    path = os.path.join(run_path, "stage3_advancing.csv")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"stage-3 advancing set not found: {path}")
    out = []
    with open(path) as fh:
        for row in csv.DictReader(fh):
            cand = {"train_rank": int(row["train_rank"]),
                    "trial_id": int(row["trial"]),
                    "advance_rank": int(row["advance_rank"])}
            for name in search_space.PARAM_NAMES:
                cand[name] = (int(float(row[name]))
                              if name in search_space.INT_PARAMS
                              else float(row[name]))
            out.append(cand)
    out.sort(key=lambda c: c["advance_rank"])
    return out


def _policy_distance(a, b) -> float:
    """Normalised RMS distance between two risk policies."""
    ranges = {"leverage": risk_policy.LEVERAGE,
              "risk_per_trade_pct": risk_policy.RISK_PER_TRADE_PCT,
              "max_position_allocation_pct": risk_policy.MAX_ALLOCATION_PCT}
    total = 0.0
    for name, (low, high, _step) in ranges.items():
        span = high - low
        total += ((float(a[name]) - float(b[name])) / span) ** 2
    return (total / len(ranges)) ** 0.5


def _shortlist(ranked: List[Dict[str, Any]], size: int) -> List[Dict[str, Any]]:
    """Best-first, skipping policies that duplicate one already taken."""
    out: List[Dict[str, Any]] = []
    for row in ranked:
        if len(out) >= size:
            break
        if any(_policy_distance(row, kept) < SHORTLIST_MIN_DISTANCE for kept in out):
            continue
        out.append(row)
    return out


def run(preset, prepared, run_path: str, progress=None,
        n_trials: Optional[int] = None, max_advancing: int = MAX_ADVANCING,
        show_dashboard: bool = False,
        campaign_started: Optional[float] = None) -> Dict[str, Any]:
    """Execute stage 4 against a completed stage-3 run directory."""

    def say(msg):
        if progress is not None:
            progress(msg)

    started = time.time()
    strategies = load_advancing(run_path)
    if not strategies:
        raise RuntimeError("stage 3 advanced no candidates; stage 4 has nothing to do")

    budget = int(n_trials if n_trials is not None else resolve_trials(preset))

    train_days = (prepared.train.end - prepared.train.start).total_seconds() / 86400.0
    valid_days = (prepared.validation.end - prepared.validation.start).total_seconds() / 86400.0
    min_trades = scoring.minimum_trades(train_days)
    min_valid_trades = scoring.thin_validation_threshold(min_trades, train_days, valid_days)

    trial_ledger = stage3._Ledger(os.path.join(run_path, TRIAL_FILE),
                                  TRIAL_FIELDS, ["train_rank", "risk_trial"],
                                  group_field="train_rank")

    status = stage_status_from_preset(preset.stages, running=4)
    results: List[Dict[str, Any]] = []
    shortlist_rows: List[Dict[str, Any]] = []

    with Stage4Dashboard(budget, len(strategies), status, enabled=show_dashboard,
                         campaign_started=campaign_started) as dash:
        for idx, strat in enumerate(strategies, start=1):
            params = {k: strat[k] for k in search_space.PARAM_NAMES}
            rank = strat["train_rank"]
            dash.set_candidate(idx, rank)
            say(f"strategy {idx}/{len(strategies)} (TRAIN rank {rank}) "
                f"- {budget} risk trials")

            # Neutral-policy trade count: the reference for the entry-retention
            # gate. A sizing policy that loses entries is trading a different
            # strategy than the one stage 3 certified.
            neutral = evaluation.run_backtest(prepared, "train", params, preset)
            neutral_trades = int((neutral or {}).get("trades", 0)) or None

            # Resume: the Optuna study replays its own completed trials from
            # SQLite, so only genuinely new ones reach the objective. The ledger
            # numbering continues from where it stopped rather than restarting.
            done = {int(r["risk_trial"]) for r in trial_ledger.for_candidate(rank)}
            counter = {"n": (max(done) + 1) if done else 0}

            def objective(trial: optuna.Trial) -> float:
                policy = risk_policy.suggest(trial)
                n = counter["n"]
                counter["n"] += 1

                metrics = evaluation.run_on_context(
                    prepared.context_for("train"), params, preset,
                    risk_policy.as_fractions(policy))
                failures = risk_policy.gate_failures(metrics, min_trades, neutral_trades)
                score = risk_policy.risk_score_v1(metrics, min_trades, neutral_trades)

                row = {"train_rank": rank, "trial_id": strat["trial_id"],
                       "risk_trial": n, "valid": bool(metrics),
                       "score": round(score, 4),
                       "gate_failures": "; ".join(failures), **policy}
                if metrics:
                    row.update({k: metrics[k] for k in
                                ("net_return_pct", "net_pnl", "profit_factor",
                                 "sharpe", "max_dd_pct", "trades")})
                trial_ledger.append(row)

                dash.trial_done(policy, metrics, score, rejected=bool(failures))
                return score

            study = optuna.create_study(
                study_name=f"stage4_risk_rank{rank}",
                direction="maximize",
                sampler=TPESampler(seed=risk_policy.SEED),
                storage=artifacts.study_storage_url_named(run_path, "stage4_risk"),
                load_if_exists=True,
            )
            already = len([t for t in study.trials
                           if t.state == optuna.trial.TrialState.COMPLETE])
            if budget > already:
                study.optimize(objective, n_trials=budget - already, n_jobs=1,
                               show_progress_bar=False, catch=(Exception,))
            dash.candidate_finished()

            # ---- TRAIN shortlist, then VALIDATION -------------------------
            rows = [dict(r) for r in trial_ledger.for_candidate(rank)
                    if float(r["score"] or 0) > 0]
            for r in rows:
                for k in ("leverage", "risk_per_trade_pct",
                          "max_position_allocation_pct", "score"):
                    r[k] = float(r[k])
            rows.sort(key=lambda r: -r["score"])
            shortlist = _shortlist(rows, SHORTLIST_SIZE)

            say(f"strategy {idx}/{len(strategies)} - validating "
                f"{len(shortlist)} risk policies")
            best = None
            for srank, row in enumerate(shortlist, start=1):
                policy = {k: row[k] for k in risk_policy.PARAM_NAMES}
                valid = evaluation.run_backtest(prepared, "validation", params,
                                                preset,
                                                risk_policy.as_fractions(policy))
                train_metrics = {k: float(row[k]) for k in
                                 ("net_return_pct", "profit_factor", "sharpe",
                                  "max_dd_pct")}
                train_metrics["trades"] = int(float(row["trades"]))
                entry = {
                    "train_rank": rank, "trial_id": strat["trial_id"],
                    "rank": srank, **policy,
                    "train_score": row["score"],
                    "valid_status": risk_policy.classify(train_metrics, valid,
                                                         min_valid_trades),
                    "selected": False,
                }
                for k in ("net_return_pct", "profit_factor", "sharpe",
                          "max_dd_pct", "trades"):
                    entry[f"train_{k}"] = train_metrics[k]
                    entry[f"valid_{k}"] = (valid or {}).get(k)
                shortlist_rows.append(entry)

                # Deterministic winner: the best TRAIN score among policies that
                # also survived out of sample. VALIDATION filters, it never ranks.
                if best is None and entry["valid_status"] == "GENERALIZES":
                    best = entry

            if best is None:
                say(f"strategy {idx}: no risk policy generalised")
                results.append({"train_rank": rank, "trial_id": strat["trial_id"],
                                "passed": False, "reason": "no_generalizing_risk_policy",
                                "risk_score": 0.0, **params})
                continue

            best["selected"] = True
            results.append({
                "train_rank": rank, "trial_id": strat["trial_id"], "passed": True,
                "reason": "", "risk_score": best["train_score"],
                "leverage": best["leverage"],
                "risk_per_trade_pct": best["risk_per_trade_pct"],
                "max_position_allocation_pct": best["max_position_allocation_pct"],
                **{f"train_{k}": best[f"train_{k}"] for k in
                   ("net_return_pct", "profit_factor", "sharpe", "max_dd_pct", "trades")},
                **{f"valid_{k}": best[f"valid_{k}"] for k in
                   ("net_return_pct", "profit_factor", "sharpe", "max_dd_pct", "trades")},
                **params,
            })
            dash.set_best(rank, best)

        survivors = [r for r in results if r["passed"]]
        survivors.sort(key=lambda r: (-r["risk_score"], r["train_rank"]))
        advancing = survivors[:max_advancing]
        dash.finish_stage(4, "PASS" if survivors else "FAILED")

    _write_shortlist(run_path, shortlist_rows)
    _write_candidates(run_path, results)
    _write_advancing(run_path, advancing)
    manifest = _manifest(preset, prepared, run_path, strategies, budget,
                         results, survivors, advancing, min_trades,
                         min_valid_trades, len(trial_ledger.rows),
                         time.time() - started)
    with open(os.path.join(run_path, "stage4_manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2, default=str)

    return {"strategies": strategies, "results": results,
            "shortlist": shortlist_rows, "survivors": survivors,
            "advancing": advancing, "trials_per_strategy": budget,
            "seconds": time.time() - started, "manifest": manifest,
            "failed": not survivors}


def skipped(preset, run_path: str) -> Dict[str, Any]:
    """`stages.risk_management == false`: forward stage 3 on the neutral policy."""
    strategies = load_advancing(run_path)
    neutral = evaluation.NEUTRAL_RISK.as_dict()
    advancing = []
    for strat in strategies[:MAX_ADVANCING]:
        row = dict(strat)
        row.update({
            "passed": True, "reason": "risk_management_disabled",
            "risk_score": 0.0,
            "leverage": neutral["leverage"],
            "risk_per_trade_pct": neutral["risk_per_trade_pct"] * 100.0,
            "max_position_allocation_pct": neutral["max_position_allocation_pct"] * 100.0,
        })
        advancing.append(row)
    _write_advancing(run_path, advancing)
    manifest = {"stage": "stage_4_risk_management", "skipped": True,
                "reason": "stages.risk_management == false",
                "risk_policy": "neutral (stage-A default)",
                "neutral_risk": neutral,
                "advancing_train_ranks": [r["train_rank"] for r in advancing],
                "selection_required_from_human": False,
                "unseen_accessed": False}
    with open(os.path.join(run_path, "stage4_manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2, default=str)
    return {"skipped": True, "advancing": advancing, "manifest": manifest,
            "failed": False}


# --- artifacts --------------------------------------------------------------

CANDIDATE_FIELDS = (["train_rank", "trial_id", "passed", "reason", "risk_score"]
                    + list(risk_policy.PARAM_NAMES)
                    + [f"train_{k}" for k in ("net_return_pct", "profit_factor",
                                              "sharpe", "max_dd_pct", "trades")]
                    + [f"valid_{k}" for k in ("net_return_pct", "profit_factor",
                                              "sharpe", "max_dd_pct", "trades")]
                    + list(search_space.PARAM_NAMES))

ADVANCING_FIELDS = (["advance_rank", "train_rank", "trial_id", "risk_score"]
                    + list(risk_policy.PARAM_NAMES)
                    + list(search_space.PARAM_NAMES))


def _write(path, fields, rows):
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_shortlist(run_path, rows):
    _write(os.path.join(run_path, SHORTLIST_FILE), SHORTLIST_FIELDS, rows)


def _write_candidates(run_path, results):
    rows = sorted(results, key=lambda r: (not r["passed"], -r["risk_score"],
                                          r["train_rank"]))
    _write(os.path.join(run_path, "stage4_candidates.csv"), CANDIDATE_FIELDS, rows)


def _write_advancing(run_path, advancing):
    rows = []
    for i, r in enumerate(advancing, start=1):
        rows.append(dict(r, advance_rank=i))
    _write(os.path.join(run_path, "stage4_advancing.csv"), ADVANCING_FIELDS, rows)


def _manifest(preset, prepared, run_path, strategies, budget, results,
              survivors, advancing, min_trades, min_valid_trades,
              evaluations, seconds) -> Dict[str, Any]:
    stage3_manifest = {}
    path = os.path.join(run_path, "stage3_manifest.json")
    if os.path.isfile(path):
        with open(path) as fh:
            stage3_manifest = json.load(fh)
    return {
        "stage": "stage_4_risk_management",
        "skipped": False,
        "phase_a_run_id": stage3_manifest.get("phase_a_run_id"),
        "phase_a_data_checksum": stage3_manifest.get("phase_a_data_checksum"),
        "input_train_ranks": [s["train_rank"] for s in strategies],
        "input_trial_ids": [s["trial_id"] for s in strategies],
        "risk_policy": risk_policy.describe(),
        "sampler": "TPESampler",
        "seed": risk_policy.SEED,
        "trials_per_strategy": budget,
        "total_trials": budget * len(strategies),
        "trial_budget_source": ("auto" if isinstance(preset.trials, str)
                                else f"derived from preset trials={preset.trials}"),
        "evaluations_recorded": evaluations,
        "shortlist_size": SHORTLIST_SIZE,
        "shortlist_min_distance": SHORTLIST_MIN_DISTANCE,
        "partitions_used": ["TRAIN", "VALIDATION"],
        "train": {"start": str(prepared.train.start), "end": str(prepared.train.end)},
        "validation": {"start": str(prepared.validation.start),
                       "end": str(prepared.validation.end)},
        "min_train_trades": min_trades,
        "min_validation_trades": min_valid_trades,
        "selected_risk_per_strategy": {
            str(r["train_rank"]): {k: r.get(k) for k in risk_policy.PARAM_NAMES}
            for r in results if r["passed"]},
        "survivors": [r["train_rank"] for r in survivors],
        "advancing_train_ranks": [r["train_rank"] for r in advancing],
        "max_advancing": MAX_ADVANCING,
        "stage_failed": not survivors,
        "selection_required_from_human": False,
        "runtime_seconds": round(seconds, 2),
        "unseen_accessed": False,
    }
