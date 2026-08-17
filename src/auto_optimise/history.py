"""History-window resolution for the optimizer preset.

Four explicit history modes:

  auto        "mode": "auto", days/start_date/end_date/candles null
              -> availability-aware target-bar policy:
                 AUTO_TARGET_EVALUABLE_BARS = 43,200
                 AUTO_WARMUP_BARS = 1,000
                 AUTO_MIN_EVALUABLE_BARS = 1,000
                 AUTO_MIN_TOTAL_BARS = 2,000
                 Resolved dynamically during data prep based on genuine platform data availability.

  days        "mode": "days", "days": N, others null
              -> N positive integer >= 1

  date_range  "mode": "date_range", "start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD", others null
              -> explicit calendar range in UTC

  candles     "mode": "candles", "candles": N, others null
              -> N positive integer >= 1 evaluable candles before partitioning
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional
from dateutil.relativedelta import relativedelta

DATE_FMT = "%Y-%m-%d"
TIMEFRAME_PATTERN = re.compile(r"^([1-9][0-9]*)(m|h|d|w|M|Y)$")

AUTO_TARGET_EVALUABLE_BARS = 43_200
AUTO_WARMUP_BARS = 1_000
AUTO_MIN_EVALUABLE_BARS = 1_000
AUTO_MIN_TOTAL_BARS = 2_000

VALID_MODES = ("auto", "days", "date_range", "candles")


class HistoryError(ValueError):
    """Raised when the history block is inconsistent or invalid."""


def timeframe_relativedelta(timeframe: str) -> Optional[relativedelta]:
    """Return a relativedelta for M or Y calendar units, or None for fixed units."""
    m = TIMEFRAME_PATTERN.match(timeframe)
    if not m:
        raise HistoryError(f"unsupported or invalid timeframe format: {timeframe!r}")
    val = int(m.group(1))
    unit = m.group(2)
    if unit == "M":
        return relativedelta(months=val)
    elif unit == "Y":
        return relativedelta(years=val)
    return None


def parse_timeframe_minutes(timeframe: str) -> float:
    """Parse fixed timeframe units (m, h, d, w) into minutes.

    Raises HistoryError for calendar units (M, Y) as they do not have a fixed minute count.
    """
    m = TIMEFRAME_PATTERN.match(timeframe)
    if not m:
        raise HistoryError(f"unsupported or invalid timeframe format: {timeframe!r}")
    val = int(m.group(1))
    unit = m.group(2)
    if unit == "m":
        return float(val)
    elif unit == "h":
        return float(val * 60)
    elif unit == "d":
        return float(val * 1440)
    elif unit == "w":
        return float(val * 7 * 1440)
    elif unit in ("M", "Y"):
        raise HistoryError(
            f"timeframe unit {unit!r} is a calendar unit and does not have a fixed minute count; "
            "use timeframe_relativedelta() instead"
        )
    raise HistoryError(f"unhandled timeframe unit: {unit!r}")


@dataclass(frozen=True)
class History:
    mode: str                      # "auto" | "days" | "date_range" | "candles"
    days: Optional[int] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    candles: Optional[int] = None

    def evaluable_candles(self, timeframe: str = "15m") -> int:
        if self.mode == "auto":
            return AUTO_TARGET_EVALUABLE_BARS
        if self.mode == "candles":
            return int(self.candles)
        if self.mode == "days":
            delta = timeframe_relativedelta(timeframe)
            if delta is not None:
                now = datetime.now(timezone.utc)
                past = now - delta
                days_per_bar = (now - past).total_seconds() / 86400.0
                return int(round(self.days / days_per_bar))
            else:
                minutes = parse_timeframe_minutes(timeframe)
                return int(round(self.days * 1440.0 / minutes))
        if self.mode == "date_range":
            span_days = (self.end_date - self.start_date).days
            delta = timeframe_relativedelta(timeframe)
            if delta is not None:
                now = datetime.now(timezone.utc)
                past = now - delta
                days_per_bar = (now - past).total_seconds() / 86400.0
                return int(round(span_days / days_per_bar))
            else:
                minutes = parse_timeframe_minutes(timeframe)
                return int(round(span_days * 1440.0 / minutes))
        return AUTO_TARGET_EVALUABLE_BARS

    def span_days(self, timeframe: str = "15m") -> int:
        eval_c = self.evaluable_candles(timeframe)
        delta = timeframe_relativedelta(timeframe)
        if delta is not None:
            now = datetime.now(timezone.utc)
            past = now - delta
            days_per_bar = (now - past).total_seconds() / 86400.0
            return max(1, int(round(eval_c * days_per_bar)))
        else:
            minutes = parse_timeframe_minutes(timeframe)
            return max(1, int(round(eval_c * minutes / 1440.0)))

    def is_custom_short(self, timeframe: str = "15m") -> bool:
        """True only if an explicit custom mode's evaluable candles are fewer than 43,200.

        Must NEVER return True for auto mode.
        """
        if self.mode == "auto":
            return False
        return self.evaluable_candles(timeframe) < AUTO_TARGET_EVALUABLE_BARS

    def describe(self, timeframe: str = "15m") -> str:
        if self.mode == "auto":
            return f"mode: auto (target {AUTO_TARGET_EVALUABLE_BARS:,} evaluable bars + {AUTO_WARMUP_BARS:,} warmup bars, availability resolved at prep)"
        if self.mode == "days":
            c_count = self.evaluable_candles(timeframe)
            return f"mode: days ({self.days} days, ~{c_count:,} candles at {timeframe})"
        if self.mode == "candles":
            return f"mode: candles ({self.candles:,} candles at {timeframe})"
        if self.mode == "date_range":
            s_str = self.start_date.strftime(DATE_FMT)
            e_str = self.end_date.strftime(DATE_FMT)
            c_count = self.evaluable_candles(timeframe)
            return f"mode: date_range ({s_str} -> {e_str}, ~{c_count:,} candles at {timeframe})"
        return f"mode: {self.mode}"


def _parse_date(value, field: str) -> date:
    if not isinstance(value, str):
        raise HistoryError(f"history.{field} must be a 'YYYY-MM-DD' string, got {value!r}")
    try:
        return datetime.strptime(value, DATE_FMT).date()
    except ValueError:
        raise HistoryError(
            f"history.{field} is not a valid 'YYYY-MM-DD' date: {value!r}"
        )


def resolve(block) -> History:
    """Validate and resolve the preset's `history` block."""
    if not isinstance(block, dict):
        raise HistoryError("history must be an object with mode, days, start_date, end_date, candles")

    allowed_keys = {"mode", "days", "start_date", "end_date", "candles"}
    unknown = set(block) - allowed_keys
    if unknown:
        raise HistoryError(
            f"unknown key(s) in history: {', '.join(sorted(unknown))} "
            "(allowed: mode, days, start_date, end_date, candles)"
        )

    mode = block.get("mode")
    if mode not in VALID_MODES:
        raise HistoryError(
            f"history.mode must be one of {list(VALID_MODES)}, got {mode!r}"
        )

    days = block.get("days")
    raw_start = block.get("start_date")
    raw_end = block.get("end_date")
    candles = block.get("candles")

    if mode == "auto":
        non_null = [k for k in ("days", "start_date", "end_date", "candles") if block.get(k) is not None]
        if non_null:
            raise HistoryError(
                f"for history mode 'auto', all other history fields (days, start_date, end_date, candles) "
                f"must be null; got {', '.join(non_null)}"
            )
        return History(mode="auto")

    if mode == "days":
        if days is None:
            raise HistoryError("history mode 'days' requires 'days' field to be set (positive integer)")
        if isinstance(days, bool) or not isinstance(days, int) or days < 1:
            raise HistoryError(f"history.days must be a positive integer >= 1, got {days!r}")
        non_null = [k for k in ("start_date", "end_date", "candles") if block.get(k) is not None]
        if non_null:
            raise HistoryError(
                f"for history mode 'days', start_date, end_date, and candles must be null; "
                f"got {', '.join(non_null)}"
            )
        return History(mode="days", days=days)

    if mode == "date_range":
        if raw_start is None or raw_end is None:
            missing = [k for k in ("start_date", "end_date") if block.get(k) is None]
            raise HistoryError(
                f"history mode 'date_range' requires both start_date and end_date; missing {', '.join(missing)}"
            )
        non_null = [k for k in ("days", "candles") if block.get(k) is not None]
        if non_null:
            raise HistoryError(
                f"for history mode 'date_range', days and candles must be null; "
                f"got {', '.join(non_null)}"
            )
        start = _parse_date(raw_start, "start_date")
        end = _parse_date(raw_end, "end_date")
        if end <= start:
            raise HistoryError(
                f"history.end_date ({end}) must be strictly after start_date ({start})"
            )
        return History(mode="date_range", start_date=start, end_date=end)

    if mode == "candles":
        if candles is None:
            raise HistoryError("history mode 'candles' requires 'candles' field to be set (positive integer)")
        if isinstance(candles, bool) or not isinstance(candles, int) or candles < 1:
            raise HistoryError(f"history.candles must be a positive integer >= 1, got {candles!r}")
        non_null = [k for k in ("days", "start_date", "end_date") if block.get(k) is not None]
        if non_null:
            raise HistoryError(
                f"for history mode 'candles', days, start_date, and end_date must be null; "
                f"got {', '.join(non_null)}"
            )
        return History(mode="candles", candles=candles)

    raise HistoryError(f"unhandled history mode: {mode}")
