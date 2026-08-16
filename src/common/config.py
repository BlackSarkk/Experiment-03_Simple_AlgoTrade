"""
Central configuration module for ETH Strategy Pipeline.
Defines dataclasses for Strategy, Risk, Execution, Platform, and Pipeline configuration.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class StrategyConfig:
    # Asset & Timeframe
    symbol: str = "ETHUSDT"
    resolution: str = "3h"

    # Technical Indicators
    ema_period: int = 51
    rsi_period: int = 14
    rsi_overbought: float = 65.0
    rsi_oversold: float = 35.0
    atr_period: int = 14

    # Consolidation Detection
    consolidation_candles: int = 8
    consolidation_atr_mult: float = 2.2

    # Swing High/Low Structure
    swing_lookback: int = 8

    # Volume Filter
    volume_sma_period: int = 20
    use_volume_filter: bool = True
    volume_mult: float = 1.0

    # Side Isolation
    long_enabled: bool = True
    short_enabled: bool = True

    # Target & Filters
    risk_reward_ratio: float = 1.5
    use_breakeven_at_1r: bool = False
    use_ema_slope_filter: bool = False
    use_trend_filter: bool = False
    trend_ema_period: int = 200
    pyramiding: int = 0


@dataclass
class RiskConfig:
    """Risk policy.

    RISK CONVENTION (GROSS / PRICE-RISK):
      risk_per_trade_pct is a *price-risk* budget only: size = (equity * pct) / |entry - sl|.
      Fees and slippage are NOT deducted from this budget; they are accounted separately
      in the execution/accounting layer. Realized loss on a stopped-out trade is therefore
      slightly larger than risk_per_trade_pct. Optimizer objectives must be read with this
      in mind.

    ALLOCATION CAP SEMANTICS (MARGIN-BASED):
      max_position_allocation_pct is the maximum fraction of account equity committed as
      MARGIN. Leverage then converts that margin into notional:
          max_margin   = equity * max_position_allocation_pct
          max_notional = max_margin * leverage
      (Previously the cap was applied to notional directly and then multiplied by leverage,
      which made a "50% cap" mean 175% of equity at 3.5x. It is now unambiguous.)
      At leverage 1.0 both formulations coincide, so the frozen baseline is unchanged.
    """
    initial_capital: float = 10000.0
    risk_per_trade_pct: float = 0.015       # 1.5% compounding equity price-risk per trade
    max_position_allocation_pct: float = 0.50  # 50% of equity max committed as margin
    leverage: float = 1.0                   # SINGLE SOURCE OF TRUTH for the leverage default
    min_position_size: float = 0.001        # Minimum order size in base asset
    max_leverage_limit: float = 50.0        # Hard leverage cap
    quantity_step: float = 0.001            # Instrument quantity step (ETHUSDT futures = 0.001)

    # Sizing mode — A/B harness only. "RISK_BASED" is the production default.
    #   RISK_BASED     : compounding price-risk budget + margin allocation cap (production)
    #   FIXED_NOTIONAL : flat notional per trade, no risk budget, no allocation cap
    sizing_mode: str = "RISK_BASED"
    fixed_notional: float = 0.0             # 0 -> falls back to initial_capital


@dataclass
class ExecutionConfig:
    mode: str = "REFERENCE"                 # "REFERENCE" or "REALISTIC"
    maker_fee_pct: float = 0.0002           # 0.02% Maker fee
    taker_fee_pct: float = 0.0005           # 0.05% Taker fee
    slippage_pct: float = 0.0003            # 0.03% Slippage for REALISTIC mode
    slippage_ticks: float = 1.0             # 1 tick (0.1 USDT on ETH) for REFERENCE mode
    tick_size: float = 0.01                 # Instrument tick size (e.g., 0.01 for ETHUSDT)
    use_next_candle_open: bool = True       # Execute on next candle open
    same_bar_sl_priority: bool = True       # Stop loss takes precedence if both SL & TP hit on same bar


@dataclass
class PlatformConfig:
    platform: str = "BINANCE_FUTURES"       # "BINANCE_FUTURES", "BINANCE", or "DELTA"
    symbol: str = "ETHUSDT"
    resolution: str = "3h"
    start_date: Optional[str] = "2024-01-01"
    end_date: Optional[str] = "2026-08-13"
    days: Optional[int] = None


@dataclass
class PipelineConfig:
    run_backtest: bool = True
    run_robustness: bool = False
    run_forward_test: bool = False

    execution_mode: str = "REFERENCE"       # "REFERENCE" or "REALISTIC"
    forward_mode: str = "PAPER"             # "PAPER"

    # Reset & Cache Controls (Defaults must always be False for safety!)
    hard_reset: bool = False                # Complete destruction of all generated files
    risk_policy_source: str = ""       # Provenance of the active risk policy (set at load)
    reset: bool = False                     # Stage-scoped reset
    clear_cache: bool = False               # Market data cache deletion ONLY
    reset_cache: bool = False               # Alias for clear_cache
    reset_forward_state: bool = False       # Stage-specific forward state reset
    resume_forward_state: bool = True       # Default resume enabled when reset=False

    # Paper Forward Controls
    experiment_duration_days: float = 7.0   # Calendar Days
    equity_snapshot_interval_mins: int = 10 # Periodic 10-min equity snapshots
    auto_save_seconds: float = 30.0         # Atomic recovery state auto-save frequency (sec)

    data_dir: str = "data"
    results_dir: str = "results"
    logs_dir: str = "logs"

    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    platform: PlatformConfig = field(default_factory=PlatformConfig)
