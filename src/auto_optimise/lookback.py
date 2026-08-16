"""Warmup sizing.

Indicators must never be computed on a partition slice — doing so restarts every
rolling window at the partition edge and silently changes results. The optimizer
therefore loads a frame that begins BEFORE the requested evaluation window and
computes indicators once across the whole thing.

How much lead-in is needed is derived from the largest lookback any trial could
request, not from one config's current values, because the search will move those
values around. The bounds below cover:

  * `StrategyConfig` defaults, including `trend_ema_period` (200), which
    `compute_all_indicators` always computes regardless of preset content.
  * the frozen Candidate #158 values in configs/config/config1-ETHUSDTP15m-long.json
    (EMA 104, RSI 20, ATR 7, consolidation 7, swing 17, volume SMA 12).
  * the widest ranges the legacy optimizers ever sampled
    (`src/optimization/*`: EMA up to 200, RSI/ATR up to 35, volume SMA up to 50).
  * `BollingerFilterConfig` (length + expansion_lookback).

EMA and RSI are exponential and never fully "expire", so a multiple of the period
is used rather than the period itself.
"""

# Largest lookback, in candles, any single indicator may require.
MAX_INDICATOR_LOOKBACK = 200        # trend EMA 200 / searched EMA up to 200
MAX_FILTER_LOOKBACK = 60            # Bollinger length + expansion lookback headroom

# EMA/RSI are recursive: N periods is not enough to converge. 5x is the usual
# rule of thumb for an EMA to lose its seeding bias to below noise level.
SAFETY_MULTIPLIER = 5

# Floor, so short windows on high timeframes still get real context.
MIN_WARMUP_CANDLES = 500


def required_warmup_candles() -> int:
    """Candles of pre-TRAIN context required before any indicator is trustworthy."""
    largest = max(MAX_INDICATOR_LOOKBACK, MAX_FILTER_LOOKBACK)
    return max(MIN_WARMUP_CANDLES, largest * SAFETY_MULTIPLIER)


# Minimum context each individual indicator needs before a value is meaningful.
# Used by the post-slice assertions.
MIN_CONTEXT_CANDLES = MAX_INDICATOR_LOOKBACK
