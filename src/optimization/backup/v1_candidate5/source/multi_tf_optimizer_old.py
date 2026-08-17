"""
Multi-Timeframe Parameter Optimizer — Phase 5
Optimizes strategy parameters per timeframe using Optuna TPE.
Train=50%, Validation=25%, Holdout=25%.
Does NOT modify production code. Only varies config inputs.

Usage:
    TQDM_DISABLE=1 PYTHONPATH=src python src/optimization/multi_tf_optimizer.py

Data must be pre-downloaded (use fetch_data.py first).
"""

import os
import sys
import json
import hashlib
import time
import warnings

warnings.filterwarnings("ignore")

# Add src to path
_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_DIR, ".."))

import numpy as np
import pandas as pd
import optuna
from optuna.samplers import TPESampler

optuna.logging.set_verbosity(optuna.logging.WARNING)

from common.config import PipelineConfig, StrategyConfig, RiskConfig, ExecutionConfig, PlatformConfig
from common.market_data import MarketDataLoader
from strategy.indicators import compute_all_indicators
from backtest.engine import BacktestEngine

SEED = 42
DATA_DIR = "data"
OUT_DIR = "results/multi_tf_optimization"
INITIAL_CAPITAL = 10000.0
START_DATE = "2024-01-01"
END_DATE = "2026-08-13"
TRIALS_PER_TF = 750

TIMEFRAMES = ["1m", "2m", "3m", "5m", "15m", "30m", "1h", "2h", "3h", "4h"]

# 2m is not native on Binance Futures — resampled from 1m
RESAMPLE_FROM_1M = {"2m"}

TF_MINUTES = {
    "1m": 1, "2m": 2, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "3h": 180, "4h": 240,
}


def resample_1m_to(df_1m: pd.DataFrame, target_tf: str) -> pd.DataFrame:
    """Resample a 1m DataFrame to a target timeframe (e.g. '2m')."""
    tf_mins = TF_MINUTES[target_tf]
    rule = f"{tf_mins}min"
    df = df_1m.copy()
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    df = df.set_index("datetime").sort_index()
    resampled = (
        df.resample(rule, closed="left", label="left")
        .agg({"timestamp": "first", "open": "first", "high": "max",
              "low": "min", "close": "last", "volume": "sum"})
        .dropna()
        .reset_index()
    )
    resampled["timestamp"] = resampled["timestamp"].astype(int)
    return resampled


def make_cfg(params: dict, resolution: str) -> PipelineConfig:
    cfg = PipelineConfig(execution_mode="REFERENCE")
    cfg.platform = PlatformConfig(
        platform="BINANCE_FUTURES", symbol="ETHUSDT", resolution=resolution,
        start_date=START_DATE, end_date=END_DATE,
    )
    cfg.strategy = StrategyConfig(
        symbol="ETHUSDT", resolution=resolution,
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
        long_enabled=params["long_enabled"],
        short_enabled=params["short_enabled"],
        risk_reward_ratio=params["risk_reward_ratio"],
    )
    cfg.risk = RiskConfig(
        initial_capital=INITIAL_CAPITAL,
        risk_per_trade_pct=params["risk_per_trade_pct"],
        max_position_allocation_pct=params["max_position_allocation_pct"],
        leverage=params["leverage"],
    )
    cfg.execution = ExecutionConfig(mode="REFERENCE")
    return cfg


def _empty_metrics() -> dict:
    return {
        "n_trades": 0, "net_return_pct": -100.0, "final_balance": INITIAL_CAPITAL,
        "profit_factor": 0.0, "max_dd_pct": 100.0, "sharpe": -10.0,
        "win_rate": 0.0, "expectancy": -9999.0, "gross_profit": 0.0,
        "gross_loss": 0.0, "total_fees": 0.0,
    }


