"""
Indicator calculation module for Delta Exchange ETHUSD 1-Hour Strategy.
Calculates:
- 51 EMA (Exponential Moving Average)
- 14 RSI (Relative Strength Index)
- 14 ATR (Average True Range)
- 8-Candle ATR-based Consolidation Detector
- Confirmed Swing High / Swing Low Support and Resistance
"""

import numpy as np
import pandas as pd


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
    Measures the aggregate high-low span over the last N completed candles
    relative to ATR. If the range is compressed within atr_multiplier * ATR,
    the market is classified as consolidating.
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


def calculate_swing_levels(df: pd.DataFrame, lookback: int = 10) -> pd.DataFrame:
    """
    Calculate confirmed Swing High and Swing Low over a rolling lookback window.
    Used for structural Stop-Loss placement.
    """
    # Shifted by 1 so current bar cannot see its own high/low for lookahead safety
    df["swing_high"] = df["high"].shift(1).rolling(window=lookback, min_periods=3).max()
    df["swing_low"] = df["low"].shift(1).rolling(window=lookback, min_periods=3).min()
    return df


def calculate_volume_sma(series: pd.Series, period: int = 20) -> pd.Series:
    """Calculate simple moving average of volume."""
    return series.rolling(window=period, min_periods=1).mean()


def compute_all_indicators(
    df: pd.DataFrame,
    ema_period: int = 51,
    rsi_period: int = 14,
    atr_period: int = 14,
    consolidation_candles: int = 8,
    consolidation_atr_mult: float = 2.0,
    swing_lookback: int = 8,
    trend_ema_period: int = 200,
) -> pd.DataFrame:
    """Compute and attach all technical indicators to the DataFrame."""
    df = df.copy()
    
    # 51 EMA & 200 Trend EMA
    df["ema_51"] = calculate_ema(df["close"], period=ema_period)
    df["ema_200"] = calculate_ema(df["close"], period=trend_ema_period)
    
    # 51 EMA Slope (3-period rate of change)
    df["ema_51_slope"] = df["ema_51"] - df["ema_51"].shift(2)
    
    # 14 RSI
    df["rsi"] = calculate_rsi(df["close"], period=rsi_period)
    
    # 14 ATR
    df["atr"] = calculate_atr(df, period=atr_period)
    
    # Volume SMA (20)
    df["vol_sma_20"] = calculate_volume_sma(df["volume"], period=20)
    
    # Consolidation
    df = calculate_consolidation(df, consolidation_candles=consolidation_candles, atr_multiplier=consolidation_atr_mult)
    
    # Swing Levels
    df = calculate_swing_levels(df, lookback=swing_lookback)
    
    return df
