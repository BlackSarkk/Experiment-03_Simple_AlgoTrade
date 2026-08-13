"""
Central configuration module for Delta Exchange ETHUSD 1-Hour Trading Strategy.
Defines strategy hyperparameters, risk management rules, execution constraints, and I/O settings.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class StrategyConfig:
    # Asset & Timeframe
    symbol: str = "ETHUSDT"               # Delta Exchange contract symbol (ETHUSDT perpetual)
    resolution: str = "1h"                # 1-hour timeframe (60m)
    
    # Technical Indicators
    ema_period: int = 51                  # 51 Exponential Moving Average
    rsi_period: int = 14                  # 14 Relative Strength Index
    rsi_overbought: float = 65.0          # RSI Overbought threshold (>= 65)
    rsi_oversold: float = 35.0            # RSI Oversold threshold (<= 35)
    
    # ATR & Consolidation Detection
    atr_period: int = 14                  # 14 Average True Range
    consolidation_candles: int = 8        # 8-candle consolidation detection window
    consolidation_atr_mult: float = 2.2   # Optimal 2.2x ATR consolidation envelope
    
    # Swing High/Low Structure
    swing_lookback: int = 8               # Lookback window for confirmed swing high/low stop-loss
    
    # Risk-Reward Target
    risk_reward_ratio: float = 1.5        # Optimized 1:1.5 Risk to Reward target for 1H crypto cycles

    # Enhanced Filters for PnL & Win Rate Improvement
    use_breakeven_at_1r: bool = False     # Breakeven stop toggle
    use_volume_filter: bool = True        # Require breakout volume >= 20-period Volume SMA
    volume_mult: float = 1.00             # Volume threshold multiplier (1.0 = at or above average volume)
    use_ema_slope_filter: bool = False    # EMA slope filter toggle
    use_trend_filter: bool = False        # 200 EMA macro filter toggle
    trend_ema_period: int = 200           # Macro 200 EMA period


@dataclass
class RiskConfig:
    # Portfolio, Sizing & Leverage
    initial_capital: float = 10000.0      # Initial account balance in USD / USDT
    risk_per_trade_pct: float = 0.015     # 1.5% compounding account risk per trade
    max_position_allocation_pct: float = 0.50  # 50% maximum position capital allocation cap
    leverage: float = 1.0                 # Account Leverage (e.g. 1.0 = Spot/1x, 2.0 = 2x, 5.0 = 5x, 10.0 = 10x)
    
    # Minimum trade constraints
    min_position_size: float = 0.001      # Minimum trade size in base asset (e.g. 0.001 ETH)
    max_leverage_limit: float = 50.0      # Hard leverage safety constraint


@dataclass
class ExecutionConfig:
    # Realism & Friction
    maker_fee_pct: float = 0.0002         # 0.02% Maker fee
    taker_fee_pct: float = 0.0005         # 0.05% Taker fee (standard Delta Exchange taker fee)
    slippage_pct: float = 0.0003          # 0.03% Slippage per order
    use_next_candle_open: bool = True     # Strict execution at next candle open (zero look-ahead bias)


@dataclass
class AppConfig:
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    
    # API & Data
    api_base_url: str = "https://api.delta.exchange"
    lookback_days: int = 180              # Default download period in days
    data_dir: str = "data"
    output_dir: str = "output"
