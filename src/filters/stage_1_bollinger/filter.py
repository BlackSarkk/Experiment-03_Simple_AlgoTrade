"""
Phase 4 Stage 1 — Bollinger chop filter.

This is a SIGNAL GATE, not a strategy. It never creates a signal and never alters
entry/exit prices, SL/TP, sizing, fees or execution. It only removes signals that the
existing strategy already produced, when the Bollinger state says the market is choppy.

    existing LONG signal -> enabled? -> pass -> allow
                                     -> fail -> block

Three defensible chop conditions, each independently disableable by setting its
threshold to 0.0 (which is the neutral/off value):

  1. bandwidth compression   bandwidth% = (upper - lower) / middle * 100
                             block if bandwidth% < min_bandwidth_pct
                             (narrow bands = compressed, directionless market)

  2. band expansion          block if bandwidth% < bandwidth%[t - lookback] * expansion_min_ratio
                             (bands must be opening, not still contracting)

  3. middle-band distance    dist = |close - middle| / (upper - lower)
                             block if dist < min_mid_distance
                             (price pinned to the mean = no displacement)

Lookahead safety: every series is built from rolling windows over the CURRENT and PAST
candles only. A signal is evaluated at its own candle's close, at which point that
candle's OHLC is fully known. `.rolling()` and `.shift(+n)` are backward-looking; no
`.shift(-n)` is used anywhere in this module.
"""

from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import pandas as pd

from strategy.baseline_strategy import BaselineStrategy, Signal


@dataclass
class BollingerFilterConfig:
    enabled: bool = False
    length: int = 20
    std: float = 2.0
    min_bandwidth_pct: float = 0.0      # 0.0 -> condition disabled
    expansion_lookback: int = 5
    expansion_min_ratio: float = 0.0    # 0.0 -> condition disabled
    min_mid_distance: float = 0.0       # 0.0 -> condition disabled

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "BollingerFilterConfig":
        d = d or {}
        return cls(
            enabled=bool(d.get("enabled", False)),
            length=int(d.get("length", 20)),
            std=float(d.get("std", 2.0)),
            min_bandwidth_pct=float(d.get("min_bandwidth_pct", 0.0)),
            expansion_lookback=int(d.get("expansion_lookback", 5)),
            expansion_min_ratio=float(d.get("expansion_min_ratio", 0.0)),
            min_mid_distance=float(d.get("min_mid_distance", 0.0)),
        )

    def to_dict(self) -> dict:
        return {"enabled": self.enabled, "length": self.length, "std": self.std,
                "min_bandwidth_pct": self.min_bandwidth_pct,
                "expansion_lookback": self.expansion_lookback,
                "expansion_min_ratio": self.expansion_min_ratio,
                "min_mid_distance": self.min_mid_distance}


def compute_bollinger(df: pd.DataFrame, cfg: BollingerFilterConfig) -> pd.DataFrame:
    """Attach bb_mid / bb_up / bb_lo / bb_bandwidth / bb_mid_dist. Backward-looking only."""
    close = df["close"]
    mid = close.rolling(window=cfg.length, min_periods=cfg.length).mean()
    sd = close.rolling(window=cfg.length, min_periods=cfg.length).std(ddof=0)
    up = mid + cfg.std * sd
    lo = mid - cfg.std * sd
    width = (up - lo)
    out = df.copy()
    out["bb_mid"] = mid
    out["bb_up"] = up
    out["bb_lo"] = lo
    out["bb_bandwidth"] = (width / mid.replace(0, np.nan)) * 100.0
    out["bb_mid_dist"] = (close - mid).abs() / width.replace(0, np.nan)
    return out


def allow_mask(df_bb: pd.DataFrame, cfg: BollingerFilterConfig) -> np.ndarray:
    """True = signal allowed. Rows with undefined Bollinger (warmup) are ALLOWED, so the
    filter can never manufacture a difference purely from indicator warmup."""
    n = len(df_bb)
    allow = np.ones(n, dtype=bool)
    bw = df_bb["bb_bandwidth"].to_numpy(dtype=float)
    md = df_bb["bb_mid_dist"].to_numpy(dtype=float)

    if cfg.min_bandwidth_pct > 0.0:
        allow &= ~(np.nan_to_num(bw, nan=np.inf) < cfg.min_bandwidth_pct)

    if cfg.expansion_min_ratio > 0.0 and cfg.expansion_lookback > 0:
        prev = df_bb["bb_bandwidth"].shift(cfg.expansion_lookback).to_numpy(dtype=float)
        with np.errstate(invalid="ignore"):
            ratio = np.divide(bw, prev, out=np.full(n, np.inf), where=~np.isnan(prev) & (prev > 0))
        allow &= ~(np.nan_to_num(ratio, nan=np.inf) < cfg.expansion_min_ratio)

    if cfg.min_mid_distance > 0.0:
        allow &= ~(np.nan_to_num(md, nan=np.inf) < cfg.min_mid_distance)

    return allow


class BollingerFilteredStrategy(BaselineStrategy):
    """Wraps BaselineStrategy. generate_signals() delegates to the frozen implementation,
    then drops blocked signals. Strategy formulas are never touched."""

    def __init__(self, strategy_config=None, filter_config: Optional[BollingerFilterConfig] = None):
        super().__init__(strategy_config)
        self.filter_config = filter_config or BollingerFilterConfig()
        self.blocked_count = 0
        self.total_signals = 0

    def generate_signals(self, df: pd.DataFrame) -> List[Signal]:
        signals = super().generate_signals(df)          # unchanged strategy output
        self.total_signals = len(signals)
        self.blocked_count = 0
        if not self.filter_config.enabled or not signals:
            return signals
        allow = allow_mask(compute_bollinger(df, self.filter_config), self.filter_config)
        kept = [s for s in signals if allow[s.candle_idx]]
        self.blocked_count = len(signals) - len(kept)
        return kept
