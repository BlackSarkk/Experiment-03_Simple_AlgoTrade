"""Trial-budget allocation across the five canonical V3 stages.

The human writes ONE number (or "auto"). The five per-stage budgets are derived
here and are never preset inputs.

REFERENCE ALLOCATION
--------------------
V3's canonical budget is 1,850 trials, allocated exactly:

    1a broad strategy       400
    1b narrowed strategy    800
    1c risk-only            200
    2a final joint          300
    2b Bollinger            150

Any other integer total scales this reference deterministically (see `allocate`).
`1850` always reproduces the reference split exactly, by construction and by test.
"""

from collections import OrderedDict
from typing import Dict, Union

REFERENCE_TOTAL = 1850
REFERENCE = OrderedDict((
    ("stage_1a_broad", 400),
    ("stage_1b_narrow", 800),
    ("stage_1c_risk", 200),
    ("stage_2a_final", 300),
    ("stage_2b_bollinger", 150),
))
STAGE_KEYS = tuple(REFERENCE)

# A stage below its floor cannot produce a usable TPE posterior: 1a/1b must clear
# TPE's 10 startup trials by a wide margin, and 2a must exceed the enqueued seed.
MINIMUMS = OrderedDict((
    ("stage_1a_broad", 40),
    ("stage_1b_narrow", 40),
    ("stage_1c_risk", 20),
    ("stage_2a_final", 30),
    ("stage_2b_bollinger", 15),
))
MIN_TOTAL = sum(MINIMUMS.values())          # 145
MAX_TOTAL = 100_000

# `trials: "auto"` — a documented total per timeframe. Cost per trial scales with
# the candle count of one backtest, which for a fixed calendar span is inversely
# proportional to the timeframe; higher timeframes are cheap per trial but yield
# fewer trades, so they need more samples. Both effects push the same way. 15m is
# anchored at the canonical 1,850 because that is the budget every V3 campaign in
# this repo (Phases 14/16/17/18/19) actually ran.
AUTO_TOTAL_BY_TIMEFRAME = OrderedDict((
    ("1m", 900),
    ("3m", 1200),
    ("5m", 1500),
    ("15m", 1850),
    ("30m", 2200),
    ("1h", 2800),
    ("2h", 3400),
    ("3h", 3800),
    ("4h", 4200),
))
SUPPORTED_TIMEFRAMES = tuple(AUTO_TOTAL_BY_TIMEFRAME)

# History longer than this many days shifts `auto` up one notch; shorter shifts
# it down. Applied once, deterministically, and reported in the run plan.
LONG_HISTORY_DAYS = 540
SHORT_HISTORY_DAYS = 120
LONG_HISTORY_FACTOR = 1.25
SHORT_HISTORY_FACTOR = 0.75


class BudgetError(ValueError):
    """Raised when a total cannot be allocated across the five stages."""


def auto_total(timeframe: str, span_days: int = None) -> "tuple[int, str]":
    """Resolve `trials: "auto"` to a total. Returns (total, human explanation)."""
    if timeframe not in AUTO_TOTAL_BY_TIMEFRAME:
        raise BudgetError(f"unsupported timeframe for auto trials: {timeframe!r}")
    base = AUTO_TOTAL_BY_TIMEFRAME[timeframe]
    note = f"{timeframe} baseline {base}"
    total = base
    if span_days is not None:
        if span_days >= LONG_HISTORY_DAYS:
            total = int(round(base * LONG_HISTORY_FACTOR))
            note += (f", x{LONG_HISTORY_FACTOR} for {span_days}d history "
                     f">= {LONG_HISTORY_DAYS}d")
        elif span_days < SHORT_HISTORY_DAYS:
            total = int(round(base * SHORT_HISTORY_FACTOR))
            note += (f", x{SHORT_HISTORY_FACTOR} for {span_days}d history "
                     f"< {SHORT_HISTORY_DAYS}d")
    total = max(total, MIN_TOTAL)
    return total, note


def allocate(total: int) -> Dict[str, int]:
    """Split `total` across the five stages. Deterministic; sums to `total` exactly.

    Method (largest-remainder, then floors):
      1. exact_i   = total * REFERENCE_i / 1850
      2. take floor(exact_i); distribute the leftover one at a time to the stages
         with the largest fractional remainder, ties broken by the fixed stage
         order above, so the result depends only on `total`.
      3. raise any stage below its documented minimum, taking the shortfall from
         the largest stages first (again in fixed order) so the sum is preserved.
    """
    if isinstance(total, bool) or not isinstance(total, int):
        raise BudgetError(f"trial total must be a whole number, got {total!r}")
    if total < MIN_TOTAL:
        raise BudgetError(
            f"trial total {total} is below the minimum {MIN_TOTAL} "
            f"({', '.join(f'{k}>={v}' for k, v in MINIMUMS.items())})"
        )
    if total > MAX_TOTAL:
        raise BudgetError(f"trial total {total} exceeds the maximum {MAX_TOTAL}")

    if total == REFERENCE_TOTAL:
        return dict(REFERENCE)

    exact = {k: total * v / REFERENCE_TOTAL for k, v in REFERENCE.items()}
    out = {k: int(v) for k, v in exact.items()}
    leftover = total - sum(out.values())
    if leftover:
        order = sorted(STAGE_KEYS,
                       key=lambda k: (-(exact[k] - int(exact[k])), STAGE_KEYS.index(k)))
        for k in order[:leftover]:
            out[k] += 1

    # Enforce floors while preserving the total.
    for k, floor in MINIMUMS.items():
        while out[k] < floor:
            donor = max((d for d in STAGE_KEYS if d != k),
                        key=lambda d: (out[d] - MINIMUMS[d], -STAGE_KEYS.index(d)))
            if out[donor] - 1 < MINIMUMS[donor]:
                raise BudgetError(
                    f"trial total {total} cannot satisfy every stage minimum"
                )
            out[donor] -= 1
            out[k] += 1

    assert sum(out.values()) == total, (out, total)
    return {k: out[k] for k in STAGE_KEYS}


def resolve(trials: Union[str, int], timeframe: str, span_days: int = None):
    """Return (total, allocation, was_auto, explanation)."""
    if isinstance(trials, str):
        total, note = auto_total(timeframe, span_days)
        return total, allocate(total), True, note
    total = int(trials)
    return total, allocate(total), False, "explicit total from preset"


def enabled_allocation(allocation: Dict[str, int], stages) -> Dict[str, int]:
    """Zero out stages the preset disabled. Skipped stages run no trials."""
    out = dict(allocation)
    if not stages.risk_management:
        out["stage_1c_risk"] = 0
    if not stages.bollinger:
        out["stage_2b_bollinger"] = 0
    return out
