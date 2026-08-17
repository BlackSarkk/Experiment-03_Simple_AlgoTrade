"""Stage [1/6] — Data preparation.

    resolve requested history
      -> load / download market data (warmup + requested window)
      -> compute_all_indicators ONCE on the full frame
      -> slice the evaluation window
      -> reserve the final 20% chronologically as sealed UNSEEN
      -> split the remaining DEV by V3's own 70/30 ratio, by row count
      -> expose TRAIN and VALIDATION, seal UNSEEN

    Effective full-history split:  TRAIN 56% / VALID 24% / UNSEEN 20%

The ordering is the point: indicators exist before any slice is taken, so no
rolling window ever restarts at a partition boundary. Every later phase consumes
the frames produced here and never re-loads or re-slices raw data itself.
"""

import hashlib
import os
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

import pandas as pd

from common.config import PipelineConfig, StrategyConfig
from common.market_data import MarketDataLoader
from strategy.indicators import compute_all_indicators

from . import history as history_mod, lookback
from .unseen import UnseenVault

# ---------------------------------------------------------------------------
# CANONICAL PARTITION POLICY
#
#   full requested history
#     -> reserve the final UNSEEN_FRACTION chronologically as sealed UNSEEN
#     -> the remaining span is DEV
#     -> V3's own fixed 70/30 split applies WITHIN DEV
#
# Effective full-history split at the default 20% UNSEEN:
#
#     TRAIN 56%  /  VALID 24%  /  UNSEEN 20%
#
# UNSEEN is reserved FIRST, before any split of DEV, and is physically removed
# from the frame the optimizer receives. It stays inaccessible until the single
# final confirmation, after the winner is frozen.
# ---------------------------------------------------------------------------
UNSEEN_FRACTION = 0.20
DEV_FRACTION = 1.0 - UNSEEN_FRACTION

from optimization.v3 import spec as _V3_SPEC          # stdlib-only module

DEV_TRAIN_FRACTION = _V3_SPEC.TRAIN_FRAC              # 0.70, canonical, not tunable
DEV_VALID_FRACTION = 1.0 - DEV_TRAIN_FRACTION         # 0.30

# Effective whole-history fractions, derived — never hardcoded.
TRAIN_FRACTION = DEV_FRACTION * DEV_TRAIN_FRACTION    # 0.56
VALID_FRACTION = DEV_FRACTION * DEV_VALID_FRACTION    # 0.24


def effective_ratios(unseen_fraction: float = UNSEEN_FRACTION) -> dict:
    """The three whole-history percentages implied by an UNSEEN reservation."""
    dev = 1.0 - float(unseen_fraction)
    return {
        "train_pct": round(100.0 * dev * DEV_TRAIN_FRACTION, 1),
        "valid_pct": round(100.0 * dev * DEV_VALID_FRACTION, 1),
        "unseen_pct": round(100.0 * float(unseen_fraction), 1),
        "dev_pct": round(100.0 * dev, 1),
        "dev_train_pct": round(100.0 * DEV_TRAIN_FRACTION, 1),
        "dev_valid_pct": round(100.0 * DEV_VALID_FRACTION, 1),
    }


TIMEFRAME_MINUTES = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "3h": 180, "4h": 240,
}

MIN_PARTITION_CANDLES = 100


class DataPreparationError(RuntimeError):
    """Raised when the requested history cannot be turned into usable partitions."""


@dataclass(frozen=True)
class Partition:
    name: str
    frame: pd.DataFrame
    start: pd.Timestamp
    end: pd.Timestamp

    @property
    def n_candles(self) -> int:
        return len(self.frame)


