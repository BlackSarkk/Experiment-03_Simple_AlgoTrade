import os
import sys
import time
import json
import math
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "src"))

from common.config import PipelineConfig, StrategyConfig
from strategy.indicators import compute_all_indicators
from backtest.engine import BacktestEngine
from common.utils import mute_console_loggers

# Ensure logs directory exists
os.makedirs(os.path.join(PROJECT_ROOT, "logs"), exist_ok=True)
os.makedirs(os.path.join(PROJECT_ROOT, "results", "robustness"), exist_ok=True)

LOG_FILE = os.path.join(PROJECT_ROOT, "logs", "candidate5_robustness.log")

def log(msg):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] {msg}"
    print(formatted, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(formatted + "\n")
        f.flush()

# Candidate #5 Base Parameters
CANDIDATE_5_PARAMS = {
    'ema_period': 51,
    'rsi_period': 21,
    'rsi_overbought': 65.0,
    'rsi_oversold': 45.0,
    'atr_period': 21,
    'consolidation_candles': 8,
    'consolidation_atr_mult': 2.8,
    'swing_lookback': 12,
    'volume_sma_period': 20,
    'use_volume_filter': True,
    'volume_mult': 1.6,
    'risk_reward_ratio': 3.0,
    'use_ema_slope_filter': False,
    'use_trend_filter': False,
    'long_enabled': True,
    'short_enabled': False
}

def load_data():
    csv_path = os.path.join(PROJECT_ROOT, "data", "candles_futures_binance_futures_ETHUSDT_15m.csv")
    if not os.path.exists(csv_path):
        log(f"ERROR: Dataset file not found at {csv_path}")
        sys.exit(1)
    df = pd.read_csv(csv_path)
    return df

def run_single_backtest(df_raw, params_dict, taker_fee=0.0005, slippage_ticks=1.0):
    cfg = PipelineConfig()
    cfg.platform.symbol = "ETHUSDT"
    cfg.platform.resolution = "15m"
    cfg.risk.initial_capital = 10000.0
    cfg.risk.leverage = 3.5
    cfg.risk.risk_per_trade_pct = 0.015

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

def format_eta(seconds):
    if seconds < 0 or math.isinf(seconds) or math.isnan(seconds):
        return "00:00:00"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"

def main():
    mute_console_loggers()
    with open(LOG_FILE, "w") as f:
        f.write("=== CANDIDATE #5 ROBUSTNESS TEST LOG ===\n")
    
    log("Starting Candidate #5 Robustness Evaluation Pipeline...")
    df_all = load_data()
    total_candles = len(df_all)
    log(f"Dataset loaded: {total_candles} candles")

    # Build Test Configurations List
    test_queue = []

    # 1. Parameter Sensitivity (±5% and ±10% for numerical params)
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

    # Base run
    test_queue.append({
        'category': 'BASELINE',
        'name': 'Candidate #5 Baseline (Full Period)',
        'params': CANDIDATE_5_PARAMS.copy(),
        'df': df_all,
        'taker_fee': 0.0005,
        'slippage': 1.0,
        'info': 'Baseline full run'
    })

    # Sensitivity variations
    for param_name, (ptype, min_v, max_v) in num_params.items():
        base_val = CANDIDATE_5_PARAMS[param_name]
        for mult, pct_str in [(0.90, '-10%'), (0.95, '-5%'), (1.05, '+5%'), (1.10, '+10%')]:
            if ptype == int:
                new_val = max(min_v, min(max_v, int(round(base_val * mult))))
            else:
                new_val = max(min_v, min(max_v, round(base_val * mult, 2)))
            
            if new_val == base_val:
                continue
            
            p_copy = CANDIDATE_5_PARAMS.copy()
            p_copy[param_name] = new_val
            test_queue.append({
                'category': 'PARAM_SENSITIVITY',
                'name': f"{param_name} {pct_str} ({new_val})",
                'params': p_copy,
                'df': df_all,
                'taker_fee': 0.0005,
                'slippage': 1.0,
                'info': f"Param {param_name} changed to {new_val}"
            })

    # 2. Rolling Time Windows (4 non-overlapping equal sub-periods)
    n_splits = 4
    chunk_size = total_candles // n_splits
    for i in range(n_splits):
        sub_df = df_all.iloc[i*chunk_size : (i+1)*chunk_size if i < n_splits-1 else total_candles].reset_index(drop=True)
        start_t = sub_df['datetime'].iloc[0] if 'datetime' in sub_df.columns else str(i)
        end_t = sub_df['datetime'].iloc[-1] if 'datetime' in sub_df.columns else str(i+1)
        test_queue.append({
            'category': 'ROLLING_WINDOW',
            'name': f"Window Quarter {i+1} ({start_t[:10]} to {end_t[:10]})",
            'params': CANDIDATE_5_PARAMS.copy(),
            'df': sub_df,
            'taker_fee': 0.0005,
            'slippage': 1.0,
            'info': f"Sub-window {i+1}/4 ({len(sub_df)} candles)"
        })

    # 3. LONG vs SHORT Isolation
    for mode, long_e, short_e in [('Long Only', True, False), ('Short Only', False, True), ('Both Sides', True, True)]:
        p_copy = CANDIDATE_5_PARAMS.copy()
        p_copy['long_enabled'] = long_e
        p_copy['short_enabled'] = short_e
        test_queue.append({
            'category': 'SIDE_ISOLATION',
            'name': f"Side: {mode}",
            'params': p_copy,
            'df': df_all,
            'taker_fee': 0.0005,
            'slippage': 1.0,
            'info': f"Long={long_e}, Short={short_e}"
        })

    # 4. Fee & Friction Sensitivity
    friction_scenarios = [
        ('Zero Friction', 0.0, 0.0),
        ('Low Friction (0.03% / 1 tick)', 0.0003, 1.0),
        ('Baseline Friction (0.05% / 1 tick)', 0.0005, 1.0),
        ('High Friction (0.075% / 2 ticks)', 0.00075, 2.0),
        ('Extreme Friction (0.10% / 3 ticks)', 0.0010, 3.0)
    ]
    for fname, fee, slip in friction_scenarios:
        test_queue.append({
            'category': 'FEE_FRICTION',
            'name': f"Friction: {fname}",
            'params': CANDIDATE_5_PARAMS.copy(),
            'df': df_all,
            'taker_fee': fee,
            'slippage': slip,
            'info': f"Fee: {fee*100:.3f}%, Slip: {slip} ticks"
        })

    total_tests = len(test_queue)
    log(f"Total test suite created: {total_tests} test cases.")

    results_list = []
    start_time = time.time()

    for idx, test_case in enumerate(test_queue, 1):
        category = test_case['category']
        t_name = test_case['name']
        p = test_case['params']
        sub_df = test_case['df']
        fee = test_case['taker_fee']
        slip = test_case['slippage']
        info = test_case['info']

        # Execute backtest
        summary, trades = run_single_backtest(sub_df, p, taker_fee=fee, slippage_ticks=slip)

        # Process trade distribution stats
        if trades and len(trades) > 0:
            pnls = [t.net_pnl for t in trades]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]
            avg_win = float(np.mean(wins)) if wins else 0.0
            avg_loss = float(np.mean(losses)) if losses else 0.0
            max_win = float(np.max(pnls)) if pnls else 0.0
            max_loss = float(np.min(pnls)) if pnls else 0.0
            expectancy = float(np.mean(pnls)) if pnls else 0.0
            fees_paid = sum([getattr(t, 'entry_fee', 0.0) + getattr(t, 'exit_fee', 0.0) for t in trades])
            gp = sum([t.net_pnl for t in trades if t.net_pnl > 0])
            gl = abs(sum([t.net_pnl for t in trades if t.net_pnl < 0]))
            pf = (gp / gl) if gl > 0 else 0.0
        else:
            avg_win = avg_loss = max_win = max_loss = expectancy = fees_paid = gp = gl = pf = 0.0

        init_capital = 10000.0
        fin_bal = summary.get('final_balance', init_capital)
        net_ret_pct = ((fin_bal - init_capital) / init_capital) * 100.0

        res_item = {
            'test_index': idx,
            'category': category,
            'name': t_name,
            'info': info,
            'net_return_pct': net_ret_pct,
            'final_balance': fin_bal,
            'gross_profit': gp,
            'gross_loss': gl,
            'profit_factor': pf,
            'win_rate_pct': (len(wins)/len(trades)*100.0) if trades else 0.0,
            'total_trades': len(trades),
            'max_drawdown_pct': summary.get('max_drawdown_pct', 0.0),
            'fees_paid': fees_paid,
            'expectancy': expectancy,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'max_win': max_win,
            'max_loss': max_loss
        }
        results_list.append(res_item)

        elapsed = time.time() - start_time
        pct_comp = (idx / total_tests) * 100.0
        avg_per_test = elapsed / idx
        rem_tests = total_tests - idx
        eta_seconds = rem_tests * avg_per_test
        eta_str = format_eta(eta_seconds)

        log(
            f"[PROGRESS] Completed {idx}/{total_tests} ({pct_comp:.1f}%) | "
            f"Elapsed: {format_eta(elapsed)} | ETA: {eta_str} | "
            f"Current Test: {category} -> {t_name} | "
            f"Return: {net_ret_pct:+.2f}% | PF: {pf:.2f}"
        )

    # Save final results CSV & JSON
    df_results = pd.DataFrame(results_list)
    csv_out = os.path.join(PROJECT_ROOT, "results", "robustness", "candidate5_robustness_results.csv")
    json_out = os.path.join(PROJECT_ROOT, "results", "robustness", "candidate5_robustness_results.json")
    df_results.to_csv(csv_out, index=False)
    with open(json_out, "w") as f:
        json.dump(results_list, f, indent=2)

    total_elapsed = time.time() - start_time
    log(f"Candidate #5 Robustness Evaluation COMPLETE in {format_eta(total_elapsed)}.")
    log(f"Results saved to {csv_out}")

if __name__ == "__main__":
    main()
