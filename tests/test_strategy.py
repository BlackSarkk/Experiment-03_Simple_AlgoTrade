"""
Unit tests for BaselineStrategy and indicators using unittest.
"""

import unittest
import sys
import os
sys.path.insert(0, os.path.abspath("src"))

import pandas as pd
import numpy as np
from common.config import StrategyConfig
from strategy.indicators import compute_all_indicators
from strategy.baseline_strategy import BaselineStrategy


def create_synthetic_candles(n: int = 100) -> pd.DataFrame:
    timestamps = [1700000000 + i * 10800 for i in range(n)]
    datetimes = pd.to_datetime(timestamps, unit="s", utc=True)

    base_price = 3000.0 + np.sin(np.linspace(0, 10, n)) * 100.0

    df = pd.DataFrame({
        "timestamp": timestamps,
        "datetime": datetimes,
        "open": base_price - 2.0,
        "high": base_price + 10.0,
        "low": base_price - 10.0,
        "close": base_price,
        "volume": 1000.0 + np.random.rand(n) * 500.0
    })
    return df


class TestStrategy(unittest.TestCase):

    def test_indicator_computation(self):
        df = create_synthetic_candles(100)
        cfg = StrategyConfig()
        df_ind = compute_all_indicators(df, cfg)

        self.assertIn("ema_51", df_ind.columns)
        self.assertIn("rsi", df_ind.columns)
        self.assertIn("atr", df_ind.columns)
        self.assertIn("is_consolidating", df_ind.columns)
        self.assertIn("swing_high", df_ind.columns)
        self.assertIn("swing_low", df_ind.columns)
        self.assertIn("vol_sma_20", df_ind.columns)

        expected_swing_high = df["high"].iloc[2:10].max()
        self.assertTrue(np.isclose(df_ind["swing_high"].iloc[10], expected_swing_high))

    def test_strategy_signal_generation(self):
        df = create_synthetic_candles(120)
        cfg = StrategyConfig()
        df_ind = compute_all_indicators(df, cfg)

        strat = BaselineStrategy(cfg)
        signals = strat.generate_signals(df_ind)

        self.assertIsInstance(signals, list)
        for sig in signals:
            self.assertIn(sig.signal_type, ["LONG", "SHORT"])
            self.assertGreater(sig.sl_price, 0)
            self.assertGreater(sig.tp_price, 0)
            if sig.signal_type == "LONG":
                self.assertGreater(sig.tp_price, sig.close_price)
                self.assertLess(sig.sl_price, sig.close_price)
            else:
                self.assertLess(sig.tp_price, sig.close_price)
                self.assertGreater(sig.sl_price, sig.close_price)


if __name__ == "__main__":
    unittest.main()
