"""
Baseline Risk Management Module.

Sizing policy (see RiskConfig docstring for the authoritative definition):
  * risk_per_trade_pct  -> GROSS price-risk budget; fees/slippage tracked separately.
  * max_position_allocation_pct -> max fraction of equity committed as MARGIN.
    max_notional = (equity * max_position_allocation_pct) * leverage.
  * Invalid / inverted stops are REJECTED, never substituted.
  * Quantity is floored to the instrument quantity step so realized risk can never
    exceed the computed budget.
  * SL/TP are rounded to the instrument tick size.
"""

import math
from dataclasses import dataclass
from typing import Optional

from common.config import RiskConfig, StrategyConfig, ExecutionConfig
from common.utils import setup_logger

logger = setup_logger("RiskManager")


def floor_to_step(value: float, step: float) -> float:
    """Round DOWN to the nearest multiple of `step` (never rounds up)."""
    if step <= 0:
        return value
    steps = math.floor((value / step) + 1e-9)
    return round(steps * step, 10)


def round_to_tick(price: float, tick: float) -> float:
    """Round a price to the nearest instrument tick."""
    if tick <= 0:
        return price
    return round(round(price / tick) * tick, 10)


@dataclass
class PositionSizingResult:
    is_valid: bool
    position_size: float             # In base asset (e.g. ETH)
    nominal_position_value: float    # position_size * entry_price
    margin_required: float           # nominal_value / leverage
    effective_leverage: float        # nominal_value / equity
    capital_allocation_pct: float    # (margin_required / equity) * 100
    risk_amount: float               # Dollar price-risk taken (gross of fees)
    risk_pct: float                  # % of equity risked
    sl_price: float                  # Confirmed Stop-Loss price
    tp_price: float                  # Take-Profit price
    reason: str                      # Validation details


class BaselineRiskManager:
    """Calculates trade sizing, leverage margin, and risk limits."""

    def __init__(
        self,
        risk_config: Optional[RiskConfig] = None,
        strategy_config: Optional[StrategyConfig] = None,
        execution_config: Optional[ExecutionConfig] = None,
    ):
        self.risk_config = risk_config or RiskConfig()
        self.strategy_config = strategy_config or StrategyConfig()
        self.execution_config = execution_config or ExecutionConfig()

    def _reject(self, reason: str, sl_price: float, tp_price: float = 0.0) -> PositionSizingResult:
        logger.warning(f"Position sizing rejected: {reason}")
        return PositionSizingResult(
            is_valid=False, position_size=0.0, nominal_position_value=0.0,
            margin_required=0.0, effective_leverage=0.0, capital_allocation_pct=0.0,
            risk_amount=0.0, risk_pct=0.0, sl_price=sl_price, tp_price=tp_price,
            reason=reason
        )

    def calculate_position(
        self,
        equity: float,
        entry_price: float,
        sl_price: float,
        signal_type: str,
    ) -> PositionSizingResult:
        tick = self.execution_config.tick_size
        step = self.risk_config.quantity_step

        if equity <= 0:
            return self._reject("Account equity depleted", sl_price)

        if entry_price <= 0:
            return self._reject(f"Invalid entry price {entry_price}", sl_price)

        leverage = max(1.0, min(self.risk_config.leverage, self.risk_config.max_leverage_limit))

        # 1. Risk per unit — invalid/inverted stops are rejected, NOT substituted.
        if signal_type == "LONG":
            risk_per_unit = entry_price - sl_price
            if risk_per_unit <= 0:
                return self._reject(
                    f"Invalid LONG stop: sl_price {sl_price} >= entry_price {entry_price}", sl_price
                )
            tp_price = entry_price + (self.strategy_config.risk_reward_ratio * risk_per_unit)
        elif signal_type == "SHORT":
            risk_per_unit = sl_price - entry_price
            if risk_per_unit <= 0:
                return self._reject(
                    f"Invalid SHORT stop: sl_price {sl_price} <= entry_price {entry_price}", sl_price
                )
            tp_price = entry_price - (self.strategy_config.risk_reward_ratio * risk_per_unit)
        else:
            return self._reject(f"Unknown signal_type '{signal_type}'", sl_price)

        sl_out = round_to_tick(sl_price, tick)
        tp_out = round_to_tick(tp_price, tick)

        if self.risk_config.sizing_mode.upper() == "FIXED_NOTIONAL":
            # A/B baseline: flat notional per trade. No risk budget, no allocation cap.
            notional = self.risk_config.fixed_notional or self.risk_config.initial_capital
            max_margin = equity
            final_size = floor_to_step(notional / entry_price, step)
        else:
            # 2. Compounding gross price-risk budget
            raw_size = (equity * self.risk_config.risk_per_trade_pct) / risk_per_unit

            # 3. Margin-based allocation cap:
            #    max_margin = equity * allocation_pct ; max_notional = max_margin * leverage
            max_margin = equity * self.risk_config.max_position_allocation_pct
            max_notional = max_margin * leverage
            max_size_by_allocation = max_notional / entry_price

            # 4. Stricter constraint wins, then floor to the instrument quantity step
            #    (floor, so realized risk can never exceed the budget).
            final_size = floor_to_step(min(raw_size, max_size_by_allocation), step)

        if final_size < self.risk_config.min_position_size or final_size <= 0:
            return self._reject(
                f"Position size {final_size} below min threshold {self.risk_config.min_position_size}",
                sl_out, tp_out
            )

        nominal_value = final_size * entry_price
        margin_required = nominal_value / leverage

        # 5. Margin safety — never open a position we cannot fund.
        if margin_required > equity + 1e-9:
            return self._reject(
                f"Required margin ${margin_required:.2f} exceeds available capital ${equity:.2f}",
                sl_out, tp_out
            )
        if margin_required > max_margin + 1e-9:
            return self._reject(
                f"Required margin ${margin_required:.2f} exceeds allocation cap ${max_margin:.2f}",
                sl_out, tp_out
            )

        actual_risk_dollars = final_size * risk_per_unit

        return PositionSizingResult(
            is_valid=True,
            position_size=final_size,
            nominal_position_value=round(nominal_value, 2),
            margin_required=round(margin_required, 2),
            effective_leverage=round(nominal_value / equity, 2),
            capital_allocation_pct=round((margin_required / equity) * 100.0, 2),
            risk_amount=round(actual_risk_dollars, 2),
            risk_pct=round((actual_risk_dollars / equity) * 100.0, 2),
            sl_price=sl_out,
            tp_price=tp_out,
            reason="Valid Position Sizing"
        )
