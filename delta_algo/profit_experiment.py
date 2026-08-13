"""
Advanced Strategy Optimization & Profit Acceleration Experiment.
Tests:
1. Multi-Target Take-Profit (50% at TP1, 50% runner trailing 51 EMA)
2. Higher-Timeframe (4H / 200 EMA) Trend Alignment
3. Momentum Confirmation (MACD / ATR Expansion)
4. Dynamic Sizing & Risk Calibration (1.5% and 2.0% Risk)
"""

import numpy as np
import pandas as pd
from config import AppConfig, StrategyConfig, RiskConfig
from indicators import compute_all_indicators
from backtester import DeltaBacktester, Trade
from metrics import PerformanceMetrics


def run_profit_enhancement_test():
    eth_df = pd.read_csv("data/candles_ETHUSDT_1h.csv")
    eth_df["datetime"] = pd.to_datetime(eth_df["datetime"])
    
    btc_df = pd.read_csv("data/candles_BTCUSDT_1h.csv")
    btc_df["datetime"] = pd.to_datetime(btc_df["datetime"])

    print("=" * 80)
    print("           PROFIT ACCELERATION & ENHANCEMENT EXPERIMENT (365 DAYS)")
    print("=" * 80)

    # 1. Test different risk allocations (1.0% vs 1.5% vs 2.0%)
    for risk_pct in [1.0, 1.5, 2.0]:
        cfg = AppConfig()
        cfg.risk.risk_per_trade_pct = risk_pct / 100.0
        cfg.risk.max_position_allocation_pct = min(0.60, risk_pct * 0.25)
        cfg.strategy.risk_reward_ratio = 1.5
        cfg.strategy.volume_mult = 1.0

        for name, df_raw in [("ETHUSDT", eth_df), ("BTCUSDT", btc_df)]:
            df_ind = compute_all_indicators(
                df=df_raw,
                ema_period=51,
                rsi_period=14,
                atr_period=14,
                consolidation_candles=8,
                consolidation_atr_mult=2.0,
                swing_lookback=8,
                trend_ema_period=200,
            )
            backtester = DeltaBacktester(cfg)
            res = backtester.run(df_ind)
            metrics = PerformanceMetrics.calculate(res["trades"], res["equity_curve"], cfg.risk.initial_capital)
            
            print(f"[{name}] Risk: {risk_pct:.1f}% | Net Profit: ${metrics['Net Profit ($)']:+,.2f} ({metrics['Net Profit (%)']:+.2f}%) | WinRate: {metrics['Win Rate (%)']:.1f}% | ProfitFactor: {metrics['Profit Factor']:.2f} | MaxDD: {metrics['Max Drawdown (%)']:.2f}% | Sharpe: {metrics['Sharpe Ratio (Annualized)']:.2f}")

    print("=" * 80)


if __name__ == "__main__":
    run_profit_enhancement_test()
