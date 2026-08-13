"""
Strategy Signal Generation Engine for Delta Exchange ETHUSD 1-Hour Strategy.
Ultra-fast numpy vectorized implementation.
"""

from dataclasses import dataclass
from typing import List, Optional
import numpy as np
import pandas as pd
from config import StrategyConfig


@dataclass
class Signal:
    candle_idx: int                       # Index of the completed signal candle
    timestamp: int                        # Timestamp of completed candle
    datetime_str: str                     # Human-readable UTC time
    signal_type: str                      # 'LONG' or 'SHORT'
    close_price: float                    # Close of signal candle (reference price)
    ema_51: float                         # 51 EMA value at signal candle
    rsi: float                            # RSI value at signal candle
    atr: float                            # ATR value at signal candle
    sl_price: float                       # Proposed Stop-Loss price
    tp_price: float                       # Proposed Take-Profit price (1:2 RR)
    risk_per_unit: float                  # Absolute dollar risk per unit (Entry - SL)
    consolidation_detected: bool          # Confirmation of preceding consolidation
    reason: str                           # Signal trigger description


class Delta1HStrategy:
    """Implements the full 1-hour ETHUSD algorithmic trading logic."""

    def __init__(self, config: Optional[StrategyConfig] = None):
        self.config = config or StrategyConfig()

    def generate_signals(self, df: pd.DataFrame) -> List[Signal]:
        signals: List[Signal] = []
        n = len(df)
        min_warmup = max(self.config.ema_period + 10, 60)
        if n < min_warmup:
            return signals

        # Numpy extractions for lightning-fast scanning
        closes = df["close"].to_numpy()
        opens = df["open"].to_numpy()
        highs = df["high"].to_numpy()
        lows = df["low"].to_numpy()
        ema_51 = df["ema_51"].to_numpy()
        rsis = df["rsi"].to_numpy()
        atrs = df["atr"].to_numpy()
        timestamps = df["timestamp"].to_numpy()
        datetimes = df["datetime"].astype(str).to_numpy()
        
        swing_lows = df["swing_low"].to_numpy() if "swing_low" in df.columns else (closes - atrs * 1.5)
        swing_highs = df["swing_high"].to_numpy() if "swing_high" in df.columns else (closes + atrs * 1.5)
        is_cons = df["is_consolidating"].to_numpy() if "is_consolidating" in df.columns else np.ones(n, dtype=bool)

        ema_200 = df["ema_200"].to_numpy() if "ema_200" in df.columns else closes
        ema_slope = df["ema_51_slope"].to_numpy() if "ema_51_slope" in df.columns else np.zeros(n)
        vols = df["volume"].to_numpy() if "volume" in df.columns else np.ones(n)
        vol_smas = df["vol_sma_20"].to_numpy() if "vol_sma_20" in df.columns else np.ones(n)

        # Precompute boolean masks
        rsi_was_oversold = (pd.Series(rsis).rolling(6).min() <= self.config.rsi_oversold).to_numpy()
        rsi_was_overbought = (pd.Series(rsis).rolling(6).max() >= self.config.rsi_overbought).to_numpy()
        prior_cons = pd.Series(is_cons).shift(1).rolling(3).max().fillna(0).astype(bool).to_numpy()

        for i in range(min_warmup, n):
            close = closes[i]
            prev_close = closes[i - 1]
            open_p = opens[i]
            ema = ema_51[i]
            prev_ema = ema_51[i - 1]
            rsi = rsis[i]
            atr = atrs[i]
            sw_low = swing_lows[i] if not np.isnan(swing_lows[i]) else (close - atr * 1.5)
            sw_high = swing_highs[i] if not np.isnan(swing_highs[i]) else (close + atr * 1.5)

            # --- LONG ENTRY CONDITIONS ---
            ema_cross_up = (prev_close <= prev_ema and close > ema) or (close > ema and prev_close > prev_ema and open_p < ema)
            rsi_long_valid = (rsi < self.config.rsi_overbought) and (rsi >= 40.0 or rsi_was_oversold[i])
            cons_long_valid = prior_cons[i] or is_cons[i]

            trend_long_ok = (not self.config.use_trend_filter) or (close >= ema_200[i])
            slope_long_ok = (not self.config.use_ema_slope_filter) or (ema_slope[i] >= -0.05)
            vol_long_ok = (not self.config.use_volume_filter) or (vols[i] >= vol_smas[i] * self.config.volume_mult)

            if ema_cross_up and rsi_long_valid and cons_long_valid and trend_long_ok and slope_long_ok and vol_long_ok:
                sl = min(sw_low, lows[i])
                if (close - sl) < (0.4 * atr):
                    sl = close - (0.85 * atr)
                risk_dist = close - sl
                if risk_dist > 0:
                    tp = close + (self.config.risk_reward_ratio * risk_dist)
                    signals.append(Signal(
                        candle_idx=i,
                        timestamp=int(timestamps[i]),
                        datetime_str=datetimes[i],
                        signal_type="LONG",
                        close_price=float(close),
                        ema_51=float(ema),
                        rsi=float(rsi),
                        atr=float(atr),
                        sl_price=round(float(sl), 2),
                        tp_price=round(float(tp), 2),
                        risk_per_unit=round(float(risk_dist), 2),
                        consolidation_detected=True,
                        reason="Bullish 51 EMA crossover with consolidation, volume confirmation & trend alignment"
                    ))
                    continue

            # --- SHORT ENTRY CONDITIONS ---
            ema_cross_down = (prev_close >= prev_ema and close < ema) or (close < ema and prev_close < prev_ema and open_p > ema)
            rsi_short_valid = (rsi > self.config.rsi_oversold) and (rsi <= 60.0 or rsi_was_overbought[i])
            cons_short_valid = prior_cons[i] or is_cons[i]

            trend_short_ok = (not self.config.use_trend_filter) or (close <= ema_200[i])
            slope_short_ok = (not self.config.use_ema_slope_filter) or (ema_slope[i] <= 0.05)
            vol_short_ok = (not self.config.use_volume_filter) or (vols[i] >= vol_smas[i] * self.config.volume_mult)

            if ema_cross_down and rsi_short_valid and cons_short_valid and trend_short_ok and slope_short_ok and vol_short_ok:
                sl = max(sw_high, highs[i])
                if (sl - close) < (0.4 * atr):
                    sl = close + (0.85 * atr)
                risk_dist = sl - close
                if risk_dist > 0:
                    tp = close - (self.config.risk_reward_ratio * risk_dist)
                    signals.append(Signal(
                        candle_idx=i,
                        timestamp=int(timestamps[i]),
                        datetime_str=datetimes[i],
                        signal_type="SHORT",
                        close_price=float(close),
                        ema_51=float(ema),
                        rsi=float(rsi),
                        atr=float(atr),
                        sl_price=round(float(sl), 2),
                        tp_price=round(float(tp), 2),
                        risk_per_unit=round(float(risk_dist), 2),
                        consolidation_detected=True,
                        reason="Bearish 51 EMA crossover with consolidation, volume confirmation & trend alignment"
                    ))

        return signals
