"""Phase-A run artifacts.

Everything a run produces lives under `results/auto_optimise/<run_id>/`, well away
from `configs/config/`: no runnable strategy config is written until a stage
actually selects a winner.

UNSEEN metrics never appear in any artifact written here — the vault is still
locked while Phase A runs, so there is nothing to leak, and that stays true by
construction rather than by filtering.
"""

import csv
import json
import os
from datetime import datetime, timezone

RESULTS_ROOT = os.path.join("results", "auto_optimise")


def new_run_id(preset) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}_{preset.symbol}_{preset.timeframe}"


def run_dir(run_id: str) -> str:
    path = os.path.join(RESULTS_ROOT, run_id)
    os.makedirs(path, exist_ok=True)
    return path


def study_storage_url(path: str) -> str:
    return "sqlite:///" + os.path.abspath(os.path.join(path, "phase_a.db"))


def study_storage_url_named(path: str, name: str) -> str:
    """Storage for a stage's own study. Never shares a file with phase_a.db."""
    return "sqlite:///" + os.path.abspath(os.path.join(path, f"{name}.db"))


def _write_csv(path: str, rows, fieldnames):
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


TRIAL_FIELDS = [
    "trial", "state", "score", "rejection", "net_return_pct", "net_pnl",
    "gross_profit", "gross_loss", "profit_factor", "sharpe", "max_dd_pct",
    "trades", "wins", "losses", "win_rate", "long_trades", "short_trades", "fees",
]

SHORTLIST_FIELDS = [
    "rank", "trial", "train_score",
    "train_net_return_pct", "train_net_pnl", "train_profit_factor", "train_sharpe",
    "train_max_dd_pct", "train_trades", "train_win_rate", "train_fees",
    "valid_net_return_pct", "valid_net_pnl", "valid_profit_factor", "valid_sharpe",
    "valid_max_dd_pct", "valid_trades", "valid_win_rate", "valid_fees",
    "valid_rejection", "valid_status",
]


def write_trials(path: str, rows, param_names):
    fields = TRIAL_FIELDS + list(param_names)
    _write_csv(os.path.join(path, "phase_a_trials.csv"), rows, fields)


def write_shortlist(path: str, rows, param_names):
    """The RAW TRAIN shortlist — the audit trail. Never de-duplicated."""
    fields = SHORTLIST_FIELDS + list(param_names)
    _write_csv(os.path.join(path, "phase_a_shortlist.csv"), rows, fields)


DIVERSITY_FIELDS = [
    "train_rank", "trial", "selected", "reason",
    "nearest_selected_rank", "nearest_distance",
]


def write_diversity_decisions(path: str, decisions):
    rows = [{"train_rank": d.train_rank, "trial": d.trial,
             "selected": d.selected, "reason": d.reason,
             "nearest_selected_rank": d.nearest_selected_rank,
             "nearest_distance": ("" if d.nearest_distance != d.nearest_distance
                                  else round(d.nearest_distance, 4))}
            for d in decisions]
    _write_csv(os.path.join(path, "phase_a_diversity_decisions.csv"),
               rows, DIVERSITY_FIELDS)


def write_diverse_shortlist(path: str, rows, param_names):
    fields = SHORTLIST_FIELDS + list(param_names)
    _write_csv(os.path.join(path, "phase_a_diverse_shortlist.csv"), rows, fields)


def write_manifest(path: str, manifest: dict):
    with open(os.path.join(path, "phase_a_manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2, default=str)


def read_manifest(path: str) -> dict:
    with open(os.path.join(path, "phase_a_manifest.json")) as fh:
        return json.load(fh)


def preset_snapshot(preset) -> dict:
    """Exactly what the human asked for, captured when the preset was loaded."""
    return dict(preset.snapshot or {})
