"""Stage [5/6] — Bollinger filter optimization.

    Stage-4 advancing candidates (strategy AND risk frozen)
      -> Bollinger OFF baseline, evaluated first
      -> per-candidate TPE search over the 6 filter fields on TRAIN
      -> TRAIN shortlist -> VALIDATION screening (never reaches TPE)
      -> bollinger_gate_v1 decides ON or OFF, per candidate
      -> ranked strategy+risk+filter combinations advance to stage [6/6]

OFF is a real competitor: it is scored 0.0 by construction and a filter must beat
it on merit. Concluding OFF for every candidate is a valid outcome.
"""

import csv
import json
import os
import time
from typing import Any, Dict, List, Optional

import optuna
from optuna.samplers import TPESampler

from . import (artifacts, bollinger_policy, evaluation, risk_policy, scoring,
               search_space, stage3)
from .dashboard import Stage5Dashboard, stage_status_from_preset

optuna.logging.set_verbosity(optuna.logging.WARNING)

MAX_ADVANCING = 3

# Six dimensions — twice Stage 4's and half Phase A's. 300 trials gives TPE room
# in a 6-D space without the runaway cost of a full strategy search.
AUTO_TRIALS_PER_CANDIDATE = 300
MIN_TRIALS_PER_CANDIDATE = 80
MAX_TRIALS_PER_CANDIDATE = 600

SHORTLIST_SIZE = 8

TRIAL_FILE = "stage5_bollinger_trials.csv"
SHORTLIST_FILE = "stage5_bollinger_shortlist.csv"

TRIAL_FIELDS = (["train_rank", "trial_id", "filter_trial", "valid", "score",
                 "noop"] + list(bollinger_policy.PARAM_NAMES)
                + ["net_return_pct", "net_pnl", "profit_factor", "sharpe",
                   "max_dd_pct", "trades", "raw_signals", "signals_blocked",
                   "signals_passed", "trade_retention"])

SHORTLIST_FIELDS = (["train_rank", "trial_id", "rank", "score"]
                    + list(bollinger_policy.PARAM_NAMES)
                    + ["train_net_return_pct", "train_profit_factor",
                       "train_sharpe", "train_max_dd_pct", "train_trades",
                       "train_retention",
                       "valid_net_return_pct", "valid_profit_factor",
                       "valid_sharpe", "valid_max_dd_pct", "valid_trades",
                       "valid_retention", "gate", "gate_failures", "selected"])

CANDIDATE_FIELDS = (["train_rank", "trial_id", "bollinger_enabled", "decision",
                     "gate_failures", "bollinger_score"]
                    + list(bollinger_policy.PARAM_NAMES)
                    + ["off_train_return", "off_train_pf", "off_train_dd",
                       "off_train_trades", "off_valid_return", "off_valid_pf",
                       "off_valid_dd", "off_valid_trades",
                       "on_train_return", "on_train_pf", "on_train_dd",
                       "on_train_trades", "on_valid_return", "on_valid_pf",
                       "on_valid_dd", "on_valid_trades",
                       "train_retention", "valid_retention",
                       "raw_signals", "signals_blocked",
                       "d_valid_return", "d_valid_pf", "d_valid_dd"]
                    + list(risk_policy.PARAM_NAMES)
                    + list(search_space.PARAM_NAMES))

ADVANCING_FIELDS = (["advance_rank", "train_rank", "trial_id", "final_score",
                     "bollinger_enabled"]
                    + list(bollinger_policy.PARAM_NAMES)
                    + list(risk_policy.PARAM_NAMES)
                    + list(search_space.PARAM_NAMES))


def resolve_trials(preset) -> int:
    raw = getattr(preset, "trials", "auto")
    if isinstance(raw, str):
        return AUTO_TRIALS_PER_CANDIDATE
    return int(max(MIN_TRIALS_PER_CANDIDATE,
                   min(MAX_TRIALS_PER_CANDIDATE, round(int(raw) * 0.4))))


def load_advancing(run_path: str) -> List[Dict[str, Any]]:
    """Read stage [4/6]'s decision, including its selected risk policy."""
    path = os.path.join(run_path, "stage4_advancing.csv")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"stage-4 advancing set not found: {path}")
    out = []
    with open(path) as fh:
        for row in csv.DictReader(fh):
            cand = {"train_rank": int(row["train_rank"]),
                    "trial_id": int(row["trial_id"]),
                    "advance_rank": int(row["advance_rank"])}
            for name in risk_policy.PARAM_NAMES:
                cand[name] = float(row[name])
            for name in search_space.PARAM_NAMES:
                cand[name] = (int(float(row[name]))
                              if name in search_space.INT_PARAMS
                              else float(row[name]))
            out.append(cand)
    out.sort(key=lambda c: c["advance_rank"])
    return out


