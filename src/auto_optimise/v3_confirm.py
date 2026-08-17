"""UNSEEN confirmation — the one and only read of the locked partition.

This runs AFTER the winner is frozen. Its result is recorded and reported, and it
can never change the selection: there is no path from here back into any search,
narrowing, seed, risk or Bollinger stage. If UNSEEN disappoints, the honest
outcome is a config whose manifest says so — not a retune.
"""

from typing import Any, Dict

from optimization.v3 import optimizer as V3
from optimization.v3 import spec as V3_SPEC
from strategy.indicators import compute_all_indicators
from backtest.engine import BacktestEngine

UNLOCK_REASON = ("final UNSEEN confirmation, after the V3 winner and Bollinger "
                 "filter were frozen")


def _evaluate(full_frame, cfg, fcfg, lo: int, hi: int) -> Dict[str, Any]:
    """Indicators on the FULL frame first, then slice — never the reverse."""
    ind = compute_all_indicators(full_frame.copy(), cfg.strategy)
    window = ind.iloc[lo:hi].reset_index(drop=True)
    engine = BacktestEngine(cfg)
    strat = V3.SkipHeadStrategy(cfg.strategy, fcfg, V3_SPEC.EVAL_SKIP_BARS)
    engine.strategy = strat
    return V3.metrics(engine.run(window), strat.blocked_count, strat.head_dropped)


def confirm(preset, prepared, winner: Dict[str, Any], bollinger) -> Dict[str, Any]:
    """Open UNSEEN once and measure the frozen winner, BB OFF and BB ON."""
    if not prepared.unseen.is_locked:
        raise RuntimeError(
            "UNSEEN was already unlocked; a campaign may open it exactly once"
        )
    prepared.unseen.unlock(UNLOCK_REASON)

    lo, hi = prepared._bounds["unseen"]
    cfg = V3.build_cfg(preset.symbol, preset.timeframe, winner)
    off = _evaluate(prepared.raw_full, cfg, V3.OFF, lo, hi)
    on = _evaluate(prepared.raw_full, cfg, bollinger, lo, hi)

    return {
        "status": "CONFIRMATION_ONLY",
        "note": ("Measured once, after the winner was frozen. These numbers did "
                 "not influence selection and must never be used to retune."),
        "unlock_reason": UNLOCK_REASON,
        "rows": int(hi - lo),
        "start": str(prepared.unseen_start),
        "end": str(prepared.unseen_end),
        "bollinger_off": off,
        "bollinger_on": on,
    }
