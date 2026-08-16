import os
import sys
import pandas as pd

_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_DIR, ".."))
from common.market_data import MarketDataLoader
from common.config import PlatformConfig

loader = MarketDataLoader('data')
cfg = PlatformConfig(
    platform="BINANCE_FUTURES", symbol="ETHUSDT", resolution="1m",
    start_date="2026-08-13", end_date="2026-08-15"
)
print("Fetching 1m delta...")
df_new = loader.load_ohlcv(cfg, quiet=False, reset_cache=True)
print(f"Fetched {len(df_new)} new candles.")

cache_path = 'data/candles_futures_binance_futures_ETHUSDT_1m.csv'
df_old = pd.read_csv(cache_path)
df_old['timestamp'] = df_old['timestamp'].astype(int)
df_new['timestamp'] = df_new['timestamp'].astype(int)

df_combined = pd.concat([df_old, df_new]).drop_duplicates(subset=['timestamp']).sort_values('timestamp')
df_combined.to_csv(cache_path, index=False)
print(f"Updated cache: {len(df_combined)} total candles.")
