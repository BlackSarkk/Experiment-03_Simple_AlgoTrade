"""Evaluate frozen systems on a window outside any campaign's partitions.

Used for the Candidate #158 benchmark month (2026-07-16 -> 2026-08-15), which
sits entirely after the rematch campaign's data ends. It is deliberately NOT a
`PreparedData` partition: nothing here can be reached by the optimizer, so a
benchmark window can never leak into a search.

The frame is built the same way Stage 1 builds its own — warmup rows prepended,
still-forming candle dropped, indicators computed by the caller on the whole
thing and the lead-in dropped afterwards.
"""

import csv
import json
import os
from typing import Any, Dict, List, Optional

import pandas as pd

from common.config import PipelineConfig
from common.market_data import MarketDataLoader

from . import evaluation, lookback, search_space

FIELDS = ["system", "bollinger", "window_start", "window_end", "net_return_pct",
          "net_pnl", "gross_profit", "gross_loss", "profit_factor", "sharpe",
          "max_dd_pct", "trades", "wins", "losses", "win_rate", "fees"]


def build_context(preset, start, end, data_dir: str = "data"):
    """(frame_with_warmup, lead_rows) covering [start, end] plus warmup."""
    warmup = lookback.required_warmup_candles()
    tf_minutes = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
                  "1h": 60, "2h": 120, "3h": 180, "4h": 240}[preset.timeframe]
    start = pd.Timestamp(start, tz="UTC")
    end = pd.Timestamp(end, tz="UTC") + pd.Timedelta(hours=23, minutes=59, seconds=59)

    cfg = PipelineConfig()
    cfg.platform.platform = preset.platform
    cfg.platform.symbol = preset.symbol
    cfg.platform.resolution = preset.timeframe
    cfg.platform.start_date = (
        start - pd.Timedelta(minutes=tf_minutes * warmup)).strftime("%Y-%m-%d")
    cfg.platform.end_date = end.strftime("%Y-%m-%d")
    cfg.platform.days = None

    df = MarketDataLoader(data_dir).load_ohlcv(cfg.platform, reset_cache=False,
                                               quiet=True)
    if df is None or df.empty:
        raise RuntimeError(f"no market data for {preset.symbol} {preset.timeframe}")

    df = df.copy()
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df.sort_values("datetime").drop_duplicates("datetime").reset_index(drop=True)

    bar = pd.Timedelta(minutes=tf_minutes)
    df = df.loc[df["datetime"] + bar <= pd.Timestamp.now(tz="UTC")]
    df = df.loc[df["datetime"] <= end].reset_index(drop=True)

    pre = df.index[df["datetime"] < start]
    if len(pre) < lookback.MIN_CONTEXT_CANDLES:
        raise RuntimeError(
            f"only {len(pre)} warmup candles before {start}; "
            f"{lookback.MIN_CONTEXT_CANDLES} required")
    if len(pre) > warmup:
        df = df.loc[pre[-warmup]:].reset_index(drop=True)
        pre = df.index[df["datetime"] < start]

    return df, int(len(pre))


def evaluate(preset, systems: List[Dict[str, Any]], start, end,
             data_dir: str = "data") -> List[Dict[str, Any]]:
    """Run each system on the window. `systems` are already-frozen definitions."""
    context = build_context(preset, start, end, data_dir)
    rows = []
    for system in systems:
        metrics = evaluation.run_on_context(
            context, system["params"], preset, system.get("risk"),
            system.get("bollinger"))
        row = {"system": system["name"],
               "bollinger": system.get("bollinger_label", "OFF"),
               "window_start": str(start), "window_end": str(end)}
        for key in ("net_return_pct", "net_pnl", "gross_profit", "gross_loss",
                    "profit_factor", "sharpe", "max_dd_pct", "trades", "wins",
                    "losses", "win_rate", "fees"):
            row[key] = (metrics or {}).get(key)
        rows.append(row)
    return rows


def write(rows: List[Dict[str, Any]], path: str):
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def system_from_config(path: str, name: str, force_bollinger: Optional[bool] = None
                       ) -> Dict[str, Any]:
    """Build a frozen system definition straight from a runnable config JSON."""
    with open(path) as fh:
        cfg = json.load(fh)
    strat, risk = cfg["strategy"], cfg["risk"]
    bb = dict(cfg.get("filters", {}).get("bollinger", {}))
    enabled = bb.get("enabled", False) if force_bollinger is None else force_bollinger
    return {
        "name": name,
        "params": {k: (int(strat[k]) if k in search_space.INT_PARAMS
                       else float(strat[k])) for k in search_space.PARAM_NAMES},
        "risk": {"leverage": float(risk["leverage"]),
                 "risk_per_trade_pct": float(risk["risk_per_trade_pct"]) / 100.0,
                 "max_position_allocation_pct":
                     float(risk["max_position_allocation_pct"]) / 100.0},
        "bollinger": dict(bb, enabled=True) if enabled else None,
        "bollinger_label": "ON" if enabled else "OFF",
    }
