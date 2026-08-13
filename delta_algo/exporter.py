"""
Reporting and Export Module for Delta Exchange Strategy.
Generates:
1. candles.csv (OHLCV + all calculated indicators)
2. signals.csv (All generated signals with detailed reason and metrics)
3. trade_log.csv (Full chronological execution and accounting log)
4. performance_metrics.csv & performance_metrics.json
5. dashboard.html (Standalone interactive visual report with charts and metrics)
"""

import os
import json
from typing import List, Dict, Any
from dataclasses import asdict
import pandas as pd
from backtester import Trade
from strategy import Signal


class DeltaExporter:
    """Exports structured strategy outputs to CSV, JSON, and interactive HTML."""

    def __init__(self, output_dir: str = "output"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def export_candles(self, df: pd.DataFrame, filename: str = "candles.csv") -> str:
        filepath = os.path.join(self.output_dir, filename)
        df.to_csv(filepath, index=False)
        return filepath

    def export_signals(self, signals: List[Signal], filename: str = "signals.csv") -> str:
        filepath = os.path.join(self.output_dir, filename)
        if signals:
            sig_dicts = [asdict(s) for s in signals]
            df = pd.DataFrame(sig_dicts)
        else:
            df = pd.DataFrame(columns=[
                "candle_idx", "timestamp", "datetime_str", "signal_type", "close_price",
                "ema_51", "rsi", "atr", "sl_price", "tp_price", "risk_per_unit",
                "consolidation_detected", "reason"
            ])
        df.to_csv(filepath, index=False)
        return filepath

    def export_trade_log(self, trades: List[Trade], filename: str = "trade_log.csv") -> str:
        filepath = os.path.join(self.output_dir, filename)
        if trades:
            trade_dicts = [asdict(t) for t in trades]
            df = pd.DataFrame(trade_dicts)
        else:
            df = pd.DataFrame(columns=[
                "trade_id", "signal_type", "signal_time", "signal_price", "entry_bar_idx",
                "entry_time", "entry_price", "size", "nominal_value", "capital_allocation_pct",
                "risk_budget", "sl_price", "tp_price", "exit_bar_idx", "exit_time",
                "exit_price", "exit_reason", "duration_bars", "gross_pnl", "entry_fee",
                "exit_fee", "total_fees", "slippage_cost", "net_pnl", "net_return_pct",
                "r_multiple", "equity_after"
            ])
        df.to_csv(filepath, index=False)
        return filepath

    def export_metrics(self, metrics: Dict[str, Any], prefix: str = "performance_metrics") -> Dict[str, str]:
        csv_path = os.path.join(self.output_dir, f"{prefix}.csv")
        json_path = os.path.join(self.output_dir, f"{prefix}.json")

        # JSON
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(metrics, f, indent=2)

        # CSV
        df = pd.DataFrame([metrics])
        df.to_csv(csv_path, index=False)
        return {"csv": csv_path, "json": json_path}

    def export_dashboard_html(
        self,
        metrics: Dict[str, Any],
        trades: List[Trade],
        equity_curve: List[Dict[str, Any]],
        symbol: str = "ETHUSDT",
        filename: str = "dashboard.html"
    ) -> str:
        filepath = os.path.join(self.output_dir, filename)
        
        # Prepare charts data
        eq_dates = [x["datetime"] for x in equity_curve]
        eq_values = [x["equity"] for x in equity_curve]

        trades_json = json.dumps([asdict(t) for t in trades])
        eq_dates_json = json.dumps(eq_dates)
        eq_values_json = json.dumps(eq_values)

        metric_cards_html = ""
        key_metrics = [
            ("Net Profit ($)", f"${metrics.get('Net Profit ($)', 0):,.2f}", "#10b981" if metrics.get('Net Profit ($)', 0) >= 0 else "#ef4444"),
            ("Net Return (%)", f"{metrics.get('Net Profit (%)', 0):.2f}%", "#10b981" if metrics.get('Net Profit (%)', 0) >= 0 else "#ef4444"),
            ("Win Rate (%)", f"{metrics.get('Win Rate (%)', 0):.1f}%", "#3b82f6"),
            ("Profit Factor", f"{metrics.get('Profit Factor', 0):.2f}", "#8b5cf6"),
            ("Max Drawdown (%)", f"{metrics.get('Max Drawdown (%)', 0):.2f}%", "#f59e0b"),
            ("Sharpe Ratio", f"{metrics.get('Sharpe Ratio (Annualized)', 0):.2f}", "#06b6d4"),
            ("Total Trades", f"{metrics.get('Total Trades', 0)}", "#64748b"),
            ("Avg R-Multiple", f"{metrics.get('Average R-Multiple', 0):.2f}R", "#ec4899"),
        ]

        for label, val, color in key_metrics:
            metric_cards_html += f"""
            <div class="card" style="border-top: 3px solid {color};">
                <div class="card-label">{label}</div>
                <div class="card-value" style="color: {color};">{val}</div>
            </div>
            """

        trades_rows_html = ""
        for t in trades:
            pnl_color = "#10b981" if t.net_pnl > 0 else "#ef4444"
            type_color = "#10b981" if t.signal_type == "LONG" else "#ef4444"
            trades_rows_html += f"""
            <tr>
                <td>{t.trade_id}</td>
                <td><span class="badge" style="background: {type_color}22; color: {type_color};">{t.signal_type}</span></td>
                <td>{t.entry_time}</td>
                <td>${t.entry_price:,.2f}</td>
                <td>${t.sl_price:,.2f}</td>
                <td>${t.tp_price:,.2f}</td>
                <td>{t.exit_time or '-'}</td>
                <td>${t.exit_price:,.2f}</td>
                <td><span class="badge">{t.exit_reason}</span></td>
                <td>{t.size:.4f}</td>
                <td style="color: {pnl_color}; font-weight: 600;">${t.net_pnl:+,.2f}</td>
                <td style="color: {pnl_color};">{t.r_multiple:+.2f}R</td>
                <td>${t.equity_after:,.2f}</td>
            </tr>
            """

        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Delta Exchange {symbol} 1H Algo Report</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {{
            --bg: #0f172a;
            --surface: #1e293b;
            --surface-hover: #334155;
            --border: #334155;
            --text: #f8fafc;
            --text-muted: #94a3b8;
            --primary: #3b82f6;
            --success: #10b981;
            --danger: #ef4444;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; }}
        body {{ background: var(--bg); color: var(--text); padding: 28px; line-height: 1.5; }}
        .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; padding-bottom: 16px; border-bottom: 1px solid var(--border); }}
        .header h1 {{ font-size: 24px; font-weight: 700; color: #fff; }}
        .header .badge-tag {{ background: rgba(59, 130, 246, 0.15); color: var(--primary); padding: 6px 12px; border-radius: 6px; font-size: 13px; font-weight: 600; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }}
        .card {{ background: var(--surface); padding: 18px; border-radius: 8px; border: 1px solid var(--border); }}
        .card-label {{ font-size: 13px; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px; }}
        .card-value {{ font-size: 24px; font-weight: 700; }}
        .section-title {{ font-size: 18px; font-weight: 600; margin: 24px 0 12px 0; color: var(--text); display: flex; align-items: center; gap: 8px; }}
        .chart-box {{ background: var(--surface); padding: 20px; border-radius: 8px; border: 1px solid var(--border); margin-bottom: 24px; }}
        .table-container {{ background: var(--surface); border-radius: 8px; border: 1px solid var(--border); overflow-x: auto; max-height: 480px; }}
        table {{ width: 100%; border-collapse: collapse; text-align: left; font-size: 13px; }}
        th {{ background: #111827; color: var(--text-muted); padding: 12px 14px; position: sticky; top: 0; z-index: 10; font-weight: 600; text-transform: uppercase; font-size: 11px; }}
        td {{ padding: 12px 14px; border-bottom: 1px solid var(--border); }}
        tr:hover td {{ background: var(--surface-hover); }}
        .badge {{ padding: 3px 8px; border-radius: 4px; font-weight: 600; font-size: 12px; }}
        .footer {{ margin-top: 32px; font-size: 12px; color: var(--text-muted); text-align: center; }}
    </style>
</head>
<body>
    <div class="header">
        <div>
            <h1>Delta Exchange ETHUSD 1H Algo Backtest</h1>
            <p style="color: var(--text-muted); font-size: 14px; margin-top: 4px;">51 EMA • RSI [35-65] • 8-Candle Consolidation • 1:2 RR • 1% Risk • Next-Candle Open</p>
        </div>
        <div class="badge-tag">Delta Exchange {symbol} (1H)</div>
    </div>

    <div class="grid">
        {metric_cards_html}
    </div>

    <div class="section-title">📈 Equity Curve (USD)</div>
    <div class="chart-box">
        <canvas id="equityChart" height="90"></canvas>
    </div>

    <div class="section-title">📋 Executed Trades Log ({len(trades)} Trades)</div>
    <div class="table-container">
        <table>
            <thead>
                <tr>
                    <th>#</th>
                    <th>Type</th>
                    <th>Entry Time</th>
                    <th>Entry</th>
                    <th>Stop Loss</th>
                    <th>Take Profit</th>
                    <th>Exit Time</th>
                    <th>Exit</th>
                    <th>Exit Reason</th>
                    <th>Size (ETH)</th>
                    <th>Net PnL</th>
                    <th>R:R</th>
                    <th>Equity</th>
                </tr>
            </thead>
            <tbody>
                {trades_rows_html}
            </tbody>
        </table>
    </div>

    <div class="footer">
        Generated by Delta Exchange ETHUSD 1H Strategy Engine • Zero Look-Ahead Bias Enforced
    </div>

    <script>
        const ctx = document.getElementById('equityChart').getContext('2d');
        const eqDates = {eq_dates_json};
        const eqValues = {eq_values_json};

        new Chart(ctx, {{
            type: 'line',
            data: {{
                labels: eqDates,
                datasets: [{{
                    label: 'Portfolio Equity ($)',
                    data: eqValues,
                    borderColor: '#10b981',
                    backgroundColor: 'rgba(16, 185, 129, 0.08)',
                    borderWidth: 2,
                    fill: true,
                    tension: 0.1,
                    pointRadius: 0
                }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: true,
                scales: {{
                    x: {{
                        grid: {{ color: '#33415522' }},
                        ticks: {{ color: '#94a3b8', maxTicksLimit: 12 }}
                    }},
                    y: {{
                        grid: {{ color: '#33415544' }},
                        ticks: {{ color: '#94a3b8', callback: value => '$' + value.toLocaleString() }}
                    }}
                }},
                plugins: {{
                    legend: {{ display: false }},
                    tooltip: {{
                        mode: 'index',
                        intersect: false,
                        callbacks: {{
                            label: (ctx) => ' Equity: $' + ctx.raw.toLocaleString(undefined, {{ minimumFractionDigits: 2, maximumFractionDigits: 2 }})
                        }}
                    }}
                }}
            }}
        }});
    </script>
</body>
</html>
"""
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html_content)
        return filepath
