"""Warmup Ablation Study Harness.

QUARANTINED RESEARCH SCRIPT:
Lives only in src/optimization/backup/warmup_ablation_dump/

Evaluates the impact of warmup lengths (0, 170, 250, 500, 750, 1000, 2000)
against the 2,000-bar baseline across representative target timeframes (1m, 15m, 1h).
"""

import os
import sys
import glob
import json
import hashlib
import numpy as np
import pandas as pd

DUMP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(DUMP_DIR, "..", "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from common.config import PipelineConfig, StrategyConfig, RiskConfig, ExecutionConfig
from strategy.indicators import compute_all_indicators
from backtest.engine import BacktestEngine

REPORT_PATH = os.path.join(DUMP_DIR, "report.md")
CSV_PATH = os.path.join(DUMP_DIR, "ablation_results.csv")

PROTECTED_DIRS = ["src/auto_optimise", "src/optimization/v3", "configs", "data", "results", "pine"]

WARMUP_LEVELS = [0, 170, 250, 500, 750, 1000, 2000]
BASELINE_WARMUP = 2000
EVALUABLE_BARS = 3000


def get_directory_checksum(dir_path):
    full_path = os.path.join(ROOT, dir_path)
    if not os.path.exists(full_path):
        return "NONE"
    hashes = []
    for root, _, files in os.walk(full_path):
        for f in sorted(files):
            fp = os.path.join(root, f)
            with open(fp, "rb") as fh:
                hashes.append(hashlib.sha256(fh.read()).hexdigest())
    return hashlib.sha256("".join(hashes).encode()).hexdigest()[:16]


def get_all_protected_checksums():
    return {d: get_directory_checksum(d) for d in PROTECTED_DIRS}


def load_datasets():
    data_dir = os.path.join(ROOT, "data")
    datasets = {}

    # 15m native
    p15 = os.path.join(data_dir, "candles_futures_binance_futures_ETHUSDT_15m.csv")
    if os.path.exists(p15):
        df = pd.read_csv(p15)
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
        df = df.sort_values("datetime").reset_index(drop=True)
        datasets["15m"] = df

        # Resample 15m to 1h
        df_1h = df.set_index("datetime").resample("1h").agg({
            "timestamp": "first",
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum"
        }).dropna().reset_index()
        datasets["1h"] = df_1h

    # 1m native
    p1 = os.path.join(data_dir, "candles_futures_binance_futures_ETHUSDT_1m.csv")
    if os.path.exists(p1):
        df = pd.read_csv(p1)
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
        df = df.sort_values("datetime").reset_index(drop=True)
        datasets["1m"] = df

    return datasets


def create_vector_configs():
    # Vector A: Real frozen V3 Phase-16 style long-only vector
    cfg_a = StrategyConfig()
    cfg_a.ema_period = 51
    cfg_a.trend_ema_period = 200
    cfg_a.rsi_period = 14
    cfg_a.atr_period = 14
    cfg_a.volume_sma_period = 20
    cfg_a.swing_lookback = 8
    cfg_a.rsi_buy = 32.0
    cfg_a.rsi_sell = 68.0
    cfg_a.long_enabled = True
    cfg_a.short_enabled = False

    # Vector B: Max lookback stress vector
    cfg_b = StrategyConfig()
    cfg_b.ema_period = 220
    cfg_b.trend_ema_period = 220
    cfg_b.rsi_period = 50
    cfg_b.atr_period = 50
    cfg_b.volume_sma_period = 50
    cfg_b.swing_lookback = 20
    cfg_b.rsi_buy = 35.0
    cfg_b.rsi_sell = 65.0
    cfg_b.long_enabled = True
    cfg_b.short_enabled = False

    risk_cfg = RiskConfig()
    risk_cfg.stop_loss_pct = 0.03
    risk_cfg.take_profit_pct = 0.06
    risk_cfg.leverage = 1.0

    return {
        "Vector A (Standard V3 200-EMA)": cfg_a,
        "Vector B (Max Lookback 220-EMA)": cfg_b,
    }, risk_cfg


def run_single_evaluation(full_raw_df, eval_start_idx, evaluable_count, warmup_bars, strat_cfg, risk_cfg):
    start_idx = max(0, eval_start_idx - warmup_bars)
    frame = full_raw_df.iloc[start_idx:eval_start_idx + evaluable_count].copy().reset_index(drop=True)
    actual_lead = eval_start_idx - start_idx

    # Compute indicators on full (warmup + evaluable) frame
    full_ind = compute_all_indicators(frame, strat_cfg)

    # Indicator values at bar 0 and bar 170 of evaluable window
    ind_bar_0 = {
        "ema_51": float(full_ind["ema_51"].iloc[actual_lead]),
        "ema_200": float(full_ind["ema_200"].iloc[actual_lead]),
        "rsi": float(full_ind["rsi"].iloc[actual_lead]),
        "atr": float(full_ind["atr"].iloc[actual_lead])
    }
    bar_170_idx = min(len(full_ind) - 1, actual_lead + 170)
    ind_bar_170 = {
        "ema_51": float(full_ind["ema_51"].iloc[bar_170_idx]),
        "ema_200": float(full_ind["ema_200"].iloc[bar_170_idx]),
        "rsi": float(full_ind["rsi"].iloc[bar_170_idx]),
        "atr": float(full_ind["atr"].iloc[bar_170_idx])
    }

    # Slice evaluation window for backtest engine
    eval_ind = full_ind.iloc[actual_lead:].reset_index(drop=True)

    p_cfg = PipelineConfig()
    p_cfg.strategy = strat_cfg
    p_cfg.risk = risk_cfg
    p_cfg.execution = ExecutionConfig()

    engine = BacktestEngine(p_cfg)
    result = engine.run(eval_ind)

    trades = []
    if "trades" in result and result["trades"]:
        for t in result["trades"]:
            trades.append((
                str(getattr(t, "entry_time", "")),
                str(getattr(t, "exit_time", "")),
                str(getattr(t, "signal_type", "")),
                round(float(getattr(t, "gross_pnl", 0.0)), 4)
            ))

    metrics = {
        "trade_count": int(result.get("total_trades", len(trades))),
        "win_rate": round(float(result.get("win_rate", 0.0)), 2),
        "net_profit": round(float(result.get("net_profit", 0.0)), 2),
        "return_pct": round(float(result.get("return_pct", 0.0)), 2),
        "profit_factor": round(float(result.get("profit_factor", 0.0)), 4),
        "max_drawdown": round(float(result.get("max_drawdown_pct", 0.0)), 2)
    }

    return {
        "warmup_bars": warmup_bars,
        "actual_lead": actual_lead,
        "ind_bar_0": ind_bar_0,
        "ind_bar_170": ind_bar_170,
        "trades": trades,
        "metrics": metrics
    }


def compare_runs(test_run, baseline_run):
    b_ind0 = baseline_run["ind_bar_0"]
    t_ind0 = test_run["ind_bar_0"]

    b_ind170 = baseline_run["ind_bar_170"]
    t_ind170 = test_run["ind_bar_170"]

    ema_diff_0 = abs(t_ind0["ema_200"] - b_ind0["ema_200"])
    ema_diff_170 = abs(t_ind170["ema_200"] - b_ind170["ema_200"])

    rsi_diff_0 = abs(t_ind0["rsi"] - b_ind0["rsi"])

    trades_match = (test_run["trades"] == baseline_run["trades"])
    metrics_match = (test_run["metrics"] == baseline_run["metrics"])

    divergence = "NONE"
    if not trades_match:
        t_list = test_run["trades"]
        b_list = baseline_run["trades"]
        min_len = min(len(t_list), len(b_list))
        div_found = False
        for i in range(min_len):
            if t_list[i] != b_list[i]:
                divergence = f"Trade #{i+1} entry {t_list[i][0]} vs baseline {b_list[i][0]}"
                div_found = True
                break
        if not div_found:
            divergence = f"Trade count mismatch ({len(t_list)} vs baseline {len(b_list)})"
    elif ema_diff_0 > 1e-4:
        divergence = f"EMA-200 bar 0 diff = {ema_diff_0:.6f}"

    exact_match = (ema_diff_0 <= 1e-4) and trades_match and metrics_match

    return {
        "ema_diff_0": ema_diff_0,
        "ema_diff_170": ema_diff_170,
        "rsi_diff_0": rsi_diff_0,
        "trades_match": trades_match,
        "metrics_match": metrics_match,
        "divergence": divergence,
        "exact_match": exact_match
    }


def main():
    checksums_before = get_all_protected_checksums()
    print("Initial protection checksums:", checksums_before)

    datasets = load_datasets()
    vector_configs, risk_cfg = create_vector_configs()

    results_records = []
    timeframes_tested = ["1m", "15m", "1h"]

    smallest_safe_by_case = {}
    trade_changing_cases = []

    for tf in timeframes_tested:
        if tf not in datasets:
            print(f"Skipping {tf}: data not available in data/")
            continue

        df_full = datasets[tf]
        n_total = len(df_full)
        eval_count = min(EVALUABLE_BARS, n_total - BASELINE_WARMUP)

        if eval_count < 100:
            eval_count = max(50, n_total - BASELINE_WARMUP)
        eval_start_idx = n_total - eval_count

        for vec_name, strat_cfg in vector_configs.items():
            case_key = f"{tf} | {vec_name}"

            # Baseline run (2,000 warmup bars)
            baseline = run_single_evaluation(
                df_full, eval_start_idx, eval_count, BASELINE_WARMUP, strat_cfg, risk_cfg
            )

            smallest_safe = None

            for w in WARMUP_LEVELS:
                res = run_single_evaluation(
                    df_full, eval_start_idx, eval_count, w, strat_cfg, risk_cfg
                )
                cmp = compare_runs(res, baseline)

                rec = {
                    "timeframe": tf,
                    "vector": vec_name,
                    "warmup_bars": w,
                    "actual_lead": res["actual_lead"],
                    "ema200_bar0_diff": round(cmp["ema_diff_0"], 6),
                    "ema200_bar170_diff": round(cmp["ema_diff_170"], 6),
                    "rsi_bar0_diff": round(cmp["rsi_diff_0"], 6),
                    "trade_count": res["metrics"]["trade_count"],
                    "baseline_trades": baseline["metrics"]["trade_count"],
                    "trades_match": cmp["trades_match"],
                    "return_pct": res["metrics"]["return_pct"],
                    "baseline_return": baseline["metrics"]["return_pct"],
                    "max_dd_pct": res["metrics"]["max_drawdown"],
                    "profit_factor": res["metrics"]["profit_factor"],
                    "divergence": cmp["divergence"],
                    "exact_match": cmp["exact_match"]
                }
                results_records.append(rec)

                if cmp["exact_match"] and smallest_safe is None:
                    smallest_safe = w

                if not cmp["trades_match"]:
                    trade_changing_cases.append(f"{tf} / {vec_name} / W={w} (Trades: {res['metrics']['trade_count']} vs Baseline {baseline['metrics']['trade_count']})")

            smallest_safe_by_case[case_key] = smallest_safe if smallest_safe is not None else 2000

    # Save CSV
    df_res = pd.DataFrame(results_records)
    df_res.to_csv(CSV_PATH, index=False)

    # Determine universal smallest safe
    all_safes = list(smallest_safe_by_case.values())
    universal_smallest = max(all_safes) if all_safes else 1000
    is_1000_justified = (universal_smallest >= 1000)

    # Build report
    report_lines = []
    report_lines.append("# Warmup Ablation Research Report")
    report_lines.append("")
    report_lines.append("## Executive Summary")
    report_lines.append(f"- **1000-bar warmup justified**: {'YES' if is_1000_justified else 'NO'}")
    report_lines.append(f"- **Smallest safe universal warmup**: {universal_smallest} bars")
    report_lines.append("")
    report_lines.append("## Detailed Ablation Results")
    report_lines.append("")
    report_lines.append("| Timeframe | Vector | Warmup | EMA-200 Diff (Bar 0) | EMA-200 Diff (Bar 170) | Trades Match | Trade Count | Return % | Max DD % | Divergence |")
    report_lines.append("| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |")

    for r in results_records:
        report_lines.append(
            f"| {r['timeframe']} | {r['vector']} | {r['warmup_bars']} | {r['ema200_bar0_diff']:.6f} | "
            f"{r['ema200_bar170_diff']:.6f} | {'YES' if r['trades_match'] else 'NO'} | {r['trade_count']} | "
            f"{r['return_pct']:.2f}% | {r['max_dd_pct']:.2f}% | {r['divergence']} |"
        )

    report_lines.append("")
    report_lines.append("## Case-by-Case Smallest Safe Warmup")
    report_lines.append("")
    for k, v in smallest_safe_by_case.items():
        report_lines.append(f"- `{k}`: **{v} bars**")

    report_lines.append("")
    report_lines.append("## Trade-Divergent Configurations (< Baseline 2,000)")
    if trade_changing_cases:
        for c in trade_changing_cases:
            report_lines.append(f"- {c}")
    else:
        report_lines.append("- None")

    report_lines.append("")
    report_lines.append("## Conclusion & Decision Verification")
    report_lines.append(f"1000-bar warmup justified: {'YES' if is_1000_justified else 'NO'}")
    report_lines.append(f"smallest safe universal warmup: {universal_smallest}")
    report_lines.append(f"any timeframe/config where lower warmup changes trades: {'; '.join(trade_changing_cases) if trade_changing_cases else 'None'}")

    with open(REPORT_PATH, "w") as fh:
        fh.write("\n".join(report_lines))

    print("\nAblation complete. Report written to:", REPORT_PATH)

    checksums_after = get_all_protected_checksums()
    print("Final protection checksums:", checksums_after)
    assert checksums_before == checksums_after, "PROTECTED PRODUCTION FILES WERE MODIFIED!"
    print("ASSERTION PASSED: Production files remain 100% untouched.")


if __name__ == "__main__":
    main()
