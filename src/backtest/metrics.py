"""
Comprehensive Backtest Metrics Engine.
Computes exhaustive quantitative performance, risk, cost, time, and trade distribution metrics.
"""

import math
from typing import List, Dict, Any
import numpy as np
import pandas as pd


class BacktestMetrics:
    """Calculates granular performance, risk, cost, and time analytics."""

    @staticmethod
    def calculate(
        trades: List[Any],
        equity_curve: List[Dict[str, Any]],
        initial_capital: float = 10000.0,
    ) -> Dict[str, Any]:
        eq_df = pd.DataFrame(equity_curve)

        if eq_df.empty:
            return {"error": "Empty equity curve"}

        final_capital = float(eq_df["equity"].iloc[-1])
        net_profit_dollar = final_capital - initial_capital
        net_profit_pct = (net_profit_dollar / initial_capital) * 100.0

        # Drawdown calculation
        eq_df["peak"] = eq_df["equity"].cummax()
        eq_df["drawdown_dollar"] = eq_df["peak"] - eq_df["equity"]
        eq_df["drawdown_pct"] = (eq_df["drawdown_dollar"] / eq_df["peak"]) * 100.0

        max_dd_dollar = float(eq_df["drawdown_dollar"].max())
        max_dd_pct = float(eq_df["drawdown_pct"].max())
        avg_dd_pct = float(eq_df["drawdown_pct"].mean())

        # Drawdown duration in bars
        is_dd = eq_df["drawdown_dollar"] > 0
        dd_runs = (~is_dd).cumsum()[is_dd]
        max_dd_duration_bars = int(dd_runs.value_counts().max()) if not dd_runs.empty else 0

        # Returns for Sharpe and Sortino
        eq_df["bar_ret"] = eq_df["equity"].pct_change().fillna(0)
        mean_ret = eq_df["bar_ret"].mean()
        std_ret = eq_df["bar_ret"].std()

        # Assuming 3-hour candles (~2920 bars per year in 24/7 market)
        bars_per_year = 2920.0
        annual_factor = math.sqrt(bars_per_year)

        sharpe_ratio = (mean_ret / std_ret * annual_factor) if std_ret > 1e-8 else 0.0
        neg_rets = eq_df["bar_ret"][eq_df["bar_ret"] < 0]
        downside_std = neg_rets.std() if len(neg_rets) > 1 else 0.0
        sortino_ratio = (mean_ret / downside_std * annual_factor) if downside_std > 1e-8 else 0.0

        total_bars = len(eq_df)
        years = (total_bars * 3.0) / 8760.0  # 3h candles
        cagr_pct = (((final_capital / initial_capital) ** (1.0 / max(years, 0.01))) - 1.0) * 100.0 if years > 0.05 and final_capital > 0 else net_profit_pct

        total_trades = len(trades)
        if total_trades == 0:
            return {
                "Capital": {
                    "Initial Balance": round(initial_capital, 2),
                    "Final Balance": round(final_capital, 2),
                    "Final Equity": round(final_capital, 2),
                    "Net Profit": 0.0,
                    "Net Loss": 0.0,
                    "Net Return %": 0.0,
                    "Gross Profit": 0.0,
                    "Gross Loss": 0.0,
                },
                "Trades": {
                    "Total Trades": 0, "LONG Trades": 0, "SHORT Trades": 0,
                    "Winners": 0, "Losers": 0, "Breakeven": 0,
                    "Win Rate %": 0.0, "Loss Rate %": 0.0,
                },
                "Profitability": {
                    "Gross PF": 0.0, "Net PF": 0.0, "Average Trade": 0.0,
                    "Median Trade": 0.0, "Average Winner": 0.0, "Average Loser": 0.0,
                    "Largest Winner": 0.0, "Largest Loser": 0.0, "Expectancy": 0.0,
                    "Average R Multiple": 0.0,
                },
                "Risk": {
                    "Max Drawdown $": round(max_dd_dollar, 2),
                    "Max Drawdown %": round(max_dd_pct, 2),
                    "Average Drawdown %": round(avg_dd_pct, 2),
                    "Drawdown Duration (bars)": max_dd_duration_bars,
                    "Sharpe Ratio": round(sharpe_ratio, 2),
                    "Sortino Ratio": round(sortino_ratio, 2),
                    "Longest Winning Streak": 0, "Longest Losing Streak": 0,
                },
                "Costs": {
                    "Total Commission": 0.0, "Total Slippage": 0.0,
                    "Total Transaction Costs": 0.0, "Fees % of Gross Profit": 0.0,
                    "Average Cost Per Trade": 0.0,
                },
                "LONG_vs_SHORT": {
                    "LONG Return $": 0.0, "LONG Win Rate %": 0.0, "LONG Trades": 0,
                    "SHORT Return $": 0.0, "SHORT Win Rate %": 0.0, "SHORT Trades": 0,
                },
                "Time_Analysis": {
                    "Avg Holding Bars": 0.0, "Median Holding Bars": 0.0,
                    "Trades Per Month": 0.0,
                }
            }

        # Detailed calculations
        net_pnls = [t.net_pnl for t in trades]
        gross_pnls = [t.gross_pnl for t in trades]
        wins = [p for p in net_pnls if p > 0]
        losses = [p for p in net_pnls if p < 0]
        be = [p for p in net_pnls if p == 0]

        win_count = len(wins)
        loss_count = len(losses)
        be_count = len(be)
        win_rate = (win_count / total_trades) * 100.0
        loss_rate = (loss_count / total_trades) * 100.0

        gross_profit = sum(p for p in gross_pnls if p > 0)
        gross_loss = abs(sum(p for p in gross_pnls if p < 0))
        gross_pf = (gross_profit / gross_loss) if gross_loss > 0 else 999.0

        net_gross_profit = sum(wins)
        net_gross_loss = abs(sum(losses))
        net_pf = (net_gross_profit / net_gross_loss) if net_gross_loss > 0 else 999.0

        avg_trade = sum(net_pnls) / total_trades
        median_trade = float(np.median(net_pnls))
        avg_winner = (sum(wins) / win_count) if win_count > 0 else 0.0
        avg_loser = (sum(losses) / loss_count) if loss_count > 0 else 0.0
        largest_winner = max(net_pnls) if net_pnls else 0.0
        largest_loser = min(net_pnls) if net_pnls else 0.0
        expectancy = avg_trade
        avg_r = sum(t.r_multiple for t in trades) / total_trades

        # Streaks
        win_streak = max_win_streak = 0
        loss_streak = max_loss_streak = 0
        for p in net_pnls:
            if p > 0:
                win_streak += 1
                loss_streak = 0
                max_win_streak = max(max_win_streak, win_streak)
            elif p < 0:
                loss_streak += 1
                win_streak = 0
                max_loss_streak = max(max_loss_streak, loss_streak)

        # Costs
        total_fees = sum(t.total_fees for t in trades)
        total_slippage = sum(t.slippage_cost for t in trades)
        total_costs = total_fees + total_slippage
        fees_pct_gross = (total_fees / gross_profit * 100.0) if gross_profit > 0 else 0.0
        avg_cost_per_trade = total_costs / total_trades

        # LONG vs SHORT
        long_trades = [t for t in trades if t.signal_type == "LONG"]
        short_trades = [t for t in trades if t.signal_type == "SHORT"]

        long_pnls = [t.net_pnl for t in long_trades]
        short_pnls = [t.net_pnl for t in short_trades]

        long_win_rate = (sum(1 for p in long_pnls if p > 0) / len(long_trades) * 100.0) if long_trades else 0.0
        short_win_rate = (sum(1 for p in short_pnls if p > 0) / len(short_trades) * 100.0) if short_trades else 0.0

        # Time Analysis
        durations = [t.duration_bars for t in trades]
        avg_holding = float(np.mean(durations)) if durations else 0.0
        median_holding = float(np.median(durations)) if durations else 0.0
        shortest_trade = min(durations) if durations else 0
        longest_trade = max(durations) if durations else 0

        months = max(years * 12.0, 0.1)
        trades_per_month = total_trades / months

        return {
            "Capital": {
                "Initial Balance": round(initial_capital, 2),
                "Final Balance": round(final_capital, 2),
                "Final Equity": round(final_capital, 2),
                "Net Profit": round(net_profit_dollar, 2),
                "Net Loss": round(sum(losses), 2),
                "Net Return %": round(net_profit_pct, 2),
                "Gross Profit": round(gross_profit, 2),
                "Gross Loss": round(gross_loss, 2),
            },
            "Trades": {
                "Total Trades": total_trades,
                "LONG Trades": len(long_trades),
                "SHORT Trades": len(short_trades),
                "Winners": win_count,
                "Losers": loss_count,
                "Breakeven": be_count,
                "Win Rate %": round(win_rate, 2),
                "Loss Rate %": round(loss_rate, 2),
            },
            "Profitability": {
                "Gross PF": round(gross_pf, 2),
                "Net PF": round(net_pf, 2),
                "Average Trade": round(avg_trade, 2),
                "Median Trade": round(median_trade, 2),
                "Average Winner": round(avg_winner, 2),
                "Average Loser": round(avg_loser, 2),
                "Largest Winner": round(largest_winner, 2),
                "Largest Loser": round(largest_loser, 2),
                "Expectancy": round(expectancy, 2),
                "Average R Multiple": round(avg_r, 2),
            },
            "Risk": {
                "Max Drawdown $": round(max_dd_dollar, 2),
                "Max Drawdown %": round(max_dd_pct, 2),
                "Average Drawdown %": round(avg_dd_pct, 2),
                "Drawdown Duration (bars)": max_dd_duration_bars,
                "Sharpe Ratio": round(sharpe_ratio, 2),
                "Sortino Ratio": round(sortino_ratio, 2),
                "Longest Winning Streak": max_win_streak,
                "Longest Losing Streak": max_loss_streak,
            },
            "Costs": {
                "Total Commission": round(total_fees, 2),
                "Total Slippage": round(total_slippage, 2),
                "Total Transaction Costs": round(total_costs, 2),
                "Fees % of Gross Profit": round(fees_pct_gross, 2),
                "Average Cost Per Trade": round(avg_cost_per_trade, 2),
            },
            "LONG_vs_SHORT": {
                "LONG Return $": round(sum(long_pnls), 2),
                "LONG Win Rate %": round(long_win_rate, 2),
                "LONG Trades": len(long_trades),
                "SHORT Return $": round(sum(short_pnls), 2),
                "SHORT Win Rate %": round(short_win_rate, 2),
                "SHORT Trades": len(short_trades),
            },
            "Time_Analysis": {
                "Avg Holding Bars": round(avg_holding, 1),
                "Median Holding Bars": round(median_holding, 1),
                "Shortest Trade Bars": shortest_trade,
                "Longest Trade Bars": longest_trade,
                "Trades Per Month": round(trades_per_month, 1),
            }
        }
