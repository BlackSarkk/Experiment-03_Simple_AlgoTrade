"""Generic precomputed-mask signal gate.

`MaskedStrategy` wraps the frozen `BaselineStrategy` and drops signals whose candle is
disallowed by a boolean mask that was computed elsewhere. It is deliberately filter-agnostic:
it knows nothing about Bollinger or any other stage, so no filter has to depend on another
filter's package to reuse it.

Why a precomputed mask instead of each filter subclassing BaselineStrategy directly:
filter indicators need the same pre-window warmup the strategy indicators get. `main.py`
computes every filter mask on the FULL cached frame, ANDs them together, slices the result
with the same evaluation-window mask applied to the bars, and hands the sliced mask here.
Computing a filter on the already-sliced frame would restart its rolling windows at the
window edge and silently change which signals are blocked.

Invariants:
  * It can only ever REMOVE signals. It never creates one, and never touches entry/exit
    prices, SL/TP, sizing, fees or execution.
  * With no mask (or an all-True mask) the output is byte-identical to the baseline.
  * Strategy formulas are never modified — `super().generate_signals()` is called unchanged.
"""

from typing import List, Optional

import numpy as np
import pandas as pd

from strategy.baseline_strategy import BaselineStrategy, Signal


class MaskedStrategy(BaselineStrategy):
    """BaselineStrategy + a precomputed per-candle allow mask (True = signal allowed)."""

    def __init__(self, strategy_config=None, allow_mask: Optional[np.ndarray] = None):
        super().__init__(strategy_config)
        self.allow_mask = None if allow_mask is None else np.asarray(allow_mask, dtype=bool)
        self.blocked_count = 0
        self.total_signals = 0

    def generate_signals(self, df: pd.DataFrame) -> List[Signal]:
        signals = super().generate_signals(df)          # unchanged strategy output
        self.total_signals = len(signals)
        self.blocked_count = 0

        if self.allow_mask is None or not signals:
            return signals

        if len(self.allow_mask) != len(df):
            raise ValueError(
                f"MaskedStrategy allow_mask length {len(self.allow_mask)} does not match the "
                f"{len(df)}-row evaluation frame. The mask must be sliced with the same "
                f"evaluation-window mask as the bars."
            )

        kept = [s for s in signals if self.allow_mask[s.candle_idx]]
        self.blocked_count = len(signals) - len(kept)
        return kept
