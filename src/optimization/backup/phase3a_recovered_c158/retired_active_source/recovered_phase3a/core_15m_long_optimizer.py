"""
Phase 3A — Core strategy optimizer: ETHUSDT 15m, LONG ONLY.

Forked from multi_tf_optimizer.py. Keeps its useful infrastructure (Optuna TPE, seed=42,
SQLite resumable storage, dataset SHA provenance, real production BacktestEngine) and
replaces three things that made it unusable for Phase 3:

  1. risk_per_trade_pct / max_position_allocation_pct / leverage are NO LONGER sampled.
     The risk policy is loaded frozen from configs/riskmanager.json.
  2. `side_choice` is removed. This campaign is strictly long_enabled=True/short_enabled=False.
  3. The dollar-return-weighted robust_score() is replaced by a sizing-neutral score built
     from expectancy_R / PF / Sharpe, with an explicit DD penalty and consistency gap.

Entry point:
    .venv/bin/python3 src/optimization/core_15m_long_optimizer.py --stage 1 --trials 400
    .venv/bin/python3 src/optimization/core_15m_long_optimizer.py --smoke        # 4 trials

Nothing here mutates strategy, risk, execution, accounting, replay or forward behaviour.
"""

import os
import sys
import json
import time
import math
import hashlib
import argparse
from typing import Dict, Any, Tuple, Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pandas as pd
import optuna
from optuna.samplers import TPESampler
from optuna.pruners import MedianPruner

from common.config import PipelineConfig, StrategyConfig, RiskConfig, ExecutionConfig, PlatformConfig
from common.market_data import MarketDataLoader
from strategy.indicators import compute_all_indicators
from backtest.engine import BacktestEngine

optuna.logging.set_verbosity(optuna.logging.WARNING)

# ---------------------------------------------------------------------------
# Campaign definition (Phase 3A)
# ---------------------------------------------------------------------------
OPTIMIZER_VERSION = "phase3a-core-15m-long-1.0"
SYMBOL = "ETHUSDT"
PLATFORM = "BINANCE_FUTURES"
RESOLUTION = "15m"
START_DATE = "2022-01-01"
END_DATE = "2026-08-15"
SEED = 42

SPLIT_TRAIN, SPLIT_VAL = 0.60, 0.20          # remainder (0.20) is the untouched holdout
WARMUP_BARS = 300                            # >= max(ema 120, vol_sma 50) + margin

MIN_TRAIN_TRADES = 200
MIN_VAL_TRADES = 60
EPSILON = 1e-9

RISK_POLICY_PATH = "configs/riskmanager.json"
RESULTS_DIR = os.path.join("results", "optimization", "phase3a_15m_long")

# Frozen — these must never appear in the search space.
FROZEN_RISK_KEYS = {
    "sizing_mode", "leverage", "risk_per_trade_pct",
    "max_position_allocation_pct", "quantity_step", "initial_capital",
}

SEARCH_SPACE = {
    "ema_period":             ("int",   20,   120,  1),
    "rsi_period":             ("int",    7,    21,  1),
    "rsi_overbought":         ("float", 55.0, 80.0, 1.0),
    "rsi_oversold":           ("float", 20.0, 45.0, 1.0),
    "atr_period":             ("int",    7,    21,  1),
    "consolidation_candles":  ("int",    4,    20,  1),
    "consolidation_atr_mult": ("float",  1.0,  4.0, 0.1),
    "swing_lookback":         ("int",    4,    20,  1),
    "volume_sma_period":      ("int",   10,    50,  1),
    "volume_mult":            ("float",  0.5,  2.0, 0.1),
    "risk_reward_ratio":      ("float",  1.0,  4.0, 0.1),
}

