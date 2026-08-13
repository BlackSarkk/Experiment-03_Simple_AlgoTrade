"""
Main Python CLI Entrypoint for ETH Strategy Pipeline.
Parses CLI parameters, displays Rich Run Configuration Panel,
handles stage-scoped RESET, CLEAR_CACHE, and CLEAR_CACHE_ONLY operations, loads market data,
and executes Backtesting, Robustness Testing, or Paper Forward Testing.
"""

import sys
import os
import argparse
from typing import Dict, Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from common.config import PipelineConfig, StrategyConfig, RiskConfig, ExecutionConfig, PlatformConfig
from common.market_data import MarketDataLoader
from strategy.indicators import compute_all_indicators
from backtest.engine import BacktestEngine
from backtest.metrics import BacktestMetrics
from backtest.reports import BacktestExporter
from backtest.robustness import RobustnessEvaluator
from forward_test.paper_engine import PaperForwardEngine
from common.utils import setup_logger, format_currency, format_percent

logger = setup_logger("Main")
console = Console()


def print_banner():
    banner = """
================================================================================
          ETH STRATEGY PIPELINE — ETHUSDT 3H RULE-BASED ENGINE
       51 EMA | RSI [35-65] | 8-Candle Consolidation | 1.5R | 3.5x Leverage
================================================================================
"""
    print(banner)


def print_run_configuration_panel(cfg: PipelineConfig):
    """Display clean Rich configuration panel at startup."""
    stage_str = "BACKTEST"
    if cfg.run_robustness:
        stage_str = "ROBUSTNESS SUITE"
    elif cfg.run_forward_test:
        stage_str = f"FORWARD ({cfg.forward_mode})"

    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", justify="left")
    table.add_column(style="bold white", justify="left")

    table.add_row("Stage", stage_str)
    table.add_row("Symbol", f"{cfg.platform.symbol}.P ({cfg.platform.platform})")
    table.add_row("Timeframe", cfg.platform.resolution)
    table.add_row("Initial Balance", format_currency(cfg.risk.initial_capital))
    table.add_row("Leverage", f"{cfg.risk.leverage:.1f}x")
    table.add_row("RESET", "[bold red]TRUE[/bold red]" if cfg.reset else "FALSE")
    table.add_row("CLEAR CACHE", "[bold yellow]TRUE[/bold yellow]" if cfg.clear_cache else "FALSE")
    table.add_row("Resume", "TRUE" if (cfg.resume_forward_state and not cfg.reset) else "FALSE")

    panel = Panel(
        table,
        title="[bold magenta]RUN CONFIGURATION[/bold magenta]",
        border_style="magenta",
        expand=False
    )
    console.print(panel)


def print_metrics_table(metrics: Dict[str, Any]):
    print("\n" + "=" * 70)
    print("                 DETAILED BACKTEST PERFORMANCE SCORECARD")
    print("=" * 70)

    for section, subdict in metrics.items():
        if isinstance(subdict, dict):
            print(f"\n --- {section.upper()} ---")
            for k, v in subdict.items():
                print(f"  {k:<35}: {v}")
    print("=" * 70 + "\n")


def run_pipeline(cfg: PipelineConfig, clear_cache_only: bool = False):
    if clear_cache_only:
        data_loader = MarketDataLoader(data_dir=cfg.data_dir)
        data_loader.clear_market_cache(cfg.platform)
        logger.info("[+] Market cache cleared. Exiting immediately (--clear-cache-only).")
        sys.exit(0)

    print_banner()
    print_run_configuration_panel(cfg)

    # Handle CLEAR_CACHE if requested
    data_loader = MarketDataLoader(data_dir=cfg.data_dir)
    if cfg.clear_cache or cfg.reset_cache:
        data_loader.clear_market_cache(cfg.platform)

    # 1. Market Data Loading
    df_raw = data_loader.load_ohlcv(cfg.platform, reset_cache=cfg.clear_cache)

    # 2. Indicator Calculation
    logger.info("Computing technical indicators (51 EMA, 14 RSI, 14 ATR, 8 Consolidation, 8 Swing S/R, 20 Vol SMA)...")
    df_indicators = compute_all_indicators(df_raw, cfg.strategy)
    logger.info(f"Indicators attached to {len(df_indicators)} candles.")

    # 3. Backtest Execution
    if cfg.run_backtest and not cfg.run_robustness and not cfg.run_forward_test:
        logger.info(f"Executing Backtest Engine in {cfg.execution_mode} Mode...")
        engine = BacktestEngine(cfg)
        res = engine.run(df_indicators)

        metrics = BacktestMetrics.calculate(
            trades=res["trades"],
            equity_curve=res["equity_curve"],
            initial_capital=cfg.risk.initial_capital
        )

        exporter = BacktestExporter(results_dir=os.path.join(cfg.results_dir, "backtest"))
        trades_file = exporter.export_trades(res["trades"], filename="trades.csv")
        equity_file = exporter.export_equity_curve(res["equity_curve"], filename="equity_curve.csv")

        logger.info(f"Exported trade log to: {os.path.abspath(trades_file)}")
        logger.info(f"Exported equity curve to: {os.path.abspath(equity_file)}")

        print_metrics_table(metrics)

    # 4. Robustness Testing Suite
    if cfg.run_robustness:
        logger.info("Executing Full 5-Stage Robustness Evaluation Suite...")
        evaluator = RobustnessEvaluator(cfg)
        evaluator.run_full_robustness_suite()

    # 5. Forward Testing (Paper Mode)
    if cfg.run_forward_test:
        logger.info(f"Executing Paper Forward Testing Engine (Mode: {cfg.forward_mode})...")
        forward_engine = PaperForwardEngine(cfg)
        forward_engine.run_forward_session()


