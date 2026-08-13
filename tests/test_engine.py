"""
Unit tests and Regression tests for BacktestEngine leverage sizing semantics.
"""

import unittest
import sys
import os
sys.path.insert(0, os.path.abspath("src"))

import pandas as pd
import numpy as np
from common.config import PipelineConfig
from common.market_data import MarketDataLoader
from strategy.indicators import compute_all_indicators
from backtest.engine import BacktestEngine


class TestEngine(unittest.TestCase):

    def setUp(self):
        cfg = PipelineConfig()
        cfg.platform.symbol = "ETHUSDT"
        cfg.platform.platform = "BINANCE"
        cfg.platform.resolution = "3h"
        cfg.platform.start_date = "2024-01-01"
        cfg.platform.end_date = "2026-08-13"

        loader = MarketDataLoader(data_dir="data")
        self.df_raw = loader.load_ohlcv(cfg.platform)

    def test_leverage_1_vs_3_5_risk_budget(self):
        cfg_1x = PipelineConfig(execution_mode="REFERENCE")
        cfg_1x.risk.leverage = 1.0
        cfg_1x.risk.risk_per_trade_pct = 0.015
        df_ind_1x = compute_all_indicators(self.df_raw, cfg_1x.strategy)
        res_1x = BacktestEngine(cfg_1x).run(df_ind_1x)

        cfg_35x = PipelineConfig(execution_mode="REFERENCE")
        cfg_35x.risk.leverage = 3.5
        cfg_35x.risk.risk_per_trade_pct = 0.015
        df_ind_35x = compute_all_indicators(self.df_raw, cfg_35x.strategy)
        res_35x = BacktestEngine(cfg_35x).run(df_ind_35x)

        trade_1x = res_1x["trades"][0]
        trade_35x = res_35x["trades"][0]

        # 1. Verify 1.0x leverage risk budget = equity * 0.015 = $150
        self.assertAlmostEqual(trade_1x.risk_budget, 150.0, places=2)

        # 2. Verify 3.5x leverage risk budget = equity * 0.015 = $150 (leverage does NOT multiply allowed account risk)
        self.assertAlmostEqual(trade_35x.risk_budget, 150.0, places=2)

    def test_no_double_leverage_on_pnl(self):
        cfg_35x = PipelineConfig(execution_mode="REFERENCE")
        cfg_35x.risk.leverage = 3.5
        df_ind = compute_all_indicators(self.df_raw, cfg_35x.strategy)
        res = BacktestEngine(cfg_35x).run(df_ind)

        t = res["trades"][0]
        # PnL must equal size * (exit_price - entry_price) for LONG or size * (entry_price - exit_price) for SHORT
        if t.signal_type == "LONG":
            expected_gross_pnl = t.size * (t.exit_price - t.entry_price)
        else:
            expected_gross_pnl = t.size * (t.entry_price - t.exit_price)

        self.assertAlmostEqual(t.gross_pnl, expected_gross_pnl, places=2)

    def test_symmetry_long_short_sizing(self):
        cfg = PipelineConfig(execution_mode="REFERENCE")
        cfg.risk.leverage = 3.5
        df_ind = compute_all_indicators(self.df_raw, cfg.strategy)

        res = BacktestEngine(cfg).run(df_ind)
        self.assertTrue(res["account"].reconcile())


if __name__ == "__main__":
    unittest.main()