# ---------------------------------------------------------------------------
# Stage-2 search space.
#
# Derived from the top 15% (n=50) of Stage-1 valid trials. Those split into two
# regimes that separate almost entirely on rsi_oversold:
#     regime A  rsi_oversold 21-33  (mean score 0.376, n=17)
#     regime B  rsi_oversold 35-41  (mean score 0.367, n=33)
# The regimes overlap on every other dimension, so narrowing around the single best
# trial would have deleted regime B. Ranges below are the UNION of both regimes'
# observed min/max, widened by one step on each side and clipped to the Stage-1
# bounds. rsi_oversold deliberately spans 20-42 so the search can move between
# regimes rather than being locked into one.
# ---------------------------------------------------------------------------
STAGE2_SPACE = {
    "ema_period":             ("int",   82,   108,  1),
    "rsi_period":             ("int",    9,    21,  1),
    "rsi_overbought":         ("float", 73.0, 80.0, 1.0),
    "rsi_oversold":           ("float", 20.0, 42.0, 1.0),
    "atr_period":             ("int",   11,    21,  1),
    "consolidation_candles":  ("int",    7,    20,  1),
    "consolidation_atr_mult": ("float",  2.6,  3.9, 0.1),
    "swing_lookback":         ("int",    4,    18,  1),
    "volume_sma_period":      ("int",   29,    50,  1),
    "volume_mult":            ("float",  1.3,  2.0, 0.1),
    "risk_reward_ratio":      ("float",  1.9,  3.1, 0.1),
}


# ---------------------------------------------------------------------------
# Frozen risk policy
# ---------------------------------------------------------------------------
def load_frozen_risk_policy() -> Tuple[RiskConfig, str, Dict[str, Any]]:
    """Load configs/riskmanager.json. Fails loudly — the campaign is invalid without it."""
    if not os.path.exists(RISK_POLICY_PATH):
        raise FileNotFoundError(
            f"{RISK_POLICY_PATH} not found. The frozen Phase-2 risk policy is required."
        )
    raw = open(RISK_POLICY_PATH, "rb").read()
    policy = json.loads(raw.decode())
    r = policy.get("risk", {})
    rc = RiskConfig()
    rc.initial_capital = r.get("initial_capital", rc.initial_capital)
    rc.leverage = r.get("leverage", rc.leverage)
    rc.risk_per_trade_pct = r.get("risk_per_trade_pct", 1.5) / 100.0
    rc.max_position_allocation_pct = r.get("max_position_allocation_pct", 50.0) / 100.0
    rc.quantity_step = r.get("quantity_step", rc.quantity_step)
    rc.sizing_mode = r.get("sizing_mode", rc.sizing_mode)
    return rc, hashlib.sha256(raw).hexdigest(), r


def assert_search_space_is_clean():
    """Guard: frozen risk fields must be unreachable from the search space."""
    leaked = FROZEN_RISK_KEYS & set(SEARCH_SPACE.keys())
    if leaked:
        raise AssertionError(f"Frozen risk fields present in search space: {sorted(leaked)}")


# ---------------------------------------------------------------------------
# Data + chronological partitions
# ---------------------------------------------------------------------------
def load_dataset() -> Tuple[pd.DataFrame, str]:
    loader = MarketDataLoader(data_dir="data")
    cfg = PlatformConfig(
        platform=PLATFORM, symbol=SYMBOL, resolution=RESOLUTION,
        start_date=START_DATE, end_date=END_DATE,
    )
    df = loader.load_ohlcv(cfg, quiet=True)
    dt = pd.to_datetime(df["datetime"], utc=True)
    req_start = pd.Timestamp(START_DATE, tz="UTC")
    req_end = pd.Timestamp(END_DATE, tz="UTC") + pd.Timedelta(hours=23, minutes=59, seconds=59)
    df = df[(dt >= req_start) & (dt <= req_end)].reset_index(drop=True)
    sha = hashlib.sha256(
        pd.util.hash_pandas_object(df[["timestamp", "open", "high", "low", "close", "volume"]],
                                   index=False).values.tobytes()
    ).hexdigest()
    return df, sha


def split_indices(n: int) -> Dict[str, Tuple[int, int]]:
    t1 = int(n * SPLIT_TRAIN)
    t2 = int(n * (SPLIT_TRAIN + SPLIT_VAL))
    return {"train": (0, t1), "validation": (t1, t2), "holdout": (t2, n)}


def partition_frame(df: pd.DataFrame, bounds: Tuple[int, int]) -> Tuple[pd.DataFrame, int]:
    """Return (frame_with_warmup_prefix, n_warmup_rows).

    The warmup prefix seeds indicators only; those rows are dropped before evaluation, so
    no trade, PnL or drawdown can originate outside the partition. Warmup for VALIDATION
    comes from the tail of TRAIN and for HOLDOUT from the tail of VALIDATION — that is
    indicator seeding, not label leakage, because nothing there is ever evaluated.
    """
    lo, hi = bounds
    warm_lo = max(0, lo - WARMUP_BARS)
    return df.iloc[warm_lo:hi].reset_index(drop=True), lo - warm_lo


