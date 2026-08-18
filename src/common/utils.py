"""
Utility functions for logging, timestamp handling, formatting, and file paths.
"""

import os
import sys
import logging
from typing import Optional
from datetime import datetime, timezone



_QUOTE_ASSETS = ("USDT", "USDC", "BUSD", "USD", "PERP")


def base_asset(symbol: str) -> str:
    """Base asset of a trading symbol, for display only (ETHUSDT -> ETH)."""
    up = (symbol or "").strip().upper()
    for quote in _QUOTE_ASSETS:
        if up.endswith(quote) and len(up) > len(quote):
            return up[: -len(quote)]
    return up or "UNITS"


def setup_logger(name: str = "ETH_Pipeline", log_file: Optional[str] = None, level: int = logging.INFO) -> logging.Logger:
    """Set up and configure a logger instance."""
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.handlers.clear()

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Console Handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # File Handler
    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        fh = logging.FileHandler(log_file)
        fh.setFormatter(formatter)
        logger.addHandler(fh)

    return logger


_disabled_stream_handlers = {}


def mute_console_loggers():
    """Remove StreamHandlers from all active loggers so sys.stdout is unpolluted for Rich Live dashboard."""
    global _disabled_stream_handlers
    _disabled_stream_handlers.clear()

    loggers = [logging.getLogger(name) for name in logging.root.manager.loggerDict]
    loggers.append(logging.getLogger())

    for lg in loggers:
        streams = [h for h in lg.handlers if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)]
        if streams:
            _disabled_stream_handlers[lg.name] = streams
            for s in streams:
                lg.removeHandler(s)


def unmute_console_loggers():
    """Restore StreamHandlers to loggers when Rich Live dashboard stops."""
    global _disabled_stream_handlers
    for name, streams in _disabled_stream_handlers.items():
        lg = logging.getLogger(name)
        for s in streams:
            if s not in lg.handlers:
                lg.addHandler(s)
    _disabled_stream_handlers.clear()

def parse_datetime_to_ts(dt_str: str) -> int:
    """Convert YYYY-MM-DD or YYYY-MM-DD HH:MM:SS string to UTC unix timestamp in seconds."""
    if len(dt_str) == 10:
        dt = datetime.strptime(dt_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    else:
        dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def format_currency(value: float) -> str:
    """Format float as currency string."""
    return f"${value:,.2f}"


def format_percent(value: float) -> str:
    """Format float as percentage string."""
    return f"{value:+.2f}%"


def resolution_to_seconds(res: str) -> int:
    """Convert resolution string (e.g. 1m, 3h, 1d) to seconds.

    Raises ValueError for unrecognized resolution strings to prevent silent misconfiguration.
    """
    res = str(res).lower().strip()
    if res.endswith("m"):
        return int(res[:-1]) * 60
    elif res.endswith("h"):
        return int(res[:-1]) * 3600
    elif res.endswith("d"):
        return int(res[:-1]) * 86400
    raise ValueError(f"Unrecognized resolution string: '{res}'. Expected format: '1m', '3h', '1d', etc.")
