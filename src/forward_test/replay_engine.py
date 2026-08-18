import pandas as pd
from typing import Dict, Any, List
import time
from common.config import PipelineConfig
from forward_test.paper_engine import PaperForwardEngine
from strategy.indicators import compute_all_indicators


class WholeFrameSignals:
    """Serve the backtest's signal set to the replay engine, one closed candle at a time.

    BacktestEngine calls `generate_signals(df)` ONCE on the whole evaluation frame and
    indexes the result by candle. The replay loop instead hands the strategy a short
    rolling slice per bar, which restarts every rolling window at the slice edge and makes
    frame-wide gates (a Bollinger mask, for one) impossible to apply.

    This adapter closes that gap without touching either engine or duplicating any
    calculation: the REAL strategy — whatever `main.py` injected, `MaskedStrategy` with a
    filter mask included — is run once over the full frame here, exactly as the backtest
    runs it. Per bar, the adapter simply hands back the signal belonging to the candle that
    just closed (the last row of the slice), so `signals[-1]` and the engine's
    signal-timestamp freshness check downstream keep working unchanged.
    """

    def __init__(self, strategy, df_full: pd.DataFrame):
        self.strategy = strategy
        signals = strategy.generate_signals(df_full)
        self.by_timestamp = {int(s.timestamp): s for s in signals}
        self.total_signals = len(signals)
        self.blocked_count = getattr(strategy, "blocked_count", 0)

    def generate_signals(self, df_slice: pd.DataFrame) -> List:
        if df_slice is None or len(df_slice) == 0:
            return []
        closed_ts = int(df_slice["timestamp"].iloc[-1])
        sig = self.by_timestamp.get(closed_ts)
        return [sig] if sig is not None else []


