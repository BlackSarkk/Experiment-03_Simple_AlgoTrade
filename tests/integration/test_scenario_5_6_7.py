import time
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src")))
from common.config import PipelineConfig
from forward_test.paper_engine import PaperForwardEngine
import pandas as pd

def run_test():
    cfg = PipelineConfig()
    cfg.platform.symbol = "ETHUSDT"
    cfg.platform.platform = "BINANCE_FUTURES"
    cfg.platform.resolution = "1m"
    cfg.reset = True
    
    engine = PaperForwardEngine(cfg)
    
    print("\n--- SCENARIO 5: BACKFILL ---")
    now_ts = int(time.time())
    engine.feed.last_closed_3h_ts = now_ts - 120 # Missed 2 minutes
    engine.feed._check_3h_candle_boundary()
    print(f"Candles recovered: {engine.feed.processed_closed_candles}")
    
    print("\n--- SCENARIO 6: DUPLICATE ---")
    prev = engine.feed.processed_closed_candles
    engine.feed.last_closed_3h_ts = engine.feed.last_closed_3h_ts - 60
    engine.feed._check_3h_candle_boundary()
    new = engine.feed.processed_closed_candles
    print(f"Candles recovered again? Diff: {new - prev}")
    
    print("\n--- SCENARIO 7: LATE/STALE ---")
    # Feed monotonic check should drop this
    old_ts = engine.feed.last_update_ts
    
    print(f"Original TS: {old_ts}")
    # Force a WS message from the past
    msg = '{"e":"24hrTicker","E":' + str((now_ts - 120) * 1000) + ',"c":"2000.0"}'
    engine.feed.on_message(None, msg)
    print(f"After stale update, TS: {engine.feed.last_update_ts}")
    
if __name__ == '__main__':
    run_test()
