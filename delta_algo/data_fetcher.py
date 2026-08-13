"""
Delta Exchange Historical Candle Downloader.
Fetches 1-hour OHLCV candles directly from Delta Exchange API with pagination,
rate limit handling, schema validation, and local disk caching.
"""

import os
import time
import requests
import pandas as pd
from datetime import datetime, timezone
from typing import Optional


class DeltaDataFetcher:
    """Downloader and preprocessor for Delta Exchange historical candlestick data."""

    def __init__(self, base_url: str = "https://api.delta.exchange", data_dir: str = "data"):
        self.base_url = base_url.rstrip("/")
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)

    def get_candle_cache_path(self, symbol: str, resolution: str) -> str:
        safe_symbol = symbol.replace("/", "_").replace("-", "_")
        return os.path.join(self.data_dir, f"candles_{safe_symbol}_{resolution}.csv")

    def fetch_candles(
        self,
        symbol: str = "ETHUSDT",
        resolution: str = "1h",
        days: int = 180,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        force_download: bool = False,
    ) -> pd.DataFrame:
        """
        Download historical candles from Delta Exchange API.
        
        Parameters:
            symbol: Contract symbol (e.g. 'ETHUSDT')
            resolution: Timeframe resolution (e.g. '1h')
            days: Historical lookback in days if start_ts is not provided
            start_ts: Custom start Unix timestamp in seconds
            end_ts: Custom end Unix timestamp in seconds
            force_download: If True, bypass cache and re-download from API
        """
        cache_path = self.get_candle_cache_path(symbol, resolution)
        
        if not force_download and os.path.exists(cache_path):
            print(f"[*] Loading cached {symbol} {resolution} candles from: {cache_path}")
            df = pd.read_csv(cache_path)
            if "datetime" in df.columns:
                df["datetime"] = pd.to_datetime(df["datetime"])
            return df

        now_ts = int(time.time())
        if end_ts is None:
            end_ts = now_ts
        if start_ts is None:
            start_ts = end_ts - (days * 86400)

        print(f"[*] Downloading {symbol} {resolution} candles from Delta Exchange API...")
        print(f"    From: {datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print(f"    To:   {datetime.fromtimestamp(end_ts, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")

        all_candles = []
        chunk_window = 86400 * 20  # Fetch in 20-day chunks (~480 candles)
        cur_start = start_ts

        endpoint = f"{self.base_url}/v2/history/candles"

        while cur_start < end_ts:
            cur_end = min(cur_start + chunk_window, end_ts)
            params = {
                "resolution": resolution,
                "symbol": symbol,
                "start": cur_start,
                "end": cur_end,
            }

            try:
                response = requests.get(endpoint, params=params, timeout=15)
                if response.status_code == 200:
                    data = response.json()
                    candles = data.get("result", [])
                    if candles:
                        all_candles.extend(candles)
                else:
                    print(f"[!] Warning: API returned status {response.status_code} for range {cur_start}-{cur_end}")
            except Exception as e:
                print(f"[!] Error fetching batch {cur_start}-{cur_end}: {e}")

            cur_start = cur_end + 1
            time.sleep(0.1)  # Respect API rate limits

        if not all_candles:
            raise ValueError(f"No candle data returned from Delta Exchange for symbol '{symbol}'. Check symbol name.")

        df = pd.DataFrame(all_candles)
        # Delta schema: time, open, high, low, close, volume
        # Ensure correct column naming and data types
        df = df.rename(columns={"time": "timestamp"})
        df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = df[col].astype(float)

        df["datetime"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
        
        # Save cache
        df.to_csv(cache_path, index=False)
        print(f"[+] Successfully downloaded & cached {len(df)} candles to: {cache_path}")
        return df
