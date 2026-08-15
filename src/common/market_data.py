"""
Market Data Client & Validator.
Fetches historical OHLCV candlestick data from Binance USD-M Futures (fapi.binance.com) REST API,
applies resampling for resolutions like 3h, 2h, 6h from 1h base data,
wraps operations in tqdm progress bars, handles local disk caching and market-cache reset operations,
and supports quiet mode and progress callbacks for seamless Rich Live terminal dashboard integration.
"""

import os
import glob
import time
from datetime import datetime, timezone
from typing import Optional, List, Callable, Dict, Any
import requests
import pandas as pd
from tqdm import tqdm

from common.config import PlatformConfig
from common.utils import parse_datetime_to_ts, setup_logger

logger = setup_logger("MarketData")


class MarketDataLoader:
    """Historical OHLCV fetcher, resampler, and data validator for Binance Futures & Delta Exchange."""

    TIMEFRAME_MINUTES = {
        "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
        "1h": 60, "2h": 120, "3h": 180, "4h": 240, "6h": 360, "12h": 720, "1d": 1440
    }

    def __init__(self, data_dir: str = "data"):
        self.data_dir = data_dir
        os.makedirs(self.data_dir, exist_ok=True)

    def get_cache_filename(self, symbol: str, resolution: str, platform: str) -> str:
        safe_symbol = symbol.replace("/", "_").replace("-", "_")
        return os.path.join(self.data_dir, f"candles_futures_{platform.lower()}_{safe_symbol}_{resolution}.csv")

    def clear_market_cache(self, cfg: PlatformConfig) -> List[str]:
        """Delete ONLY generated/downloaded market-data cache files in data_dir matching symbol/platform."""
        safe_symbol = cfg.symbol.replace("/", "_").replace("-", "_")
        pattern = os.path.join(self.data_dir, f"candles_*{safe_symbol}*.csv")
        deleted_files = []

        files_to_delete = glob.glob(pattern)
        if not files_to_delete:
            cache_file = self.get_cache_filename(cfg.symbol, cfg.resolution, cfg.platform)
            if os.path.exists(cache_file):
                files_to_delete.append(cache_file)

        print("\n" + "=" * 70)
        print("                  CLEAR_CACHE=true MARKET DATA CLEANUP")
        print("=" * 70)

        if files_to_delete:
            print("Deleting market data cache files:")
            for fpath in tqdm(files_to_delete, desc="Deleting market data cache", unit="file"):
                if os.path.exists(fpath):
                    os.remove(fpath)
                    deleted_files.append(fpath)
                    print(f"  [-] Removed: {fpath}")
        else:
            print("  [i] No market data cache files found to delete.")

        print("\nPreserving non-cache assets:")
        print("  [+] Preserved: results/ (Backtest, Robustness, & Forward outputs)")
        print("  [+] Preserved: logs/forward_state.json (Forward account & timer state)")
        print("  [+] Preserved: results/tracker.csv (Global experiment performance tracker)")
        print("  [+] Preserved: src/ & config (Strategy logic and code files)")
        print("=" * 70 + "\n")

        return deleted_files

    def load_ohlcv(
        self,
        cfg: PlatformConfig,
        reset_cache: bool = False,
        quiet: bool = False,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None
    ) -> pd.DataFrame:
        cache_path = self.get_cache_filename(cfg.symbol, cfg.resolution, cfg.platform)

        # Clear old generic/spot cache files if present
        old_spot_cache = os.path.join(self.data_dir, f"candles_binance_{cfg.symbol}_{cfg.resolution}.csv")
        if os.path.exists(old_spot_cache):
            if not quiet:
                logger.info(f"Removing legacy spot cache file: {old_spot_cache}")
            os.remove(old_spot_cache)

        if reset_cache and os.path.exists(cache_path):
            if not quiet:
                logger.info(f"RESET_CACHE=True: Deleting cached file {cache_path}")
            os.remove(cache_path)

        if os.path.exists(cache_path):
            if not quiet:
                logger.info(f"Loading cached market data from: {cache_path}")
            df = pd.read_csv(cache_path)
            if "datetime" in df.columns:
                df["datetime"] = pd.to_datetime(df["datetime"])
            return self.validate_ohlcv(df, cfg.resolution, quiet=quiet)

        # Check if requested resolution needs resampling from 1h
        fetch_res = cfg.resolution
        needs_resample = cfg.resolution in ["2h", "3h", "6h", "12h"]
        if needs_resample:
            fetch_res = "1h"

        if not quiet:
            logger.info(f"Fetching Binance USD-M Futures {cfg.symbol} ({fetch_res}) from fapi.binance.com...")

        if cfg.platform.upper() in ["BINANCE", "BINANCE_FUTURES"]:
            df_base = self._fetch_binance_futures(cfg, resolution=fetch_res, quiet=quiet, progress_callback=progress_callback)
        else:
            df_base = self._fetch_delta(cfg, resolution=fetch_res, quiet=quiet, progress_callback=progress_callback)

        if needs_resample:
            if not quiet:
                logger.info(f"Resampling 1h Futures candles to {cfg.resolution} candles (UTC-aligned)...")
            df = self.resample_ohlcv(df_base, cfg.resolution)
        else:
            df = df_base

        df = self.validate_ohlcv(df, cfg.resolution, quiet=quiet)
        df.to_csv(cache_path, index=False)
        if not quiet:
            logger.info(f"Cached {len(df)} validated Futures candles to: {cache_path}")
        return df

    def resample_ohlcv(self, df_1h: pd.DataFrame, target_resolution: str) -> pd.DataFrame:
        """Resample 1h OHLCV DataFrame into target resolution (e.g. 3h) with exact count validation."""
        tf_mins = self.TIMEFRAME_MINUTES.get(target_resolution, 180)
        rule = f"{tf_mins}min"

        df = df_1h.copy()
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
        df = df.set_index("datetime").sort_index()

        resampled = (
            df.resample(rule, closed="left", label="left")
            .agg({
                "timestamp": "first",
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            })
            .dropna()
            .reset_index()
        )

        resampled["timestamp"] = resampled["timestamp"].astype(int)
        return resampled

    def download_binance_klines(
        self,
        symbol: str,
        interval: str,
        days: int = 1,
        quiet: bool = True,
    ) -> pd.DataFrame:
        """Public wrapper for feed.py backfill: download recent Binance Futures klines by symbol/interval/days.

        Bridges the gap between feed.py's simple (symbol, interval, days) call signature
        and the internal _fetch_binance_futures(cfg, ...) which expects a PlatformConfig.
        """
        from common.config import PlatformConfig  # local import to avoid circular at module level
        cfg = PlatformConfig()
        cfg.symbol = symbol.upper()
        cfg.resolution = interval
        cfg.platform = "BINANCE_FUTURES"
        cfg.start_date = None
        cfg.end_date = None
        cfg.days = days
        return self._fetch_binance_futures(cfg, resolution=interval, quiet=quiet)

    def _fetch_binance_futures(
        self,
        cfg: PlatformConfig,
        resolution: Optional[str] = None,
        quiet: bool = False,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None
    ) -> pd.DataFrame:
        symbol = cfg.symbol.upper()
        res = resolution or cfg.resolution

        end_ts = int(time.time())
        if cfg.end_date:
            end_ts = parse_datetime_to_ts(cfg.end_date)
        if cfg.start_date:
            start_ts = parse_datetime_to_ts(cfg.start_date)
        elif cfg.days:
            fetch_days = 10 if (res == "1m" and cfg.days == 60) else cfg.days
            start_ts = end_ts - (fetch_days * 86400)
        else:
            start_ts = end_ts - (365 * 86400)

        # Binance USD-M Perpetual Futures REST API Endpoint
        url = "https://fapi.binance.com/fapi/v1/klines"
        tf_mins = self.TIMEFRAME_MINUTES.get(res, 60)
        step_ms = tf_mins * 60 * 1000 * 1000

        cur_start_ms = start_ts * 1000
        end_ms = end_ts * 1000

        all_klines = []
        total_steps = max(1, int((end_ms - cur_start_ms) / step_ms) + 1)
        step_count = 0
        start_time = time.time()

        use_tqdm = not quiet and progress_callback is None
        pbar = tqdm(total=total_steps, desc=f"Downloading Binance USD-M Futures {symbol} {res}", unit="batch", disable=not use_tqdm)

        try:
            while cur_start_ms < end_ms:
                params = {
                    "symbol": symbol,
                    "interval": res,
                    "startTime": cur_start_ms,
                    "endTime": end_ms,
                    "limit": 1000,
                }
                try:
                    response = requests.get(url, params=params, timeout=15)
                    if response.status_code == 200:
                        data = response.json()
                        if not data:
                            break
                        all_klines.extend(data)
                        cur_start_ms = data[-1][0] + 1
                    else:
                        if not quiet:
                            logger.warning(f"Binance Futures API returned status {response.status_code}")
                        break
                except Exception as e:
                    if not quiet:
                        logger.error(f"Error fetching Binance Futures batch: {e}")
                    break

                step_count += 1
                if use_tqdm:
                    pbar.update(1)

                if progress_callback:
                    now = time.time()
                    elapsed = now - start_time
                    speed = step_count / max(elapsed, 0.1)
                    rem_steps = max(0, total_steps - step_count)
                    eta = rem_steps / speed if speed > 0 else 0
                    pct = min(100.0, (step_count / total_steps) * 100.0)

                    progress_callback({
                        "stage": f"Downloading Binance Futures {symbol} ({res})",
                        "current": step_count,
                        "total": total_steps,
                        "pct": pct,
                        "elapsed": f"{int(elapsed)}s",
                        "eta": f"{int(eta)}s",
                        "speed": f"{speed:.1f} batch/s"
                    })

                time.sleep(0.05)
        finally:
            pbar.close()

        if not all_klines:
            raise ValueError(f"No candle data returned from Binance Futures for {symbol} ({res})")

        records = []
        for k in all_klines:
            records.append({
                "timestamp": int(k[0] / 1000),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            })

        df = pd.DataFrame(records)
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
        return df

    def _fetch_delta(
        self,
        cfg: PlatformConfig,
        resolution: Optional[str] = None,
        quiet: bool = False,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None
    ) -> pd.DataFrame:
        symbol = cfg.symbol.upper()
        res = resolution or cfg.resolution

        end_ts = int(time.time())
        if cfg.end_date:
            end_ts = parse_datetime_to_ts(cfg.end_date)
        if cfg.start_date:
            start_ts = parse_datetime_to_ts(cfg.start_date)
        elif cfg.days:
            start_ts = end_ts - (cfg.days * 86400)
        else:
            start_ts = end_ts - (365 * 86400)

        url = "https://api.delta.exchange/v2/history/candles"
        chunk_seconds = 86400 * 20
        cur_start = start_ts

        all_candles = []
        total_chunks = int((end_ts - start_ts) / chunk_seconds) + 1
        chunk_count = 0
        start_time = time.time()

        use_tqdm = not quiet and progress_callback is None
        pbar = tqdm(total=total_chunks, desc=f"Downloading Delta {symbol} {res}", unit="chunk", disable=not use_tqdm)

        try:
            while cur_start < end_ts:
                cur_end = min(cur_start + chunk_seconds, end_ts)
                params = {
                    "resolution": res,
                    "symbol": symbol,
                    "start": cur_start,
                    "end": cur_end,
                }
                try:
                    response = requests.get(url, params=params, timeout=15)
                    if response.status_code == 200:
                        data = response.json().get("result", [])
                        if data:
                            all_candles.extend(data)
                except Exception as e:
                    if not quiet:
                        logger.error(f"Error fetching Delta batch: {e}")
                cur_start = cur_end + 1
                chunk_count += 1

                if use_tqdm:
                    pbar.update(1)

                if progress_callback:
                    now = time.time()
                    elapsed = now - start_time
                    speed = chunk_count / max(elapsed, 0.1)
                    rem_chunks = max(0, total_chunks - chunk_count)
                    eta = rem_chunks / speed if speed > 0 else 0
                    pct = min(100.0, (chunk_count / total_chunks) * 100.0)

                    progress_callback({
                        "stage": f"Downloading Delta {symbol} ({res})",
                        "current": chunk_count,
                        "total": total_chunks,
                        "pct": pct,
                        "elapsed": f"{int(elapsed)}s",
                        "eta": f"{int(eta)}s",
                        "speed": f"{speed:.1f} chunk/s"
                    })

                time.sleep(0.05)
        finally:
            pbar.close()

        if not all_candles:
            raise ValueError(f"No candle data returned from Delta Exchange for {symbol} ({res})")

        df = pd.DataFrame(all_candles)
        df = df.rename(columns={"time": "timestamp"})
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)
        df["datetime"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
        return df

    def validate_ohlcv(self, df: pd.DataFrame, resolution: str, quiet: bool = False) -> pd.DataFrame:
        if df.empty:
            raise ValueError("Candle DataFrame is empty.")

        initial_len = len(df)
        df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
        dropped_dups = initial_len - len(df)
        if dropped_dups > 0 and not quiet:
            logger.info(f"Validation: Removed {dropped_dups} duplicate timestamp rows.")

        null_counts = df[["open", "high", "low", "close", "volume"]].isnull().sum().sum()
        if null_counts > 0:
            if not quiet:
                logger.warning(f"Validation: Found {null_counts} NaN values. Forward-filling...")
            df = df.ffill().bfill()

        tf_mins = self.TIMEFRAME_MINUTES.get(resolution, 180)
        expected_diff = tf_mins * 60
        diffs = df["timestamp"].diff().dropna()
        gaps = diffs[diffs > expected_diff]

        if not gaps.empty and not quiet:
            logger.info(f"Validation: Detected {len(gaps)} potential timestamp gaps (> {tf_mins} mins).")

        return df
