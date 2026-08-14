import os
import sys
import time
import math
import json
import optuna
import pandas as pd
import numpy as np
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))

from common.config import PipelineConfig, PlatformConfig, RiskConfig, StrategyConfig
from common.market_data import MarketDataLoader
from strategy.indicators import compute_all_indicators
from backtest.engine import BacktestEngine
from common.utils import mute_console_loggers

# Turn off Optuna default verbose logging
optuna.logging.set_verbosity(optuna.logging.WARNING)

os.makedirs(os.path.join(PROJECT_ROOT, "logs"), exist_ok=True)
os.makedirs(os.path.join(PROJECT_ROOT, "results", "multi_tf"), exist_ok=True)

LOG_FILE = os.path.join(PROJECT_ROOT, "logs", "multi_tf_optimization.log")

def log(msg):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] {msg}"
    print(formatted, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(formatted + "\n")
        f.flush()

TIMEFRAMES = ['4h', '3h', '2h', '1h', '30m', '5m', '3m', '1m']

def format_eta(seconds):
    if seconds < 0 or math.isinf(seconds) or math.isnan(seconds):
        return "00:00:00"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

def run_single_backtest(df_raw, params_dict, tf, taker_fee=0.0005, slippage_ticks=1.0):
    cfg = PipelineConfig()
    cfg.platform.symbol = "ETHUSDT"
    cfg.platform.resolution = tf
    cfg.risk.initial_capital = 10000.0
    cfg.risk.leverage = 1.0  # STRICTLY FIXED
    cfg.risk.risk_per_trade_pct = 0.015
    cfg.risk.max_position_allocation_pct = 0.50

    cfg.execution.taker_fee_pct = taker_fee
    cfg.execution.slippage_ticks = float(slippage_ticks)

    # Set strategy params
    for k, v in params_dict.items():
        if hasattr(cfg.strategy, k):
            setattr(cfg.strategy, k, v)

    df_ind = compute_all_indicators(df_raw.copy(), cfg.strategy)
    bt_engine = BacktestEngine(cfg)
    summary = bt_engine.run(df_ind)
    trades = summary.get("trades", [])
    return summary, trades

def run_robustness(df_raw, best_params, tf):
    log(f"[{tf}] Running Robustness Tests for Best Candidate...")
    
    test_queue = []
    
    # 1. Parameter Sensitivity
    num_params = {
        'ema_period': (int, 1, 200),
        'rsi_period': (int, 2, 100),
        'rsi_overbought': (float, 50.0, 95.0),
        'rsi_oversold': (float, 5.0, 50.0),
        'atr_period': (int, 1, 100),
        'consolidation_candles': (int, 1, 50),
        'consolidation_atr_mult': (float, 0.5, 10.0),
        'swing_lookback': (int, 2, 50),
        'volume_sma_period': (int, 2, 100),
        'volume_mult': (float, 0.1, 5.0),
        'risk_reward_ratio': (float, 0.5, 10.0)
    }
    
    for param_name, (ptype, min_v, max_v) in num_params.items():
        base_val = best_params.get(param_name)
        if base_val is None: continue
        for mult in [0.90, 0.95, 1.05, 1.10]:
            if ptype == int:
                new_val = max(min_v, min(max_v, int(round(base_val * mult))))
            else:
                new_val = max(min_v, min(max_v, round(base_val * mult, 2)))
            if new_val == base_val: continue
            
            p_copy = best_params.copy()
            p_copy[param_name] = new_val
            test_queue.append({'params': p_copy, 'df': df_raw, 'fee': 0.0005, 'slip': 1.0})

    # 2. Rolling Windows
    n_splits = 4
    chunk_size = len(df_raw) // n_splits
    for i in range(n_splits):
        sub_df = df_raw.iloc[i*chunk_size : (i+1)*chunk_size if i < n_splits-1 else len(df_raw)].reset_index(drop=True)
        test_queue.append({'params': best_params.copy(), 'df': sub_df, 'fee': 0.0005, 'slip': 1.0})

    # 3. SIDE Isolation
    for mode, long_e, short_e in [('Long Only', True, False), ('Short Only', False, True), ('Both Sides', True, True)]:
        p_copy = best_params.copy()
        p_copy['long_enabled'] = long_e
        p_copy['short_enabled'] = short_e
        test_queue.append({'params': p_copy, 'df': df_raw, 'fee': 0.0005, 'slip': 1.0})

    # 4. Fee & Friction Sensitivity
    friction_scenarios = [
        (0.0, 0.0), (0.0003, 1.0), (0.0005, 1.0), (0.00075, 2.0), (0.0010, 3.0)
    ]
    for fee, slip in friction_scenarios:
        test_queue.append({'params': best_params.copy(), 'df': df_raw, 'fee': fee, 'slip': slip})

    # Execute all tests and check profitability
    profitable_tests = 0
    total_tests = len(test_queue)
    for test in test_queue:
        summary, trades = run_single_backtest(test['df'], test['params'], tf, test['fee'], test['slip'])
        if summary.get('net_return_pct', 0) > 0:
            profitable_tests += 1
            
    pct_robust = (profitable_tests / total_tests) * 100
    is_robust = pct_robust > 80.0  # Require > 80% of robustness tests to be profitable
    log(f"[{tf}] Robustness: {profitable_tests}/{total_tests} ({pct_robust:.1f}%) profitable -> {'YES' if is_robust else 'NO'}")
    return is_robust

def optimize_timeframe(tf, loader):
    log(f"==================================================")
    log(f"[{tf}] STARTING STRATEGY OPTIMIZATION")
    log(f"==================================================")
    
    # 1. Load Data
    cfg = PlatformConfig(symbol="ETHUSDT", resolution=tf, platform="BINANCE_FUTURES", start_date="2024-01-01", end_date="2026-08-13")
    try:
        df_raw = loader.load_ohlcv(cfg, quiet=True)
    except Exception as e:
        log(f"[{tf}] ERROR loading data: {e}")
        return None
        
    total_len = len(df_raw)
    start_dt = df_raw.iloc[0].get('datetime', str(df_raw.iloc[0].get('timestamp', '')))
    end_dt = df_raw.iloc[-1].get('datetime', str(df_raw.iloc[-1].get('timestamp', '')))
    log(f"[{tf}] Dataset: {total_len} candles ({start_dt} to {end_dt})")

    # 2. Split Data: 50% Train, 25% Validation, 25% Holdout
    n_train = int(total_len * 0.50)
    n_val = int(total_len * 0.25)
    
    df_train = df_raw.iloc[:n_train].reset_index(drop=True)
    df_val = df_raw.iloc[n_train:n_train+n_val].reset_index(drop=True)
    df_holdout = df_raw.iloc[n_train+n_val:].reset_index(drop=True)

    # 3. Optuna Objective on TRAIN ONLY
    best_train_score = -999.0
    best_params = None

    def objective(trial: optuna.Trial) -> float:
        nonlocal best_train_score, best_params
        
        params = {
            "ema_period": trial.suggest_categorical("ema_period", [20, 30, 40, 51, 60, 75, 100]),
            "rsi_period": trial.suggest_categorical("rsi_period", [7, 10, 14, 21]),
            "rsi_overbought": trial.suggest_float("rsi_overbought", 55.0, 75.0, step=2.5),
            "rsi_oversold": trial.suggest_float("rsi_oversold", 25.0, 45.0, step=2.5),
            "atr_period": trial.suggest_categorical("atr_period", [7, 10, 14, 21]),
            "consolidation_candles": trial.suggest_int("consolidation_candles", 4, 16, step=2),
            "consolidation_atr_mult": trial.suggest_float("consolidation_atr_mult", 1.2, 3.2, step=0.2),
            "swing_lookback": trial.suggest_int("swing_lookback", 4, 16, step=2),
            "volume_sma_period": trial.suggest_categorical("volume_sma_period", [10, 14, 20, 30]),
            "use_volume_filter": trial.suggest_categorical("use_volume_filter", [True, False]),
            "risk_reward_ratio": trial.suggest_float("risk_reward_ratio", 1.0, 3.5, step=0.25),
            "use_ema_slope_filter": trial.suggest_categorical("use_ema_slope_filter", [True, False]),
            "use_trend_filter": trial.suggest_categorical("use_trend_filter", [True, False]),
            "long_enabled": trial.suggest_categorical("long_enabled", [True, False]),
            "short_enabled": trial.suggest_categorical("short_enabled", [True, False])
        }
        params["volume_mult"] = trial.suggest_float("volume_mult", 0.6, 2.0, step=0.2) if params["use_volume_filter"] else 1.0
        params["trend_ema_period"] = trial.suggest_categorical("trend_ema_period", [100, 150, 200, 300]) if params["use_trend_filter"] else 200

        if not params["long_enabled"] and not params["short_enabled"]:
            return -999.0

        summary, _ = run_single_backtest(df_train, params, tf)
        ret = summary.get("net_return_pct", -999.0)
        pf = summary.get("profit_factor", 0.0)
        trades = summary.get("total_trades", 0)

        # Objective Function: heavily penalize low trades and sub-1 PF
        if trades < 30 or pf < 1.05:
            score = ret - 100.0
        else:
            score = ret * min(pf, 3.0)

        trial.set_user_attr("net_return_pct", ret)
        trial.set_user_attr("profit_factor", pf)

        if score > best_train_score:
            best_train_score = score
            best_params = params.copy()

        return score

    optuna_start = time.time()
    def optuna_callback(study, trial):
        # Log progress every 20 trials or at the end
        if trial.number % 20 == 0 or trial.number == 1499:
            elapsed = time.time() - optuna_start
            completed = trial.number + 1
            pct = (completed / 1500) * 100.0
            tps = completed / elapsed if elapsed > 0 else 0.0
            eta = (1500 - completed) / tps if tps > 0 else 0.0
            best_trial = study.best_trial
            best_val = best_trial.value if best_trial else -999.0
            best_ret = best_trial.user_attrs.get("net_return_pct", 0.0) if best_trial else 0.0
            best_pf = best_trial.user_attrs.get("profit_factor", 0.0) if best_trial else 0.0
            
            log(
                f"[PROGRESS] TF: {tf} | Trial: {completed}/1500 ({pct:.1f}%) | "
                f"Elapsed: {format_eta(elapsed)} | ETA: {format_eta(eta)} | "
                f"{tps:.1f} trials/sec | "
                f"Best Score: {best_val:.2f} | Best PF: {best_pf:.2f} | Best Ret: {best_ret:+.2f}%"
            )

    log(f"[{tf}] Running 1500 Optuna Trials on Train Set...")
    sampler = optuna.samplers.TPESampler(seed=42)
    study = optuna.create_study(direction="maximize", sampler=sampler)
    study.optimize(objective, n_trials=1500, n_jobs=1, callbacks=[optuna_callback])

    log(f"[{tf}] Optimization Complete. Best Train Score: {best_train_score:.2f}")

    if not best_params:
        log(f"[{tf}] No valid candidates found.")
        return None

    # 4. Evaluate Best on Full, Val, Holdout
    full_sum, _ = run_single_backtest(df_raw, best_params, tf)
    val_sum, _ = run_single_backtest(df_val, best_params, tf)
    ho_sum, ho_trades = run_single_backtest(df_holdout, best_params, tf)

    log(f"[{tf}] Best Params: {best_params}")
    log(f"[{tf}] Holdout: Ret {ho_sum.get('net_return_pct',0):+.2f}%, PF {ho_sum.get('profit_factor',0):.2f}, DD {ho_sum.get('max_drawdown_pct',0):.2f}%")

    is_robust = False
    if ho_sum.get('net_return_pct', 0) > 0 and ho_sum.get('profit_factor', 0) > 1.0 and ho_sum.get('total_trades', 0) >= 10:
        is_robust = run_robustness(df_raw, best_params, tf)
    else:
        log(f"[{tf}] Skipping Robustness - Holdout not promising.")

    fees_paid = sum([getattr(t, 'entry_fee', 0.0) + getattr(t, 'exit_fee', 0.0) for t in ho_trades]) if ho_trades else 0.0

    return {
        'tf': tf,
        'best_params': best_params,
        'full_ret': full_sum.get('net_return_pct', 0),
        'full_pf': full_sum.get('profit_factor', 0),
        'full_dd': full_sum.get('max_drawdown_pct', 0),
        'ho_ret': ho_sum.get('net_return_pct', 0),
        'ho_pf': ho_sum.get('profit_factor', 0),
        'ho_dd': ho_sum.get('max_drawdown_pct', 0),
        'ho_trades': ho_sum.get('total_trades', 0),
        'ho_fees': fees_paid,
        'is_robust': is_robust,
        'start_dt': start_dt,
        'end_dt': end_dt,
        'total_candles': total_len
    }

def main():
    mute_console_loggers()
    with open(LOG_FILE, "w") as f:
        f.write("=== MULTI-TIMEFRAME STRATEGY OPTIMIZATION (1.0x LEVERAGE) ===\n")
    
    loader = MarketDataLoader(os.path.join(PROJECT_ROOT, "data"))
    results = []
    
    for tf in TIMEFRAMES:
        res = optimize_timeframe(tf, loader)
        if res:
            results.append(res)
            # Save incremental results
            df_res = pd.DataFrame(results)
            df_res.to_csv(os.path.join(PROJECT_ROOT, "results", "multi_tf", "multi_tf_summary.csv"), index=False)

    log("\n==================================================")
    log("FINAL MULTI-TIMEFRAME SUMMARY")
    log("==================================================")
    
    df_res = pd.DataFrame(results)
    
    # Format and log the table
    log(f"{'TF':>5} | {'Full Ret':>10} | {'Full PF':>7} | {'Full DD':>8} | {'HO Ret':>10} | {'HO PF':>7} | {'HO DD':>8} | {'Trades':>6} | {'Fees':>8} | {'Robust?':>7}")
    log("-" * 95)
    for r in results:
        log(f"{r['tf']:>5} | {r['full_ret']:>9.2f}% | {r['full_pf']:>7.2f} | {r['full_dd']:>7.2f}% | {r['ho_ret']:>9.2f}% | {r['ho_pf']:>7.2f} | {r['ho_dd']:>7.2f}% | {r['ho_trades']:>6} | ${r['ho_fees']:>7.2f} | {'YES' if r['is_robust'] else 'NO':>7}")

    # Determine Best
    if len(results) > 0:
        # Rank by holdout return among robust candidates, fallback to highest holdout return
        robust_res = [r for r in results if r['is_robust']]
        best = max(robust_res, key=lambda x: x['ho_ret']) if robust_res else max(results, key=lambda x: x['ho_ret'])
        
        log("\nBEST OVERALL TIMEFRAME:")
        log(f"Timeframe: {best['tf']}")
        log(f"Best candidate parameters: {best['best_params']}")
        log(f"Best Holdout Return: {best['ho_ret']:+.2f}%")
        log(f"Best Holdout PF: {best['ho_pf']:.2f}")
        log(f"Max DD: {best['ho_dd']:.2f}%")
        log(f"Number of trades: {best['ho_trades']}")
        
        ls_conf = "Both" if (best['best_params'].get('long_enabled') and best['best_params'].get('short_enabled')) else \
                  "Long Only" if best['best_params'].get('long_enabled') else "Short Only"
        log(f"Long/Short/Both: {ls_conf}")
        log(f"Robust: {'YES' if best['is_robust'] else 'NO'}")

if __name__ == "__main__":
    main()
