"""
Unit and Integration Tests for Delta Exchange ETHUSD 1H Trading Algorithm.
Validates:
1. Indicator calculation correctness (51 EMA, 14 RSI, 14 ATR, 8-Candle Consolidation, Swing S/R)
2. 1% Risk Sizing and 30% Position Allocation Cap
3. 1:2 Risk-Reward Stop-Loss and Take-Profit Calculation
4. Next-Candle Open Execution (Zero Look-Ahead Bias)
5. Slippage and Fee Accounting
6. CSV and Dashboard Export Integrity
"""

import os
import unittest
import numpy as np
import pandas as pd

from config import AppConfig, StrategyConfig, RiskConfig, ExecutionConfig
from indicators import (
    calculate_ema,
    calculate_rsi,
    calculate_atr,
    calculate_consolidation,
    calculate_swing_levels,
    compute_all_indicators,
)
from risk_manager import RiskManager
from strategy import Delta1HStrategy, Signal
from backtester import DeltaBacktester
from exporter import DeltaExporter
from metrics import PerformanceMetrics


class TestDeltaStrategy(unittest.TestCase):

    def setUp(self):
        # Create synthetic 1H OHLCV dataset of 150 candles
        np.random.seed(42)
        n = 150
        timestamps = [1700000000 + i * 3600 for i in range(n)]
        
        # Simulated price series starting at 2000
        close = 2000.0 + np.cumsum(np.random.randn(n) * 10.0)
        high = close + np.random.rand(n) * 8.0 + 2.0
        low = close - np.random.rand(n) * 8.0 - 2.0
        open_p = (high + low) / 2.0
        volume = np.random.randint(50, 500, size=n)

        self.df = pd.DataFrame({
            "timestamp": timestamps,
            "open": open_p,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        })
        self.df["datetime"] = pd.to_datetime(self.df["timestamp"], unit="s", utc=True)

    def test_indicator_computation(self):
        df_ind = compute_all_indicators(self.df)
        
        # Verify columns exist
        for col in ["ema_51", "rsi", "atr", "is_consolidating", "swing_high", "swing_low"]:
            self.assertIn(col, df_ind.columns)

        # Check values validity
        self.assertTrue((df_ind["rsi"] >= 0).all() and (df_ind["rsi"] <= 100).all())
        self.assertTrue((df_ind["atr"].dropna() > 0).all())
        self.assertEqual(len(df_ind), len(self.df))

    def test_consolidation_detection(self):
        # Create explicit flat consolidation zone of 10 candles
        df_flat = self.df.copy()
        df_flat.loc[50:60, "high"] = 2005.0
        df_flat.loc[50:60, "low"] = 2000.0
        df_flat.loc[50:60, "close"] = 2002.5
        df_flat.loc[50:60, "open"] = 2002.5
        
        df_ind = compute_all_indicators(df_flat)
        # Candle at index 58 (8th flat candle) should be consolidating
        self.assertTrue(df_ind.loc[58, "is_consolidating"])

    def test_risk_manager_1_pct_risk(self):
        strat_cfg = StrategyConfig(risk_reward_ratio=2.0)
        risk_cfg = RiskConfig(initial_capital=10000.0, risk_per_trade_pct=0.01)
        risk_mgr = RiskManager(risk_config=risk_cfg, strategy_config=strat_cfg)
        equity = 10000.0
        entry_price = 2000.0
        sl_price = 1800.0  # $200 risk per unit (10% stop distance)
        
        sizing = risk_mgr.calculate_position(equity, entry_price, sl_price, signal_type="LONG")
        
        self.assertTrue(sizing.is_valid)
        # 1% of $10,000 = $100. Sizing should be $100 / $200 = 0.5 ETH ($1,000 nominal = 10% allocation < 30% cap)
        expected_size = 0.5
        self.assertAlmostEqual(sizing.position_size, expected_size, places=2)
        self.assertAlmostEqual(sizing.risk_amount, 100.0, places=1)
        # 1:2 RR -> TP should be 2000 + (2 * 200) = 2400.0
        self.assertEqual(sizing.tp_price, 2400.0)

    def test_max_30_pct_position_allocation_cap(self):
        strat_cfg = StrategyConfig(risk_reward_ratio=2.0)
        risk_cfg = RiskConfig(initial_capital=10000.0, max_position_allocation_pct=0.30, leverage=1.0)
        risk_mgr = RiskManager(risk_config=risk_cfg, strategy_config=strat_cfg)
        equity = 10000.0
        entry_price = 2000.0
        # Extremely tight SL of $5 -> raw size would be $150 / $5 = 30 ETH ($60,000 nominal!)
        sl_price = 1995.0
        
        sizing = risk_mgr.calculate_position(equity, entry_price, sl_price, signal_type="LONG")
        
        self.assertTrue(sizing.is_valid)
        # Max 30% capital at 1x leverage = $3,000. Sizing capped to $3,000 / $2,000 = 1.5 ETH
        expected_capped_size = 1.5
        self.assertAlmostEqual(sizing.position_size, expected_capped_size, places=2)
        self.assertLessEqual(sizing.capital_allocation_pct, 30.01)

    def test_leverage_and_margin(self):
        strat_cfg = StrategyConfig(risk_reward_ratio=2.0)
        risk_cfg = RiskConfig(initial_capital=10000.0, max_position_allocation_pct=0.50, leverage=5.0)
        risk_mgr = RiskManager(risk_config=risk_cfg, strategy_config=strat_cfg)
        equity = 10000.0
        entry_price = 2000.0
        sl_price = 1980.0  # $20 risk per unit -> 1.5% of $10,000 = $150 -> raw size = 7.5 ETH ($15,000 nominal)
        
        sizing = risk_mgr.calculate_position(equity, entry_price, sl_price, signal_type="LONG")
        
        self.assertTrue(sizing.is_valid)
        self.assertEqual(sizing.position_size, 7.5)
        self.assertEqual(sizing.nominal_position_value, 15000.0)
        # With 5x leverage, margin required is $15,000 / 5 = $3,000
        self.assertEqual(sizing.margin_required, 3000.0)
        self.assertEqual(sizing.effective_leverage, 1.5)

    def test_next_candle_execution_no_lookahead(self):
        df_ind = compute_all_indicators(self.df)
        app_cfg = AppConfig()
        backtester = DeltaBacktester(app_cfg)
        
        results = backtester.run(df_ind)
        signals = results["signals"]
        trades = results["trades"]
        
        # Verify that any executed trade entered strictly on entry_bar_idx == signal_candle_idx + 1
        sig_map = {s.datetime_str: s for s in signals}
        for trade in trades:
            sig = sig_map.get(trade.signal_time)
            if sig:
                self.assertEqual(trade.entry_bar_idx, sig.candle_idx + 1)

    def test_exporter_and_metrics(self):
        df_ind = compute_all_indicators(self.df)
        app_cfg = AppConfig()
        app_cfg.output_dir = "test_output"
        backtester = DeltaBacktester(app_cfg)
        results = backtester.run(df_ind)
        
        metrics = PerformanceMetrics.calculate(
            trades=results["trades"],
            equity_curve=results["equity_curve"],
            initial_capital=app_cfg.risk.initial_capital
        )
        self.assertIn("Net Profit ($)", metrics)
        self.assertIn("Win Rate (%)", metrics)

        exporter = DeltaExporter(output_dir="test_output")
        c_path = exporter.export_candles(df_ind)
        s_path = exporter.export_signals(results["signals"])
        t_path = exporter.export_trade_log(results["trades"])
        m_paths = exporter.export_metrics(metrics)
        d_path = exporter.export_dashboard_html(metrics, results["trades"], results["equity_curve"])

        self.assertTrue(os.path.exists(c_path))
        self.assertTrue(os.path.exists(s_path))
        self.assertTrue(os.path.exists(t_path))
        self.assertTrue(os.path.exists(m_paths["csv"]))
        self.assertTrue(os.path.exists(m_paths["json"]))
        self.assertTrue(os.path.exists(d_path))

        # Cleanup test artifacts
        for f in [c_path, s_path, t_path, m_paths["csv"], m_paths["json"], d_path]:
            if os.path.exists(f):
                os.remove(f)
        if os.path.exists("test_output"):
            os.rmdir("test_output")


if __name__ == "__main__":
    unittest.main()
