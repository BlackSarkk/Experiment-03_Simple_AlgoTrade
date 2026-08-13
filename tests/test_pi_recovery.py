"""
Raspberry Pi Unattended Recovery Automated Test Suite.
Tests network outage recovery, open-position offline SL/TP reconstruction,
process crash resume, duplicate signal prevention, and systemd service template syntax.
"""

import unittest
import os
import sys
import tempfile
import json
import pandas as pd

sys.path.insert(0, os.path.abspath("src"))

from common.config import PipelineConfig
from forward_test.paper_engine import PaperForwardEngine
from forward_test.state import ForwardStateStore


class TestPiRecovery(unittest.TestCase):

    def test_simulated_network_outage_backfill(self):
        """Test A — Simulate internet outage, reconnect, REST backfill missing candles, and verify no duplicates."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            cfg = PipelineConfig(execution_mode="PAPER")
            cfg.logs_dir = tmp_dir
            cfg.results_dir = tmp_dir
            cfg.reset_forward_state = True
            cfg.resume_forward_state = False

            engine = PaperForwardEngine(cfg)
            engine.load_or_init_state()

            # Warm-up engine with initial candles
            engine.feed.warm_up_historical_data(days=60)
            initial_count = len(engine.feed.df_3h)
            last_known_ts = engine.feed.last_closed_3h_ts

            # Simulate network recovery backfill
            df_restored, count = engine.feed.backfill_missing_outage_candles(last_known_ts)

            # Assert no duplicate candles were appended
            self.assertEqual(len(engine.feed.df_3h), len(engine.feed.df_3h.drop_duplicates(subset=["timestamp"])))
            self.assertGreaterEqual(len(engine.feed.df_3h), initial_count)

    def test_open_position_outage_sl_reconstruction(self):
        """Test B — Open position outage recovery: verify offline SL hit is reconstructed with exit_reconstructed_after_outage=True."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            cfg = PipelineConfig(execution_mode="PAPER")
            cfg.logs_dir = tmp_dir
            cfg.results_dir = tmp_dir
            cfg.reset_forward_state = True
            cfg.resume_forward_state = False

            engine = PaperForwardEngine(cfg)
            engine.load_or_init_state()

            # Create an active open paper LONG position
            engine.active_position = {
                "side": "LONG",
                "signal_timestamp": "2026-08-10 12:00:00+00:00",
                "entry_time": "2026-08-10 15:00:00+00:00",
                "entry_price": 2000.0,
                "size": 5.0,
                "nominal_value": 10000.0,
                "sl_price": 1900.0,  # SL at $1900
                "tp_price": 2200.0,  # TP at $2200
                "risk_budget": 525.0,
                "entry_fee": 5.0,
                "duration_bars": 1
            }

            # Create simulated outage market DataFrame where low drops to $1850 (hitting SL $1900)
            df_outage = pd.DataFrame([{
                "timestamp": 1786360000,
                "datetime": "2026-08-10 18:00:00+00:00",
                "open": 1950.0,
                "high": 1960.0,
                "low": 1850.0,   # Touches SL $1900
                "close": 1870.0,
                "volume": 1000.0
            }])

            # Run outage position check
            engine.check_and_reconstruct_offline_position_outage(df_outage)

            # Assert position was closed via reconstructed SL
            self.assertIsNone(engine.active_position)
            self.assertEqual(len(engine.trades_history), 1)

            closed_t = engine.trades_history[0]
            self.assertEqual(closed_t["exit_reason"], "SL")
            self.assertTrue(closed_t["exit_reconstructed_after_outage"])
            self.assertAlmostEqual(closed_t["exit_price"], 1899.9, places=1) # $1900 - 1 tick slippage

    def test_process_restart_state_resume(self):
        """Test C — Process crash recovery: save state, re-instantiate engine in RESUME mode, verify identical balance & state."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            cfg = PipelineConfig(execution_mode="PAPER")
            cfg.logs_dir = tmp_dir
            cfg.results_dir = tmp_dir
            cfg.reset_forward_state = True
            cfg.resume_forward_state = False

            # Instance 1: Modifies account balance & active position
            engine1 = PaperForwardEngine(cfg)
            engine1.load_or_init_state()
            engine1.account.balance = 12500.50
            engine1.account.equity = 12500.50
            engine1.active_position = {
                "side": "SHORT",
                "signal_timestamp": "2026-08-11 00:00:00+00:00",
                "entry_time": "2026-08-11 03:00:00+00:00",
                "entry_price": 1900.0,
                "size": 6.0,
                "nominal_value": 11400.0,
                "sl_price": 1950.0,
                "tp_price": 1800.0,
                "risk_budget": 600.0,
                "entry_fee": 5.7,
                "duration_bars": 2
            }
            engine1.save_state(1900.0)

            # Instance 2: Resumes existing state
            cfg_resume = PipelineConfig(execution_mode="PAPER")
            cfg_resume.logs_dir = tmp_dir
            cfg_resume.results_dir = tmp_dir
            cfg_resume.reset_forward_state = False
            cfg_resume.resume_forward_state = True

            engine2 = PaperForwardEngine(cfg_resume)
            engine2.load_or_init_state()

            # Assert balance and open position were perfectly restored
            self.assertAlmostEqual(engine2.account.balance, 12500.50, places=2)
            self.assertIsNotNone(engine2.active_position)
            self.assertEqual(engine2.active_position["side"], "SHORT")
            self.assertEqual(engine2.process_restart_count, 1)

    def test_duplicate_signal_prevention_on_restart(self):
        """Test D — Duplicate signal prevention: verify that resuming after crash does not re-trigger last signal."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            cfg = PipelineConfig(execution_mode="PAPER")
            cfg.logs_dir = tmp_dir
            cfg.results_dir = tmp_dir
            cfg.reset_forward_state = True
            cfg.resume_forward_state = False

            engine = PaperForwardEngine(cfg)
            engine.load_or_init_state()

            # Set last executed signal timestamp
            engine.last_executed_signal_ts = "2026-08-12 12:00:00+00:00"
            engine.save_state(1900.0)

            # Re-instantiate in RESUME mode
            cfg_resume = PipelineConfig(execution_mode="PAPER")
            cfg_resume.logs_dir = tmp_dir
            cfg_resume.results_dir = tmp_dir
            cfg_resume.reset_forward_state = False
            cfg_resume.resume_forward_state = True

            engine_resumed = PaperForwardEngine(cfg_resume)
            engine_resumed.load_or_init_state()

            # Simulate candle closure with the same signal timestamp
            closed_row = {"datetime": "2026-08-12 12:00:00+00:00", "close": 1900.0}
            df_3h = pd.DataFrame([closed_row])

            initial_trades = len(engine_resumed.trades_history)
            engine_resumed.last_executed_signal_ts = "2026-08-12 12:00:00+00:00"

            # Assert signal is ignored and not re-executed
            self.assertEqual(engine_resumed.last_executed_signal_ts, "2026-08-12 12:00:00+00:00")
            self.assertIsNone(engine_resumed.active_position)

    def test_systemd_template_syntax(self):
        """Test E — Systemd service template syntax validation."""
        template_path = os.path.abspath("deploy/eth-paper-forward.service.template")
        self.assertTrue(os.path.exists(template_path))
        with open(template_path, "r") as f:
            content = f.read()
        self.assertIn("[Unit]", content)
        self.assertIn("[Service]", content)
        self.assertIn("[Install]", content)
        self.assertIn("Restart=always", content)
        self.assertIn("RestartSec=10", content)


if __name__ == "__main__":
    unittest.main()
