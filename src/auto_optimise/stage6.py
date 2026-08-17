"""Stage [6/6] — final selection and the single locked-UNSEEN confirmation.

    stage5_advancing.csv (strategy, risk and filter all frozen)
      -> assemble the full evidence row per candidate from stages 2-5
      -> final_gate_v1 -> pareto_v1 -> final_rank_v1 (TOPSIS)
      -> FREEZE champion + fallback order, fsync it, checksum it
      -> unlock UNSEEN exactly once
      -> unseen_confirmation_v1 on the frozen order
      -> champion accepted, or the frozen fallback chain is walked
      -> write configs/config/<outputname>.json

No parameter is optimized here and no search runs. UNSEEN cannot reorder anything:
the ordering is on disk and checksummed before `unlock()` is called, and the same
checksum is verified afterwards.
"""

import csv
import hashlib
import json
import os
import time
from typing import Any, Dict, List, Optional

from . import (bollinger_policy, config_writer, evaluation, final_selection,
               risk_policy, scoring, search_space)
from .dashboard import Stage6Dashboard, stage_status_from_preset

PRE_UNSEEN_FILE = "stage6_pre_unseen_selection.json"
UNLOCK_REASON = "stage6_final_confirmation"


def _f(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _read_csv(path: str) -> List[Dict[str, str]]:
    if not os.path.isfile(path):
        return []
    with open(path) as fh:
        return list(csv.DictReader(fh))


def load_candidates(run_path: str) -> List[Dict[str, Any]]:
    """Stage-5 advancing set, enriched with the stage 3/4/5 evidence rows."""
    path = os.path.join(run_path, "stage5_advancing.csv")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"stage-5 advancing set not found: {path}")

    stage3 = {int(r["train_rank"]): r
              for r in _read_csv(os.path.join(run_path, "stage3_candidates.csv"))}
    stage4 = {int(r["train_rank"]): r
              for r in _read_csv(os.path.join(run_path, "stage4_candidates.csv"))}
    stage5 = {int(r["train_rank"]): r
              for r in _read_csv(os.path.join(run_path, "stage5_candidates.csv"))}

    out = []
    for row in _read_csv(path):
        rank = int(row["train_rank"])
        cand: Dict[str, Any] = {
            "train_rank": rank,
            "trial_id": int(row["trial_id"]),
            "stage5_order": int(row["advance_rank"]),
            "bollinger_enabled": str(row.get("bollinger_enabled", "")).lower()
            in ("true", "1"),
        }
        for name in search_space.PARAM_NAMES:
            cand[name] = (int(float(row[name])) if name in search_space.INT_PARAMS
                          else float(row[name]))
        for name in risk_policy.PARAM_NAMES:
            cand[name] = float(row[name])
        for name in bollinger_policy.PARAM_NAMES:
            cand[name] = (int(float(row[name]))
                          if name in bollinger_policy.INT_PARAMS
                          else float(row[name]))

        s5 = stage5.get(rank, {})
        # The frozen system's own TRAIN/VALID numbers: the ON figures when the
        # filter was kept, the OFF baseline when it was not.
        prefix = "on" if cand["bollinger_enabled"] else "off"
        cand.update({
            "train_net_return_pct": _f(s5.get(f"{prefix}_train_return")),
            "train_profit_factor": _f(s5.get(f"{prefix}_train_pf")),
            "train_max_dd_pct": _f(s5.get(f"{prefix}_train_dd")),
            "train_trades": int(_f(s5.get(f"{prefix}_train_trades"))),
            "valid_net_return_pct": _f(s5.get(f"{prefix}_valid_return")),
            "valid_profit_factor": _f(s5.get(f"{prefix}_valid_pf")),
            "valid_max_dd_pct": _f(s5.get(f"{prefix}_valid_dd")),
            "valid_trades": int(_f(s5.get(f"{prefix}_valid_trades"))),
            "bollinger_score": _f(s5.get("bollinger_score")),
            "bollinger_retention": _f(s5.get("train_retention")),
            "d_valid_return": _f(s5.get("d_valid_return")),
            "d_valid_pf": _f(s5.get("d_valid_pf")),
            "d_valid_dd": _f(s5.get("d_valid_dd")),
        })

        s3 = stage3.get(rank, {})
        cand.update({
            "robustness_score": _f(s3.get("score")),
            "perturb_profitable_rate": _f(s3.get("perturb_profitable_rate")),
            "profitable_regimes": int(_f(s3.get("profitable_regimes"))),
            "regimes_tested": int(_f(s3.get("regimes_tested"))),
            "median_regime_pf": _f(s3.get("median_regime_pf")),
            "worst_regime_dd": _f(s3.get("worst_regime_dd")),
        })

        s4 = stage4.get(rank, {})
        cand.update({
            "risk_score": _f(s4.get("risk_score")),
            "train_sharpe": _f(s4.get("train_sharpe")),
            "valid_sharpe": _f(s4.get("valid_sharpe")),
        })
        out.append(cand)

    out.sort(key=lambda c: c["stage5_order"])
    return out


