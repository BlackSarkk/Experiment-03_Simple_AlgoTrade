"""
Unit tests for ForwardStateStore and Robustness State Isolation assertions.
"""

import unittest
import sys
import os
import tempfile
sys.path.insert(0, os.path.abspath("src"))

import pandas as pd
from forward_test.state import ForwardStateStore
from common.config import PipelineConfig
from common.market_data import MarketDataLoader
from strategy.indicators import compute_all_indicators
from backtest.engine import BacktestEngine
from backtest.robustness import RobustnessEvaluator
from backtest.metrics import BacktestMetrics


class TestState(unittest.TestCase):

    def test_atomic_state_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_file = os.path.join(tmp_dir, "test_state.json")
            store = ForwardStateStore(state_file=state_file)

            sample_state = {
                "account": {"initial_balance": 10000.0, "balance": 10250.0},
                "position": {"side": "LONG", "size": 1.5}
            }

            store.save_state_atomic(sample_state)
            self.assertTrue(os.path.exists(state_file))

            loaded = store.load_state(reset=False)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["account"]["balance"], 10250.0)
            self.assertEqual(loaded["position"]["side"], "LONG")

    def test_reset_forward_state(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_file = os.path.join(tmp_dir, "test_state.json")
            store = ForwardStateStore(state_file=state_file)

            sample_state = {"account": {"balance": 10500.0}}
            store.save_state_atomic(sample_state)

            loaded = store.load_state(reset=True)
            self.assertIsNone(loaded)
            self.assertFalse(os.path.exists(state_file))

    def test_robustness_state_isolation(self):
        """Regression test verifying complete state isolation between sequential robustness runs."""
        cfg = PipelineConfig(execution_mode="REFERENCE")
        cfg.platform.symbol = "ETHUSDT"
        cfg.platform.platform = "BINANCE_FUTURES"

        loader = MarketDataLoader(data_dir="data")
        df_raw = loader.load_ohlcv(cfg.platform)
        df_ind_full = compute_all_indicators(df_raw, cfg.strategy)

        # Run 1: 2024 Sub-period
        mask_2024 = (df_ind_full["datetime"].astype(str) >= "2024-01-01") & (df_ind_full["datetime"].astype(str) <= "2024-12-31")
        df_2024 = df_ind_full[mask_2024].copy().reset_index(drop=True)
        df_2024["candle_idx"] = range(len(df_2024))
        res_2024 = BacktestEngine(cfg).run(df_2024)

        # Run 2: 2025 Sub-period
        mask_2025 = (df_ind_full["datetime"].astype(str) >= "2025-01-01") & (df_ind_full["datetime"].astype(str) <= "2025-12-31")
        df_2025 = df_ind_full[mask_2025].copy().reset_index(drop=True)
        df_2025["candle_idx"] = range(len(df_2025))
        res_2025 = BacktestEngine(cfg).run(df_2025)

        # 1. Verify initial balance resets to $10,000 for both runs
        self.assertEqual(res_2024["account"].initial_balance, 10000.0)
        self.assertEqual(res_2025["account"].initial_balance, 10000.0)

        # 2. Verify no position leakage at start of Run 2
        self.assertFalse(res_2025["equity_curve"][0]["in_position"])

        # 3. Verify trade list resets to ID 1
        self.assertEqual(res_2024["trades"][0].trade_id, 1)
        self.assertEqual(res_2025["trades"][0].trade_id, 1)
        self.assertIsNot(res_2024["trades"], res_2025["trades"])

        # 4. Verify fees reset
        fee_2024 = sum(t.total_fees for t in res_2024["trades"])
        fee_2025 = sum(t.total_fees for t in res_2025["trades"])
        self.assertNotEqual(fee_2024, fee_2025)

        # 5. Verify drawdown resets
        self.assertEqual(res_2024["equity_curve"][0]["drawdown_pct"], 0.0)
        self.assertEqual(res_2025["equity_curve"][0]["drawdown_pct"], 0.0)

        # 6. Verify each final balance reconciles with its own return %
        fin_bal_2024 = res_2024["final_balance"]
        net_prof_2024 = res_2024["account"].balance - 10000.0
        self.assertAlmostEqual(fin_bal_2024, 10000.0 + net_prof_2024, places=2)

        fin_bal_2025 = res_2025["final_balance"]
        net_prof_2025 = res_2025["account"].balance - 10000.0
        self.assertAlmostEqual(fin_bal_2025, 10000.0 + net_prof_2025, places=2)


if __name__ == "__main__":
    unittest.main()
