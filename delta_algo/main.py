"""
Main CLI Application for Delta Exchange ETHUSD 1-Hour Algorithmic Trading Strategy.
Performs:
1. Direct candle ingestion from Delta Exchange REST API
2. Technical indicator processing (51 EMA, 14 RSI, 14 ATR, 8-candle consolidation, swing S/R)
3. Event-driven next-candle backtest simulation with realistic fees and slippage
4. CSV logs, metrics, and interactive HTML dashboard export
"""

import os
import sys
import argparse
from typing import Optional

from config import AppConfig, StrategyConfig, RiskConfig, ExecutionConfig
from data_fetcher import DeltaDataFetcher
from indicators import compute_all_indicators
from backtester import DeltaBacktester
from metrics import PerformanceMetrics
from exporter import DeltaExporter


def print_banner():
    banner = """
================================================================================
   DELTA EXCHANGE ETHUSD 1-HOUR ALGORITHMIC TRADING ENGINE
   51 EMA | RSI [35-65] | 8-Candle Consolidation | 1:2 RR | 1% Risk Sizing
================================================================================
"""
    print(banner)


def print_metrics_table(metrics: dict):
    print("\n" + "=" * 65)
    print("                    PERFORMANCE SCORECARD")
    print("=" * 65)
    
    key_fields = [
        "Initial Capital ($)",
        "Final Capital ($)",
        "Net Profit ($)",
        "Net Profit (%)",
        "CAGR / Ann. Return (%)",
        "Total Trades",
        "Winning Trades",
        "Losing Trades",
        "Win Rate (%)",
        "Profit Factor",
        "Payoff Ratio (Avg Win / Avg Loss)",
        "Avg Win ($)",
        "Avg Loss ($)",
        "Expectancy per Trade ($)",
        "Average R-Multiple",
        "Max Drawdown ($)",
        "Max Drawdown (%)",
        "Max Drawdown Duration (hours)",
        "Sharpe Ratio (Annualized)",
        "Sortino Ratio (Annualized)",
        "Long Trades",
        "Short Trades",
        "Take-Profit Hits",
        "Breakeven Hits",
        "Stop-Loss Hits",
        "Avg Trade Duration (hours)",
        "Total Fees Paid ($)",
        "Total Slippage Cost ($)",
    ]

    for field in key_fields:
        val = metrics.get(field, "N/A")
        print(f"  {field:<38}: {val}")
    print("=" * 65)


