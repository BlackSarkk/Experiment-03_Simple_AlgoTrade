"""
Risk Management and Position Sizing Module with Leverage Support.
Enforces:
- Compounding Account Equity Risk per trade (e.g. 1.0%, 1.5%, 2.0%)
- Configurable Leverage Multiplier (1x to 50x)
- Maximum position capital allocation cap
- Exact Margin calculations
- Dynamic 1:1.5 / 1:2 Take-Profit recalibration based on realized entry price
"""

from dataclasses import dataclass
from typing import Optional, Tuple
from config import RiskConfig, StrategyConfig


@dataclass
class SizingResult:
    is_valid: bool
    position_size: float             # In base asset (e.g. ETH)
    nominal_position_value: float    # position_size * entry_price
    margin_required: float           # nominal_value / leverage
    effective_leverage: float        # nominal_value / equity
    capital_allocation_pct: float    # nominal_value / current_equity
    risk_amount: float               # Dollar risk taken on the trade
    risk_pct: float                  # Percentage of equity risked
    sl_price: float                  # Confirmed Stop-Loss price
    tp_price: float                  # Recalibrated Take-Profit price
    reason: str                      # Sizing / validation explanation


class RiskManager:
    """Computes exact order sizes, leverage, margin, and risk guardrails."""

    def __init__(self, risk_config: Optional[RiskConfig] = None, strategy_config: Optional[StrategyConfig] = None):
        self.risk_config = risk_config or RiskConfig()
        self.strategy_config = strategy_config or StrategyConfig()

    def calculate_position(
        self,
        equity: float,
        entry_price: float,
        sl_price: float,
        signal_type: str,
    ) -> SizingResult:
        if equity <= 0:
            return SizingResult(
                is_valid=False, position_size=0.0, nominal_position_value=0.0,
                margin_required=0.0, effective_leverage=0.0,
                capital_allocation_pct=0.0, risk_amount=0.0, risk_pct=0.0,
                sl_price=sl_price, tp_price=0.0, reason="Account equity depleted"
            )

        leverage = max(1.0, min(self.risk_config.leverage, self.risk_config.max_leverage_limit))

        # 1. Calculate Risk per Unit
        if signal_type == "LONG":
            risk_per_unit = entry_price - sl_price
            if risk_per_unit <= 0:
                sl_price = entry_price * 0.99
                risk_per_unit = entry_price - sl_price
            tp_price = entry_price + (self.strategy_config.risk_reward_ratio * risk_per_unit)
        else:  # SHORT
            risk_per_unit = sl_price - entry_price
            if risk_per_unit <= 0:
                sl_price = entry_price * 1.01
                risk_per_unit = sl_price - entry_price
            tp_price = entry_price - (self.strategy_config.risk_reward_ratio * risk_per_unit)

        # 2. Risk-Budget Sizing
        target_risk_dollars = equity * self.risk_config.risk_per_trade_pct
        raw_size = target_risk_dollars / risk_per_unit

        # 3. Leverage and Max Allocation Cap
        # Maximum allowable nominal position value = equity * leverage * allocation_ratio
        max_nominal_capacity = equity * leverage * self.risk_config.max_position_allocation_pct
        max_size_by_leverage = max_nominal_capacity / entry_price

        # Take the stricter limit
        final_size = min(raw_size, max_size_by_leverage)
        final_size = round(final_size, 4)

        nominal_value = final_size * entry_price
        margin_required = round(nominal_value / leverage, 2)
        effective_leverage = round(nominal_value / equity, 2)
        allocation_pct = round((nominal_value / equity) * 100.0, 2)
        actual_risk_dollars = round(final_size * risk_per_unit, 2)
        actual_risk_pct = round((actual_risk_dollars / equity) * 100.0, 2)

        if final_size < self.risk_config.min_position_size:
            return SizingResult(
                is_valid=False,
                position_size=final_size,
                nominal_position_value=round(nominal_value, 2),
                margin_required=margin_required,
                effective_leverage=effective_leverage,
                capital_allocation_pct=allocation_pct,
                risk_amount=actual_risk_dollars,
                risk_pct=actual_risk_pct,
                sl_price=round(sl_price, 2),
                tp_price=round(tp_price, 2),
                reason=f"Position size {final_size} below minimum {self.risk_config.min_position_size}"
            )

        capped_notice = " [Leverage Cap Enforced]" if raw_size > max_size_by_leverage else ""
        return SizingResult(
            is_valid=True,
            position_size=final_size,
            nominal_position_value=round(nominal_value, 2),
            margin_required=margin_required,
            effective_leverage=effective_leverage,
            capital_allocation_pct=allocation_pct,
            risk_amount=actual_risk_dollars,
            risk_pct=actual_risk_pct,
            sl_price=round(sl_price, 2),
            tp_price=round(tp_price, 2),
            reason=f"Valid ({leverage}x Leverage, Margin: ${margin_required:.2f}, Risk: ${actual_risk_dollars:.2f}{capped_notice})"
        )
