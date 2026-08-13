"""
Multi-Stage Partial Take-Profit + Trailing Runner Simulation.
"""

import numpy as np
import pandas as pd
from config import AppConfig, StrategyConfig
from indicators import compute_all_indicators


def run_trailing_runner_sim():
    eth_df = pd.read_csv("data/candles_ETHUSDT_1h.csv")
    eth_df["datetime"] = pd.to_datetime(eth_df["datetime"])

    df_ind = compute_all_indicators(
        df=eth_df,
        ema_period=51,
        rsi_period=14,
        atr_period=14,
        consolidation_candles=8,
        consolidation_atr_mult=2.2,
        swing_lookback=8,
        trend_ema_period=200,
    )

    # Let's inspect the potential of compounding vs fixed risk:
    print("Testing Compounding Equity + Dynamic Risk Allocation...")


if __name__ == "__main__":
    run_trailing_runner_sim()