def run_pipeline(
    symbol: str = "ETHUSDT",
    resolution: str = "1h",
    days: int = 180,
    capital: float = 10000.0,
    risk_pct: float = 1.5,
    max_alloc_pct: float = 50.0,
    leverage: float = 1.0,
    maker_fee_pct: float = 0.02,
    taker_fee_pct: float = 0.05,
    slippage_pct: float = 0.03,
    force_download: bool = False,
    data_dir: str = "data",
    output_dir: str = "output",
):
    print_banner()

    # 1. Setup Configuration
    cfg = AppConfig()
    cfg.strategy.symbol = symbol
    cfg.strategy.resolution = resolution
    cfg.risk.initial_capital = capital
    cfg.risk.risk_per_trade_pct = risk_pct / 100.0
    cfg.risk.max_position_allocation_pct = max_alloc_pct / 100.0
    cfg.risk.leverage = leverage
    cfg.execution.maker_fee_pct = maker_fee_pct / 100.0
    cfg.execution.taker_fee_pct = taker_fee_pct / 100.0
    cfg.execution.slippage_pct = slippage_pct / 100.0
    cfg.data_dir = data_dir
    cfg.output_dir = output_dir

    print(f"[*] Configuration Initialized:")
    print(f"    - Symbol: {symbol} ({resolution})")
    print(f"    - Initial Capital: ${capital:,.2f}")
    print(f"    - Leverage Setting: {leverage:.1f}x")
    print(f"    - Account Risk per Trade: {risk_pct:.1f}%")
    print(f"    - Max Position Allocation: {max_alloc_pct:.1f}%")
    print(f"    - Fees: Maker {maker_fee_pct:.2f}%, Taker {taker_fee_pct:.2f}% | Slippage: {slippage_pct:.2f}%")
    print(f"    - Execution Model: Next-Candle Open (Zero Look-Ahead Bias)\n")

    # 2. Data Ingestion
    fetcher = DeltaDataFetcher(base_url=cfg.api_base_url, data_dir=cfg.data_dir)
    df_raw = fetcher.fetch_candles(
        symbol=symbol,
        resolution=resolution,
        days=days,
        force_download=force_download,
    )

    # 3. Compute Indicators
    print(f"\n[*] Computing Technical Indicators (51 EMA, 200 Trend EMA, 14 RSI, 14 ATR, 8-Candle Consolidation, Swing S/R)...")
    df_indicators = compute_all_indicators(
        df=df_raw,
        ema_period=cfg.strategy.ema_period,
        rsi_period=cfg.strategy.rsi_period,
        atr_period=cfg.strategy.atr_period,
        consolidation_candles=cfg.strategy.consolidation_candles,
        consolidation_atr_mult=cfg.strategy.consolidation_atr_mult,
        swing_lookback=cfg.strategy.swing_lookback,
        trend_ema_period=cfg.strategy.trend_ema_period,
    )
    print(f"[+] Indicators successfully attached to {len(df_indicators)} candles.")

    # 4. Run Backtester
    print(f"\n[*] Executing Strategy Backtest Engine...")
    backtester = DeltaBacktester(cfg)
    results = backtester.run(df_indicators)

    trades = results["trades"]
    signals = results["signals"]
    equity_curve = results["equity_curve"]
    print(f"[+] Backtest Complete: {len(signals)} signals identified, {len(trades)} trades executed.")

    # 5. Compute Quantitative Metrics
    metrics = PerformanceMetrics.calculate(
        trades=trades,
        equity_curve=equity_curve,
        initial_capital=capital,
    )

    # 6. Exporter
    print(f"\n[*] Exporting Strategy Results and Logs...")
    exporter = DeltaExporter(output_dir=cfg.output_dir)
    
    candles_file = exporter.export_candles(df_indicators, filename="candles.csv")
    signals_file = exporter.export_signals(signals, filename="signals.csv")
    trades_file = exporter.export_trade_log(trades, filename="trade_log.csv")
    metrics_files = exporter.export_metrics(metrics, prefix="performance_metrics")
    dashboard_file = exporter.export_dashboard_html(
        metrics=metrics,
        trades=trades,
        equity_curve=equity_curve,
        symbol=symbol,
        filename="dashboard.html"
    )

    print_metrics_table(metrics)

    print("\n" + "=" * 65)
    print("                    EXPORTED ARTIFACTS")
    print("=" * 65)
    print(f"  [Candles Data]       : {os.path.abspath(candles_file)}")
    print(f"  [Signals Log]        : {os.path.abspath(signals_file)}")
    print(f"  [Trade Log]          : {os.path.abspath(trades_file)}")
    print(f"  [Metrics CSV]        : {os.path.abspath(metrics_files['csv'])}")
    print(f"  [Metrics JSON]       : {os.path.abspath(metrics_files['json'])}")
    print(f"  [Visual Dashboard]   : {os.path.abspath(dashboard_file)}")
    print("=" * 65 + "\n")

    return results, metrics


def main():
    parser = argparse.ArgumentParser(description="Delta Exchange ETHUSD 1H Algorithmic Trading Strategy")
    parser.add_argument("--symbol", type=str, default="ETHUSDT", help="Delta Exchange contract symbol (default: ETHUSDT)")
    parser.add_argument("--resolution", type=str, default="1h", help="Timeframe resolution (default: 1h)")
    parser.add_argument("--days", type=int, default=180, help="Lookback period in days (default: 180)")
    parser.add_argument("--capital", type=float, default=10000.0, help="Initial account capital in USD (default: 10000)")
    parser.add_argument("--risk-pct", type=float, default=1.5, help="Risk per trade as percentage of equity (default: 1.5)")
    parser.add_argument("--max-alloc-pct", type=float, default=50.0, help="Maximum position allocation percentage (default: 50.0)")
    parser.add_argument("--leverage", type=float, default=1.0, help="Account leverage multiplier (e.g. 1.0, 2.0, 3.0, 5.0, 10.0) (default: 1.0)")
    parser.add_argument("--maker-fee", type=float, default=0.02, help="Maker fee percentage (default: 0.02)")
    parser.add_argument("--taker-fee", type=float, default=0.05, help="Taker fee percentage (default: 0.05)")
    parser.add_argument("--slippage", type=float, default=0.03, help="Slippage percentage per order (default: 0.03)")
    parser.add_argument("--force-download", action="store_true", help="Force fresh download from Delta Exchange API")
    parser.add_argument("--data-dir", type=str, default="data", help="Directory to cache raw candle data")
    parser.add_argument("--output-dir", type=str, default="output", help="Directory for output files")

    args = parser.parse_args()

    run_pipeline(
        symbol=args.symbol,
        resolution=args.resolution,
        days=args.days,
        capital=args.capital,
        risk_pct=args.risk_pct,
        max_alloc_pct=args.max_alloc_pct,
        leverage=args.leverage,
        maker_fee_pct=args.maker_fee,
        taker_fee_pct=args.taker_fee,
        slippage_pct=args.slippage,
        force_download=args.force_download,
        data_dir=args.data_dir,
        output_dir=args.output_dir,
    )


if __name__ == "__main__":
    main()
