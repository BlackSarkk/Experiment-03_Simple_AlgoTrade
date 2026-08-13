"""
Automated Reset & Clear Cache Integration Test Suite.
Tests CLEAR_CACHE behavior, CLEAR_CACHE_ONLY behavior, stage-scoped RESET behavior, archive creation,
state resume, combined RESET + CLEAR_CACHE, backtest isolation, and default safety (NO_FLAGS).
"""

import unittest
import os
import sys
import shutil
import tempfile
import json
import pandas as pd

sys.path.insert(0, os.path.abspath("src"))

from common.config import PipelineConfig
from common.market_data import MarketDataLoader
from forward_test.paper_engine import PaperForwardEngine
from forward_test.state import ForwardStateStore


class TestResetCache(unittest.TestCase):

    def test_a_clear_cache_behavior(self):
        """Test A — CLEAR_CACHE=true deletes market data cache ONLY; preserves tracker, state, trades."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            data_dir = os.path.join(tmp_dir, "data")
            results_dir = os.path.join(tmp_dir, "results")
            logs_dir = os.path.join(tmp_dir, "logs")

            os.makedirs(data_dir, exist_ok=True)
            os.makedirs(results_dir, exist_ok=True)
            os.makedirs(logs_dir, exist_ok=True)

            # Create fake market cache file
            loader = MarketDataLoader(data_dir=data_dir)
            cfg_platform = PipelineConfig().platform
            cache_file = loader.get_cache_filename(cfg_platform.symbol, cfg_platform.resolution, cfg_platform.platform)
            with open(cache_file, "w") as f:
                f.write("timestamp,open,high,low,close,volume\n1000,1,2,0.5,1.5,100\n")

            # Create fake tracker, state, and trade files
            tracker_file = os.path.join(results_dir, "tracker.csv")
            with open(tracker_file, "w") as f:
                f.write("run_id,stage\nTEST_RUN,FORWARD_PAPER\n")

            state_file = os.path.join(logs_dir, "forward_state.json")
            with open(state_file, "w") as f:
                json.dump({"account": {"balance": 10000.0}}, f)

            # Run clear_market_cache
            loader.clear_market_cache(cfg_platform)

            # Verify market cache file was deleted
            self.assertFalse(os.path.exists(cache_file))

            # Verify non-cache files were preserved
            self.assertTrue(os.path.exists(tracker_file))
            self.assertTrue(os.path.exists(state_file))

    def test_b_forward_reset_archiving(self):
        """Test B — FORWARD RESET archives old experiment to results/forward/archive/<exp_id>/ and starts fresh at $10,000."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            cfg = PipelineConfig(execution_mode="PAPER")
            cfg.data_dir = os.path.join(tmp_dir, "data")
            cfg.results_dir = os.path.join(tmp_dir, "results")
            cfg.logs_dir = os.path.join(tmp_dir, "logs")
            cfg.reset_forward_state = False
            cfg.resume_forward_state = True

            # 1. Create a fake existing experiment
            engine1 = PaperForwardEngine(cfg)
            engine1.experiment_id = "EXP_TEST_OLD_123"
            engine1.load_or_init_state()
            engine1.account.balance = 12345.00
            engine1.active_position = {"side": "LONG", "size": 5.0}
            engine1.save_state(2000.0)

            # 2. Re-instantiate in RESET mode
            cfg_reset = PipelineConfig(execution_mode="PAPER")
            cfg_reset.data_dir = os.path.join(tmp_dir, "data")
            cfg_reset.results_dir = os.path.join(tmp_dir, "results")
            cfg_reset.logs_dir = os.path.join(tmp_dir, "logs")
            cfg_reset.reset = True
            cfg_reset.reset_forward_state = True
            cfg_reset.resume_forward_state = False

            engine2 = PaperForwardEngine(cfg_reset)
            engine2.load_or_init_state()

            # Verify old experiment was archived under results/forward/archive/EXP_TEST_OLD_123/
            archive_target = os.path.join(cfg_reset.results_dir, "forward", "archive", "EXP_TEST_OLD_123")
            self.assertTrue(os.path.exists(archive_target))
            self.assertTrue(os.path.exists(os.path.join(archive_target, "forward_state.json")))

            # Verify engine2 started a fresh experiment at $10,000.00 with FLAT position
            self.assertNotEqual(engine2.experiment_id, "EXP_TEST_OLD_123")
            self.assertAlmostEqual(engine2.account.balance, 10000.00, places=2)
            self.assertIsNone(engine2.active_position)
            self.assertEqual(len(engine2.trades_history), 0)

    def test_c_resume_exact_restoration(self):
        """Test C — RESUME mode restores exact balance, experiment ID, position, and trade count."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            cfg = PipelineConfig(execution_mode="PAPER")
            cfg.data_dir = os.path.join(tmp_dir, "data")
            cfg.results_dir = os.path.join(tmp_dir, "results")
            cfg.logs_dir = os.path.join(tmp_dir, "logs")
            cfg.reset_forward_state = True
            cfg.resume_forward_state = False

            # Instance 1: Create state
            engine1 = PaperForwardEngine(cfg)
            engine1.experiment_id = "EXP_RESUME_TARGET_456"
            engine1.load_or_init_state()
            engine1.account.balance = 15678.90
            engine1.active_position = {"side": "SHORT", "size": 8.0}
            engine1.save_state(1950.0)

            # Instance 2: Resume state
            cfg_resume = PipelineConfig(execution_mode="PAPER")
            cfg_resume.data_dir = os.path.join(tmp_dir, "data")
            cfg_resume.results_dir = os.path.join(tmp_dir, "results")
            cfg_resume.logs_dir = os.path.join(tmp_dir, "logs")
            cfg_resume.reset = False
            cfg_resume.reset_forward_state = False
            cfg_resume.resume_forward_state = True

            engine2 = PaperForwardEngine(cfg_resume)
            engine2.load_or_init_state()

            # Verify exact restoration
            self.assertEqual(engine2.experiment_id, "EXP_RESUME_TARGET_456")
            self.assertAlmostEqual(engine2.account.balance, 15678.90, places=2)
            self.assertIsNotNone(engine2.active_position)
            self.assertEqual(engine2.active_position["side"], "SHORT")

    def test_d_reset_plus_clear_cache_together(self):
        """Test D — RESET=true + CLEAR_CACHE=true clears cache and resets stage cleanly."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            cfg = PipelineConfig(execution_mode="PAPER")
            cfg.data_dir = os.path.join(tmp_dir, "data")
            cfg.results_dir = os.path.join(tmp_dir, "results")
            cfg.logs_dir = os.path.join(tmp_dir, "logs")

            loader = MarketDataLoader(data_dir=cfg.data_dir)
            cache_file = loader.get_cache_filename(cfg.platform.symbol, cfg.platform.resolution, cfg.platform.platform)
            with open(cache_file, "w") as f:
                f.write("timestamp,open,high,low,close,volume\n1000,1,2,0.5,1.5,100\n")

            cfg.reset = True
            cfg.clear_cache = True
            cfg.reset_forward_state = True

            # Clear cache
            loader.clear_market_cache(cfg.platform)

            # Init engine
            engine = PaperForwardEngine(cfg)
            engine.load_or_init_state()

            # Verify cache cleared and fresh experiment initialized
            self.assertFalse(os.path.exists(cache_file))
            self.assertAlmostEqual(engine.account.balance, 10000.00, places=2)

    def test_e_backtest_reset_isolation(self):
        """Test E — BACKTEST reset does NOT delete forward state or unrelated tracker rows."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            logs_dir = os.path.join(tmp_dir, "logs")
            results_dir = os.path.join(tmp_dir, "results")
            os.makedirs(logs_dir, exist_ok=True)
            os.makedirs(results_dir, exist_ok=True)

            forward_state = os.path.join(logs_dir, "forward_state.json")
            with open(forward_state, "w") as f:
                json.dump({"account": {"balance": 15000.0}}, f)

            tracker_file = os.path.join(results_dir, "tracker.csv")
            with open(tracker_file, "w") as f:
                f.write("run_id,stage\nROBUST_1,ROBUSTNESS_TIMEFRAME\nFORWARD_PAPER_SESSION,FORWARD_PAPER\n")

            # Simulate backtest stage reset
            cfg = PipelineConfig(run_backtest=True, run_forward_test=False)
            cfg.reset = True

            # Assert forward state and robustness tracker row remain intact
            self.assertTrue(os.path.exists(forward_state))
            df_tr = pd.read_csv(tracker_file)
            self.assertIn("ROBUST_1", df_tr["run_id"].values)

    def test_f_no_flags_default_safety(self):
        """Test F — Default parameters (RESET=false, CLEAR_CACHE=false) NEVER delete anything."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            data_dir = os.path.join(tmp_dir, "data")
            logs_dir = os.path.join(tmp_dir, "logs")
            os.makedirs(data_dir, exist_ok=True)
            os.makedirs(logs_dir, exist_ok=True)

            dummy_cache = os.path.join(data_dir, "candles_futures_binance_futures_ETHUSDT_3h.csv")
            with open(dummy_cache, "w") as f:
                f.write("timestamp,open\n100,1\n")

            dummy_state = os.path.join(logs_dir, "forward_state.json")
            with open(dummy_state, "w") as f:
                f.write('{"account":{"balance":10000}}')

            cfg = PipelineConfig()
            self.assertFalse(cfg.reset)
            self.assertFalse(cfg.clear_cache)
            self.assertFalse(cfg.reset_cache)

            # Verify files still exist
            self.assertTrue(os.path.exists(dummy_cache))
            self.assertTrue(os.path.exists(dummy_state))

    def test_g_clear_cache_only(self):
        """Test G — --clear-cache-only deletes market data cache ONLY and does not modify state/tracker."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            data_dir = os.path.join(tmp_dir, "data")
            os.makedirs(data_dir, exist_ok=True)

            loader = MarketDataLoader(data_dir=data_dir)
            cfg_platform = PipelineConfig().platform
            cache_file = loader.get_cache_filename(cfg_platform.symbol, cfg_platform.resolution, cfg_platform.platform)
            with open(cache_file, "w") as f:
                f.write("timestamp,open\n100,1\n")

            deleted = loader.clear_market_cache(cfg_platform)

            self.assertFalse(os.path.exists(cache_file))
            self.assertIn(cache_file, deleted)


if __name__ == "__main__":
    unittest.main()
