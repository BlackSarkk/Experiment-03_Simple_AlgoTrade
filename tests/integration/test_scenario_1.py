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
    cfg.platform.resolution = "1m"
    cfg.reset = True
    
    engine = PaperForwardEngine(cfg)
    
    # Run in background
    t = threading.Thread(target=engine.run_forward_session, kwargs={"duration_seconds": 25})
    t.daemon = True
    t.start()
    
    time.sleep(10)
    print("\n--- INJECTING WS DISCONNECT (FLAT) ---")
    engine.feed.trigger_forced_disconnect()
    
    time.sleep(2)
    print(f"Feed healthy? {engine.feed.is_feed_healthy()}")
    
    time.sleep(10)
    print(f"Feed healthy? {engine.feed.is_feed_healthy()}")
    print(f"Trades count: {len(engine.trades_history)}")
    
if __name__ == '__main__':
    run_test()
