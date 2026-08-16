"""Stage [1/6] — Data preparation.

    resolve requested history
      -> load / download market data (warmup + requested window)
      -> compute_all_indicators ONCE on the full frame
      -> slice the evaluation window
      -> split chronologically 60 / 20 / 20
      -> expose TRAIN and VALIDATION, seal UNSEEN

The ordering is the point: indicators exist before any slice is taken, so no
rolling window ever restarts at a partition boundary. Every later phase consumes
the frames produced here and never re-loads or re-slices raw data itself.
"""

import hashlib
from dataclasses import dataclass
from datetime import timedelta
from typing import Optional

import pandas as pd

from common.config import PipelineConfig, StrategyConfig
from common.market_data import MarketDataLoader
from strategy.indicators import compute_all_indicators

from . import lookback
from .unseen import UnseenVault

TRAIN_FRACTION = 0.60
VALID_FRACTION = 0.20
# UNSEEN takes the remainder, so the three always sum to exactly the window.

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
    symbol: str
    timeframe: str
    requested_start: pd.Timestamp
    requested_end: pd.Timestamp
    warmup_start: pd.Timestamp
    warmup_end: pd.Timestamp
    warmup_candles: int
    full_candles: int
    checksum: str
    train: Partition
    validation: Partition
    unseen: UnseenVault
    unseen_start: pd.Timestamp
    unseen_end: pd.Timestamp
    unseen_candles: int


