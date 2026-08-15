import time
import threading
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src")))
from common.config import PipelineConfig
from forward_test.paper_engine import PaperForwardEngine

def run_test():
    cfg = PipelineConfig()
    cfg.platform.symbol = "ETHUSDT"
    cfg.platform.platform = "BINANCE_FUTURES"
    cfg.platform.resolution = "3h"
    cfg.auto_save_seconds = 60
    
    engine = PaperForwardEngine(cfg)
    
    # Start engine in background thread
    t = threading.Thread(target=engine.run_forward_session, kwargs={"duration_seconds": 30})
    t.daemon = True
    t.start()
    
    time.sleep(10)
    print("\n--- INJECTING NETWORK OUTAGE (Skipping on_message) ---")
    
    # Save the real on_message
    real_last = engine.feed.last_message_ts
    
    # We will simulate the feed being stale by setting last_message_ts to an old value
    # and preventing it from updating.
    def mock_on_message(ws, msg):
        pass # Do nothing, simulate no messages arriving
        
    engine.feed._on_tick_callbacks = [] # Clear callbacks to simulate no data
    engine.feed.last_message_ts = time.time() - 10.0 # Make it stale immediately
    
    time.sleep(15)
    print("\n--- RESTORING NETWORK (Resuming on_message) ---")
    
    engine.feed.last_message_ts = time.time()
    
    time.sleep(5)
    
if __name__ == '__main__':
    run_test()
