"""
Live Binance USD-M Futures Market Data Feed.
Streams live price ticks via WebSocket (wss://fstream.binance.com) with REST fallback.
Performs single historical warm-up download at startup, tracks feed speed, 24h ticker data,
latency, evaluates 3h candle closures without re-downloading history, handles auto WS reconnect,
executes REST backfill ONLY for missing gap upon recovery, and enforces thread-safe concurrency guards.
"""

import time
import threading
import json
import collections
from datetime import datetime, timezone, timedelta
from typing import Callable, Optional, Dict, Any, List, Tuple
import requests
import pandas as pd
import websocket  # websocket-client required; add websocket-client>=1.3.0 to requirements.txt

from common.config import PlatformConfig
from common.market_data import MarketDataLoader
from common.utils import setup_logger, resolution_to_seconds

logger = setup_logger("MarketFeed")
IST = timezone(timedelta(hours=5, minutes=30))


class LiveMarketFeed:
    """Live streaming market feed for Binance USD-M Perpetual Futures."""

    WS_URL_TEMPLATE = "wss://fstream.binance.com/ws/{symbol}@ticker"  # symbol injected at connect time
    REST_TICKER_URL = "https://fapi.binance.com/fapi/v1/ticker/24hr"
    REST_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"

    def __init__(self, symbol: str = "ETHUSDT", resolution: str = "3h", data_dir: str = "data"):
        self.symbol = symbol.upper()
        self.resolution = resolution
        self.data_loader = MarketDataLoader(data_dir=data_dir)

        self.current_price: float = 0.0
        self.bid_price: float = 0.0
        self.ask_price: float = 0.0
        self.price_change_pct_24h: float = 0.0

        self.last_update_ts: float = 0.0
        self.latency_ms: float = 0.0
        self.ws_connected: bool = False
        self.reconnect_count: int = 0
        self.disconnect_count: int = 0
        self.recovered_candles_count: int = 0
        self.is_running: bool = False
        self.active_progress_task: Optional[Dict[str, Any]] = None

        self.bytes_received: int = 0
        self._byte_samples: collections.deque = collections.deque(maxlen=300)
        self.last_bytes_count: int = 0
        self.last_speed_calc_time: float = time.time()
        self.current_feed_speed_bytes: float = 0.0
        # Note: last_update_ts declared once above — do NOT redeclare here
        self.last_message_ts: float = 0.0
        self.last_market_message_monotonic: float = 0.0
        self.feed_initialized: bool = False
        self.feed_healthy: bool = False
        self.STALE_TIMEOUT: float = 15.0
        self._ws_app = None

        # Diagnostics & Call Counters
        self.warmup_calls: int = 0
        self.full_history_download_calls: int = 0
        self.backfill_calls: int = 0
        self.websocket_connects: int = 0
        self.websocket_disconnects: int = 0
        self.watchdog_disconnects: int = 0
        self.genuine_ws_errors: int = 0
        self.reconnect_attempts: int = 0
        self.successful_reconnects: int = 0
        self.processed_closed_candles: int = 0
        self.has_connected_once: bool = False
        self.reconnect_in_progress: bool = False

        # Concurrency Guard Lock
        self._download_lock = threading.Lock()
        self.is_downloading_or_backfilling: bool = False

        self._ws_thread: Optional[threading.Thread] = None
        self._rest_thread: Optional[threading.Thread] = None
        self._watchdog_thread: Optional[threading.Thread] = None
        # Set when _ws_loop exits unexpectedly (not via stop_feed) so callers can detect failure
        self.ws_thread_died: threading.Event = threading.Event()
        self._on_tick_callbacks: List[Callable[[float], None]] = []
        self._on_3h_close_callbacks: List[Callable[[pd.DataFrame, Dict[str, Any], str], None]] = []

        self.df_3h: pd.DataFrame = pd.DataFrame()
        self.last_closed_3h_ts: int = 0

    def _progress_cb(self, info: Dict[str, Any]):
        self.active_progress_task = info

    def add_tick_callback(self, callback: Callable[[float], None]):
        self._on_tick_callbacks.append(callback)

    def add_3h_close_callback(self, callback: Callable[[pd.DataFrame, Dict[str, Any], str], None]):
        self._on_3h_close_callbacks.append(callback)

    def get_diagnostics_dict(self) -> Dict[str, int]:
        return {
            "warmup_calls": self.warmup_calls,
            "full_history_download_calls": self.full_history_download_calls,
            "backfill_calls": self.backfill_calls,
            "websocket_connects": self.websocket_connects,
            "websocket_disconnects": self.websocket_disconnects,
            "watchdog_disconnects": self.watchdog_disconnects,
            "genuine_ws_errors": self.genuine_ws_errors,
            "reconnect_attempts": self.reconnect_attempts,
            "successful_reconnects": self.successful_reconnects,
            "processed_closed_candles": self.processed_closed_candles
        }

    def log_diagnostics(self):
        logger.info(
            f"Diagnostics: warmup_calls={self.warmup_calls}, "
            f"full_history_download_calls={self.full_history_download_calls}, "
            f"backfill_calls={self.backfill_calls}, "
            f"websocket_connects={self.websocket_connects}, "
            f"processed_closed_candles={self.processed_closed_candles}"
        )

    def warm_up_historical_data(self, days: int = 60) -> pd.DataFrame:
        """Download historical 1h candles, resample to 3h, and set initial closed candle timestamp ONCE per startup."""
        with self._download_lock:
            if self.is_downloading_or_backfilling:
                logger.warning("Warm-up skipped: Another download/backfill task is currently active.")
                return self.df_3h

            self.is_downloading_or_backfilling = True
            self.warmup_calls += 1
            self.full_history_download_calls += 1

        self.log_diagnostics()

        try:
            warmup_days = 10 if (self.resolution == "1m" and days == 60) else days
            cfg = PlatformConfig(
                platform="BINANCE_FUTURES",
                symbol=self.symbol,
                resolution=self.resolution,
                days=warmup_days,
                start_date=None,
                end_date=None
            )
            self.active_progress_task = {"stage": "Warming up market data (60 days)", "current": 0, "total": 100, "pct": 0.0, "elapsed": "0s", "eta": "0s", "speed": "0 batch/s"}
            self.df_3h = self.data_loader.load_ohlcv(cfg, quiet=True, progress_callback=self._progress_cb)

            if not self.df_3h.empty:
                self.last_closed_3h_ts = int(self.df_3h.iloc[-1]["timestamp"])
                self.current_price = float(self.df_3h.iloc[-1]["close"])
                self.bid_price = self.current_price * 0.9999
                self.ask_price = self.current_price * 1.0001
        finally:
            self.active_progress_task = None
            with self._download_lock:
                self.is_downloading_or_backfilling = False

        return self.df_3h

    def fetch_latest_ticker(self) -> float:
        """Fetch latest REST 24hr ticker for bid/ask, 24h change, and price fallback."""
        try:
            start_req = time.time()
            resp = requests.get(self.REST_TICKER_URL, params={"symbol": self.symbol}, timeout=5)
            self.latency_ms = (time.time() - start_req) * 1000.0
            if resp.status_code == 200:
                data = resp.json()
                b_count = len(resp.content)
                self.bytes_received += b_count
                self._byte_samples.append((time.time(), self.bytes_received))
                price = float(data.get("lastPrice", data.get("price", 0.0)))
                if price > 0:
                    self.current_price = price
                    self.bid_price = float(data.get("bidPrice", price * 0.9999))
                    self.ask_price = float(data.get("askPrice", price * 1.0001))
                    self.price_change_pct_24h = float(data.get("priceChangePercent", 0.0))
                    self.last_update_ts = time.time()
                    self.last_market_message_monotonic = time.monotonic()
                    self.feed_initialized = True
                    self.feed_healthy = True
                    return price
        except Exception as e:
            logger.debug(f"[fetch_latest_ticker] REST request failed: {e}")

        if self.current_price > 0:
            self.bid_price = self.current_price * 0.9999
            self.ask_price = self.current_price * 1.0001
        return self.current_price

    # Rolling window for the throughput readout. This MUST be comfortably longer than the
    # gap between arrivals: the REST ticker fallback refreshes about every 6s, so a 5s
    # window pruned itself down to a single sample between polls, hit the `>= 2` guard and
    # reported "0 B/s" continuously even while the feed was perfectly healthy.
    FEED_SPEED_WINDOW_SEC = 30.0

    def get_feed_speed_str(self) -> str:
        """Compute feed transfer speed using rolling window with automatic units (B/s, KB/s, MB/s)."""
        now = time.time()
        while self._byte_samples and (now - self._byte_samples[0][0] > self.FEED_SPEED_WINDOW_SEC):
            self._byte_samples.popleft()

        spd = 0.0
        if len(self._byte_samples) >= 2:
            dt = now - self._byte_samples[0][0]
            if dt >= 0.5:
                bytes_delta = self.bytes_received - self._byte_samples[0][1]
                spd = max(0.0, bytes_delta / dt)
            else:
                spd = getattr(self, "_last_calculated_spd", 0.0)
        elif self._byte_samples:
            # Exactly one arrival inside the window: still a real, measurable rate —
            # bytes since that sample over the time since it landed.
            dt = max(1e-6, now - self._byte_samples[0][0])
            if dt >= 0.5:
                spd = max(0.0, (self.bytes_received - self._byte_samples[0][1]) / dt)
            else:
                spd = getattr(self, "_last_calculated_spd", 0.0)

        self._last_calculated_spd = spd

        if spd >= 1024 * 1024:
            return f"{spd / (1024 * 1024):.1f} MB/s"
        elif spd >= 1024:
            return f"{spd / 1024:.1f} KB/s"
        else:
            return f"{spd:.0f} B/s"

    def _get_fetch_interval(self) -> Tuple[str, bool]:
        """Determine REST interval to fetch and whether resampling is needed."""
        res = str(self.resolution).lower().strip()
        if res in ["2h", "3h", "6h", "12h"]:
            return "1h", True
        return res, False

    def backfill_missing_outage_candles(self, last_known_ts: int = 0) -> Tuple[pd.DataFrame, int]:
        """Fetch closed candles missing during network outages and merge seamlessly."""
        with self._download_lock:
            if self.is_downloading_or_backfilling:
                return self.df_3h, 0
            self.is_downloading_or_backfilling = True

        self.backfill_calls += 1
        logger.info(f"[+] [MarketData]: Backfilling outage candles for {self.symbol} ({self.resolution})...")
        new_candles_count = 0
        try:
            if not self.df_3h.empty:
                # Use last_closed_3h_ts which is already maintained as a correct UNIX-seconds timestamp.
                # Do NOT divide df_3h["timestamp"] by 1000 — df_3h timestamps are in seconds, not ms.
                last_cached_ts = self.last_closed_3h_ts if self.last_closed_3h_ts > 0 else int(self.df_3h["timestamp"].max())
                now_ts = int(time.time())
                interval_sec = resolution_to_seconds(self.resolution)
                missing_sec = now_ts - last_cached_ts
                if missing_sec > interval_sec:
                    missing_candles = int(missing_sec / interval_sec)
                    logger.info(f"[+] Outage detected: {missing_candles} candles missing. Downloading...")
                    fetch_res, needs_resample = self._get_fetch_interval()

                    new_raw_df = self.data_loader.download_binance_klines(
                        symbol=self.symbol,
                        interval=fetch_res,
                        days=min(30, max(1, int(missing_sec / 86400) + 1))
                    )

                    if needs_resample:
                        new_df = self.data_loader.resample_ohlcv(new_raw_df, self.resolution)
                    else:
                        new_df = new_raw_df

                    if not new_df.empty:
                        # Drop currently forming candle (not fully closed)
                        now_ts = int(time.time())
                        interval_sec = resolution_to_seconds(self.resolution)
                        new_df = new_df[new_df["timestamp"] + interval_sec <= now_ts]
                        
                        merged = pd.concat([self.df_3h, new_df]).drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
                        new_candles_count = len(merged) - len(self.df_3h)
                        self.df_3h = merged
                        self.recovered_candles_count += new_candles_count
                        logger.info(f"[+] Backfill complete. Merged {new_candles_count} new closed candles.")
        except Exception as e:
            logger.error(f"[-] Backfill failed: {e}")
        finally:
            with self._download_lock:
                self.is_downloading_or_backfilling = False

        return self.df_3h, new_candles_count

    def check_for_new_closed_3h_candle(self) -> Tuple[pd.DataFrame, int]:
        """Fetch latest candle data and check if a new 3h candle has closed."""
        with self._download_lock:
            if self.is_downloading_or_backfilling:
                return self.df_3h, 0

        fetch_res, needs_resample = self._get_fetch_interval()
        raw_df = self.data_loader.download_binance_klines(symbol=self.symbol, interval=fetch_res, days=1)
        if raw_df.empty:
            return self.df_3h, 0

        if needs_resample:
            new_df = self.data_loader.resample_ohlcv(raw_df, self.resolution)
        else:
            new_df = raw_df

        if new_df.empty:
            return self.df_3h, 0

        # Drop currently forming candle (not fully closed)
        now_ts = int(time.time())
        interval_sec = resolution_to_seconds(self.resolution)
        new_df = new_df[new_df["timestamp"] + interval_sec <= now_ts]

        old_len = len(self.df_3h)
        merged = pd.concat([self.df_3h, new_df]).drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)

        new_closed_count = len(merged) - old_len
        if new_closed_count > 0:
            self.df_3h = merged
            self.processed_closed_candles += new_closed_count

        return self.df_3h, new_closed_count

    def start_feed(self):
        """Start WebSocket and REST polling fallback background threads."""
        self.is_running = True
        self.feed_initialized = False
        self.feed_healthy = False
        self.last_market_message_monotonic = 0.0
        self.fetch_latest_ticker()

        if self._ws_thread is None or not self._ws_thread.is_alive():
            self._ws_thread = threading.Thread(target=self._ws_loop, daemon=True)
            self._ws_thread.start()

        if self._rest_thread is None or not self._rest_thread.is_alive():
            self._rest_thread = threading.Thread(target=self._rest_polling_loop, daemon=True)
            self._rest_thread.start()
        
        if self._watchdog_thread is None or not self._watchdog_thread.is_alive():
            self._watchdog_thread = threading.Thread(target=self._watchdog_loop, daemon=True)
            self._watchdog_thread.start()

    def stop_feed(self):
        self.is_running = False
        self.ws_connected = False
        if self._ws_app:
            try:
                self._ws_app.keep_running = False
                if self._ws_app.sock:
                    self._ws_app.sock.close()
                self._ws_app.close()
            except Exception:
                pass  # Best-effort socket close on shutdown — ignore errors

    def trigger_forced_disconnect(self):
        """Forced disconnect helper for testing LIVE -> DISCONNECTED -> RECONNECTING -> BACKFILL -> LIVE."""
        self.ws_connected = False
        self.feed_healthy = False
        self.disconnect_count += 1
        self.websocket_disconnects += 1
        if self._ws_app:
            try:
                self._ws_app.keep_running = False
                if self._ws_app.sock:
                    self._ws_app.sock.close()
                self._ws_app.close()
            except Exception:
                pass  # Best-effort socket close on forced disconnect

    def _watchdog_loop(self):
        """Independent periodic watchdog running every 0.5s."""
        while self.is_running:
            if not self.feed_initialized:
                time.sleep(0.5)
                continue
                
            data_age = time.monotonic() - self.last_market_message_monotonic
            if data_age > self.STALE_TIMEOUT:
                if self.feed_healthy:
                    self.feed_healthy = False
                    self.ws_connected = False
                    self.disconnect_count += 1
                    self.websocket_disconnects += 1
                    self.watchdog_disconnects += 1
                    logger.warning(
                        f"[Watchdog] Stale market feed detected (data_age={data_age:.1f}s > {self.STALE_TIMEOUT}s). "
                        f"watchdog_disconnects={self.watchdog_disconnects}. Closing socket."
                    )
                    if self._ws_app:
                        try:
                            self._ws_app.keep_running = False
                            if self._ws_app.sock:
                                self._ws_app.sock.close()
                            self._ws_app.close()
                        except Exception:
                            pass  # Best-effort socket close from watchdog
            else:
                self.feed_healthy = True
            time.sleep(0.5)

    def is_feed_healthy(self) -> bool:
        """Check if feed is initialized and has received a fresh message within STALE_TIMEOUT."""
        if not self.feed_initialized and not self.last_market_message_monotonic:
            return False
        if not self.last_market_message_monotonic:
            return False
        data_age = time.monotonic() - self.last_market_message_monotonic
        return self.feed_healthy and (data_age <= self.STALE_TIMEOUT)

    def is_feed_stale(self) -> bool:
        """Check if feed hasn't received a message within STALE_TIMEOUT."""
        return not self.is_feed_healthy()

    def _ws_loop(self):
        backoff = 1.0
        # Build WS URL from symbol — do NOT use the hardcoded WS_URL_TEMPLATE class attr
        ws_url = self.WS_URL_TEMPLATE.format(symbol=self.symbol.lower())
        is_first_attempt = True

        while self.is_running:
            if not is_first_attempt or self.has_connected_once:
                self.reconnect_in_progress = True
                self.reconnect_attempts += 1
                self.reconnect_count = self.reconnect_attempts
                logger.info(f"[*] Launching WebSocket reconnect attempt #{self.reconnect_attempts} after {backoff:.1f}s backoff...")
                time.sleep(backoff)
                backoff = min(backoff * 2.0, 15.0)

            is_first_attempt = False

            try:
                def on_message(ws, message):
                    try:
                        b_cnt = len(message.encode("utf-8")) if isinstance(message, str) else len(message)
                        self.bytes_received += b_cnt
                        self._byte_samples.append((time.time(), self.bytes_received))
                        self.last_message_ts = time.time()
                        self.last_market_message_monotonic = time.monotonic()
                        self.feed_initialized = True
                        self.feed_healthy = True
                        self.ws_connected = True
                        self.reconnect_in_progress = False
                        
                        data = json.loads(message)
                        event_time = data.get("E")
                        if event_time:
                            self.latency_ms = max(0.0, (time.time() * 1000.0) - float(event_time))
                        else:
                            self.latency_ms = 12.0

                        price_str = data.get("c")
                        if price_str:
                            price = float(price_str)
                            self.current_price = price
                            self.bid_price = price * 0.9999
                            self.ask_price = price * 1.0001
                            self.last_update_ts = time.time()
                            for cb in self._on_tick_callbacks:
                                cb(price)
                    except Exception:
                        pass

                def on_open(ws):
                    nonlocal backoff
                    self.ws_connected = True
                    self.websocket_connects += 1
                    if self.has_connected_once or self.websocket_connects > 1:
                        self.successful_reconnects += 1
                    self.has_connected_once = True
                    self.reconnect_in_progress = False
                    backoff = 1.0
                    logger.info(f"[+] WebSocket Stream connected ({self.symbol}). Total connects: {self.websocket_connects}")

                def on_close(ws, close_status_code, close_msg):
                    if self.ws_connected:
                        self.disconnect_count += 1
                        self.websocket_disconnects += 1
                    self.ws_connected = False
                    if self.has_connected_once:
                        self.reconnect_in_progress = True
                    logger.info(f"[-] WebSocket closed (code={close_status_code}, msg={close_msg})")

                def on_error(ws, error):
                    self.genuine_ws_errors += 1
                    if self.ws_connected:
                        self.disconnect_count += 1
                        self.websocket_disconnects += 1
                    self.ws_connected = False
                    if self.has_connected_once:
                        self.reconnect_in_progress = True
                    logger.error(f"[-] WebSocket error: {error}")

                self._ws_app = websocket.WebSocketApp(
                    ws_url,
                    header=["User-Agent: Mozilla/5.0"],
                    on_open=on_open,
                    on_message=on_message,
                    on_error=on_error,
                    on_close=on_close
                )
                self._ws_app.run_forever(ping_interval=15, ping_timeout=10)

            except Exception as e:
                self.genuine_ws_errors += 1
                if self.ws_connected:
                    self.disconnect_count += 1
                    self.websocket_disconnects += 1
                self.ws_connected = False
                if self.has_connected_once:
                    self.reconnect_in_progress = True
                logger.error(f"[-] Exception in ws_loop: {e}")
            finally:
                self._ws_app = None

            if not self.is_running:
                break

        # Signal that this thread has exited while feed is still supposed to be running
        if self.is_running:
            logger.error("[CRITICAL] WebSocket worker thread (_ws_loop) exited while feed.is_running=True. "
                         "Trading is paused. Check logs for the root cause.")
            self.ws_thread_died.set()
            self.feed_healthy = False

    def _rest_polling_loop(self):
        """Fallback REST ticker polling every 2 seconds if WS is down, and 3h candle boundary checker."""
        while self.is_running:
            now = time.time()
            if not self.ws_connected or (now - self.last_update_ts > 5.0):
                price = self.fetch_latest_ticker()
                if price > 0:
                    for cb in self._on_tick_callbacks:
                        cb(price)

            self._check_3h_candle_boundary()
            time.sleep(1.5)

    def _check_3h_candle_boundary(self):
        """Check if current UTC time has passed a 3h candle boundary and fetch ALL new closed candles.

        Fixes:
        - Fetches from last_closed_3h_ts+1 using startTime so no candles are missed (no limit=5 truncation).
        - Uses Binance close-time field (k[6]) to definitively exclude the currently-open candle.
        - Fires one strategy callback per new closed candle in chronological order.
        """
        now_ts = int(time.time())
        interval_sec = resolution_to_seconds(self.resolution)
        next_close_ts = self.last_closed_3h_ts + interval_sec
        if now_ts < next_close_ts:
            return
        if self.is_downloading_or_backfilling:
            return

        try:
            fetch_res, needs_resample = self._get_fetch_interval()
            fetch_interval_sec = resolution_to_seconds(fetch_res)
            # startTime: first candle AFTER our last known closed candle (in ms)
            start_ms = (self.last_closed_3h_ts + fetch_interval_sec) * 1000
            params = {
                "symbol":    self.symbol,
                "interval":  fetch_res,
                "startTime": start_ms,
                "limit":     1000,   # max allowed; catches large gaps without truncation
            }
            resp = requests.get(self.REST_KLINES_URL, params=params, timeout=5)
            if resp.status_code != 200:
                return
            klines = resp.json()
            if not klines:
                return

            records = []
            for k in klines:
                open_ts    = int(k[0] / 1000)
                close_ts_k = int(k[6] / 1000)   # Binance close-time: definitive "is this candle closed?"
                # Only include candles whose close-time is in the past
                if close_ts_k >= now_ts:
                    continue
                records.append({
                    "timestamp": open_ts,
                    "open":   float(k[1]),
                    "high":   float(k[2]),
                    "low":    float(k[3]),
                    "close":  float(k[4]),
                    "volume": float(k[5]),
                })

            if not records:
                return

            df_base = pd.DataFrame(records)
            df_base["datetime"] = pd.to_datetime(df_base["timestamp"], unit="s", utc=True)
            if needs_resample:
                df_recent = self.data_loader.resample_ohlcv(df_base, self.resolution)
            else:
                df_recent = df_base

            df_new = df_recent[df_recent["timestamp"] > self.last_closed_3h_ts].copy()

            if df_new.empty:
                return

            # Append all new candles to historical df (deduplicated)
            self.df_3h = pd.concat([self.df_3h, df_new], ignore_index=True)
            self.df_3h = (
                self.df_3h
                .drop_duplicates(subset=["timestamp"])
                .sort_values("timestamp")
                .reset_index(drop=True)
            )
            self.processed_closed_candles += len(df_new)

            # Fire one callback per closed candle in chronological order
            for _, row in df_new.sort_values("timestamp").iterrows():
                self.last_closed_3h_ts = int(row["timestamp"])
                closed_row = row.to_dict()
                for cb in self._on_3h_close_callbacks:
                    cb(self.df_3h, closed_row, "LIVE")

        except Exception as e:
            logger.warning(f"[_check_3h_candle_boundary] Exception: {e}")
