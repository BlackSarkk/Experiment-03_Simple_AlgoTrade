import time
import threading
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src")))
from common.config import PipelineConfig
from forward_test.paper_engine import PaperForwardEngine
from strategy.baseline_strategy import Signal

def run_test():
    cfg = PipelineConfig()
    cfg.platform.symbol = "ETHUSDT"
    cfg.platform.platform = "BINANCE_FUTURES"
    cfg.platform.resolution = "1m"
    cfg.reset = True
    
    engine = PaperForwardEngine(cfg)
    
    # Run in background
    t = threading.Thread(target=engine.run_forward_session, kwargs={"duration_seconds": 30})
    t.daemon = True
    t.start()
    
    time.sleep(5)
    
    engine.active_position = {
        "side": "LONG",
        "signal_timestamp": "NOW",
        "entry_time": "NOW",
        "entry_price": 2000.0,
        "size": 0.1,
        "nominal_value": 200.0,
        "sl_price": 1900.0,
        "tp_price": 2150.0,
        "risk_budget": 10.0,
        "entry_fee": 0.1,
        "duration_bars": 0,
        "exposure_pct": 50.0,
        "leverage": 3.5,
        "pnl": 0.0,
        "pnl_pct": 0.0
    }
    time.sleep(2)
    
    print(f"Has active position? {engine.active_position is not None}")
    
    print("\n--- INJECTING WS DISCONNECT (SCENARIO 2) ---")
    engine.feed.trigger_forced_disconnect()
    
    time.sleep(2)
    print(f"Has active position during disconnect? {engine.active_position is not None}")
    
    print("\n--- INJECTING STALE FEED (SCENARIO 3) ---")
    # Simulate stale feed by hacking the monotonic time check
    old_time = time.monotonic() - 60
    engine.feed.last_market_message_monotonic = old_time
    print(f"Is feed healthy? {engine.feed.is_feed_healthy()}")
    
    time.sleep(10)
    print(f"Has active position after stale feed? {engine.active_position is not None}")
    
if __name__ == '__main__':
    run_test()
