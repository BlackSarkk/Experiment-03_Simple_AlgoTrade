"""Trial evaluation — the only place the optimizer touches the trading stack.

Every candidate is simulated by the production `BacktestEngine` driving the
production `BaselineStrategy` and `BaselineRiskManager`, and scored from the
production `BacktestMetrics`. There is no research backtester and no approximated
PnL path; nothing in this module reimplements trading behaviour.

Indicators are recomputed per trial because they depend on the trial's own
parameters. They are always computed on `PreparedData.context_for(partition)`,
which prepends the Stage-1 warmup rows, and the lead-in is dropped only afterwards.
Computing them on a bare partition frame would restart every rolling window at the
partition's first candle — the defect the legacy optimizers carry.
"""

import contextlib
import io
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

import pandas as pd

from backtest.engine import BacktestEngine
from backtest.metrics import BacktestMetrics
from common.config import PipelineConfig
from filters.masked_strategy import MaskedStrategy
from filters.stage_1_bollinger.filter import (BollingerFilterConfig,
                                              allow_mask as bb_allow_mask,
                                              compute_bollinger)
from strategy.indicators import compute_all_indicators

# ---------------------------------------------------------------------------
# Neutral Phase-A risk policy.
#
# These are the `RiskConfig` dataclass defaults, chosen deliberately:
# at leverage 1.0 the margin-based and notional-based allocation caps coincide,
# so sizing cannot advantage one candidate over another, and the frozen baseline
# was itself validated at this policy. Identical for every trial. Stage [4/6]
# searches these; Phase A never does.
# ---------------------------------------------------------------------------
NEUTRAL_LEVERAGE = 1.0
NEUTRAL_RISK_PER_TRADE_PCT = 0.015        # 1.5% price-risk budget
NEUTRAL_MAX_ALLOCATION_PCT = 0.50         # 50% of equity as margin


@dataclass(frozen=True)
class NeutralRisk:
    leverage: float = NEUTRAL_LEVERAGE
    risk_per_trade_pct: float = NEUTRAL_RISK_PER_TRADE_PCT
    max_position_allocation_pct: float = NEUTRAL_MAX_ALLOCATION_PCT

    def as_dict(self) -> Dict[str, float]:
        return {
            "leverage": self.leverage,
            "risk_per_trade_pct": self.risk_per_trade_pct,
            "max_position_allocation_pct": self.max_position_allocation_pct,
        }


NEUTRAL_RISK = NeutralRisk()


def build_config(params: Dict[str, Any], preset,
                 risk: Optional[Dict[str, float]] = None) -> PipelineConfig:
    """Assemble a full PipelineConfig for one trial.

    Strategy fields come from the trial. Risk fields are pinned to the neutral
    policy unless `risk` is supplied — stage [4/6] is the only caller that
    supplies one, and it passes fractions, not percents. Execution keeps the
    project defaults (0.05% taker, 1 tick slippage, 0.01 tick size) so cost
    modelling is identical across every evaluation in the campaign.
    """
    cfg = PipelineConfig()

    cfg.platform.platform = preset.platform
    cfg.platform.symbol = preset.symbol
    cfg.platform.resolution = preset.timeframe

    s = cfg.strategy
    s.symbol = preset.symbol
    s.resolution = preset.timeframe
    s.ema_period = int(params["ema_period"])
    s.rsi_period = int(params["rsi_period"])
    s.rsi_overbought = float(params["rsi_overbought"])
    s.rsi_oversold = float(params["rsi_oversold"])
    s.atr_period = int(params["atr_period"])
    s.consolidation_candles = int(params["consolidation_candles"])
    s.consolidation_atr_mult = float(params["consolidation_atr_mult"])
    s.swing_lookback = int(params["swing_lookback"])
    s.volume_sma_period = int(params["volume_sma_period"])
    s.use_volume_filter = s.volume_sma_period > 0
    s.volume_mult = float(params["volume_mult"])
    s.risk_reward_ratio = float(params["risk_reward_ratio"])

    # Direction comes from the preset and is shared by both sides — one parameter
    # set, one simulation, whatever the direction combination.
    s.long_enabled = bool(preset.direction.long_enabled)
    s.short_enabled = bool(preset.direction.short_enabled)

    r = cfg.risk
    r.initial_capital = float(preset.initial_balance)
    policy = risk or NEUTRAL_RISK.as_dict()
    r.leverage = float(policy["leverage"])
    r.risk_per_trade_pct = float(policy["risk_per_trade_pct"])
    r.max_position_allocation_pct = float(policy["max_position_allocation_pct"])

    return cfg


