"""
Advanced strategy test: Candle Body Quality Filter + Multi-Target Trailing Runner.
"""

import numpy as np
import pandas as pd
from config import AppConfig, StrategyConfig
from indicators import compute_all_indicators
from backtester import DeltaBacktester
from metrics import PerformanceMetrics


def run_deep_enhancement_test():
    eth_df = pd.read_csv("data/candles_ETHUSDT_1h.csv")
    eth_df["datetime"] = pd.to_datetime(eth_df["datetime"])
    
    btc_df = pd.read_csv("data/candles_BTCUSDT_1h.csv")
    btc_df["datetime"] = pd.to_datetime(btc_df["datetime"])

    # Let's test candle body quality threshold:
    # abs(close - open) / (high - low + 1e-6) >= 0.40 (ensures decisive momentum candle)
    for min_body_ratio in [0.0, 0.35, 0.45]:
        for rr in [1.3, 1.5, 1.8]:
            cfg = AppConfig()
            cfg.strategy.risk_reward_ratio = rr
            cfg.strategy.volume_mult = 1.0
            cfg.risk.risk_per_trade_pct = 0.015  # 1.5% balanced risk
            cfg.risk.max_position_allocation_pct = 0.45

            for name, df_raw in [("ETHUSDT", eth_df), ("BTCUSDT", btc_df)]:
                df_ind = compute_all_indicators(
                    df=df_raw,
                    ema_period=51,
                    rsi_period=14,
                    atr_period=14,
                    consolidation_candles=8,
                    consolidation_atr_mult=2.2,
                    swing_lookback=8,
                    trend_ema_period=200,
                )
                
                # Filter by body ratio if > 0
                if min_body_ratio > 0:
                    body_ratio = (df_ind["close"] - df_ind["open"]).abs() / (df_ind["high"] - df_ind["low"] + 1e-6)
                    df_ind["valid_body"] = body_ratio >= min_body_ratio
                else:
                    df_ind["valid_body"] = True

                backtester = DeltaBacktester(cfg)
                res = backtester.run(df_ind)
                metrics = PerformanceMetrics.calculate(res["trades"], res["equity_curve"], cfg.risk.initial_capital)
                
                print(f"[{name}] BodyRatio: {min_body_ratio} | RR: {rr} | Net: ${metrics['Net Profit ($)']:+,.2f} ({metrics['Net Profit (%)']:+.2f}%) | WR: {metrics['Win Rate (%)']:.1f}% | PF: {metrics['Profit Factor']:.2f} | MaxDD: {metrics['Max Drawdown (%)']:.2f}% | Trades: {metrics['Total Trades']}")

    print("=" * 80)


if __name__ == "__main__":
    run_deep_enhancement_test()
