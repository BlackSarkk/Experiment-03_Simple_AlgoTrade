"""Stage [2/6] — Phase A: strategy optimization.

    Optuna/TPE proposes strategy parameters
      -> production BacktestEngine simulates them on TRAIN
      -> production BacktestMetrics measures the result
      -> scoring.phase_a_score (v2) scores it
      -> next trial

TRAIN alone drives the search. VALIDATION is touched only after every trial has
finished, to screen a fixed shortlist for reporting; no VALIDATION number is ever
fed back into TPE. UNSEEN is never accessed — `unlock()` is not called anywhere in
this module, and the vault would raise if it were reached by accident.

The frozen Candidate #158 is not enqueued, not seeded, not used to narrow ranges
and not given any score treatment. The search is independent by construction; the
comparison against it belongs to a later stage.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import optuna
from optuna.samplers import TPESampler

from . import artifacts, diversity, evaluation, scoring, search_space
from .dashboard import PhaseADashboard, stage_status_from_preset

optuna.logging.set_verbosity(optuna.logging.WARNING)

SEED = 42
STUDY_NAME = "phase_a_strategy"

# Shortlist policy: proportional to the trial budget, with a floor and a cap so a
# 30-trial smoke run still produces something reviewable and a 3000-trial campaign
# does not produce a shortlist nobody can read.
SHORTLIST_FRACTION = 0.10
SHORTLIST_FLOOR = 5
SHORTLIST_CAP = 25


def shortlist_size(n_trials: int) -> int:
    return int(max(SHORTLIST_FLOOR, min(SHORTLIST_CAP, round(n_trials * SHORTLIST_FRACTION))))


@dataclass
class PhaseAResult:
    run_id: str
    run_path: str
    n_trials: int
    completed: int
    rejected: int
    min_trades: int
    min_valid_trades: int
    train_days: float
    seconds: float
    trials: List[Dict[str, Any]] = field(default_factory=list)
    shortlist: List[Dict[str, Any]] = field(default_factory=list)
    diverse: List[Dict[str, Any]] = field(default_factory=list)
    diversity_decisions: List[Any] = field(default_factory=list)
    best: Optional[Dict[str, Any]] = None


def _partition_days(part) -> float:
    return max(1.0, (part.end - part.start).total_seconds() / 86400.0)


def run(preset, prepared, run_id: Optional[str] = None,
        n_trials: Optional[int] = None, show_dashboard: bool = True,
        stage_status: Optional[Dict[int, str]] = None,
        campaign_started: Optional[float] = None) -> PhaseAResult:
    """Execute Phase A. Returns the result; writes artifacts as a side effect."""

    budget = int(n_trials if n_trials is not None else preset.resolved_trials()[0])
    run_id = run_id or artifacts.new_run_id(preset)
    path = artifacts.run_dir(run_id)

    train_days = _partition_days(prepared.train)
    valid_days = _partition_days(prepared.validation)
    min_trades = scoring.minimum_trades(train_days)
    min_valid_trades = scoring.thin_validation_threshold(
        min_trades, train_days, valid_days)

    sampler = TPESampler(seed=SEED)
    study = optuna.create_study(
        study_name=STUDY_NAME,
        direction="maximize",
        sampler=sampler,
        storage=artifacts.study_storage_url(path),
        load_if_exists=True,       # resume: existing completed trials are kept
    )

    already_done = len([t for t in study.trials
                        if t.state == optuna.trial.TrialState.COMPLETE])
    remaining = max(0, budget - already_done)

    records: List[Dict[str, Any]] = []
    started = time.time()

    status = dict(stage_status or stage_status_from_preset(preset.stages, running=2))

    with PhaseADashboard(budget, status, enabled=show_dashboard,
                         campaign_started=campaign_started) as dash:
        dash.completed = already_done

        def objective(trial: optuna.Trial) -> float:
            params = search_space.suggest(trial)

            if not search_space.is_coherent(params):
                trial.set_user_attr("rejection", "incoherent_rsi_bounds")
                dash.trial_done(None, scoring.REJECTED_SCORE, params, rejected=True)
                return scoring.REJECTED_SCORE

            metrics = evaluation.run_backtest(prepared, "train", params, preset)
            reason = scoring.rejection_reason(metrics, min_trades)
            score = scoring.phase_a_score(metrics, min_trades)

            trial.set_user_attr("rejection", reason or "")
            for key, value in (metrics or {}).items():
                trial.set_user_attr(key, value)

            dash.trial_done(metrics, score, params, rejected=reason is not None)
            return score

        if remaining:
            study.optimize(objective, n_trials=remaining, n_jobs=1,
                           show_progress_bar=False, catch=(Exception,))
        dash.finish_stage(2, "PASS")

    seconds = time.time() - started

    # ---- collect every completed trial -------------------------------------
    for trial in study.trials:
        if trial.state != optuna.trial.TrialState.COMPLETE:
            continue
        attrs = trial.user_attrs
        row = {
            "trial": trial.number,
            "state": trial.state.name,
            "score": trial.value,
            "rejection": attrs.get("rejection", ""),
        }
        for key in artifacts.TRIAL_FIELDS:
            if key in attrs:
                row[key] = attrs[key]
        row.update(trial.params)
        records.append(row)

    admissible = [r for r in records
                  if not r.get("rejection") and r["score"] > scoring.REJECTED_SCORE]
    admissible.sort(key=lambda r: r["score"], reverse=True)

    # ---- RAW TRAIN shortlist, screened on VALIDATION ------------------------
    # This artifact is the audit trail and is never de-duplicated.
    top_n = shortlist_size(budget)

    def screen(row, rank):
        params = {k: row[k] for k in search_space.PARAM_NAMES}
        valid = evaluation.run_backtest(prepared, "validation", params, preset)
        entry = {"rank": rank, "trial": row["trial"], "train_score": row["score"]}
        for key in ("net_return_pct", "net_pnl", "profit_factor", "sharpe",
                    "max_dd_pct", "trades", "win_rate", "fees"):
            entry[f"train_{key}"] = row.get(key)
            entry[f"valid_{key}"] = (valid or {}).get(key)
        entry["valid_rejection"] = scoring.rejection_reason(valid, 1) or ""
        entry["valid_status"] = _generalization_status(row, valid, min_valid_trades)
        entry.update(params)
        return entry

    shortlist = [screen(row, rank)
                 for rank, row in enumerate(admissible[:top_n], start=1)]

    # ---- diversity layer ----------------------------------------------------
    # Runs on the TRAIN ranking only. No VALIDATION or UNSEEN input, and nothing
    # here reaches the sampler — the study is already closed at this point.
    pool = []
    for rank, row in enumerate(admissible[:diversity.POOL_SIZE], start=1):
        entry = dict(row)
        entry["train_rank"] = rank
        pool.append(entry)

    diverse_rows, decisions = diversity.select(pool)
    diverse = [screen(row, row["train_rank"]) for row in diverse_rows]

    # ---- artifacts ----------------------------------------------------------
    artifacts.write_trials(path, records, search_space.PARAM_NAMES)
    artifacts.write_shortlist(path, shortlist, search_space.PARAM_NAMES)
    artifacts.write_diverse_shortlist(path, diverse, search_space.PARAM_NAMES)
    artifacts.write_diversity_decisions(path, decisions)
    artifacts.write_manifest(path, _manifest(
        run_id, preset, prepared, budget, len(records),
        len(records) - len(admissible), min_trades, seconds, top_n,
        train_days, valid_days, min_valid_trades, len(diverse)))

    return PhaseAResult(
        run_id=run_id,
        run_path=path,
        n_trials=budget,
        completed=len(records),
        rejected=len(records) - len(admissible),
        min_trades=min_trades,
        min_valid_trades=min_valid_trades,
        train_days=train_days,
        seconds=seconds,
        trials=records,
        shortlist=shortlist,
        diverse=diverse,
        diversity_decisions=decisions,
        best=admissible[0] if admissible else None,
    )


def _generalization_status(train_row, valid, min_valid_trades) -> str:
    """How a finalist behaved out of sample. Reporting only — never fed to TPE."""
    if not valid or valid.get("trades", 0) == 0:
        return "NO_TRADES"
    if valid["trades"] < min_valid_trades:
        return "THIN"
    if valid["net_return_pct"] < 0 or valid["profit_factor"] < 1.0:
        return "COLLAPSES"
    train_pf = float(train_row.get("profit_factor") or 0.0)
    if train_pf > 0 and valid["profit_factor"] < train_pf * 0.6:
        return "DEGRADES"
    return "GENERALIZES"


def _manifest(run_id, preset, prepared, budget, completed, rejected,
              min_trades, seconds, shortlist_n,
              train_days, valid_days, min_valid_trades,
              diverse_n) -> Dict[str, Any]:
    """Everything needed to reproduce or audit this Phase-A run.

    Contains no UNSEEN metric: the vault is locked for the whole of Phase A, so
    there is nothing to record and nothing to leak.
    """
    return {
        "run_id": run_id,
        "stage": "phase_a_strategy_optimization",
        "preset_path": preset.path,
        "preset_snapshot": artifacts.preset_snapshot(preset),
        "requested_history": {
            "mode": preset.history.mode,
            "days": preset.history.days,
            "start_date": str(preset.history.start_date) if preset.history.start_date else None,
            "end_date": str(preset.history.end_date) if preset.history.end_date else None,
        },
        "resolved_history": {
            "start": str(prepared.requested_start),
            "end": str(prepared.requested_end),
            "candles": (prepared.train.n_candles + prepared.validation.n_candles
                        + prepared.unseen_candles),
        },
        "data_checksum": prepared.checksum,
        "warmup": {
            "candles": prepared.warmup_candles,
            "start": str(prepared.warmup_start),
            "end": str(prepared.warmup_end),
        },
        "partitions": {
            "train": {"start": str(prepared.train.start), "end": str(prepared.train.end),
                      "candles": prepared.train.n_candles},
            "validation": {"start": str(prepared.validation.start),
                           "end": str(prepared.validation.end),
                           "candles": prepared.validation.n_candles},
            "unseen": {"start": str(prepared.unseen_start), "end": str(prepared.unseen_end),
                       "candles": prepared.unseen_candles, "state": "LOCKED"},
        },
        "seed": SEED,
        "sampler": "TPESampler",
        "study_name": STUDY_NAME,
        "trial_budget": budget,
        "trials_completed": completed,
        "trials_rejected": rejected,
        "shortlist_size": shortlist_n,
        "neutral_risk": evaluation.NEUTRAL_RISK.as_dict(),
        "search_space": search_space.describe(),
        "scoring": scoring.describe(min_trades),
        "diversity": dict(diversity.describe(diversity.MIN_DISTANCE,
                                             diversity.TARGET_DIVERSE,
                                             diversity.POOL_SIZE),
                          selected=diverse_n),
        "trade_gate": {
            "train_days": round(train_days, 1),
            "min_train_trades": min_trades,
            "validation_days": round(valid_days, 1),
            "min_validation_trades": min_valid_trades,
        },
        "direction": {
            "long_enabled": preset.direction.long_enabled,
            "short_enabled": preset.direction.short_enabled,
            "model": "shared parameter set, one combined backtest per trial",
        },
        "runtime_seconds": round(seconds, 2),
        "unseen_accessed": False,
    }