def flatten_metrics(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Reduce the production metrics dict to the fields Phase A scores on."""
    if not raw or raw.get("error"):
        return None
    try:
        cap = raw["Capital"]
        tr = raw["Trades"]
        prof = raw["Profitability"]
        risk = raw["Risk"]
        costs = raw["Costs"]
    except KeyError:
        return None

    return {
        "net_return_pct": float(cap["Net Return %"]),
        "net_pnl": float(cap["Net Profit"]),
        "gross_profit": float(cap["Gross Profit"]),
        "gross_loss": float(cap["Gross Loss"]),
        "profit_factor": float(prof["Net PF"]),
        "expectancy": float(prof["Expectancy"]),
        "sharpe": float(risk["Sharpe Ratio"]),
        "max_dd_pct": float(risk["Max Drawdown %"]),
        "trades": int(tr["Total Trades"]),
        "wins": int(tr["Winners"]),
        "losses": int(tr["Losers"]),
        "win_rate": float(tr["Win Rate %"]),
        "long_trades": int(tr["LONG Trades"]),
        "short_trades": int(tr["SHORT Trades"]),
        "fees": float(costs["Total Commission"]),
    }


def run_backtest(prepared, partition: str, params: Dict[str, Any], preset,
                 risk: Optional[Dict[str, float]] = None,
                 bollinger: Optional[Dict[str, Any]] = None
                 ) -> Optional[Dict[str, Any]]:
    """Simulate one candidate on TRAIN or VALIDATION. Returns flat metrics or None."""
    return run_on_context(prepared.context_for(partition), params, preset,
                          risk, bollinger)


def run_backtest_window(prepared, start_ts, end_ts, params: Dict[str, Any],
                        preset, risk: Optional[Dict[str, float]] = None,
                        bollinger: Optional[Dict[str, Any]] = None
                        ) -> Optional[Dict[str, Any]]:
    """Simulate one candidate on an arbitrary in-sample window (stage [3/6])."""
    return run_on_context(prepared.context_for_window(start_ts, end_ts),
                          params, preset, risk, bollinger)


def run_on_context(context, params: Dict[str, Any], preset,
                   risk: Optional[Dict[str, float]] = None,
                   bollinger: Optional[Dict[str, Any]] = None
                   ) -> Optional[Dict[str, Any]]:
    """Simulate one candidate on a (frame_with_warmup, lead_rows) pair.

    `bollinger` is a `BollingerFilterConfig`-shaped dict. When enabled, its mask is
    computed on the FULL warmup-backed frame and only then sliced, exactly as
    `main.py` does — computing it on the sliced frame would restart the band's
    rolling windows at the partition edge. The mask is applied through the frozen
    `MaskedStrategy`, which can only remove signals.
    """
    cfg = build_config(params, preset, risk)
    frame, lead = context
    masked = None
    try:
        df_ind = compute_all_indicators(frame.copy(), cfg.strategy)

        mask = None
        if bollinger and bollinger.get("enabled"):
            bb_cfg = BollingerFilterConfig.from_dict(bollinger)
            full_mask = bb_allow_mask(compute_bollinger(df_ind, bb_cfg), bb_cfg)
            mask = full_mask[lead:]

        # Drop the warmup lead-in: it seeded the indicators and can never trade.
        df_ind = df_ind.iloc[lead:].reset_index(drop=True)
        engine = BacktestEngine(cfg)
        if mask is not None:
            masked = MaskedStrategy(cfg.strategy, mask)
            engine.strategy = masked
        # The engine renders a per-bar tqdm bar and a rich console; across
        # thousands of trials that is pure noise and it would fight the live
        # dashboard for the terminal. Silence output only — behaviour is untouched.
        with open(os.devnull, "w") as devnull, \
                contextlib.redirect_stderr(devnull), \
                contextlib.redirect_stdout(io.StringIO()):
            result = engine.run(df_ind)
    except Exception:
        # A malformed parameter combination must cost one trial, not the campaign.
        return None

    raw = BacktestMetrics.calculate(
        result["trades"], result["equity_curve"], cfg.risk.initial_capital
    )
    flat = flatten_metrics(raw)
    if flat is not None:
        # Signal accounting. `raw_signals` is what the frozen strategy produced
        # before any filtering, so blocked/passed are exact rather than inferred.
        if masked is not None:
            flat["raw_signals"] = int(masked.total_signals)
            flat["signals_blocked"] = int(masked.blocked_count)
            flat["signals_passed"] = int(masked.total_signals - masked.blocked_count)
        else:
            flat["raw_signals"] = None
            flat["signals_blocked"] = 0
            flat["signals_passed"] = None
    return flat
