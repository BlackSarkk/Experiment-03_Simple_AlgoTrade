import time
import threading
import sys
from common.config import PipelineConfig
from forward_test.paper_engine import PaperForwardEngine

def run_test():
    cfg = PipelineConfig()
    cfg.platform.symbol = "ETHUSDT"
    cfg.platform.platform = "BINANCE"
    cfg.platform.resolution = "3h"
    cfg.auto_save_seconds = 60
    
    engine = PaperForwardEngine(cfg)
    
    # We don't really want the real Binance WS since it might be slow or blocked.
    # We will just inject messages manually by calling engine.feed's callbacks.
    # Wait, we can't easily do that because _ws_loop is running.
    # We'll just let it run, but we will mock `is_feed_stale`? 
    # No, we want to test the actual watchdog logic.
    pass
