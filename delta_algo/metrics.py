"""
Performance Analytics and Strategy Metrics Engine.
Computes:
- Win Rate, Profit Factor, Expectancy
- Net Total Return ($ & %) and Annualized Return
- Max Drawdown ($ & %) and Drawdown Duration
- Sharpe Ratio & Sortino Ratio (Annualized for 24/7 Crypto Market)
- Trade distribution (Long vs Short, TP vs SL)
- Fee & Slippage Impact
"""

import math
from typing import List, Dict, Any
import numpy as np
import pandas as pd
from backtester import Trade


class PerformanceMetrics:
    """Calculates quantitative performance and risk metrics."""

    @staticmethod
    def calculate(
        trades: List[Trade],
        equity_curve: List[Dict[str, Any]],
        initial_capital: float = 10000.0,
    ) -> Dict[str, Any]:
        eq_df = pd.DataFrame(equity_curve)
        
        if eq_df.empty:
            return {"error": "Empty equity curve"}

        final_capital = eq_df["equity"].iloc[-1]
        net_profit_dollar = final_capital - initial_capital
        net_profit_pct = (net_profit_dollar / initial_capital) * 100.0

        # Drawdown calculation
        eq_df["peak"] = eq_df["equity"].cummax()
        eq_df["drawdown_dollar"] = eq_df["peak"] - eq_df["equity"]
        eq_df["drawdown_pct"] = (eq_df["drawdown_dollar"] / eq_df["peak"]) * 100.0

        max_dd_dollar = float(eq_df["drawdown_dollar"].max())
        max_dd_pct = float(eq_df["drawdown_pct"].max())

        # Drawdown duration in bars
        is_dd = eq_df["drawdown_dollar"] > 0
        dd_runs = (~is_dd).cumsum()[is_dd]
        max_dd_duration_bars = int(dd_runs.value_counts().max()) if not dd_runs.empty else 0

        # Hourly returns for Sharpe and Sortino
        eq_df["hourly_ret"] = eq_df["equity"].pct_change().fillna(0)
        mean_ret = eq_df["hourly_ret"].mean()
        std_ret = eq_df["hourly_ret"].std()

        # Crypto 24/7/365 has ~8760 hours per year
        annual_factor = math.sqrt(8760)
        sharpe_ratio = (mean_ret / std_ret * annual_factor) if std_ret > 1e-8 else 0.0

        # Downside deviation for Sortino
        neg_rets = eq_df["hourly_ret"][eq_df["hourly_ret"] < 0]
        downside_std = neg_rets.std() if len(neg_rets) > 1 else 0.0
        sortino_ratio = (mean_ret / downside_std * annual_factor) if downside_std > 1e-8 else 0.0

        total_hours = len(eq_df)
        years = total_hours / 8760.0
        cagr_pct = (((final_capital / initial_capital) ** (1.0 / max(years, 0.01))) - 1.0) * 100.0 if years > 0.05 and final_capital > 0 else net_profit_pct

        # Trade-level statistics
        total_trades = len(trades)
        if total_trades == 0:
            return {
                "Initial Capital ($)": round(initial_capital, 2),
                "Final Capital ($)": round(final_capital, 2),
                "Net Profit ($)": round(net_profit_dollar, 2),
                "Net Profit (%)": round(net_profit_pct, 2),
                "Total Trades": 0,
                "Win Rate (%)": 0.0,
                "Profit Factor": 0.0,
                "Max Drawdown ($)": round(max_dd_dollar, 2),
                "Max Drawdown (%)": round(max_dd_pct, 2),
                "Max Drawdown Duration (hours)": max_dd_duration_bars,
                "Sharpe Ratio": round(sharpe_ratio, 2),
                "Sortino Ratio": round(sortino_ratio, 2),
            }

        pnls = [t.net_pnl for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]

        win_count = len(wins)
        loss_count = len(losses)
        win_rate = (win_count / total_trades) * 100.0

        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)

        avg_win = (gross_profit / win_count) if win_count > 0 else 0.0
        avg_loss = (gross_loss / loss_count) if loss_count > 0 else 0.0
        payoff_ratio = (avg_win / avg_loss) if avg_loss > 0 else 0.0

        expectancy_dollar = sum(pnls) / total_trades
        avg_r = sum(t.r_multiple for t in trades) / total_trades

        tp_hits = sum(1 for t in trades if t.exit_reason == "TP")
        sl_hits = sum(1 for t in trades if t.exit_reason == "SL")
        be_hits = sum(1 for t in trades if t.exit_reason == "BE_SL")
        long_trades = sum(1 for t in trades if t.signal_type == "LONG")
        short_trades = sum(1 for t in trades if t.signal_type == "SHORT")

        total_fees = sum(t.total_fees for t in trades)
        total_slippage = sum(t.slippage_cost for t in trades)
        avg_duration_bars = sum(t.duration_bars for t in trades) / total_trades

        return {
            "Initial Capital ($)": round(initial_capital, 2),
            "Final Capital ($)": round(final_capital, 2),
            "Net Profit ($)": round(net_profit_dollar, 2),
            "Net Profit (%)": round(net_profit_pct, 2),
            "CAGR / Ann. Return (%)": round(cagr_pct, 2),
            "Total Trades": total_trades,
            "Winning Trades": win_count,
            "Losing Trades": loss_count,
            "Win Rate (%)": round(win_rate, 2),
            "Profit Factor": round(profit_factor, 2),
            "Payoff Ratio (Avg Win / Avg Loss)": round(payoff_ratio, 2),
            "Avg Win ($)": round(avg_win, 2),
            "Avg Loss ($)": round(avg_loss, 2),
            "Expectancy per Trade ($)": round(expectancy_dollar, 2),
            "Average R-Multiple": round(avg_r, 2),
            "Max Drawdown ($)": round(max_dd_dollar, 2),
            "Max Drawdown (%)": round(max_dd_pct, 2),
            "Max Drawdown Duration (hours)": max_dd_duration_bars,
            "Sharpe Ratio (Annualized)": round(sharpe_ratio, 2),
            "Sortino Ratio (Annualized)": round(sortino_ratio, 2),
            "Long Trades": long_trades,
            "Short Trades": short_trades,
            "Take-Profit Hits": tp_hits,
            "Breakeven Hits": be_hits,
            "Stop-Loss Hits": sl_hits,
            "Avg Trade Duration (hours)": round(avg_duration_bars, 1),
            "Total Fees Paid ($)": round(total_fees, 2),
            "Total Slippage Cost ($)": round(total_slippage, 2),
        }