def _fmt(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d %H:%M")


def _warmup_strategy_config(symbol: str, timeframe: str) -> StrategyConfig:
    """Indicator config used for the one-shot warmup computation.

    Phase A will recompute indicators per trial with that trial's parameters; this
    pass exists so the partition boundaries and the warmup assertions can be
    established with the widest lookbacks the search may request.
    """
    cfg = StrategyConfig()
    cfg.symbol = symbol
    cfg.resolution = timeframe
    return cfg


def _checksum(df: pd.DataFrame) -> str:
    cols = [c for c in ("datetime", "open", "high", "low", "close", "volume") if c in df.columns]
    hashed = pd.util.hash_pandas_object(df[cols], index=False).values
    return hashlib.sha256(hashed.tobytes()).hexdigest()[:16]


def _load_raw(preset, warmup_candles: int, data_dir: str, quiet: bool) -> pd.DataFrame:
    """Load a frame that covers warmup + the requested window."""
    tf_minutes = TIMEFRAME_MINUTES[preset.timeframe]
    warmup_delta = timedelta(minutes=tf_minutes * warmup_candles)

    cfg = PipelineConfig()
    cfg.platform.platform = preset.platform
    cfg.platform.symbol = preset.symbol
    cfg.platform.resolution = preset.timeframe

    hist = preset.history
    if hist.mode == "explicit":
        cfg.platform.start_date = (
            pd.Timestamp(hist.start_date) - warmup_delta
        ).strftime("%Y-%m-%d")
        cfg.platform.end_date = hist.end_date.strftime("%Y-%m-%d")
        cfg.platform.days = None
    else:
        # Relative mode anchors on the latest available candle, which is only
        # known after the load, so fetch the requested span plus the warmup lead-in.
        cfg.platform.start_date = None
        cfg.platform.end_date = None
        cfg.platform.days = int(hist.days + (warmup_delta.total_seconds() / 86400.0) + 2)

    loader = MarketDataLoader(data_dir)
    df = loader.load_ohlcv(cfg.platform, reset_cache=False, quiet=quiet)

    if df is None or df.empty:
        raise DataPreparationError(
            f"no market data returned for {preset.symbol} {preset.timeframe}"
        )

    df = df.copy()
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df.sort_values("datetime").drop_duplicates("datetime").reset_index(drop=True)

    # Drop the still-forming candle. Its close and volume mutate on every fetch,
    # which would make indicators, partitions and checksums differ between two
    # otherwise identical runs. Only closed candles are admissible.
    bar = pd.Timedelta(minutes=tf_minutes)
    now = pd.Timestamp.now(tz="UTC")
    df = df.loc[df["datetime"] + bar <= now].reset_index(drop=True)
    if df.empty:
        raise DataPreparationError(
            f"no closed candles available for {preset.symbol} {preset.timeframe}"
        )
    return df


def _requested_window(preset, df: pd.DataFrame):
    hist = preset.history
    if hist.mode == "explicit":
        start = pd.Timestamp(hist.start_date, tz="UTC")
        # A bare end date reads as inclusive end-of-day, matching main.py.
        end = pd.Timestamp(hist.end_date, tz="UTC") + pd.Timedelta(hours=23, minutes=59, seconds=59)
    else:
        end = df["datetime"].iloc[-1]
        start = end - pd.Timedelta(days=hist.days)
    return start, end


def prepare(preset, data_dir: str = "data", quiet: bool = True,
            progress=None) -> PreparedData:
    """Run stage [1/6]. `progress` is an optional callable(str) for live output."""

    def say(msg):
        if progress is not None:
            progress(msg)

    warmup_candles = lookback.required_warmup_candles()

    say(f"Loading {preset.symbol} {preset.timeframe} "
        f"({preset.history.describe()}, +{warmup_candles} warmup candles)")
    raw = _load_raw(preset, warmup_candles, data_dir, quiet)

    requested_start, requested_end = _requested_window(preset, raw)

    available_start = raw["datetime"].iloc[0]
    available_end = raw["datetime"].iloc[-1]
    if requested_start < available_start:
        requested_start = available_start
    if requested_end > available_end:
        requested_end = available_end
    if requested_end <= requested_start:
        raise DataPreparationError(
            f"requested window is empty after clamping to available data "
            f"({_fmt(available_start)} -> {_fmt(available_end)})"
        )

    # Trim the lead-in to exactly `warmup_candles`. The cache is allowed to be
    # wider than needed and its width varies between runs, so keeping whatever
    # happened to be on disk would make indicator values — and therefore every
    # downstream result — depend on cache history rather than on the request.
    pre = raw.index[raw["datetime"] < requested_start]
    if len(pre) > warmup_candles:
        raw = raw.loc[pre[-warmup_candles]:].reset_index(drop=True)

    # --- indicators FIRST, on the whole warmup+window frame ------------------
    say("Computing indicators on the full frame (warmup included)")
    strat_cfg = _warmup_strategy_config(preset.symbol, preset.timeframe)
    full = compute_all_indicators(raw, strat_cfg)

    checksum = _checksum(full)

    # --- only now is it safe to slice ---------------------------------------
    eval_mask = (full["datetime"] >= requested_start) & (full["datetime"] <= requested_end)
    evaluation = full.loc[eval_mask].reset_index(drop=True)

    warmup_rows = int((full["datetime"] < requested_start).sum())
    if len(evaluation) == 0:
        raise DataPreparationError("requested window contains no candles")

    if warmup_rows < lookback.MIN_CONTEXT_CANDLES:
        raise DataPreparationError(
            f"only {warmup_rows} warmup candles available before "
            f"{_fmt(requested_start)}; at least {lookback.MIN_CONTEXT_CANDLES} are "
            "needed for indicators to carry real history. Extend the history range "
            "or clear the data cache so a longer span is fetched."
        )

    warmup_start = full["datetime"].iloc[0]
    warmup_end = full.loc[full["datetime"] < requested_start, "datetime"].iloc[-1]

    # --- chronological 60 / 20 / 20 over the evaluation window ---------------
    window_start = evaluation["datetime"].iloc[0]
    window_end = evaluation["datetime"].iloc[-1]
    span = window_end - window_start
    train_end = window_start + span * TRAIN_FRACTION
    valid_end = window_start + span * (TRAIN_FRACTION + VALID_FRACTION)

    dt = evaluation["datetime"]
    train_df = evaluation.loc[dt < train_end].reset_index(drop=True)
    valid_df = evaluation.loc[(dt >= train_end) & (dt < valid_end)].reset_index(drop=True)
    unseen_df = evaluation.loc[dt >= valid_end].reset_index(drop=True)

    for name, part in (("TRAIN", train_df), ("VALIDATION", valid_df), ("UNSEEN", unseen_df)):
        if len(part) < MIN_PARTITION_CANDLES:
            raise DataPreparationError(
                f"{name} partition has only {len(part)} candles "
                f"(minimum {MIN_PARTITION_CANDLES}). The requested history is too "
                "short for a 60/20/20 split at this timeframe."
            )

    _assert_partitions_sound(evaluation, train_df, valid_df, unseen_df)
    _assert_warmup_context(full, train_df, valid_df)

    train = Partition("TRAIN", train_df,
                      train_df["datetime"].iloc[0], train_df["datetime"].iloc[-1])
    validation = Partition("VALIDATION", valid_df,
                           valid_df["datetime"].iloc[0], valid_df["datetime"].iloc[-1])

    unseen_start = unseen_df["datetime"].iloc[0]
    unseen_end = unseen_df["datetime"].iloc[-1]
    vault = UnseenVault(unseen_df, unseen_start, unseen_end)

    say(f"Partitioned {len(evaluation)} candles: "
        f"TRAIN {len(train_df)} / VALID {len(valid_df)} / UNSEEN {len(unseen_df)} [LOCKED]")

    return PreparedData(
        symbol=preset.symbol,
        timeframe=preset.timeframe,
        requested_start=window_start,
        requested_end=window_end,
        warmup_start=warmup_start,
        warmup_end=warmup_end,
        warmup_candles=warmup_rows,
        full_candles=len(full),
        checksum=checksum,
        train=train,
        validation=validation,
        unseen=vault,
        unseen_start=unseen_start,
        unseen_end=unseen_end,
        unseen_candles=len(unseen_df),
    )


# ---------------------------------------------------------------------------
# Assertions. These are correctness guarantees, not debug aids: a violation
# means results would be silently wrong, so they raise rather than warn.
# ---------------------------------------------------------------------------

def _assert_partitions_sound(evaluation, train_df, valid_df, unseen_df):
    total = len(train_df) + len(valid_df) + len(unseen_df)
    if total != len(evaluation):
        raise AssertionError(
            f"partition candle counts {total} != evaluation window {len(evaluation)}: "
            "the split dropped or duplicated candles"
        )

    # Strict chronological ordering with no overlap.
    if train_df["datetime"].iloc[-1] >= valid_df["datetime"].iloc[0]:
        raise AssertionError("TRAIN overlaps VALIDATION")
    if valid_df["datetime"].iloc[-1] >= unseen_df["datetime"].iloc[0]:
        raise AssertionError("VALIDATION overlaps UNSEEN")

    # No gap: the partitions must be adjacent slices of the same ordered frame.
    joined = pd.concat([train_df["datetime"], valid_df["datetime"],
                        unseen_df["datetime"]], ignore_index=True)
    if not joined.equals(evaluation["datetime"]):
        raise AssertionError(
            "concatenating TRAIN+VALIDATION+UNSEEN does not reproduce the "
            "evaluation window: the split introduced a gap or reordering"
        )


def _assert_warmup_context(full, train_df, valid_df):
    """Prove the first candles of TRAIN and VALIDATION carry real indicator history.

    A freshly restarted EMA equals the first close it sees, and a restarted RSI
    sits at its neutral fill value. Both are checked, along with the raw count of
    preceding candles.
    """
    dt = full["datetime"]

    for name, part in (("TRAIN", train_df), ("VALIDATION", valid_df)):
        first_ts = part["datetime"].iloc[0]
        preceding = int((dt < first_ts).sum())
        if preceding < lookback.MIN_CONTEXT_CANDLES:
            raise AssertionError(
                f"{name} starts at {_fmt(first_ts)} with only {preceding} preceding "
                f"candles in the indicator frame; {lookback.MIN_CONTEXT_CANDLES} are "
                "required. Indicators were not given real history."
            )

        row = part.iloc[0]
        if "ema_51" in part.columns and pd.notna(row["ema_51"]):
            if abs(float(row["ema_51"]) - float(row["close"])) < 1e-12:
                raise AssertionError(
                    f"{name}'s first EMA value equals its first close — the EMA was "
                    "restarted at the partition boundary instead of carrying warmup."
                )
        for col in ("ema_51", "atr", "rsi"):
            if col in part.columns and pd.isna(row[col]):
                raise AssertionError(
                    f"{name}'s first candle has NaN {col}: indicators were computed "
                    "on a slice rather than the full frame."
                )
