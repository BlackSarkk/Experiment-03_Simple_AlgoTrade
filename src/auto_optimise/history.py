"""History-window resolution for the optimizer preset.

Two mutually exclusive modes:

  relative   "days": 180, start_date/end_date null
             -> latest available candle, back 180 days

  explicit   "days": null, "start_date": "2024-01-01", "end_date": "2026-01-01"
             -> exactly that calendar range

Supplying both, or neither, is an error. The concrete calendar dates for the
relative mode can only be pinned once the data layer reports the latest available
candle, so V1 resolves relative mode to a description and defers the anchor.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

DATE_FMT = "%Y-%m-%d"

MIN_DAYS = 30
MAX_DAYS = 3650


class HistoryError(ValueError):
    """Raised when the history block is inconsistent or invalid."""


@dataclass(frozen=True)
class History:
    mode: str                      # "relative" | "explicit"
    days: Optional[int] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None

    def describe(self) -> str:
        if self.mode == "relative":
            return f"latest {self.days} days"
        return (f"{self.start_date.strftime(DATE_FMT)} -> "
                f"{self.end_date.strftime(DATE_FMT)}")

    def span_days(self) -> int:
        if self.mode == "relative":
            return int(self.days)
        return (self.end_date - self.start_date).days


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
        raise HistoryError("history must be an object with days/start_date/end_date")

    unknown = set(block) - {"days", "start_date", "end_date"}
    if unknown:
        raise HistoryError(
            f"unknown key(s) in history: {', '.join(sorted(unknown))} "
            "(allowed: days, start_date, end_date)"
        )

    days = block.get("days")
    raw_start = block.get("start_date")
    raw_end = block.get("end_date")

    has_days = days is not None
    has_range = raw_start is not None or raw_end is not None

    if has_days and has_range:
        raise HistoryError(
            "history.days and history.start_date/end_date are mutually exclusive. "
            "Set days OR the date range, not both."
        )
    if not has_days and not has_range:
        raise HistoryError(
            "history needs either days, or start_date + end_date. Both are null."
        )

    if has_days:
        if isinstance(days, bool) or not isinstance(days, int):
            raise HistoryError(f"history.days must be a whole number, got {days!r}")
        if not (MIN_DAYS <= days <= MAX_DAYS):
            raise HistoryError(
                f"history.days must be between {MIN_DAYS} and {MAX_DAYS}, got {days}"
            )
        return History(mode="relative", days=days)

    if raw_start is None or raw_end is None:
        missing = "start_date" if raw_start is None else "end_date"
        raise HistoryError(
            f"explicit date range needs both start_date and end_date; {missing} is null"
        )

    start = _parse_date(raw_start, "start_date")
    end = _parse_date(raw_end, "end_date")

    if end <= start:
        raise HistoryError(
            f"history.end_date ({end}) must be after start_date ({start})"
        )
    span = (end - start).days
    if span < MIN_DAYS:
        raise HistoryError(
            f"history range is only {span} days; at least {MIN_DAYS} are needed"
        )

    return History(mode="explicit", start_date=start, end_date=end)
