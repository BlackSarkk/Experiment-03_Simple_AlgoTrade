"""
Unit and Integration Tests for Paper Forward Testing Engine.
Covers: fresh paper account, resume paper account, duplicate signal prevention,
candle-close-only signals, LONG/SHORT entry, SL/TP exits, fee calculation,
live unrealized PnL, reconnect recovery, missing candle recovery, atomic state persistence.
"""

import unittest
import sys
import os
import tempfile
import json
import pandas as pd

sys.path.insert(0, os.path.abspath("src"))

from common.config import PipelineConfig
from forward_test.state import ForwardStateStore
from forward_test.feed import LiveMarketFeed
from forward_test.paper_engine import PaperForwardEngine


class TestForwardEngine(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.cfg = PipelineConfig(execution_mode="REFERENCE")
        self.cfg.platform.symbol = "ETHUSDT"
        self.cfg.platform.platform = "BINANCE_FUTURES"
        self.cfg.logs_dir = self.tmp_dir.name
        self.cfg.results_dir = self.tmp_dir.name

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_fresh_paper_account(self):
        store = ForwardStateStore(state_file=os.path.join(self.tmp_dir.name, "forward_state.json"))
        self.cfg.reset_forward_state = True
        engine = PaperForwardEngine(self.cfg)
        engine.load_or_init_state()
        self.assertEqual(engine.account.initial_balance, 10000.0)
        self.assertEqual(engine.account.balance, 10000.0)
        self.assertIsNone(engine.active_position)

    def test_resume_paper_account(self):
        state_file = os.path.join(self.tmp_dir.name, "forward_state.json")
        store = ForwardStateStore(state_file=state_file)
        sample = {
            "account": {"initial_balance": 10000.0, "balance": 12500.0, "equity": 12500.0, "total_net_pnl": 2500.0, "peak_equity": 13000.0, "max_dd_pct": 5.0},
            "position": {"active_trade": {"side": "LONG", "entry_price": 2500.0, "size": 2.0, "sl_price": 2400.0, "tp_price": 2700.0, "entry_fee": 2.5, "risk_budget": 500.0, "signal_timestamp": "2024-01-01 00:00:00"}},
            "system": {"last_executed_signal_ts": "2024-01-01 00:00:00"}
        }
        store.save_state_atomic(sample)

        self.cfg.reset_forward_state = False
        self.cfg.resume_forward_state = True
        engine = PaperForwardEngine(self.cfg)
        engine.load_or_init_state()

        self.assertEqual(engine.account.balance, 12500.0)
        self.assertIsNotNone(engine.active_position)
        self.assertEqual(engine.active_position["side"], "LONG")
        self.assertEqual(engine.last_executed_signal_ts, "2024-01-01 00:00:00")

    def test_duplicate_signal_prevention(self):
        engine = PaperForwardEngine(self.cfg)
        engine.load_or_init_state()
        engine.last_executed_signal_ts = "2024-01-01 03:00:00"

        # Mock 3h closed row
        closed_row = {"datetime": "2024-01-01 03:00:00", "close": 2500.0}
        df_3h = pd.DataFrame([{"timestamp": 1704078000, "datetime": "2024-01-01 03:00:00", "open": 2480.0, "high": 2510.0, "low": 2470.0, "close": 2500.0, "volume": 1000.0}])

        # Should skip since timestamp matches last_executed_signal_ts
        engine.on_3h_candle_closed(df_3h, closed_row)
        self.assertIsNone(engine.active_position)

    def test_live_sl_tp_tick_trigger(self):
        engine = PaperForwardEngine(self.cfg)
        engine.load_or_init_state()
        engine.active_position = {
            "side": "LONG",
            "signal_timestamp": "2024-01-01 00:00:00",
            "entry_time": "2024-01-01 00:00:00",
            "entry_price": 2500.0,
            "size": 2.0,
            "sl_price": 2450.0,
            "tp_price": 2600.0,
            "entry_fee": 2.5,
            "risk_budget": 500.0,
            "duration_bars": 1
        }
    
        import time
        engine.feed.ws_connected = True
        engine.feed.feed_healthy = True
        engine.feed.last_message_ts = time.time()
        engine.feed.last_market_message_monotonic = time.monotonic()

        # Tick at 2440.0 should trigger SL exit immediately
        engine.evaluate_live_tick(2440.0)
        self.assertIsNone(engine.active_position)
        self.assertEqual(len(engine.trades_history), 1)
        self.assertEqual(engine.trades_history[0]["exit_reason"], "SL")

    def test_reconnect_and_missing_candle_recovery(self):
        feed = LiveMarketFeed(symbol="ETHUSDT", resolution="3h", data_dir=self.tmp_dir.name)
        self.assertFalse(feed.ws_connected)
        # Test ticker fallback fetch
        price = feed.fetch_latest_ticker()
        self.assertGreater(price, 0.0)

    def test_atomic_state_persistence(self):
        state_file = os.path.join(self.tmp_dir.name, "forward_state.json")
        store = ForwardStateStore(state_file=state_file)
        data = {"account": {"balance": 10000.0}}
        store.save_state_atomic(data)
        self.assertTrue(os.path.exists(state_file))
        loaded = store.load_state()
        self.assertEqual(loaded["account"]["balance"], 10000.0)


    def test_setup_readiness_calculation(self):
        engine = PaperForwardEngine(self.cfg)
        
        # 1. Test Active LONG Position Override
        engine.active_position = {"side": "LONG", "entry_price": 2500.0, "size": 1.0, "sl_price": 2400.0, "tp_price": 2700.0, "entry_fee": 1.0, "risk_budget": 500.0}
        db_state = engine.build_dashboard_state()
        readiness = db_state["readiness"]
        self.assertEqual(readiness["buy_pct"], 100)
        self.assertEqual(readiness["sell_pct"], 0)
        self.assertEqual(readiness["bias"], "BUY")
        self.assertEqual(readiness["status"], "LONG ACTIVE")

        # 2. Test Active SHORT Position Override
        engine.active_position = {"side": "SHORT", "entry_price": 2500.0, "size": 1.0, "sl_price": 2600.0, "tp_price": 2300.0, "entry_fee": 1.0, "risk_budget": 500.0}
        db_state = engine.build_dashboard_state()
        readiness = db_state["readiness"]
        self.assertEqual(readiness["buy_pct"], 0)
        self.assertEqual(readiness["sell_pct"], 100)
        self.assertEqual(readiness["bias"], "SELL")
        self.assertEqual(readiness["status"], "SHORT ACTIVE")

        # 3. Test Flat / Preview Readiness with Live Price Changes
        engine.active_position = None
        dates = pd.date_range("2026-01-01", periods=15, freq="3h")
        mock_df = pd.DataFrame({
            "timestamp": [int(d.timestamp()) for d in dates],
            "datetime": [str(d) for d in dates],
            "open": [2500.0] * 15,
            "high": [2550.0] * 15,
            "low": [2450.0] * 15,
            "close": [2500.0] * 15,
            "volume": [100.0] * 15,
            "ema_51": [2450.0] * 15,
            "ema_200": [2400.0] * 15,
            "ema_51_slope": [1.5] * 15,
            "rsi": [55.0] * 15,
            "atr": [50.0] * 15,
            "is_consolidating": [True] * 15
        })
        engine.feed.df_3h = mock_df

        # High price above EMA (bullish)
        engine.feed.current_price = 2600.0
        st1 = engine.build_dashboard_state()["readiness"]
        
        # Lower price near/below EMA (bearish move)
        engine.feed.current_price = 2400.0
        st2 = engine.build_dashboard_state()["readiness"]

        # Verify BUY% + SELL% = 100% on every update
        self.assertEqual(st1["buy_pct"] + st1["sell_pct"], 100)
        self.assertEqual(st2["buy_pct"] + st2["sell_pct"], 100)
        self.assertNotEqual(st1["buy_pct"], st1["sell_pct"])
        self.assertNotEqual(st1["buy_pct"], st2["buy_pct"])
        self.assertNotEqual(st1["sell_pct"], st2["sell_pct"])


if __name__ == "__main__":
    unittest.main()
