import time
import os
import sys
import threading

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
    t = threading.Thread(target=engine.run_forward_session, kwargs={"duration_seconds": 15})
    t.daemon = True
    t.start()
    
    time.sleep(3)
    print("\n--- SCENARIO 11: WORKER THREAD DIES ---")
    
    # The watchdog is the main thread inside run_forward_session!
    # Or wait, _ws_loop runs in a background thread inside feed.start().
    # We will kill _ws_loop thread by making it raise an exception!
    engine.feed._ws_thread_should_crash = True
    
    # We need to hack _ws_loop to crash
    def crashing_loop():
        raise RuntimeError("Fake thread crash")
    engine.feed._ws_thread = threading.Thread(target=crashing_loop, daemon=True)
    engine.feed._ws_thread.start()
    
    time.sleep(2)
    print(f"Is feed healthy? {engine.feed.is_feed_healthy()}")
    print("Does engine survive?")
    
    print("\n--- SCENARIO 12: REST REQUEST FAILURE ---")
    import requests
    old_get = requests.get
    
    def fake_get(*args, **kwargs):
        raise requests.exceptions.ConnectionError("Fake REST failure")
    requests.get = fake_get
    
    try:
        engine.feed._check_3h_candle_boundary()
        print("REST failure gracefully caught")
    except Exception as e:
        print(f"REST failure CRASHED engine: {e}")
        
    requests.get = old_get
    
if __name__ == '__main__':
    run_test()