# ---------------------------------------------------------------------------
# Backtest on a partition, using the production engine
# ---------------------------------------------------------------------------
def make_cfg(params: dict, risk_cfg: RiskConfig) -> PipelineConfig:
    cfg = PipelineConfig(execution_mode="REFERENCE")
    cfg.platform = PlatformConfig(platform=PLATFORM, symbol=SYMBOL, resolution=RESOLUTION,
                                  start_date=START_DATE, end_date=END_DATE)
    cfg.strategy = StrategyConfig(
        symbol=SYMBOL, resolution=RESOLUTION,
        ema_period=params["ema_period"],
        rsi_period=params["rsi_period"],
        rsi_overbought=params["rsi_overbought"],
        rsi_oversold=params["rsi_oversold"],
        atr_period=params["atr_period"],
        consolidation_candles=params["consolidation_candles"],
        consolidation_atr_mult=params["consolidation_atr_mult"],
        swing_lookback=params["swing_lookback"],
        volume_sma_period=params["volume_sma_period"],
        use_volume_filter=True,
        volume_mult=params["volume_mult"],
        long_enabled=True,      # Phase 3A: LONG ONLY — not a search dimension
        short_enabled=False,
        risk_reward_ratio=params["risk_reward_ratio"],
    )
    cfg.risk = risk_cfg          # frozen policy, identical for every trial
    cfg.execution = ExecutionConfig(mode="REFERENCE")
    return cfg


def empty_metrics() -> dict:
    return {"n_trades": 0, "expectancy_R": -1.0, "profit_factor": 0.0, "sharpe": -5.0,
            "max_dd_pct": 100.0, "win_rate": 0.0, "net_return_pct": -100.0, "total_fees": 0.0}


def run_partition(df: pd.DataFrame, bounds: Tuple[int, int], params: dict,
                  risk_cfg: RiskConfig) -> dict:
    frame, n_warm = partition_frame(df, bounds)
    if len(frame) - n_warm < 200:
        return empty_metrics()
    cfg = make_cfg(params, risk_cfg)
    try:
        df_ind = compute_all_indicators(frame.copy(), cfg.strategy)
        df_eval = df_ind.iloc[n_warm:].reset_index(drop=True)   # drop warmup: no trades there
        result = BacktestEngine(cfg).run(df_eval)
    except Exception:
        return empty_metrics()

    trades = result["trades"]
    n = len(trades)
    if n == 0:
        return empty_metrics()

    pnls = np.array([t.net_pnl for t in trades], dtype=float)
    r_mults = np.array([t.r_multiple for t in trades], dtype=float)
    gp = float(pnls[pnls > 0].sum())
    gl = float(abs(pnls[pnls < 0].sum()))

    eq = result["equity_curve"]
    if len(eq) > 2:
        v = np.array([e["equity"] for e in eq], dtype=float)
        rets = np.diff(v) / (v[:-1] + EPSILON)
        sharpe = float(rets.mean() / (rets.std() + EPSILON) * math.sqrt(35040)) if rets.std() > 1e-12 else 0.0
    else:
        sharpe = 0.0

    return {
        "n_trades": n,
        "expectancy_R": round(float(r_mults.mean()), 6),
        "profit_factor": round(gp / gl, 6) if gl > 1e-9 else (99.0 if gp > 0 else 0.0),
        "sharpe": round(sharpe, 6),
        "max_dd_pct": round(float(result["max_drawdown_pct"]), 6),
        "win_rate": round(float((pnls > 0).mean()), 6),
        "net_return_pct": round(float(result["net_return_pct"]), 6),
        "total_fees": round(float(sum(t.total_fees for t in trades)), 4),
    }


# ---------------------------------------------------------------------------
# Sizing-neutral objective
# ---------------------------------------------------------------------------
def clip(v, lo, hi):
    return max(lo, min(hi, v))


def base_score(m: dict) -> float:
    return (0.5 * clip(m["expectancy_R"], -1.0, 2.0)
            + 0.3 * clip(m["profit_factor"] - 1.0, -1.0, 1.0)
            + 0.2 * clip(m["sharpe"] / 2.0, -1.0, 1.0)
            - 0.5 * max(0.0, m["max_dd_pct"] / 100.0 - 0.20))


