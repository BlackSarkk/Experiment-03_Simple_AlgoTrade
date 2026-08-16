import pandas as pd
from typing import Dict, Any, List
import time
from common.config import PipelineConfig
from forward_test.paper_engine import PaperForwardEngine
from strategy.indicators import compute_all_indicators

class HistoricalReplayEngine(PaperForwardEngine):
    def required_history_bars(self) -> int:
        req = max(self.config.strategy.ema_period, self.config.strategy.volume_sma_period, self.config.strategy.rsi_period)
        req += self.config.strategy.swing_lookback + self.config.strategy.consolidation_candles + 10
        return req

    def __init__(self, config: PipelineConfig, df_raw: pd.DataFrame):
        super().__init__(config)
        
        import os
        # Isolate replay paths from live paper state
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
        self.df_indicators = compute_all_indicators(self.df_raw, self.config.strategy)
        
        # Prevent stale feed rejection during replay
        self.feed.is_feed_stale = lambda: False

    def run_replay(self) -> pd.DataFrame:
        print("Starting Historical Replay Engine...")
        self.session_trades_count = 0
        self.engine_state = "RUNNING"

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
            
            # Step 5: Prepare this candle to be evaluated at the NEXT open
            start_idx = max(0, i - req_history)
            prev_df_slice = self.df_indicators.iloc[start_idx:i+1]
            prev_row_dict = row_dict
            
        return pd.read_csv(self.trades_path) if self.session_trades_count > 0 else pd.DataFrame()
