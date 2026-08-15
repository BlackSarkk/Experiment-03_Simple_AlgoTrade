import time
import os
import sys
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src")))
from common.config import PipelineConfig
from forward_test.paper_engine import PaperForwardEngine

def run_test():
    cfg = PipelineConfig()
    cfg.platform.symbol = "ETHUSDT"
    cfg.platform.platform = "BINANCE_FUTURES"
    cfg.platform.resolution = "1m"
    cfg.reset = True
    
    print("\n--- SCENARIO 8: RESTART WHILE FLAT ---")
    engine1 = PaperForwardEngine(cfg)
    engine1.save_state(2000.0)
    del engine1
    
    cfg.reset = False
    cfg.resume_forward_state = True
    engine2 = PaperForwardEngine(cfg)
    engine2.load_or_init_state()
    print(f"Engine 2 active position: {engine2.active_position}")
    
    print("\n--- SCENARIO 9: RESTART WITH OPEN POSITION ---")
    engine2.active_position = {
        "side": "SHORT",
        "signal_timestamp": "NOW",
        "entry_time": "NOW",
        "entry_price": 2000.0,
        "size": 0.5,
        "nominal_value": 1000.0,
        "sl_price": 2100.0,
        "tp_price": 1800.0,
        "risk_budget": 50.0,
        "entry_fee": 0.5,
        "duration_bars": 0,
        "exposure_pct": 50.0,
        "leverage": 3.5,
        "pnl": 0.0,
        "pnl_pct": 0.0
    }
    print("Before save, engine2 active position:", engine2.active_position)
    engine2.save_state(2000.0)
    print("After save, engine2 active position:", engine2.active_position)
    with open("logs/forward_state.json") as f:
        print("JSON on disk:", [line for line in f if 'active_trade' in line])
    del engine2
    
    engine3 = PaperForwardEngine(cfg)
    engine3.load_or_init_state()
    print(f"Engine 3 active position side: {engine3.active_position.get('side') if engine3.active_position else None}")
    
    print("\n--- SCENARIO 10: CORRUPT STATE ---")
    print("State JSON before corruption:", open("logs/forward_state.json").read()[:500])
    with open("logs/forward_state.json", "w") as f:
        f.write("{ INVALID JSON")
    
    engine4 = PaperForwardEngine(cfg)
    engine4.load_or_init_state()
    print(f"Engine 4 recovered from corrupt JSON? Active pos: {engine4.active_position}")
    
if __name__ == '__main__':
    run_test()