def score_candidate(train_m: dict, val_m: Optional[dict]) -> Dict[str, float]:
    """Stage 1 passes val_m=None (train only). Stages 2+ pass both."""
    ts = base_score(train_m)
    if val_m is None:
        return {"train_score": ts, "validation_score": float("nan"),
                "consistency_gap": float("nan"), "final_score": ts}
    vs = base_score(val_m)
    gap = abs(ts - vs) / max(abs(ts), EPSILON)
    return {"train_score": ts, "validation_score": vs, "consistency_gap": gap,
            "final_score": 0.6 * ts + 0.4 * vs - gap}


def meets_minimum(train_m: dict, val_m: Optional[dict]) -> bool:
    if train_m["n_trades"] < MIN_TRAIN_TRADES:
        return False
    if val_m is not None and val_m["n_trades"] < MIN_VAL_TRADES:
        return False
    return True


ACCEPTANCE = {
    "expectancy_R_train_gt": 0.02, "expectancy_R_val_gt": 0.02,
    "val_profit_factor_gt": 1.05, "val_max_dd_lt": 30.0, "consistency_gap_lt": 0.35,
}


def passes_acceptance(train_m: dict, val_m: dict, sc: Dict[str, float]) -> bool:
    return (train_m["expectancy_R"] > ACCEPTANCE["expectancy_R_train_gt"]
            and val_m["expectancy_R"] > ACCEPTANCE["expectancy_R_val_gt"]
            and val_m["profit_factor"] > ACCEPTANCE["val_profit_factor_gt"]
            and val_m["max_dd_pct"] < ACCEPTANCE["val_max_dd_lt"]
            and sc["consistency_gap"] < ACCEPTANCE["consistency_gap_lt"])


# ---------------------------------------------------------------------------
# Campaign
# ---------------------------------------------------------------------------
def suggest(trial: optuna.Trial, space: Dict[str, tuple]) -> dict:
    p = {}
    for name, spec in space.items():
        kind, lo, hi, step = spec
        if kind == "int":
            p[name] = trial.suggest_int(name, int(lo), int(hi), step=int(step))
        else:
            p[name] = trial.suggest_float(name, float(lo), float(hi), step=float(step))
    return p


def build_manifest(sha: str, risk_hash: str, risk_raw: dict, n: int,
                   idx: Dict[str, Tuple[int, int]], df: pd.DataFrame,
                   stage: int, trials: int, space: Dict[str, tuple]) -> dict:
    def rng(b):
        return {"rows": b[1] - b[0],
                "start": str(df["datetime"].iloc[b[0]]),
                "end": str(df["datetime"].iloc[b[1] - 1])}
    return {
        "optimizer_version": OPTIMIZER_VERSION, "stage": stage, "n_trials": trials,
        "symbol": SYMBOL, "platform": PLATFORM, "timeframe": RESOLUTION,
        "date_range": {"start": START_DATE, "end": END_DATE}, "candles": n,
        "dataset_sha256": sha,
        "seed": SEED, "n_jobs": 1, "sampler": "TPESampler", "pruner": "MedianPruner",
        "risk_policy_source": RISK_POLICY_PATH, "risk_policy_sha256": risk_hash,
        "risk_policy_values": risk_raw,
        "direction": {"long_enabled": True, "short_enabled": False},
        "split": {k: rng(v) for k, v in idx.items()},
        "warmup_bars": WARMUP_BARS,
        "search_space": {k: {"type": v[0], "low": v[1], "high": v[2], "step": v[3]}
                         for k, v in space.items()},
        "frozen_not_searched": sorted(FROZEN_RISK_KEYS),
        "objective": "0.6*train + 0.4*validation - consistency_gap (sizing-neutral)",
        "acceptance": ACCEPTANCE,
        "minimum_trades": {"train": MIN_TRAIN_TRADES, "validation": MIN_VAL_TRADES},
        "generated_utc": pd.Timestamp.utcnow().isoformat(),
    }