def main():
    parser = argparse.ArgumentParser(description="ETH Strategy Pipeline CLI")

    # Mode Flags
    parser.add_argument("--backtest", action="store_true", default=False, help="Run backtest engine")
    parser.add_argument("--robustness", action="store_true", default=False, help="Run robustness testing suite")
    parser.add_argument("--forward-test", action="store_true", default=False, help="Run paper forward test engine")

    # Asset & Platform
    parser.add_argument("--symbol", type=str, default="ETHUSDT")
    parser.add_argument("--platform", type=str, default="BINANCE_FUTURES")
    parser.add_argument("--timeframe", type=str, default="3h")
    parser.add_argument("--start-date", type=str, default="2024-01-01")
    parser.add_argument("--end-date", type=str, default="2026-08-13")

    # Risk & Capital
    parser.add_argument("--initial-capital", type=float, default=10000.0)
    parser.add_argument("--leverage", type=float, default=3.5)
    parser.add_argument("--risk-pct", type=float, default=1.5)
    parser.add_argument("--max-alloc-pct", type=float, default=50.0)
    parser.add_argument("--rr-ratio", type=float, default=1.5)

    # Execution & Fees
    parser.add_argument("--execution-mode", type=str, default="REFERENCE", choices=["REFERENCE", "REALISTIC"])
    parser.add_argument("--commission-pct", type=float, default=0.05)
    parser.add_argument("--maker-fee-pct", type=float, default=0.02)
    parser.add_argument("--slippage-pct", type=float, default=0.03)
    parser.add_argument("--slippage-ticks", type=float, default=1.0)

    # Reset & Cache Controls (Defaults MUST be False for safety!)
    parser.add_argument("--reset", action="store_true", default=False, help="Stage-scoped reset")
    parser.add_argument("--clear-cache", action="store_true", default=False, help="Delete market data cache and run stage")
    parser.add_argument("--clear-cache-only", action="store_true", default=False, help="Delete market data cache and exit immediately")
    parser.add_argument("--reset-cache", action="store_true", default=False, help="Alias for --clear-cache")
    parser.add_argument("--reset-forward-state", action="store_true", default=False, help="Reset forward paper state")
    parser.add_argument("--resume", action="store_true", default=True, help="Resume existing forward paper state")
    parser.add_argument("--no-resume", action="store_false", dest="resume", help="Do not resume forward paper state")

    args = parser.parse_args()

    cfg = PipelineConfig()

    # Determine execution stage
    if args.forward_test:
        cfg.run_forward_test = True
        cfg.run_backtest = False
        cfg.run_robustness = False
        cfg.platform.start_date = None
        cfg.platform.end_date = None
        cfg.platform.days = 60
    elif args.robustness:
        cfg.run_robustness = True
        cfg.run_backtest = False
        cfg.run_forward_test = False
    else:
        cfg.run_backtest = True
        cfg.run_robustness = False
        cfg.run_forward_test = False

    cfg.execution_mode = args.execution_mode
    cfg.reset = args.reset
    cfg.clear_cache = args.clear_cache or args.reset_cache or args.clear_cache_only
    cfg.reset_cache = cfg.clear_cache
    cfg.reset_forward_state = args.reset_forward_state or args.reset
    cfg.resume_forward_state = args.resume and not args.reset

    cfg.platform.symbol = args.symbol
    cfg.platform.platform = args.platform
    cfg.platform.resolution = args.timeframe
    if cfg.run_forward_test:
        cfg.platform.start_date = None
        cfg.platform.end_date = None
        cfg.platform.days = 60
    else:
        cfg.platform.start_date = args.start_date
        cfg.platform.end_date = args.end_date

    cfg.strategy.symbol = args.symbol
    cfg.strategy.resolution = args.timeframe
    cfg.strategy.risk_reward_ratio = args.rr_ratio

    cfg.risk.initial_capital = args.initial_capital
    cfg.risk.leverage = args.leverage
    cfg.risk.risk_per_trade_pct = args.risk_pct / 100.0
    cfg.risk.max_position_allocation_pct = args.max_alloc_pct / 100.0

    cfg.execution.mode = args.execution_mode
    cfg.execution.taker_fee_pct = args.commission_pct / 100.0
    cfg.execution.maker_fee_pct = args.maker_fee_pct / 100.0
    cfg.execution.slippage_pct = args.slippage_pct / 100.0
    cfg.execution.slippage_ticks = args.slippage_ticks

    run_pipeline(cfg, clear_cache_only=args.clear_cache_only)


if __name__ == "__main__":
    main()