def run_backtest_on_slice(df_slice: pd.DataFrame, params: dict, resolution: str) -> dict:
    if len(df_slice) < 100:
        return _empty_metrics()
    cfg = make_cfg(params, resolution)
    try:
        df_ind = compute_all_indicators(df_slice.copy(), cfg.strategy)
        engine = BacktestEngine(cfg)
        result = engine.run(df_ind)
    except Exception:
        return _empty_metrics()

    trades = result["trades"]
    n = len(trades)
    if n == 0:
        return _empty_metrics()

    net_pnls = [t.net_pnl for t in trades]
    gross_profit = sum(p for p in net_pnls if p > 0)
    gross_loss = abs(sum(p for p in net_pnls if p < 0))
    wins = sum(1 for p in net_pnls if p > 0)
    win_rate = wins / n
    pf = gross_profit / gross_loss if gross_loss > 1e-9 else (99.0 if gross_profit > 0 else 0.0)
    expectancy = sum(net_pnls) / n
    total_fees = sum(t.total_fees for t in trades)
    net_ret = result["net_return_pct"]
    max_dd = result["max_drawdown_pct"]
    final_bal = result["final_balance"]

    eq_curve = result["equity_curve"]
    if len(eq_curve) > 2:
        eq_vals = np.array([e["equity"] for e in eq_curve])
        returns = np.diff(eq_vals) / (eq_vals[:-1] + 1e-9)
        sharpe = float((returns.mean() / (returns.std() + 1e-9)) * np.sqrt(252)) if returns.std() > 1e-9 else 0.0
    else:
        sharpe = 0.0

    return {
        "n_trades": n,
        "net_return_pct": round(net_ret, 4),
        "final_balance": round(final_bal, 4),
        "profit_factor": round(pf, 4),
        "max_dd_pct": round(max_dd, 4),
        "sharpe": round(sharpe, 4),
        "win_rate": round(win_rate, 4),
        "expectancy": round(expectancy, 4),
        "gross_profit": round(gross_profit, 4),
        "gross_loss": round(gross_loss, 4),
        "total_fees": round(total_fees, 4),
    }


def robust_score(m: dict) -> float:
    n = m["n_trades"]
    if n < 5:
        return -1000.0
    ret = m["net_return_pct"]
    pf = m["profit_factor"]
    dd = m["max_dd_pct"]
    sharpe = m["sharpe"]
    wr = m["win_rate"]
    exp = m["expectancy"]

    ret_score    = ret * 0.3
    pf_score     = min(pf, 5.0) * 15.0
    dd_penalty   = -dd * 1.5
    sharpe_score = min(max(sharpe, -5.0), 5.0) * 10.0
    wr_score     = wr * 20.0
    exp_score    = min(max(exp, -500), 500) * 0.05

    if n < 15:
        trade_penalty = -30.0
    elif n < 30:
        trade_penalty = -10.0
    else:
        trade_penalty = 0.0

    if dd > 40.0:
        dd_penalty -= 50.0
    elif dd > 25.0:
        dd_penalty -= 20.0

    return ret_score + pf_score + dd_penalty + sharpe_score + wr_score + exp_score + trade_penalty


def generalization_score(train_m: dict, val_m: dict) -> float:
    base = robust_score(train_m)
    if base < -500:
        return base
    val_s = robust_score(val_m)
    return base * 0.6 + val_s * 0.4