@dataclass(frozen=True)
class PreparedData:
    """Stage-1 output."""

    symbol: str
    timeframe: str
    source_timeframe: str
    is_resampled: bool
    available_bars: int
    warmup_candles: int
    evaluable_bars: int
    target_reached: bool
    availability_limited: bool
    requested_start: pd.Timestamp
    requested_end: pd.Timestamp
    warmup_start: pd.Timestamp
    warmup_end: pd.Timestamp
    full_candles: int
    checksum: str
    train: Partition
    validation: Partition
    unseen: UnseenVault
    unseen_start: pd.Timestamp
    unseen_end: pd.Timestamp
    unseen_candles: int
    raw_full: pd.DataFrame = None
    _bounds: dict = None
    _unseen_pinned: bool = False

    def context_for(self, partition: str):
        key = partition.lower()
        if key not in ("train", "validation"):
            raise ValueError(
                f"context_for({partition!r}) is not available; TRAIN and VALIDATION "
                "only. UNSEEN must be requested from the locked vault."
            )
        start, end = self._bounds[key]
        return self._context(start, end)

    def context_for_unseen(self):
        from .unseen import UnseenLockedError
        if self.unseen.is_locked:
            raise UnseenLockedError(
                "UNSEEN context requested while the vault is locked; only stage "
                "[6/6] may unlock, and only after the champion is frozen"
            )
        start, end = self._bounds["unseen"]
        return self._context(start, end)

    def context_for_window(self, start_ts, end_ts):
        import pandas as _pd
        limit = _pd.Timestamp(self.validation.end)
        start_ts = _pd.Timestamp(start_ts)
        end_ts = _pd.Timestamp(end_ts)
        if end_ts > limit:
            raise ValueError(
                f"window end {end_ts} reaches past VALIDATION ({limit}); "
                "UNSEEN is locked and can never be evaluated here"
            )
        dt = self.raw_full["datetime"]
        start = int((dt < start_ts).sum())
        end = int((dt <= end_ts).sum())
        if end <= start:
            raise ValueError(f"window {start_ts} -> {end_ts} contains no candles")
        return self._context(start, end)

    def _context(self, start: int, end: int):
        lead = max(0, start - self.warmup_candles)
        frame = self.raw_full.iloc[lead:end].reset_index(drop=True)
        return frame, start - lead

    def eval_frame(self, frame, lead):
        return frame.iloc[lead:].reset_index(drop=True)


