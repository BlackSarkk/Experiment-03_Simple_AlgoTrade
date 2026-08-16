"""Trial budget resolution.

`trials: "auto"` sizes the PHASE-A (strategy search) budget only. Phase B (risk
policy) and Phase C (Bollinger) search far fewer dimensions and derive their own
smaller budgets internally; they are not covered by this number.

RATIONALE for the per-timeframe table
-------------------------------------
Cost per trial is dominated by the candle count of one backtest, which scales
inversely with the timeframe for a fixed calendar span. A 1m backtest over the
same window processes ~15x the bars of a 15m backtest, so an equal trial count
would cost ~15x the wall clock. Higher timeframes are cheap per trial but produce
far fewer trades, so they need MORE samples before a score is meaningful. Both
effects push the same direction: trials rise as the timeframe rises.

The only measured precedent in this repo is `src/optimization/multi_tf_optimizer.py`,
which used 750 trials/timeframe, and `deep_15m_optimizer.py` at 5000 for 15m.
The 15m anchor below is set to 750 to match the workflow that actually produced
Candidate #158.

STATUS: PROVISIONAL. These values are placeholders pending a timed measurement of
real per-trial cost on this machine; the measurement task will replace them and
this notice. They are only ever used when the preset says "auto" — an explicit
integer in the preset is always honoured verbatim.
"""

from typing import Union

# timeframe -> Phase-A strategy-search trial budget
AUTO_TRIALS_BY_TIMEFRAME = {
    "1m": 300,
    "3m": 400,
    "5m": 500,
    "15m": 750,
    "30m": 900,
    "1h": 1200,
    "2h": 1500,
    "3h": 1750,
    "4h": 2000,
}

SUPPORTED_TIMEFRAMES = tuple(AUTO_TRIALS_BY_TIMEFRAME.keys())

MIN_TRIALS = 10
MAX_TRIALS = 100_000


def resolve(trials: Union[str, int], timeframe: str) -> "tuple[int, bool]":
    """Return (trial_count, was_auto). Caller has already validated `trials`."""
    if isinstance(trials, str):
        return AUTO_TRIALS_BY_TIMEFRAME[timeframe], True
    return int(trials), False
