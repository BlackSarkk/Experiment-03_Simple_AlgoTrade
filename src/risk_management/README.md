# Risk Management Architecture

This directory houses risk management and position sizing components.

## Current Baseline Risk Engine (`baseline.py`)
- **Risk Budgeting**: Compounding 1.5% account equity risk per trade.
- **Leverage Handling**: 3.5x leverage with margin allocation tracking.
- **Allocation Cap**: 50.0% cap on nominal position capital relative to available account equity.
- **Stop Loss Guardrail**: Minimum 0.4x ATR distance fallback.
- **Take Profit Geometry**: Fixed 1.5R (or 2.0R) risk-to-reward ratio.

## Extension Guidelines
Future risk managers can be implemented by subclassing or adhering to the `calculate_position` contract interface.