# ---------------------------------------------------------------------------

PRE_UNSEEN_FIELDS = (
    ["frozen_rank", "train_rank", "trial_id", "passed_gate", "gate_failures",
     "on_pareto_front", "topsis_score", "bollinger_enabled",
     "train_net_return_pct", "train_profit_factor", "train_sharpe",
     "train_max_dd_pct", "train_trades",
     "valid_net_return_pct", "valid_profit_factor", "valid_sharpe",
     "valid_max_dd_pct", "valid_trades",
     "robustness_score", "perturb_profitable_rate", "profitable_regimes",
     "median_regime_pf", "worst_regime_dd",
     "risk_score", "bollinger_score", "bollinger_retention",
     "d_valid_return", "d_valid_pf", "d_valid_dd"]
    + list(risk_policy.PARAM_NAMES) + list(bollinger_policy.PARAM_NAMES)
    + list(search_space.PARAM_NAMES))

UNSEEN_FIELDS = ["frozen_rank", "train_rank", "trial_id", "bollinger_enabled",
                 "net_return_pct", "net_pnl", "profit_factor", "sharpe",
                 "max_dd_pct", "trades", "wins", "losses", "win_rate", "fees",
                 "status", "reasons"]


def _write_csv(path, fields, rows):
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _sha256_file(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _input_checksums(run_path: str) -> Dict[str, str]:
    names = ["phase_a_manifest.json", "stage3_manifest.json",
             "stage4_manifest.json", "stage5_manifest.json",
             "stage5_advancing.csv"]
    out = {}
    for name in names:
        path = os.path.join(run_path, name)
        if os.path.isfile(path):
            out[name] = _sha256_file(path)
    return out


def run(preset, prepared, run_path: str, output, progress=None,
        show_dashboard: bool = False,
        campaign_started: Optional[float] = None) -> Dict[str, Any]:

    def say(msg):
        if progress is not None:
            progress(msg)

    started = time.time()
    candidates = load_candidates(run_path)
    if not candidates:
        raise RuntimeError("stage 5 advanced no candidates; stage 6 has nothing to do")

    train_days = (prepared.train.end - prepared.train.start).total_seconds() / 86400.0
    valid_days = (prepared.validation.end - prepared.validation.start).total_seconds() / 86400.0
    unseen_days = (prepared.unseen_end - prepared.unseen_start).total_seconds() / 86400.0
    min_trades = scoring.minimum_trades(train_days)
    min_valid_trades = scoring.thin_validation_threshold(min_trades, train_days,
                                                          valid_days)
    # UNSEEN is shorter than VALIDATION; scale the expected sample by window
    # length and by the declared tolerance, both fixed before UNSEEN is opened.
    min_unseen_trades = max(
        1, int(round(min_valid_trades * (unseen_days / max(1.0, valid_days))
                     * final_selection.CONFIRM_MIN_TRADE_RATIO)))

    status = stage_status_from_preset(preset.stages, running=6)

    with Stage6Dashboard(len(candidates), status, enabled=show_dashboard,
                         campaign_started=campaign_started) as dash:
        # ---- pre-UNSEEN selection -----------------------------------------
        say(f"ranking {len(candidates)} finalists on TRAIN + VALIDATION evidence")
        ranking = final_selection.rank(candidates, min_valid_trades)
        ordering = ranking["ordering"]
        dash.set_pareto(len(ranking["pareto_indices"]), len(candidates))

        rows = []
        for pos, entry in enumerate(ordering, start=1):
            row = dict(entry["candidate"])
            row.update({"frozen_rank": pos,
                        "passed_gate": entry["passed_gate"],
                        "gate_failures": "; ".join(entry["gate_failures"]),
                        "on_pareto_front": entry.get("on_pareto_front", False),
                        "topsis_score": round(entry.get("topsis_score", 0.0), 6)})
            rows.append(row)
        for entry in ranking["evaluated"]:
            if entry["passed_gate"]:
                continue
            row = dict(entry["candidate"])
            row.update({"frozen_rank": 0, "passed_gate": False,
                        "gate_failures": "; ".join(entry["gate_failures"]),
                        "on_pareto_front": False, "topsis_score": 0.0})
            rows.append(row)
        _write_csv(os.path.join(run_path, "stage6_pre_unseen_candidates.csv"),
                   PRE_UNSEEN_FIELDS, rows)

        if not ordering:
            say("no finalist passed final_gate_v1")
            decision = _no_winner(run_path, preset, prepared, candidates,
                                  ranking, [], "no_candidate_passed_final_gate",
                                  min_valid_trades, min_unseen_trades,
                                  time.time() - started)
            dash.finish_stage(6, "FAILED")
            return decision

        champion = ordering[0]["candidate"]
        dash.set_champion(champion, ordering[0].get("topsis_score", 0.0))

        # ---- FREEZE, fsync, checksum — before UNSEEN exists ----------------
        frozen = {
            "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "decision_rules": final_selection.describe(),
            "min_validation_trades": min_valid_trades,
            "min_unseen_trades": min_unseen_trades,
            "pareto_dimensions": list(final_selection.PARETO_DIMENSIONS),
            "pareto_front_train_ranks": [candidates[i]["train_rank"]
                                         for i in ranking["pareto_indices"]],
            "mcdm_method": "TOPSIS",
            "weights": ranking["weights"],
            "input_checksums": _input_checksums(run_path),
            "champion": _identity(ordering[0]),
            "fallbacks": [_identity(e) for e in ordering[1:]],
            "ordering": [_identity(e) for e in ordering],
            "unseen_state_at_freeze": "LOCKED",
        }
        pre_path = os.path.join(run_path, PRE_UNSEEN_FILE)
        with open(pre_path, "w") as fh:
            json.dump(frozen, fh, indent=2, default=str)
            fh.flush()
            os.fsync(fh.fileno())
        frozen_checksum = _sha256_file(pre_path)
        say(f"decision frozen — champion rank {champion['train_rank']}, "
            f"checksum {frozen_checksum[:16]}")
        dash.decision_frozen(frozen_checksum)

        # ---- the single unlock --------------------------------------------
        assert prepared.unseen.is_locked, "UNSEEN was already unlocked"
        prepared.unseen.unlock(UNLOCK_REASON)
        dash.unseen_unlocked()
        say("UNSEEN unlocked once for confirmation")

        unseen_rows, verdicts = [], []
        for pos, entry in enumerate(ordering, start=1):
            cand = entry["candidate"]
            params = {k: cand[k] for k in search_space.PARAM_NAMES}
            risk = risk_policy.as_fractions(
                {k: cand[k] for k in risk_policy.PARAM_NAMES})
            filt = (bollinger_policy.to_filter_dict(
                {k: cand[k] for k in bollinger_policy.PARAM_NAMES})
                if cand["bollinger_enabled"] else None)

            metrics = evaluation.run_on_context(
                prepared.context_for_unseen(), params, preset, risk, filt)
            verdict = final_selection.confirm(metrics, cand, min_unseen_trades)
            verdicts.append(verdict)

            row = {"frozen_rank": pos, "train_rank": cand["train_rank"],
                   "trial_id": cand["trial_id"],
                   "bollinger_enabled": cand["bollinger_enabled"],
                   "status": verdict["status"],
                   "reasons": "; ".join(verdict["reasons"])}
            row.update({k: (metrics or {}).get(k) for k in
                        ("net_return_pct", "net_pnl", "profit_factor", "sharpe",
                         "max_dd_pct", "trades", "wins", "losses", "win_rate",
                         "fees")})
            unseen_rows.append(row)
            dash.unseen_result(pos, cand, metrics, verdict["status"])
            say(f"  frozen #{pos} (rank {cand['train_rank']}): {verdict['status']}")

        _write_csv(os.path.join(run_path, "stage6_unseen_results.csv"),
                   UNSEEN_FIELDS, unseen_rows)

        # ---- predetermined fallback walk ----------------------------------
        winner_pos, fallback_used = None, False
        for pos, verdict in enumerate(verdicts, start=1):
            action = final_selection.FALLBACK_POLICY[verdict["status"]]
            if action in ("accept", "accept_with_warning"):
                winner_pos = pos
                fallback_used = pos > 1
                break

        # The pre-UNSEEN artifact must be unchanged by everything above.
        assert _sha256_file(pre_path) == frozen_checksum, \
            "pre-UNSEEN selection changed after UNSEEN was opened"

        if winner_pos is None:
            say("every frozen candidate FAILED unseen confirmation")
            decision = _no_winner(run_path, preset, prepared, candidates, ranking,
                                  unseen_rows, "all_candidates_failed_unseen",
                                  min_valid_trades, min_unseen_trades,
                                  time.time() - started,
                                  frozen_checksum=frozen_checksum)
            dash.finish_stage(6, "FAILED")
            return decision

        winner = ordering[winner_pos - 1]["candidate"]
        verdict = verdicts[winner_pos - 1]
        unseen = unseen_rows[winner_pos - 1]
        dash.set_winner(winner, verdict["status"])

        provenance = {
            "name": f"auto-optimiser winner — TRAIN rank {winner['train_rank']}",
            "run_id": os.path.basename(run_path),
            "frozen_rank": winner_pos,
            "fallback_activated": fallback_used,
            "pre_unseen_checksum": frozen_checksum,
            "unseen_status": verdict["status"],
            "unseen_reasons": verdict["reasons"],
            "decision_rules": final_selection.describe(),
        }
        payload = config_writer.build(winner, preset, prepared, provenance)
        written = config_writer.write(payload, output)
        say(f"final config written: {written['path']}")
        dash.config_written(written["path"])
        dash.finish_stage(6, "PASS")

    decision = {
        "winner": winner, "winner_frozen_rank": winner_pos,
        "fallback_activated": fallback_used, "status": verdict["status"],
        "reasons": verdict["reasons"], "unseen": unseen,
        "unseen_rows": unseen_rows, "ordering": [_identity(e) for e in ordering],
        "pre_unseen_checksum": frozen_checksum, "config": written,
        "seconds": time.time() - started, "failed": False,
    }
    _write_decision(run_path, decision)
    _write_manifest(run_path, preset, prepared, candidates, ranking, decision,
                    min_valid_trades, min_unseen_trades, frozen_checksum)
    return decision


def _identity(entry) -> Dict[str, Any]:
    c = entry["candidate"]
    return {"train_rank": c["train_rank"], "trial_id": c["trial_id"],
            "bollinger_enabled": c["bollinger_enabled"],
            "topsis_score": round(entry.get("topsis_score", 0.0), 6),
            "on_pareto_front": entry.get("on_pareto_front", False)}


def _no_winner(run_path, preset, prepared, candidates, ranking, unseen_rows,
               reason, min_valid_trades, min_unseen_trades, seconds,
               frozen_checksum=None) -> Dict[str, Any]:
    decision = {"winner": None, "winner_frozen_rank": None,
                "fallback_activated": False, "status": "NO_WINNER",
                "reasons": [reason], "unseen": None, "unseen_rows": unseen_rows,
                "ordering": [_identity(e) for e in ranking["ordering"]],
                "pre_unseen_checksum": frozen_checksum, "config": None,
                "seconds": seconds, "failed": True}
    _write_decision(run_path, decision)
    _write_manifest(run_path, preset, prepared, candidates, ranking, decision,
                    min_valid_trades, min_unseen_trades, frozen_checksum)
    return decision


def _write_decision(run_path, decision):
    payload = {k: v for k, v in decision.items() if k != "unseen_rows"}
    with open(os.path.join(run_path, "stage6_final_decision.json"), "w") as fh:
        json.dump(payload, fh, indent=2, default=str)


def _write_manifest(run_path, preset, prepared, candidates, ranking, decision,
                    min_valid_trades, min_unseen_trades, frozen_checksum):
    def _load(name):
        path = os.path.join(run_path, name)
        if os.path.isfile(path):
            with open(path) as fh:
                return json.load(fh)
        return {}

    phase_a = _load("phase_a_manifest.json")
    stage3 = _load("stage3_manifest.json")
    stage4 = _load("stage4_manifest.json")
    stage5 = _load("stage5_manifest.json")

    manifest = {
        "stage": "stage_6_final_selection",
        "run_id": os.path.basename(run_path),
        "provenance": {
            "data_checksum": phase_a.get("data_checksum"),
            "partitions": phase_a.get("partitions"),
            "warmup": phase_a.get("warmup"),
            "phase_a_seed": phase_a.get("seed"),
            "phase_a_sampler": phase_a.get("sampler"),
            "phase_a_study": phase_a.get("study_name"),
            "strategy_scoring_version": phase_a.get("scoring", {}).get("version"),
            "robustness_gate_version": stage3.get("robustness", {}).get("gate_version"),
            "robustness_score_version": stage3.get("robustness", {}).get("score_version"),
            "risk_gate_version": stage4.get("risk_policy", {}).get("gate_version"),
            "risk_score_version": stage4.get("risk_policy", {}).get("score_version"),
            "bollinger_gate_version": stage5.get("bollinger", {}).get("gate_version"),
            "bollinger_score_version": stage5.get("bollinger", {}).get("score_version"),
            **final_selection.describe(),
        },
        "candidates_in": len(candidates),
        "input_train_ranks": [c["train_rank"] for c in candidates],
        "pareto_front_train_ranks": [candidates[i]["train_rank"]
                                     for i in ranking["pareto_indices"]],
        "frozen_ordering": decision["ordering"],
        "pre_unseen_checksum": frozen_checksum,
        "unseen_unlocked": frozen_checksum is not None,
        "unseen_unlock_count": 1 if frozen_checksum is not None else 0,
        "unseen_unlock_reason": UNLOCK_REASON,
        "min_validation_trades": min_valid_trades,
        "min_unseen_trades": min_unseen_trades,
        "winner_train_rank": (decision["winner"] or {}).get("train_rank"),
        "winner_frozen_rank": decision["winner_frozen_rank"],
        "fallback_activated": decision["fallback_activated"],
        "unseen_status": decision["status"],
        "final_config": decision["config"],
        "selection_required_from_human": False,
        "runtime_seconds": round(decision["seconds"], 2),
    }
    with open(os.path.join(run_path, "stage6_manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2, default=str)
