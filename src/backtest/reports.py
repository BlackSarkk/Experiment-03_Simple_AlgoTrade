"""
Report Exporter Module.
Exports trades.csv (including entry indicator snapshots), equity_curve.csv, and summary scorecards.
"""

import os
from typing import List, Dict, Any
import pandas as pd


class BacktestExporter:
    """Exports structured CSV logs and metrics files."""

    def __init__(self, results_dir: str = "results/backtest"):
        self.results_dir = results_dir
        os.makedirs(self.results_dir, exist_ok=True)

    def export_trades(self, trades: List[Any], filename: str = "trades.csv") -> str:
        filepath = os.path.join(self.results_dir, filename)
        records = []
        for t in trades:
            records.append({
                "trade_id": t.trade_id,
                "side": t.signal_type,
                "signal_timestamp": t.signal_time,
                "entry_timestamp": t.entry_time,
                "entry_price": t.entry_price,
                "exit_timestamp": t.exit_time,
                "exit_price": t.exit_price,
                "quantity": t.size,
                "position_notional": t.nominal_value,
                "margin": t.margin_required,
                "leverage": t.effective_leverage,
                "holding_duration": t.duration_bars,
                "sl": t.sl_price,
                "tp": t.tp_price,
                "gross_pnl": t.gross_pnl,
                "fees": t.total_fees,
                "slippage": t.slippage_cost,
                "net_pnl": t.net_pnl,
                "return_pct": t.net_return_pct,
                "r_multiple": t.r_multiple,
                "balance_before": round(t.equity_after - t.net_pnl, 2),
                "balance_after": t.equity_after,
                "exit_reason": t.exit_reason,
                # Entry Indicator Snapshots
                "ema_51": getattr(t, "ema_51", None),
                "rsi": getattr(t, "rsi", None),
                "atr": getattr(t, "atr", None),
                "consolidation_range": getattr(t, "consolidation_range", None),
                "volume": getattr(t, "volume", None),
                "vol_sma_20": getattr(t, "vol_sma_20", None),
                "swing_high": getattr(t, "swing_high", None),
                "swing_low": getattr(t, "swing_low", None),
            })

        df = pd.DataFrame(records)
        df.to_csv(filepath, index=False)
        return filepath

    def export_equity_curve(self, equity_curve: List[Dict[str, Any]], filename: str = "equity_curve.csv") -> str:
        filepath = os.path.join(self.results_dir, filename)
        df = pd.DataFrame(equity_curve)
        df.to_csv(filepath, index=False)
        return filepath