def suggest_params(trial: optuna.Trial, resolution: str) -> dict:
    is_fast = resolution in ["1m", "2m", "3m", "5m"]
    cons_candles_max = 15 if is_fast else 20

    ema_period               = trial.suggest_int("ema_period", 10, 200)
    rsi_period               = trial.suggest_int("rsi_period", 7, 21)
    rsi_ob                   = trial.suggest_float("rsi_overbought", 55.0, 80.0, step=1.0)
    rsi_os                   = trial.suggest_float("rsi_oversold", 20.0, 45.0, step=1.0)
    atr_period               = trial.suggest_int("atr_period", 7, 21)
    consolidation_candles    = trial.suggest_int("consolidation_candles", 4, cons_candles_max)
    consolidation_atr_mult   = trial.suggest_float("consolidation_atr_mult", 1.0, 4.0, step=0.1)
    swing_lookback           = trial.suggest_int("swing_lookback", 4, 20)
    volume_sma_period        = trial.suggest_int("volume_sma_period", 10, 50)
    volume_mult              = trial.suggest_float("volume_mult", 0.5, 2.0, step=0.1)
    risk_reward_ratio        = trial.suggest_float("risk_reward_ratio", 1.0, 4.0, step=0.1)
    risk_per_trade_pct       = trial.suggest_float("risk_per_trade_pct", 0.005, 0.03, step=0.001)
    max_position_allocation_pct = trial.suggest_float("max_position_allocation_pct", 0.25, 0.75, step=0.05)
    leverage                 = trial.suggest_float("leverage", 1.0, 10.0, step=0.5)
    side_choice              = trial.suggest_categorical("side_choice", ["both", "long_only", "short_only"])

    return {
        "ema_period": ema_period,
        "rsi_period": rsi_period,
        "rsi_overbought": rsi_ob,
        "rsi_oversold": rsi_os,
        "atr_period": atr_period,
        "consolidation_candles": consolidation_candles,
        "consolidation_atr_mult": consolidation_atr_mult,
        "swing_lookback": swing_lookback,
        "volume_sma_period": volume_sma_period,
        "volume_mult": volume_mult,
        "risk_reward_ratio": risk_reward_ratio,
        "risk_per_trade_pct": risk_per_trade_pct,
        "max_position_allocation_pct": max_position_allocation_pct,
        "leverage": leverage,
        "long_enabled": side_choice in ("both", "long_only"),
        "short_enabled": side_choice in ("both", "short_only"),
        "side_choice": side_choice,
    }


def load_data_for_tf(resolution: str):
    """Load data for given timeframe. Handles 2m by resampling from 1m."""
    loader = MarketDataLoader(data_dir=DATA_DIR)

    if resolution in RESAMPLE_FROM_1M:
        # Load 1m, resample to target
        cfg_1m = PlatformConfig(
            platform="BINANCE_FUTURES", symbol="ETHUSDT", resolution="1m",
            start_date=START_DATE, end_date=END_DATE,
        )
        df_1m = loader.load_ohlcv(cfg_1m, quiet=True)
        df_1m["datetime"] = pd.to_datetime(df_1m["datetime"])
        df_1m = df_1m.sort_values("timestamp").reset_index(drop=True)
        df = resample_1m_to(df_1m, resolution)
    else:
        cfg = PlatformConfig(
            platform="BINANCE_FUTURES", symbol="ETHUSDT", resolution=resolution,
            start_date=START_DATE, end_date=END_DATE,
        )
        df = loader.load_ohlcv(cfg, quiet=True)
        if "datetime" in df.columns:
            df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.sort_values("timestamp").reset_index(drop=True)

    sha = hashlib.sha256(df.to_csv(index=False).encode()).hexdigest()
    return df, sha


def split_data(df: pd.DataFrame):
    n = len(df)
    t1 = int(n * 0.50)
    t2 = int(n * 0.75)
    return (df.iloc[:t1].reset_index(drop=True),
            df.iloc[t1:t2].reset_index(drop=True),
            df.iloc[t2:].reset_index(drop=True))