def run_campaign(stage: int, trials: int, study_name: str, space: Dict[str, tuple],
                 fresh: bool = False) -> optuna.Study:
    assert_search_space_is_clean()
    os.makedirs(RESULTS_DIR, exist_ok=True)

    risk_cfg, risk_hash, risk_raw = load_frozen_risk_policy()
    df, sha = load_dataset()
    n = len(df)
    idx = split_indices(n)

    print(f"[Phase3A] {SYMBOL} {RESOLUTION} LONG-ONLY | stage {stage} | {trials} trials")
    print(f"  candles {n:,} | {df['datetime'].iloc[0]} -> {df['datetime'].iloc[-1]}")
    print(f"  dataset sha {sha[:16]}… | risk policy sha {risk_hash[:16]}…")
    print(f"  risk (frozen): {risk_raw}")
    for k, (lo, hi) in idx.items():
        print(f"  {k:<11} rows {hi-lo:>7,}  {str(df['datetime'].iloc[lo])[:16]} -> {str(df['datetime'].iloc[hi-1])[:16]}")
    print("  HOLDOUT is not read during optimization.")

    storage = f"sqlite:///{os.path.join(RESULTS_DIR, study_name + '.db')}"
    if fresh:
        try:
            optuna.delete_study(study_name=study_name, storage=storage)
        except Exception:
            pass

    study = optuna.create_study(
        study_name=study_name, storage=storage, load_if_exists=True, direction="maximize",
        sampler=TPESampler(seed=SEED), pruner=MedianPruner(n_startup_trials=40),
    )

    use_val = (stage >= 2)

    def objective(trial: optuna.Trial) -> float:
        params = suggest(trial, space)
        tm = run_partition(df, idx["train"], params, risk_cfg)
        vm = run_partition(df, idx["validation"], params, risk_cfg) if use_val else None
        sc = score_candidate(tm, vm)

        for k, v in tm.items():
            trial.set_user_attr(f"train_{k}", v)
        if vm is not None:
            for k, v in vm.items():
                trial.set_user_attr(f"val_{k}", v)
        for k, v in sc.items():
            trial.set_user_attr(k, v)
        trial.set_user_attr("meets_minimum", meets_minimum(tm, vm))
        trial.set_user_attr("passes_acceptance",
                            bool(vm is not None and passes_acceptance(tm, vm, sc)))

        if not meets_minimum(tm, vm):
            return -10.0
        return sc["final_score"]

    t0 = time.time()
    study.optimize(objective, n_trials=trials, n_jobs=1)
    elapsed = time.time() - t0

    manifest = build_manifest(sha, risk_hash, risk_raw, n, idx, df, stage, trials, space)
    manifest["elapsed_seconds"] = round(elapsed, 2)
    manifest["completed_trials"] = len(study.trials)
    with open(os.path.join(RESULTS_DIR, f"{study_name}_manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    rows = []
    for t in study.trials:
        if t.state != optuna.trial.TrialState.COMPLETE:
            continue
        rows.append({"trial": t.number, "score": t.value, **t.params, **t.user_attrs})
    if rows:
        pd.DataFrame(rows).sort_values("score", ascending=False).to_csv(
            os.path.join(RESULTS_DIR, f"{study_name}_trials.csv"), index=False)

    print(f"\n[Phase3A] {len(study.trials)} trials in {elapsed:.1f}s | best {study.best_value:.5f}")
    print(f"  artifacts -> {RESULTS_DIR}/{study_name}_*.{{db,json,csv}}")
    return study


def main():
    ap = argparse.ArgumentParser(description="Phase 3A core optimizer — ETHUSDT 15m LONG only")
    ap.add_argument("--stage", type=int, default=1, choices=[1, 2],
                    help="1 = broad, TRAIN only. 2 = narrowed, TRAIN+VALIDATION.")
    ap.add_argument("--trials", type=int, default=None, help="Trial count (default: 400/800 by stage)")
    ap.add_argument("--study", type=str, default=None, help="Study name (SQLite, resumable)")
    ap.add_argument("--fresh", action="store_true", help="Delete an existing study of the same name first")
    ap.add_argument("--smoke", action="store_true", help="Tiny 4-trial verification run")
    a = ap.parse_args()

    if a.smoke:
        run_campaign(2, 4, a.study or "smoke_stage2", SEARCH_SPACE, fresh=a.fresh)
        return
    trials = a.trials if a.trials is not None else (400 if a.stage == 1 else 800)
    space = SEARCH_SPACE if a.stage == 1 else STAGE2_SPACE
    run_campaign(a.stage, trials, a.study or f"stage{a.stage}_15m_long", space, fresh=a.fresh)


if __name__ == "__main__":
    main()