def _retention(on, off) -> float:
    if not on or not off or not off.get("trades"):
        return 0.0
    return int(on["trades"]) / float(off["trades"])


def run(preset, prepared, run_path: str, progress=None,
        n_trials: Optional[int] = None, max_advancing: int = MAX_ADVANCING,
        show_dashboard: bool = False,
        campaign_started: Optional[float] = None) -> Dict[str, Any]:

    def say(msg):
        if progress is not None:
            progress(msg)

    started = time.time()
    candidates = load_advancing(run_path)
    if not candidates:
        raise RuntimeError("stage 4 advanced no candidates; stage 5 has nothing to do")

    budget = int(n_trials if n_trials is not None else resolve_trials(preset))

    train_days = (prepared.train.end - prepared.train.start).total_seconds() / 86400.0
    valid_days = (prepared.validation.end - prepared.validation.start).total_seconds() / 86400.0
    min_trades = scoring.minimum_trades(train_days)
    min_valid_trades = scoring.thin_validation_threshold(min_trades, train_days,
                                                         valid_days)

    ledger = stage3._Ledger(os.path.join(run_path, TRIAL_FILE), TRIAL_FIELDS,
                            ["train_rank", "filter_trial"], group_field="train_rank")

    status = stage_status_from_preset(preset.stages, running=5)
    results: List[Dict[str, Any]] = []
    shortlist_rows: List[Dict[str, Any]] = []

    with Stage5Dashboard(budget, len(candidates), status, enabled=show_dashboard,
                         campaign_started=campaign_started) as dash:
        for idx, cand in enumerate(candidates, start=1):
            rank = cand["train_rank"]
            params = {k: cand[k] for k in search_space.PARAM_NAMES}
            risk = risk_policy.as_fractions(
                {k: cand[k] for k in risk_policy.PARAM_NAMES})

            dash.set_candidate(idx, rank)

            # ---- OFF baseline, always, before any search ------------------
            say(f"candidate {idx}/{len(candidates)} (rank {rank}) - OFF baseline")
            off_train = evaluation.run_backtest(prepared, "train", params, preset, risk)
            off_valid = evaluation.run_backtest(prepared, "validation", params,
                                                preset, risk)
            if not off_train or not off_valid:
                results.append({"train_rank": rank, "trial_id": cand["trial_id"],
                                "bollinger_enabled": False,
                                "decision": "OFF", "bollinger_score": 0.0,
                                "gate_failures": "off_baseline_unavailable",
                                **cand})
                continue
            dash.set_off_baseline(off_train)

            say(f"candidate {idx}/{len(candidates)} (rank {rank}) - "
                f"{budget} filter trials")
            done = {int(r["filter_trial"]) for r in ledger.for_candidate(rank)}
            counter = {"n": (max(done) + 1) if done else 0}

            def objective(trial: optuna.Trial) -> float:
                filt = bollinger_policy.suggest(trial)
                n = counter["n"]
                counter["n"] += 1

                metrics = evaluation.run_backtest(
                    prepared, "train", params, preset, risk,
                    bollinger_policy.to_filter_dict(filt))
                score = bollinger_policy.bollinger_score_v1(metrics, off_train,
                                                            min_trades)
                retention = _retention(metrics, off_train)

                row = {"train_rank": rank, "trial_id": cand["trial_id"],
                       "filter_trial": n, "valid": bool(metrics),
                       "score": round(score, 4),
                       "noop": bollinger_policy.is_noop(filt),
                       "trade_retention": round(retention, 4), **filt}
                if metrics:
                    row.update({k: metrics.get(k) for k in
                                ("net_return_pct", "net_pnl", "profit_factor",
                                 "sharpe", "max_dd_pct", "trades", "raw_signals",
                                 "signals_blocked", "signals_passed")})
                ledger.append(row)
                dash.trial_done(filt, metrics, score, retention)
                return score

            study = optuna.create_study(
                study_name=f"stage5_bollinger_rank{rank}",
                direction="maximize",
                sampler=TPESampler(seed=bollinger_policy.SEED),
                storage=artifacts.study_storage_url_named(run_path, "stage5_bollinger"),
                load_if_exists=True,
            )
            already = len([t for t in study.trials
                           if t.state == optuna.trial.TrialState.COMPLETE])
            if budget > already:
                study.optimize(objective, n_trials=budget - already, n_jobs=1,
                               show_progress_bar=False, catch=(Exception,))

            # ---- TRAIN shortlist, then VALIDATION -------------------------
            rows = [dict(r) for r in ledger.for_candidate(rank)
                    if str(r.get("valid")).lower() in ("true", "1")]
            for r in rows:
                r["score"] = float(r["score"])
            # A no-op filter is OFF wearing a costume; it must not occupy a slot.
            rows = [r for r in rows if str(r.get("noop")).lower() not in ("true", "1")]
            rows.sort(key=lambda r: -r["score"])
            shortlist = rows[:SHORTLIST_SIZE]

            say(f"candidate {idx}/{len(candidates)} - validating "
                f"{len(shortlist)} filters")
            best_entry = None
            for srank, row in enumerate(shortlist, start=1):
                filt = {k: (int(float(row[k])) if k in bollinger_policy.INT_PARAMS
                            else float(row[k]))
                        for k in bollinger_policy.PARAM_NAMES}
                on_train = {k: float(row[k]) for k in
                            ("net_return_pct", "profit_factor", "sharpe", "max_dd_pct")}
                on_train["trades"] = int(float(row["trades"]))
                on_valid = evaluation.run_backtest(
                    prepared, "validation", params, preset, risk,
                    bollinger_policy.to_filter_dict(filt))

                failures = bollinger_policy.gate_failures(
                    on_train, off_train, on_valid, off_valid,
                    row["score"], min_trades)

                entry = {"train_rank": rank, "trial_id": cand["trial_id"],
                         "rank": srank, "score": row["score"], **filt,
                         "train_retention": round(_retention(on_train, off_train), 4),
                         "valid_retention": round(_retention(on_valid, off_valid), 4),
                         "gate": "PASS" if not failures else "FAIL",
                         "gate_failures": "; ".join(failures), "selected": False}
                for k in ("net_return_pct", "profit_factor", "sharpe",
                          "max_dd_pct", "trades"):
                    entry[f"train_{k}"] = on_train[k]
                    entry[f"valid_{k}"] = (on_valid or {}).get(k)
                shortlist_rows.append(entry)

                if best_entry is None and not failures:
                    best_entry = (entry, filt, on_train, on_valid)

            results.append(_candidate_result(cand, params, off_train, off_valid,
                                             best_entry, min_valid_trades))
            if best_entry is not None:
                best_entry[0]["selected"] = True
                dash.set_best(best_entry[0])

        # ---- rank the complete strategy+risk+filter combinations ----------
        for r in results:
            r["final_score"] = _final_score(r)
        ranked = sorted(results, key=lambda r: (-r["final_score"], r["train_rank"]))
        advancing = ranked[:max_advancing]
        dash.finish_stage(5, "PASS")

    _write(run_path, SHORTLIST_FILE, SHORTLIST_FIELDS, shortlist_rows)
    _write(run_path, "stage5_candidates.csv", CANDIDATE_FIELDS, ranked)
    _write_advancing(run_path, advancing)
    manifest = _manifest(preset, prepared, run_path, candidates, budget, results,
                         advancing, min_trades, min_valid_trades,
                         len(ledger.rows), time.time() - started)
    with open(os.path.join(run_path, "stage5_manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2, default=str)

    return {"candidates": candidates, "results": ranked,
            "shortlist": shortlist_rows, "advancing": advancing,
            "trials_per_candidate": budget, "seconds": time.time() - started,
            "manifest": manifest, "failed": not advancing}


def _candidate_result(cand, params, off_train, off_valid, best, min_valid_trades):
    row = {"train_rank": cand["train_rank"], "trial_id": cand["trial_id"],
           "off_train_return": off_train["net_return_pct"],
           "off_train_pf": off_train["profit_factor"],
           "off_train_dd": off_train["max_dd_pct"],
           "off_train_trades": off_train["trades"],
           "off_valid_return": off_valid["net_return_pct"],
           "off_valid_pf": off_valid["profit_factor"],
           "off_valid_dd": off_valid["max_dd_pct"],
           "off_valid_trades": off_valid["trades"]}
    row.update({k: cand[k] for k in risk_policy.PARAM_NAMES})
    row.update(params)

    if best is None:
        row.update({"bollinger_enabled": False, "decision": "OFF",
                    "bollinger_score": 0.0,
                    "gate_failures": "no filter beat the OFF baseline",
                    **{k: 0 for k in bollinger_policy.PARAM_NAMES}})
        return row

    entry, filt, on_train, on_valid = best
    d_valid = bollinger_policy.deltas(on_valid, off_valid)
    row.update({"bollinger_enabled": True, "decision": "ON",
                "bollinger_score": entry["score"], "gate_failures": "", **filt,
                "on_train_return": on_train["net_return_pct"],
                "on_train_pf": on_train["profit_factor"],
                "on_train_dd": on_train["max_dd_pct"],
                "on_train_trades": on_train["trades"],
                "on_valid_return": (on_valid or {}).get("net_return_pct"),
                "on_valid_pf": (on_valid or {}).get("profit_factor"),
                "on_valid_dd": (on_valid or {}).get("max_dd_pct"),
                "on_valid_trades": (on_valid or {}).get("trades"),
                "train_retention": entry["train_retention"],
                "valid_retention": entry["valid_retention"],
                "raw_signals": None, "signals_blocked": None,
                "d_valid_return": d_valid.get("d_return_pct"),
                "d_valid_pf": d_valid.get("d_profit_factor"),
                "d_valid_dd": d_valid.get("d_max_dd_pct")})
    return row


def _final_score(r) -> float:
    """Rank complete systems. A filter's score is a bonus over its OFF baseline,
    which is already the system stages 3-4 certified."""
    base = float(r.get("off_valid_pf") or 0.0) * 10.0 + float(
        r.get("off_valid_return") or 0.0) * 0.1
    return base + float(r.get("bollinger_score") or 0.0)


def skipped(preset, run_path: str) -> Dict[str, Any]:
    """`stages.bollinger == false`: forward stage 4 with the filter disabled."""
    candidates = load_advancing(run_path)
    advancing = []
    for cand in candidates[:MAX_ADVANCING]:
        row = dict(cand)
        row.update({"bollinger_enabled": False, "decision": "OFF",
                    "final_score": 0.0,
                    **{k: 0 for k in bollinger_policy.PARAM_NAMES}})
        advancing.append(row)
    _write_advancing(run_path, advancing)
    manifest = {"stage": "stage_5_bollinger", "skipped": True,
                "reason": "stages.bollinger == false",
                "bollinger_enabled_for_all": False,
                "advancing_train_ranks": [r["train_rank"] for r in advancing],
                "selection_required_from_human": False,
                "unseen_accessed": False}
    with open(os.path.join(run_path, "stage5_manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2, default=str)
    return {"skipped": True, "advancing": advancing, "manifest": manifest,
            "failed": False}


def _write(run_path, name, fields, rows):
    with open(os.path.join(run_path, name), "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _write_advancing(run_path, advancing):
    rows = [dict(r, advance_rank=i) for i, r in enumerate(advancing, start=1)]
    _write(run_path, "stage5_advancing.csv", ADVANCING_FIELDS, rows)


def _manifest(preset, prepared, run_path, candidates, budget, results, advancing,
              min_trades, min_valid_trades, evaluations, seconds):
    stage4 = {}
    path = os.path.join(run_path, "stage4_manifest.json")
    if os.path.isfile(path):
        with open(path) as fh:
            stage4 = json.load(fh)
    on = [r["train_rank"] for r in results if r.get("bollinger_enabled")]
    off = [r["train_rank"] for r in results if not r.get("bollinger_enabled")]
    return {
        "stage": "stage_5_bollinger",
        "skipped": False,
        "phase_a_run_id": stage4.get("phase_a_run_id"),
        "phase_a_data_checksum": stage4.get("phase_a_data_checksum"),
        "input_train_ranks": [c["train_rank"] for c in candidates],
        "bollinger": bollinger_policy.describe(),
        "sampler": "TPESampler",
        "seed": bollinger_policy.SEED,
        "trials_per_candidate": budget,
        "total_trials": budget * len(candidates),
        "trial_budget_source": ("auto" if isinstance(preset.trials, str)
                                else f"derived from preset trials={preset.trials}"),
        "evaluations_recorded": evaluations,
        "shortlist_size": SHORTLIST_SIZE,
        "partitions_used": ["TRAIN", "VALIDATION"],
        "train": {"start": str(prepared.train.start), "end": str(prepared.train.end)},
        "validation": {"start": str(prepared.validation.start),
                       "end": str(prepared.validation.end)},
        "min_train_trades": min_trades,
        "min_validation_trades": min_valid_trades,
        "bollinger_on": on,
        "bollinger_off": off,
        "advancing_train_ranks": [r["train_rank"] for r in advancing],
        "max_advancing": MAX_ADVANCING,
        "selection_required_from_human": False,
        "runtime_seconds": round(seconds, 2),
        "unseen_accessed": False,
    }