def optimize_timeframe(resolution: str, n_trials: int = TRIALS_PER_TF) -> dict:
    print(f"\n{'='*60}")
    print(f"  Optimizing: {resolution}")
    print(f"{'='*60}")

    df_full, sha = load_data_for_tf(resolution)
    n_candles = len(df_full)
    df_start = str(df_full.iloc[0]["datetime"])
    df_end = str(df_full.iloc[-1]["datetime"])

    print(f"  Data: {n_candles:,} candles | {df_start[:10]} → {df_end[:10]}")
    print(f"  SHA256: {sha[:16]}...")

    df_train, df_val, df_hold = split_data(df_full)
    print(f"  Train: {len(df_train):,} | Val: {len(df_val):,} | Hold: {len(df_hold):,}")

    start_time = time.time()
    check_interval = max(1, n_trials // 10)

    def objective(trial: optuna.Trial) -> float:
        params = suggest_params(trial, resolution)
        train_m = run_backtest_on_slice(df_train, params, resolution)
        val_m   = run_backtest_on_slice(df_val, params, resolution)
        score   = generalization_score(train_m, val_m)
        trial.set_user_attr("train_return",  train_m["net_return_pct"])
        trial.set_user_attr("train_pf",      train_m["profit_factor"])
        trial.set_user_attr("train_dd",      train_m["max_dd_pct"])
        trial.set_user_attr("train_trades",  train_m["n_trades"])
        trial.set_user_attr("val_return",    val_m["net_return_pct"])
        trial.set_user_attr("val_pf",        val_m["profit_factor"])
        trial.set_user_attr("val_dd",        val_m["max_dd_pct"])
        trial.set_user_attr("val_trades",    val_m["n_trades"])
        return score

    def progress_cb(study: optuna.Study, trial: optuna.Trial):
        t = trial.number + 1
        if t % check_interval == 0 or t == n_trials:
            elapsed = time.time() - start_time
            eta     = (elapsed / t) * (n_trials - t)
            best    = study.best_value if study.best_trial else 0.0
            print(f"  [{resolution}] {t}/{n_trials} | Best robust score: {best:.2f} | ETA: {eta:.0f}s")

    study = optuna.create_study(direction="maximize", sampler=TPESampler(seed=SEED))
    study.optimize(objective, n_trials=n_trials, callbacks=[progress_cb], show_progress_bar=False)

    # Reconstruct best params
    best_trial = study.best_trial
    params = {}
    for k, v in best_trial.params.items():
        params[k] = v
    side_choice = params.get("side_choice", "both")
    params["long_enabled"]  = side_choice in ("both", "long_only")
    params["short_enabled"] = side_choice in ("both", "short_only")

    # Final full evaluation including holdout
    train_m = run_backtest_on_slice(df_train, params, resolution)
    val_m   = run_backtest_on_slice(df_val,   params, resolution)
    hold_m  = run_backtest_on_slice(df_hold,  params, resolution)

    robust = (
        val_m["n_trades"]    >= 5   and
        hold_m["n_trades"]   >= 5   and
        val_m["profit_factor"]  >= 1.0 and
        hold_m["profit_factor"] >= 1.0 and
        val_m["max_dd_pct"]  < 40.0 and
        hold_m["max_dd_pct"] < 40.0
    )

    # Save trials CSV
    trials_rows = []
    for t in study.trials:
        row = {"trial": t.number, "score": t.value}
        row.update(t.params)
        row.update({k: t.user_attrs.get(k, 0)
                    for k in ["train_return","train_pf","train_dd","train_trades","val_return","val_pf","val_dd","val_trades"]})
        trials_rows.append(row)
    tf_safe = resolution.replace("/", "_")
    pd.DataFrame(trials_rows).to_csv(os.path.join(OUT_DIR, f"trials_{tf_safe}.csv"), index=False)

    return {
        "timeframe": resolution,
        "data_start": df_start,
        "data_end": df_end,
        "n_candles": n_candles,
        "sha256": sha,
        "n_trials": n_trials,
        "params": params,
        "train": train_m,
        "validation": val_m,
        "holdout": hold_m,
        "best_score": round(study.best_value, 4),
        "robust": robust,
    }


def format_summary_table(results: list) -> str:
    def sort_key(r):
        return (1 if r["robust"] else 0,
                r["holdout"]["net_return_pct"] + r["holdout"]["profit_factor"] * 5)

    ranked = sorted(results, key=sort_key, reverse=True)
    header = (f"{'Rank':<5} {'TF':<6} {'Tr.Ret%':>8} {'Tr.PF':>6} {'Tr.DD%':>7} {'Tr.T':>5} "
              f"{'Val.Ret%':>9} {'Val.PF':>7} {'Hld.Ret%':>9} {'Hld.PF':>7} {'Hld.T':>6} {'Robust':>7}")
    sep = "-" * len(header)
    lines = [sep, header, sep]
    for i, r in enumerate(ranked, 1):
        tr = r["train"]; va = r["validation"]; ho = r["holdout"]
        lines.append(
            f"{i:<5} {r['timeframe']:<6} {tr['net_return_pct']:>8.2f} {tr['profit_factor']:>6.2f} "
            f"{tr['max_dd_pct']:>7.2f} {tr['n_trades']:>5} "
            f"{va['net_return_pct']:>9.2f} {va['profit_factor']:>7.2f} "
            f"{ho['net_return_pct']:>9.2f} {ho['profit_factor']:>7.2f} {ho['n_trades']:>6} "
            f"{'YES' if r['robust'] else 'NO':>7}"
        )
    lines.append(sep)
    return "\n".join(lines)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    all_results = []

    for tf in TIMEFRAMES:
        try:
            result = optimize_timeframe(tf, n_trials=TRIALS_PER_TF)
            all_results.append(result)

            with open(os.path.join(OUT_DIR, "best_by_timeframe.json"), "w") as f:
                json.dump(all_results, f, indent=2)

            p  = result["params"]
            tr = result["train"]
            va = result["validation"]
            ho = result["holdout"]
            print(f"\n  ── {tf} Best Candidate ──")
            print(f"  EMA={p['ema_period']} RSI={p['rsi_period']}({p['rsi_oversold']:.0f}/{p['rsi_overbought']:.0f}) "
                  f"ATR={p['atr_period']} Cons={p['consolidation_candles']}×{p['consolidation_atr_mult']:.1f}ATR "
                  f"Swing={p['swing_lookback']} Vol={p['volume_sma_period']}×{p['volume_mult']:.1f} "
                  f"RR={p['risk_reward_ratio']:.1f} Risk={p['risk_per_trade_pct']*100:.2f}% "
                  f"Lev={p['leverage']:.1f}x MaxAlloc={p['max_position_allocation_pct']*100:.0f}% Side={p['side_choice']}")
            print(f"  Train:  Ret={tr['net_return_pct']:+.2f}% PF={tr['profit_factor']:.2f} DD={tr['max_dd_pct']:.2f}% T={tr['n_trades']}")
            print(f"  Val:    Ret={va['net_return_pct']:+.2f}% PF={va['profit_factor']:.2f} DD={va['max_dd_pct']:.2f}% T={va['n_trades']}")
            print(f"  Hold:   Ret={ho['net_return_pct']:+.2f}% PF={ho['profit_factor']:.2f} DD={ho['max_dd_pct']:.2f}% T={ho['n_trades']}")
            print(f"  Robust: {'YES ✓' if result['robust'] else 'NO ✗'}")

        except Exception as e:
            import traceback
            print(f"\n  ERROR on {tf}: {e}")
            traceback.print_exc()
            all_results.append({
                "timeframe": tf, "error": str(e), "robust": False,
                "train": _empty_metrics(), "validation": _empty_metrics(), "holdout": _empty_metrics(),
            })

    # Summary CSV
    summary_rows = []
    for r in all_results:
        if "error" in r:
            continue
        p = r.get("params", {})
        row = {
            "timeframe": r["timeframe"],
            "data_start": r.get("data_start", ""),
            "data_end": r.get("data_end", ""),
            "n_candles": r.get("n_candles", 0),
            "sha256": r.get("sha256", ""),
            "robust": r["robust"],
            "best_score": r.get("best_score", 0),
        }
        row.update({f"param_{k}": v for k, v in p.items()})
        row.update({f"train_{k}": v for k, v in r["train"].items()})
        row.update({f"val_{k}":   v for k, v in r["validation"].items()})
        row.update({f"hold_{k}":  v for k, v in r["holdout"].items()})
        summary_rows.append(row)

    if summary_rows:
        pd.DataFrame(summary_rows).to_csv(os.path.join(OUT_DIR, "summary.csv"), index=False)
        print(f"\n  Summary saved → {OUT_DIR}/summary.csv")

    valid_results = [r for r in all_results if "error" not in r]
    if valid_results:
        print("\n\n" + "=" * 100)
        print("  FINAL RANKED RESULTS (robust out-of-sample first)")
        print("=" * 100)
        print(format_summary_table(valid_results))

        print("\n\n" + "=" * 100)
        print("  DETAILED BEST PARAMETERS PER TIMEFRAME")
        print("=" * 100)
        def sort_key(r):
            return (1 if r["robust"] else 0,
                    r["holdout"]["net_return_pct"] + r["holdout"]["profit_factor"] * 5)
        for r in sorted(valid_results, key=sort_key, reverse=True):
            p  = r["params"]
            tr = r["train"]; va = r["validation"]; ho = r["holdout"]
            print(f"\n  ┌─ {r['timeframe']} | Robust={'YES ✓' if r['robust'] else 'NO ✗'} | "
                  f"{r['n_candles']:,} candles | {r['data_start'][:10]} → {r['data_end'][:10]}")
            print(f"  │  SHA256: {r['sha256']}")
            print(f"  │  Indicators: EMA={p['ema_period']}  RSI={p['rsi_period']}({p['rsi_oversold']:.0f}/{p['rsi_overbought']:.0f})  ATR={p['atr_period']}")
            print(f"  │  Consolidation: {p['consolidation_candles']} candles × {p['consolidation_atr_mult']:.1f}×ATR")
            print(f"  │  Swing Lookback: {p['swing_lookback']}   Volume SMA: {p['volume_sma_period']} × {p['volume_mult']:.1f}")
            print(f"  │  RR={p['risk_reward_ratio']:.1f}  Risk/Trade={p['risk_per_trade_pct']*100:.2f}%  Leverage={p['leverage']:.1f}x  MaxAlloc={p['max_position_allocation_pct']*100:.0f}%")
            print(f"  │  Side: {p['side_choice']}")
            print(f"  │")
            print(f"  │  Split    Ret%        PF      MaxDD%  Sharpe  Trades  WinRate%   Expectancy  Fees")
            print(f"  │  Train   {tr['net_return_pct']:>+8.2f}  {tr['profit_factor']:>7.3f}  {tr['max_dd_pct']:>8.2f}  {tr['sharpe']:>6.2f}  {tr['n_trades']:>6}  {tr['win_rate']*100:>8.1f}  {tr['expectancy']:>10.2f}  ${tr['total_fees']:.2f}")
            print(f"  │  Val     {va['net_return_pct']:>+8.2f}  {va['profit_factor']:>7.3f}  {va['max_dd_pct']:>8.2f}  {va['sharpe']:>6.2f}  {va['n_trades']:>6}  {va['win_rate']*100:>8.1f}  {va['expectancy']:>10.2f}  ${va['total_fees']:.2f}")
            print(f"  └─ Hold   {ho['net_return_pct']:>+8.2f}  {ho['profit_factor']:>7.3f}  {ho['max_dd_pct']:>8.2f}  {ho['sharpe']:>6.2f}  {ho['n_trades']:>6}  {ho['win_rate']*100:>8.1f}  {ho['expectancy']:>10.2f}  ${ho['total_fees']:.2f}")


if __name__ == "__main__":
    main()
