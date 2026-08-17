"""
Pre-download script for multi-timeframe optimization data.
Downloads all required timeframes from Binance Futures 2024-01-01 → 2026-08-13
and caches them in data/.

Run ONCE before the optimizer:
    PYTHONPATH=src python src/optimization/fetch_data.py

2m is NOT natively available on Binance Futures — it will be resampled from 1m at runtime.
"""

import os
import sys

_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_DIR, ".."))

from common.config import PlatformConfig
from common.market_data import MarketDataLoader

DATA_DIR   = "data"
START_DATE = "2024-01-01"
END_DATE   = "2026-08-13"

# Timeframes to download (2m is excluded — resampled from 1m by optimizer)
DOWNLOAD_TFS = ["1m", "3m", "5m", "15m", "30m", "1h", "2h", "3h", "4h"]


def main():
    loader = MarketDataLoader(data_dir=DATA_DIR)
    print(f"\nPre-downloading ETHUSDT Binance Futures data")
    print(f"Range: {START_DATE} → {END_DATE}")
    print(f"Timeframes: {DOWNLOAD_TFS}")
    print(f"Output dir: {DATA_DIR}/\n")
    print("NOTE: 1m download (~1.37M candles) may take 8-15 minutes.\n")

    for tf in DOWNLOAD_TFS:
        cache_path = loader.get_cache_filename("ETHUSDT", tf, "BINANCE_FUTURES")
        if os.path.exists(cache_path):
            import pandas as pd
            df = pd.read_csv(cache_path)
            print(f"  {tf:5s} ALREADY CACHED: {len(df):>9,} candles → {cache_path}")
            continue

        print(f"  {tf:5s} Downloading...")
        try:
            cfg = PlatformConfig(
                platform="BINANCE_FUTURES",
                symbol="ETHUSDT",
                resolution=tf,
                start_date=START_DATE,
                end_date=END_DATE,
            )
            df = loader.load_ohlcv(cfg, quiet=False)
            print(f"  {tf:5s} ✓  {len(df):>9,} candles cached → {cache_path}\n")
        except Exception as e:
            print(f"  {tf:5s} ✗  ERROR: {e}\n")

    print("\nAll downloads complete. Run the optimizer:\n")
    print("  TQDM_DISABLE=1 PYTHONPATH=src python src/optimization/multi_tf_optimizer.py\n")
    print("Or with tee to capture output:\n")
    print("  TQDM_DISABLE=1 PYTHONPATH=src python src/optimization/multi_tf_optimizer.py 2>&1 | tee results/multi_tf_optimization/optimizer_run.log\n")


if __name__ == "__main__":
    main()
