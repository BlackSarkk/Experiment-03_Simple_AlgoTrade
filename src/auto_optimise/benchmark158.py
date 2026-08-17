"""Candidate #158 benchmark — diagnostic only, run after the winner is immutable.

Candidate #158 never enters the gate, the Pareto filter, TOPSIS, the fallback
chain or winner selection. This module is called only once stage [6/6] has
written its decision, and it cannot change it: it reads the frozen config,
re-runs it through the same engine on the same windows, and writes a CSV.

The comparison is recalculated from the live engine and data. Nothing is taken
from the reference numbers stored in the config's metadata.
"""

import csv
import json
import os
from typing import Any, Dict, List, Optional

from . import evaluation, search_space

CONFIG_PATH = os.path.join("configs", "config", "config1-ETHUSDTP15m-long.json")

FIELDS = ["system", "bollinger", "window", "start", "end", "net_return_pct",
          "net_pnl", "profit_factor", "sharpe", "max_dd_pct", "trades",
          "wins", "losses", "win_rate", "fees"]


def load_config158(path: str = CONFIG_PATH) -> Dict[str, Any]:
    with open(path) as fh:
        return json.load(fh)


def describe_dates(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """What the stored metadata actually claims, and whether it is usable."""
    dev_start = cfg.get("_development_start")
    dev_end = cfg.get("_development_end")
    unseen_start = cfg.get("_unseen_start")
    unseen_end = cfg.get("_unseen_end")
    ambiguities = []
    if not (dev_start and dev_end):
        ambiguities.append("development window missing")
    if not (unseen_start and unseen_end):
        ambiguities.append("stored unseen window missing")
    return {"development_start": dev_start, "development_end": dev_end,
            "stored_unseen_start": unseen_start, "stored_unseen_end": unseen_end,
            "ambiguities": ambiguities}


def _params(cfg) -> Dict[str, Any]:
    strat = cfg["strategy"]
    return {name: (int(strat[name]) if name in search_space.INT_PARAMS
                   else float(strat[name]))
            for name in search_space.PARAM_NAMES}


def _risk(cfg) -> Dict[str, float]:
    risk = cfg["risk"]
    return {"leverage": float(risk["leverage"]),
            "risk_per_trade_pct": float(risk["risk_per_trade_pct"]) / 100.0,
            "max_position_allocation_pct":
                float(risk["max_position_allocation_pct"]) / 100.0}


def _row(system, bollinger, window, start, end, metrics) -> Dict[str, Any]:
    row = {"system": system, "bollinger": bollinger, "window": window,
           "start": str(start), "end": str(end)}
    for key in ("net_return_pct", "net_pnl", "profit_factor", "sharpe",
                "max_dd_pct", "trades", "wins", "losses", "win_rate", "fees"):
        row[key] = (metrics or {}).get(key)
    return row


def run(preset, prepared, run_path: str, winner: Dict[str, Any],
        progress=None) -> Dict[str, Any]:
    """Compare the frozen winner against #158 on identical windows."""

    def say(msg):
        if progress is not None:
            progress(msg)

    if not os.path.isfile(CONFIG_PATH):
        return {"available": False, "reason": f"{CONFIG_PATH} not found"}

    cfg = load_config158()
    dates = describe_dates(cfg)
    params158, risk158 = _params(cfg), _risk(cfg)
    bb158 = dict(cfg.get("filters", {}).get("bollinger", {}))

    windows = [("UNSEEN (this campaign)", prepared.unseen_start, prepared.unseen_end)]
    # #158's own stored unseen month, when it falls inside our data and does not
    # require re-partitioning. Reported separately, never merged with the above.
    stored_start, stored_end = dates["stored_unseen_start"], dates["stored_unseen_end"]
    stored_usable = False
    if stored_start and stored_end:
        import pandas as pd
        lo = pd.Timestamp(stored_start, tz="UTC")
        hi = pd.Timestamp(stored_end, tz="UTC") + pd.Timedelta(hours=23, minutes=59)
        if lo >= prepared.unseen_start and hi <= prepared.unseen_end:
            windows.append(("#158 stored unseen window", lo, hi))
            stored_usable = True

    rows: List[Dict[str, Any]] = []
    for label, start, end in windows:
        say(f"benchmark window: {label}")
        context = (prepared.context_for_unseen() if label.startswith("UNSEEN")
                   else prepared.context_for_window(start, end)
                   if end <= prepared.validation.end
                   else _unseen_subwindow(prepared, start, end))

        # The auto-optimizer winner, exactly as frozen.
        from . import bollinger_policy, risk_policy
        w_filter = (bollinger_policy.to_filter_dict(
            {k: winner[k] for k in bollinger_policy.PARAM_NAMES})
            if winner.get("bollinger_enabled") else None)
        w_metrics = evaluation.run_on_context(
            context, {k: winner[k] for k in search_space.PARAM_NAMES}, preset,
            risk_policy.as_fractions({k: winner[k] for k in risk_policy.PARAM_NAMES}),
            w_filter)
        rows.append(_row("auto-optimiser winner",
                         "ON" if winner.get("bollinger_enabled") else "OFF",
                         label, start, end, w_metrics))

        # #158 as stored (Bollinger disabled in config1).
        off = evaluation.run_on_context(context, params158, preset, risk158, None)
        rows.append(_row("Candidate #158", "OFF", label, start, end, off))

        # #158 with its own stored Bollinger block enabled.
        if bb158:
            on = evaluation.run_on_context(
                context, params158, preset, risk158, dict(bb158, enabled=True))
            rows.append(_row("Candidate #158", "ON (stored block)", label,
                             start, end, on))

    path = os.path.join(run_path, "stage6_candidate158_benchmark.csv")
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    return {"available": True, "rows": rows, "dates": dates,
            "stored_window_usable": stored_usable, "path": path,
            "config158_risk": cfg["risk"], "config158_strategy": cfg["strategy"],
            "config158_bollinger": bb158, "diagnostic_only": True,
            "affected_winner": False}


def _unseen_subwindow(prepared, start, end):
    """A window inside UNSEEN. Requires the vault to already be unlocked."""
    import pandas as pd
    frame, _ = prepared.context_for_unseen()
    dt = pd.to_datetime(frame["datetime"], utc=True)
    lead_rows = int((dt < pd.Timestamp(start)).sum())
    keep = dt <= pd.Timestamp(end)
    return frame.loc[keep].reset_index(drop=True), lead_rows
