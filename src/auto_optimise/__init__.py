"""Human-operable optimizer.

Orchestration only. Every trial ultimately runs the production BacktestEngine /
BaselineStrategy / BaselineRiskManager unchanged; nothing in this package may
modify trading, sizing, execution or accounting behaviour.

V1 scope: CLI, preset schema, history resolution, output-name guard, run plan.
The optimization phases themselves are not implemented yet.
"""

__all__ = ["preset", "history", "output_guard", "runplan", "trials", "ui"]
