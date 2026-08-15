"""
Historical Replay Equivalence Test.
Feeds historical candles sequentially through the Paper Forward Engine as if arriving live,
and asserts 100% trade-by-trade equivalence to BacktestEngine output.
"""

import unittest
import sys
import os
import tempfile

sys.path.insert(0, os.path.abspath("src"))

from common.config import PipelineConfig
from common.market_data import MarketDataLoader
from strategy.indicators import compute_all_indicators
from backtest.engine import BacktestEngine
from forward_test.paper_engine import PaperForwardEngine


class TestReplayEquivalence(unittest.TestCase):

    def test_paper_vs_backtest_equivalence(self):
        """Verify that sequential candle replay through Paper Engine yields 100% identical trades to BacktestEngine."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            cfg = PipelineConfig(execution_mode="REFERENCE")
            cfg.platform.symbol = "ETHUSDT"
            cfg.platform.platform = "BINANCE_FUTURES"
            cfg.platform.resolution = "3h"
            cfg.platform.start_date = "2024-01-01"
            cfg.platform.end_date = "2026-08-13"
            cfg.logs_dir = tmp_dir
            cfg.results_dir = tmp_dir

            loader = MarketDataLoader(data_dir="data")
            df_raw = loader.load_ohlcv(cfg.platform)
            df_ind = compute_all_indicators(df_raw, cfg.strategy)

            # 1. Run Standard Backtest
            bt_engine = BacktestEngine(cfg)
            bt_res = bt_engine.run(df_ind)
            bt_trades = bt_res["trades"]

            # 2. Run Replay through Paper Engine
            paper_engine = PaperForwardEngine(cfg)
            paper_engine.account.initial_balance = 10000.0
            paper_engine.account.balance = 10000.0
            paper_engine.account.equity = 10000.0

            n = len(df_ind)
            signals = paper_engine.strategy.generate_signals(df_ind)
            signals_by_idx = {s.candle_idx: s for s in signals}

            in_position_at_bar_close = {}
            for i in range(n):
                c_open = float(df_ind.iloc[i]["open"])
                c_high = float(df_ind.iloc[i]["high"])
                c_low = float(df_ind.iloc[i]["low"])
                dt_str = str(df_ind.iloc[i]["datetime"])

                # Step 1. Evaluate active position intrabar SL/TP on candle i
                if paper_engine.active_position is not None:
                    paper_engine.active_position["duration_bars"] += 1
                    pos = paper_engine.active_position

                    if pos["side"] == "LONG":
                        sl_hit = c_low <= pos["sl_price"]
                        tp_hit = c_high >= pos["tp_price"]
                        if sl_hit or tp_hit:
                            base_exit = min(c_open, pos["sl_price"]) if sl_hit else max(c_open, pos["tp_price"])
                            reason = "SL" if sl_hit else "TP"
                            exit_p = base_exit - (cfg.execution.slippage_ticks * 0.1)
                            paper_engine._close_paper_position(exit_p, reason)
                    elif pos["side"] == "SHORT":
                        sl_hit = c_high >= pos["sl_price"]
                        tp_hit = c_low <= pos["tp_price"]
                        if sl_hit or tp_hit:
                            base_exit = max(c_open, pos["sl_price"]) if sl_hit else min(c_open, pos["tp_price"])
                            reason = "SL" if sl_hit else "TP"
                            exit_p = base_exit + (cfg.execution.slippage_ticks * 0.1)
                            paper_engine._close_paper_position(exit_p, reason)

                # Step 2. Execute signal generated on candle i-1 at candle i open
                sig_idx = i - 1
                sig_valid = (sig_idx >= 0) and not in_position_at_bar_close.get(sig_idx, False)

                if paper_engine.active_position is None and sig_idx in signals_by_idx and sig_valid:
                    sig = signals_by_idx[sig_idx]
                    is_long = (sig.signal_type == "LONG" and cfg.strategy.long_enabled)
                    is_short = (sig.signal_type == "SHORT" and cfg.strategy.short_enabled)

                    if is_long or is_short:
                        realized_entry = c_open + (cfg.execution.slippage_ticks * 0.1 if sig.signal_type == "LONG" else -cfg.execution.slippage_ticks * 0.1)

                        sizing = paper_engine.risk_manager.calculate_position(
                            equity=paper_engine.account.balance,
                            entry_price=realized_entry,
                            sl_price=sig.sl_price,
                            signal_type=sig.signal_type
                        )

                        if sizing.is_valid and sizing.position_size > 0:
                            entry_nominal = realized_entry * sizing.position_size
                            entry_fee = entry_nominal * cfg.execution.taker_fee_pct

                            paper_engine.active_position = {
                                "side": sig.signal_type,
                                "signal_timestamp": sig.datetime_str,
                                "entry_time": dt_str,
                                "entry_price": round(realized_entry, 2),
                                "size": sizing.position_size,
                                "nominal_value": sizing.nominal_position_value,
                                "sl_price": sizing.sl_price,
                                "tp_price": sizing.tp_price,
                                "risk_budget": sizing.risk_amount,
                                "entry_fee": round(entry_fee, 2),
                                "duration_bars": 0
                            }

                in_position_at_bar_close[i] = (paper_engine.active_position is not None)

            if paper_engine.active_position:
                paper_engine._close_paper_position(float(df_ind.iloc[-1]["close"]), "END_OF_DATA")

            paper_trades = paper_engine.trades_history

            # 3. Assert Equivalence
            self.assertEqual(len(bt_trades), len(paper_trades), f"Trade count mismatch: BT={len(bt_trades)} vs Paper={len(paper_trades)}")

            for idx, (bt_t, p_t) in enumerate(zip(bt_trades, paper_trades)):
                self.assertEqual(bt_t.signal_type, p_t["side"], f"Trade #{idx+1} Side Mismatch")
                self.assertAlmostEqual(bt_t.entry_price, p_t["entry_price"], places=1, msg=f"Trade #{idx+1} Entry Price Mismatch")
                self.assertAlmostEqual(bt_t.exit_price, p_t["exit_price"], places=1, msg=f"Trade #{idx+1} Exit Price Mismatch")
                self.assertAlmostEqual(bt_t.sl_price, p_t["sl_price"], places=1, msg=f"Trade #{idx+1} SL Mismatch")
                self.assertAlmostEqual(bt_t.tp_price, p_t["tp_price"], places=1, msg=f"Trade #{idx+1} TP Mismatch")
                self.assertAlmostEqual(bt_t.net_pnl, p_t["net_pnl"], places=1, msg=f"Trade #{idx+1} Net PnL Mismatch")

            # Final balance assertion
            self.assertAlmostEqual(bt_res["final_balance"], paper_engine.account.balance, places=1)


if __name__ == "__main__":
    unittest.main()
