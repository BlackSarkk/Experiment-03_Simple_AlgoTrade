import time
import threading
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src")))
from common.config import PipelineConfig
from forward_test.paper_engine import PaperForwardEngine

def test_startup():
    print("\n--- Starting 60-Second Smoke Test ---")
    cfg = PipelineConfig()
    cfg.platform.symbol = "ETHUSDT"
    cfg.platform.platform = "BINANCE_FUTURES"
    cfg.platform.resolution = "3h"
    cfg.auto_save_seconds = 60
    
    engine = PaperForwardEngine(cfg)
    
    start_time = time.time()
    
    # We will poll engine.build_dashboard_state() over 60 seconds
    last_state = None
    
    def engine_runner():
        engine.run_forward_session(duration_seconds=5.0)
        
    t = threading.Thread(target=engine_runner, daemon=True)
    t.start()
    
    while True:
        now = time.time()
        elapsed = now - start_time
        
        if elapsed > 6.0:
            break
            
        try:
            db_state = engine.build_dashboard_state()
            top_bar = db_state.get("top_bar", {})
            current_state = f"{top_bar.get('connection', '???')} | {top_bar.get('engine_state', '???')}"
        except Exception:
            current_state = "BOOTING"
            
        if current_state != last_state:
            print(f"t={elapsed:.1f}s State Transition: {last_state} -> {current_state}")
            last_state = current_state
            
        time.sleep(0.1)
        
    print("\n--- Test Complete ---")
    t.join(timeout=5.0)

if __name__ == "__main__":
    test_startup()
