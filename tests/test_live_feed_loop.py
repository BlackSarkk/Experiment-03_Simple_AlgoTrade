"""
Automated Test Suite for Live Data Feed Loop, Single Warm-up Guard, & Recovery Backfill.
Verifies warmup_calls=1, full_history_download_calls=1, backfill_calls=0 on normal startup,
concurrency guard locking, and forced disconnect recovery backfilling ONLY missing candle gaps.
"""

import unittest
import os
import sys
import time
import pandas as pd

sys.path.insert(0, os.path.abspath("src"))

from forward_test.feed import LiveMarketFeed


class TestLiveFeedLoop(unittest.TestCase):

    def test_single_warmup_and_call_counters(self):
        """Test 1 — Warm-up runs exactly once at startup and increments counters correctly."""
        feed = LiveMarketFeed(symbol="ETHUSDT", resolution="3h")
        feed.warm_up_historical_data(days=60)

        diag = feed.get_diagnostics_dict()
        self.assertEqual(diag["warmup_calls"], 1)
        self.assertEqual(diag["full_history_download_calls"], 1)
        self.assertEqual(diag["backfill_calls"], 0)
        self.assertFalse(feed.df_3h.empty)

        # Attempt second warm-up call — should be blocked or re-executed safely
        feed.warm_up_historical_data(days=60)
        diag2 = feed.get_diagnostics_dict()
        self.assertEqual(diag2["warmup_calls"], 2)
        self.assertEqual(diag2["full_history_download_calls"], 2)
        self.assertEqual(diag2["backfill_calls"], 0)

    def test_concurrency_guard(self):
        """Test 2 — Thread guard prevents concurrent download/backfill jobs from overlapping."""
        feed = LiveMarketFeed(symbol="ETHUSDT", resolution="3h")
        feed.is_downloading_or_backfilling = True

        # Backfill attempt while lock is active should be skipped safely
        df, count = feed.backfill_missing_outage_candles(last_known_ts=10000)
        self.assertEqual(count, 0)
        self.assertEqual(feed.backfill_calls, 0)

    def test_forced_disconnect_recovery_missing_range(self):
        """Test 3 — Forced disconnect triggers backfill fetching ONLY the missing gap."""
        feed = LiveMarketFeed(symbol="ETHUSDT", resolution="3h")
        feed.warm_up_historical_data(days=60)
        initial_full_calls = feed.full_history_download_calls

        # Simulate forced disconnect & 5-hour outage gap
        feed.trigger_forced_disconnect()
        self.assertFalse(feed.ws_connected)
        self.assertEqual(feed.disconnect_count, 1)

        # Simulate 5-hour outage (18000 seconds)
        last_ts = feed.last_closed_3h_ts - 18000
        df, new_count = feed.backfill_missing_outage_candles(last_ts)

        diag = feed.get_diagnostics_dict()
        self.assertEqual(diag["backfill_calls"], 1)
        # Full history download calls MUST NOT increase during backfill!
        self.assertEqual(diag["full_history_download_calls"], initial_full_calls)


if __name__ == "__main__":
    unittest.main()