class HistoricalReplayEngine(PaperForwardEngine):
    def required_history_bars(self) -> int:
        req = max(self.config.strategy.ema_period, self.config.strategy.volume_sma_period, self.config.strategy.rsi_period)
        req += self.config.strategy.swing_lookback + self.config.strategy.consolidation_candles + 10
        return req

    def __init__(self, config: PipelineConfig, df_raw: pd.DataFrame):
        super().__init__(config)
        
        import os
        # Isolate replay paths from live paper state
        self._redirect_file_logs(os.path.join(self.config.logs_dir, "replay_debug.log"))
        self.state_store = type(self.state_store)(state_file=os.path.join(self.config.logs_dir, "replay_state.json"))
        self.trades_path = os.path.join(self.config.results_dir, "replay", "trades.csv")
        self.events_path = os.path.join(self.config.results_dir, "replay", "events.csv")
        self.equity_path = os.path.join(self.config.results_dir, "replay", "equity_curve.csv")
        self.archive_dir = os.path.join(self.config.results_dir, "replay", "archive")
        self.tracker_path = os.path.join(self.config.results_dir, "replay", "tracker.csv")
        os.makedirs(os.path.dirname(self.trades_path), exist_ok=True)
        
        # Clear previous run explicitly to avoid appending stale rows
        if os.path.exists(self.trades_path): os.remove(self.trades_path)
        if os.path.exists(self.events_path): os.remove(self.events_path)
        if os.path.exists(self.equity_path): os.remove(self.equity_path)

        # Initialize headers for replay files
        self._init_csv_headers()

        self.df_raw = df_raw.copy()
        
        # Override the start UTC to be the first candle so that time logic aligns
        first_dt = str(self.df_raw['datetime'].iloc[0])
        self.experiment_start_utc = first_dt
        self.last_warmup_candle_ts = int(pd.to_datetime("2024-01-01 00:00:00").tz_localize("UTC").timestamp())

        # Precompute indicators to avoid O(N^2)
        # main.py supplies a frame that already has indicators attached (computed on the full
        # cache, including pre-start warmup candles) and has already been sliced to the
        # evaluation window by slice_evaluation_window(). Recomputing here would re-derive
        # indicators from the sliced frame and silently lose that warmup, so only compute
        # when an un-indicatored frame is passed (e.g. direct construction in a test).
        if "ema_51" in self.df_raw.columns:
            self.df_indicators = self.df_raw
        else:
            self.df_indicators = compute_all_indicators(self.df_raw, self.config.strategy)
        
        # Prevent stale feed rejection during replay
        self.feed.is_feed_stale = lambda: False

        # Per-bar equity curve, buffered and written once at the end of the replay.
        self._equity_rows: List[Dict[str, Any]] = []

    @staticmethod
    def _redirect_file_logs(path: str):
        """Point this package's file logging at the run's own logs_dir.

        `paper_engine` binds its file handler at import time, before any config exists, so
        a replay would otherwise append to the live engine's log outside the configured
        output folder. Console handlers are left alone.
        """
        import logging
        import os

        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        logger = logging.getLogger("PaperEngine")
        for handler in list(logger.handlers):
            if isinstance(handler, logging.FileHandler):
                fmt = handler.formatter
                logger.removeHandler(handler)
                handler.close()
                replacement = logging.FileHandler(path)
                replacement.setFormatter(fmt)
                logger.addHandler(replacement)

    def _record_equity_bar(self, row_dict: Dict[str, Any], c_close: float):
        """One equity row per closed candle, mirroring the backtest's per-bar curve.

        Uses the engine's own mark-to-market helper, so the formula is shared with the live
        snapshot rather than restated here. `peak_equity` is advanced before the drawdown is
        measured, matching the backtest's ordering.
        """
        snapshot = self.equity_snapshot_row(c_close, int(row_dict["timestamp"]), str(row_dict["datetime"]))
        if snapshot["equity"] > self.peak_equity:
            self.peak_equity = snapshot["equity"]
            snapshot = self.equity_snapshot_row(c_close, int(row_dict["timestamp"]), str(row_dict["datetime"]))
        if snapshot["drawdown_pct"] > self.max_dd_pct:
            self.max_dd_pct = snapshot["drawdown_pct"]
        self._equity_rows.append(snapshot)

    def run_replay(self) -> pd.DataFrame:
        print("Starting Historical Replay Engine...")
        self.session_trades_count = 0
        self.engine_state = "RUNNING"

        # Signals are produced ONCE over the whole frame by the configured strategy — the
        # same call the backtest makes — so enabled filters see full history instead of a
        # rolling slice. Wrapped here, after main.py has had its chance to inject a filtered
        # strategy onto this engine.
        self.strategy = WholeFrameSignals(self.strategy, self.df_indicators)
        if self.strategy.blocked_count:
            print(f"Signal filters blocked {self.strategy.blocked_count} signals")

        # Mute logging to console if we want to run fast
        
        prev_df_slice = None
        prev_row_dict = None
        req_history = self.required_history_bars()

        # Iterate over the dataframe sequentially
        for i in range(len(self.df_indicators)):
            row = self.df_indicators.iloc[i]
            row_dict = row.to_dict()
            c_open = row_dict["open"]
            c_high = row_dict["high"]
            c_low = row_dict["low"]
            c_close = row_dict["close"]
            
            # Update current simulated time to this bar's open
            self.feed.current_simulated_time = str(row_dict["datetime"])
            
            # Step 1: Open Tick (with open gap logic)
            self.feed.current_price = c_open
            
            # Step 2: Execute pending signal from previous closed candle on current bar's open
            if prev_df_slice is not None:
                if len(prev_df_slice) < req_history and i >= req_history:
                    raise ValueError(f"Insufficient history slice supplied. Expected at least {req_history} bars.")
                self.on_3h_candle_closed(prev_df_slice, prev_row_dict, source="REPLAY", precomputed=True)
            
            self.evaluate_live_tick(c_open, is_open=True)
            
            # Step 3: Intrabar Ticks (Handle same-bar SL/TP collision priority)
            if self.active_position is not None:
                is_long = self.active_position["side"] == "LONG"
                sl = self.active_position["sl_price"]
                tp = self.active_position["tp_price"]
                
                hit_sl = (is_long and c_low <= sl) or (not is_long and c_high >= sl)
                hit_tp = (is_long and c_high >= tp) or (not is_long and c_low <= tp)
                
                if hit_sl and hit_tp:
                    # SL Priority Configuration: Resolve SL first for both LONG and SHORT
                    if is_long:
                        self.evaluate_live_tick(c_low, is_open=False)
                        self.evaluate_live_tick(c_high, is_open=False)
                    else:
                        self.evaluate_live_tick(c_high, is_open=False)
                        self.evaluate_live_tick(c_low, is_open=False)
                else:
                    self.evaluate_live_tick(c_low, is_open=False)
                    self.evaluate_live_tick(c_high, is_open=False)
            else:
                self.evaluate_live_tick(c_low, is_open=False)
                self.evaluate_live_tick(c_high, is_open=False)
            
            # Step 4: Close Tick
            self.feed.current_price = c_close
            self.evaluate_live_tick(c_close, is_open=False)
            
            # Step 4b: Record the bar's mark-to-market, as the backtest does every bar.
            self._record_equity_bar(row_dict, c_close)

            # Step 5: Prepare this candle to be evaluated at the NEXT open
            start_idx = max(0, i - req_history)
            prev_df_slice = self.df_indicators.iloc[start_idx:i+1]
            prev_row_dict = row_dict

        self._flush_equity_curve()
        return pd.read_csv(self.trades_path) if self.session_trades_count > 0 else pd.DataFrame()

    def _flush_equity_curve(self):
        """Write the buffered per-bar equity curve to the replay's own equity_curve.csv."""
        if not self._equity_rows:
            return
        pd.DataFrame(self._equity_rows, columns=self.EQUITY_COLUMNS).to_csv(
            self.equity_path, mode="a", header=False, index=False)