def _fmt(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d %H:%M")


def _warmup_strategy_config(symbol: str, timeframe: str) -> StrategyConfig:
    cfg = StrategyConfig()
    cfg.symbol = symbol
    cfg.resolution = timeframe
    return cfg


def _checksum(df: pd.DataFrame) -> str:
    cols = [c for c in ("datetime", "open", "high", "low", "close", "volume") if c in df.columns]
    hashed = pd.util.hash_pandas_object(df[cols], index=False).values
    return hashlib.sha256(hashed.tobytes()).hexdigest()[:16]


def _load_raw(preset, data_dir: str, quiet: bool) -> tuple[pd.DataFrame, str, bool]:
    """Load all available complete candles for the platform/symbol/timeframe."""
    tf_minutes = int(history_mod.parse_timeframe_minutes(preset.timeframe))

    cfg = PipelineConfig()
    cfg.platform.platform = preset.platform
    cfg.platform.symbol = preset.symbol
    cfg.platform.resolution = preset.timeframe

    loader = MarketDataLoader(data_dir)
    needs_resample = preset.timeframe in ["2h", "3h", "6h", "12h"]
    source_timeframe = "1h" if needs_resample else preset.timeframe
    is_resampled = needs_resample

    cache_path = loader.get_cache_filename(preset.symbol, preset.timeframe, preset.platform)
    if os.path.exists(cache_path):
        df = pd.read_csv(cache_path)
    else:
        cfg.platform.days = None
        cfg.platform.start_date = None
        cfg.platform.end_date = None
        df = loader.load_ohlcv(cfg.platform, reset_cache=False, quiet=quiet)

    if df is None or df.empty:
        raise DataPreparationError(
            f"no market data returned for {preset.symbol} {preset.timeframe}"
        )

    df = df.copy()
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df.sort_values("datetime").drop_duplicates("datetime").reset_index(drop=True)

    bar = pd.Timedelta(minutes=tf_minutes)
    now = pd.Timestamp.now(tz="UTC")
    df = df.loc[df["datetime"] + bar <= now].reset_index(drop=True)
    if df.empty:
        raise DataPreparationError(
            f"no closed candles available for {preset.symbol} {preset.timeframe}"
        )
    return df, source_timeframe, is_resampled


def prepare(preset, data_dir: str = "data", quiet: bool = True,
            progress=None) -> PreparedData:
    """Run stage [1/6]. `progress` is an optional callable(str) for live output."""

    def say(msg):
        if progress is not None:
            progress(msg)

    warmup_candles = history_mod.AUTO_WARMUP_BARS  # 1,000

    raw_df, source_timeframe, is_resampled = _load_raw(preset, data_dir, quiet)
    available_bars = len(raw_df)

    source_tf_desc = f"{source_timeframe} (resampled to {preset.timeframe})" if is_resampled else f"{source_timeframe} (native)"

    say(f"Loading {preset.symbol} {preset.timeframe} [{source_tf_desc}] "
        f"({preset.history.describe(preset.timeframe)}, +{warmup_candles} warmup candles)")

    hist = preset.history

    if hist.mode == "auto":
        if available_bars < history_mod.AUTO_MIN_TOTAL_BARS:
            raise DataPreparationError(
                f"AUTO HISTORY UNAVAILABLE: {preset.timeframe} has {available_bars:,} complete bars; "
                f"canonical V3 AUTO requires 1,000 warmup bars plus at least 1,000 evaluable bars. "
                f"Choose a lower timeframe or explicit custom mode."
            )
        evaluable_bars = min(history_mod.AUTO_TARGET_EVALUABLE_BARS, available_bars - warmup_candles)
        total_required = warmup_candles + evaluable_bars
        raw = raw_df.iloc[-total_required:].reset_index(drop=True)
        requested_start = raw["datetime"].iloc[warmup_candles]
        requested_end = raw["datetime"].iloc[-1]
        target_reached = (evaluable_bars >= history_mod.AUTO_TARGET_EVALUABLE_BARS)
        availability_limited = not target_reached

    elif hist.mode == "candles":
        requested_eval = hist.candles
        total_required = requested_eval + warmup_candles
        if available_bars < total_required:
            raise DataPreparationError(
                f"requested {requested_eval:,} candles plus 1,000 warmup candles ({total_required:,} total), "
                f"but only {available_bars:,} complete bars available for {preset.symbol} {preset.timeframe}"
            )
        evaluable_bars = requested_eval
        raw = raw_df.iloc[-total_required:].reset_index(drop=True)
        requested_start = raw["datetime"].iloc[warmup_candles]
        requested_end = raw["datetime"].iloc[-1]
        target_reached = (evaluable_bars >= history_mod.AUTO_TARGET_EVALUABLE_BARS)
        availability_limited = not target_reached

    elif hist.mode in ("days", "date_range"):
        if hist.mode == "date_range":
            start_target = pd.Timestamp(hist.start_date, tz="UTC")
            end_target = pd.Timestamp(hist.end_date, tz="UTC") + pd.Timedelta(hours=23, minutes=59, seconds=59)
        else:  # "days"
            end_target = raw_df["datetime"].iloc[-1]
            start_target = end_target - pd.Timedelta(days=hist.days)

        in_range_idx = raw_df.index[(raw_df["datetime"] >= start_target) & (raw_df["datetime"] <= end_target)]
        if len(in_range_idx) == 0:
            raise DataPreparationError(f"requested window contains no candles for {preset.symbol} {preset.timeframe}")

        eval_first_idx = in_range_idx[0]
        eval_last_idx = in_range_idx[-1]

        if eval_first_idx < warmup_candles:
            raise DataPreparationError(
                f"requested window starts at {_fmt(start_target)}, but only {eval_first_idx:,} warmup candles exist prior to start (1,000 required)"
            )

        warmup_first_idx = eval_first_idx - warmup_candles
        raw = raw_df.iloc[warmup_first_idx:eval_last_idx + 1].reset_index(drop=True)
        requested_start = raw["datetime"].iloc[warmup_candles]
        requested_end = raw["datetime"].iloc[-1]
        evaluable_bars = len(raw) - warmup_candles
        target_reached = (evaluable_bars >= history_mod.AUTO_TARGET_EVALUABLE_BARS)
        availability_limited = not target_reached
    else:
        raise DataPreparationError(f"unhandled history mode: {hist.mode}")

    if hist.is_custom_short(preset.timeframe):
        say("NOTE: Custom short history — results are experimental.")

    say("Computing indicators on the full frame (warmup included)")
    strat_cfg = _warmup_strategy_config(preset.symbol, preset.timeframe)
    full = compute_all_indicators(raw, strat_cfg)
    checksum = _checksum(full)

    # Slice partitions on evaluable rows (after index warmup_candles)
    evaluation = full.iloc[warmup_candles:].reset_index(drop=True)
    eval_len = len(evaluation)
    if eval_len == 0:
        raise DataPreparationError("requested window contains no candles")

    unseen_count = int(round(eval_len * UNSEEN_FRACTION))
    dev_count = eval_len - unseen_count

    dev_train_count = int(round(dev_count * DEV_TRAIN_FRACTION))
    dev_valid_count = dev_count - dev_train_count

    train_start_idx = warmup_candles
    train_end_idx = warmup_candles + dev_train_count
    valid_end_idx = train_end_idx + dev_valid_count
    unseen_end_idx = warmup_candles + eval_len

    train_df = full.iloc[train_start_idx:train_end_idx].reset_index(drop=True)
    valid_df = full.iloc[train_end_idx:valid_end_idx].reset_index(drop=True)
    unseen_df = full.iloc[valid_end_idx:unseen_end_idx].reset_index(drop=True)

    def _bound_ts(df: pd.DataFrame) -> tuple[pd.Timestamp, pd.Timestamp]:
        if len(df) > 0:
            return df["datetime"].iloc[0], df["datetime"].iloc[-1]
        fallback = raw["datetime"].iloc[-1]
        return fallback, fallback

    t_start, t_end = _bound_ts(train_df)
    v_start, v_end = _bound_ts(valid_df)
    u_start, u_end = _bound_ts(unseen_df)

    train_p = Partition("TRAIN", train_df, t_start, t_end)
    valid_p = Partition("VALIDATION", valid_df, v_start, v_end)

    vault = UnseenVault(
        unseen_df,
        u_start,
        u_end,
    )

    warmup_start = full["datetime"].iloc[0]
    warmup_end = full["datetime"].iloc[warmup_candles - 1]

    prep = PreparedData(
        symbol=preset.symbol,
        timeframe=preset.timeframe,
        source_timeframe=source_timeframe,
        is_resampled=is_resampled,
        available_bars=available_bars,
        warmup_candles=warmup_candles,
        evaluable_bars=evaluable_bars,
        target_reached=target_reached,
        availability_limited=availability_limited,
        requested_start=requested_start,
        requested_end=requested_end,
        warmup_start=warmup_start,
        warmup_end=warmup_end,
        full_candles=len(full),
        checksum=checksum,
        train=train_p,
        validation=valid_p,
        unseen=vault,
        unseen_start=u_start,
        unseen_end=u_end,
        unseen_candles=len(unseen_df),
        raw_full=full,
        _bounds={
            "train": (train_start_idx, train_end_idx),
            "validation": (train_end_idx, valid_end_idx),
            "unseen": (valid_end_idx, unseen_end_idx)
        }
    )

    say(f"Stage 1 data preparation resolved for {preset.symbol} {preset.timeframe}:")
    say(f"  Source timeframe:  {source_tf_desc}")
    say(f"  Available bars:    {available_bars:,} complete bars")
    say(f"  Warmup lead-in:    {warmup_candles:,} bars (outside partitions)")
    say(f"  Evaluable bars:    {evaluable_bars:,} bars ({'Target 43,200 reached' if target_reached else 'Availability-limited (< 43,200)'})")
    say(f"  Partitions (56/24/20):")
    say(f"    TRAIN:   {train_p.n_candles:,} bars ({_fmt(train_p.start)} -> {_fmt(train_p.end)})")
    say(f"    VALID:   {valid_p.n_candles:,} bars ({_fmt(valid_p.start)} -> {_fmt(valid_p.end)})")
    say(f"    UNSEEN:  {len(unseen_df):,} bars ({_fmt(u_start)} -> {_fmt(u_end)}) [SEALED]")

    return prep
