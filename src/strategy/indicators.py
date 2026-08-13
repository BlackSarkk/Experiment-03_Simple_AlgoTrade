"""
Technical Indicators Calculation Engine.
Provides high-performance vectorized computations for:
- 51 EMA & 200 Trend EMA
- 14 RSI (Wilder's exponential smoothing)
- 14 ATR (Average True Range)
- 8-Candle ATR Consolidation Range Detector
- 8-Candle Confirmed Swing High / Swing Low (shifted by 1 for lookahead safety)
- 20 Volume SMA
"""

import numpy as np
import pandas as pd
from common.config import StrategyConfig


def calculate_ema(series: pd.Series, period: int = 51) -> pd.Series:
    """Calculate Exponential Moving Average."""
    return series.ewm(span=period, adjust=False).mean()


def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Calculate Wilder's Relative Strength Index (RSI)."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    # Wilder's exponential smoothing (alpha = 1 / period)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(50.0)


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculate Average True Range (ATR)."""
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    return atr


def calculate_consolidation(
    df: pd.DataFrame,
    consolidation_candles: int = 8,
    atr_multiplier: float = 2.2
) -> pd.DataFrame:
    """
    Detect 8-candle consolidation.
    Measures aggregate High-Low span over rolling N candles relative to ATR.
    """
    roll_high = df["high"].rolling(window=consolidation_candles).max()
    roll_low = df["low"].rolling(window=consolidation_candles).min()
    consolidation_range = roll_high - roll_low

    max_allowed_range = df["atr"] * atr_multiplier
    is_consolidating = consolidation_range <= max_allowed_range

    df["cons_high"] = roll_high
    df["cons_low"] = roll_low
    df["cons_range"] = consolidation_range
    df["is_consolidating"] = is_consolidating
    return df


def calculate_swing_levels(df: pd.DataFrame, lookback: int = 8) -> pd.DataFrame:
    """
    Calculate confirmed Swing High and Swing Low.
    Shifted by 1 bar to prevent lookahead bias.
    """
    df["swing_high"] = df["high"].shift(1).rolling(window=lookback, min_periods=3).max()
    df["swing_low"] = df["low"].shift(1).rolling(window=lookback, min_periods=3).min()
    return df


def compute_all_indicators(df: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    """Compute and attach all technical indicators to DataFrame."""
    df = df.copy()

    # 51 EMA & 200 Trend EMA
    df["ema_51"] = calculate_ema(df["close"], period=cfg.ema_period)
    df["ema_200"] = calculate_ema(df["close"], period=cfg.trend_ema_period)

    # 51 EMA Slope (3-bar ROC)
    df["ema_51_slope"] = df["ema_51"] - df["ema_51"].shift(2)

    # 14 RSI
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    df["avg_gain"] = gain.ewm(alpha=1.0 / cfg.rsi_period, min_periods=cfg.rsi_period, adjust=False).mean()
    df["avg_loss"] = loss.ewm(alpha=1.0 / cfg.rsi_period, min_periods=cfg.rsi_period, adjust=False).mean()
    rs = df["avg_gain"] / df["avg_loss"].replace(0, np.nan)
    df["rsi"] = (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)

    # 14 ATR
    df["atr"] = calculate_atr(df, period=cfg.atr_period)

    # Volume SMA (20)
    df["vol_sma_20"] = df["volume"].rolling(window=cfg.volume_sma_period, min_periods=1).mean()

    # Consolidation
    df = calculate_consolidation(
        df,
        consolidation_candles=cfg.consolidation_candles,
        atr_multiplier=cfg.consolidation_atr_mult
    )

    # Swing Levels
    df = calculate_swing_levels(df, lookback=cfg.swing_lookback)

    return df
