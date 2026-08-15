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


def print_banner(cfg: PipelineConfig):
    symbol = cfg.platform.symbol or "ETHUSDT"
    res = (cfg.platform.resolution or "3H").upper()
    banner = f"""
================================================================================
          STRATEGY PIPELINE — {symbol} {res} RULE-BASED ENGINE
       {cfg.strategy.ema_period} EMA | RSI [{cfg.strategy.rsi_oversold}-{cfg.strategy.rsi_overbought}] | {cfg.strategy.consolidation_candles}-Candle Consolidation | {cfg.strategy.risk_reward_ratio}R | {cfg.risk.leverage}x Leverage
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


def str_to_bool(v):
    if isinstance(v, bool):
        return v
    return str(v).lower() in ("true", "1", "yes", "t", "y")


def print_effective_strategy_configuration(cfg: PipelineConfig):
    print("\n" + "=" * 80)
    print("                 EFFECTIVE STRATEGY CONFIGURATION")
    print("=" * 80)
    print(f"  Symbol                      : {cfg.strategy.symbol}")
    print(f"  Resolution (Timeframe)      : {cfg.strategy.resolution}")
    print(f"  EMA Period                  : {cfg.strategy.ema_period}")
    print(f"  RSI Period                  : {cfg.strategy.rsi_period}")
    print(f"  RSI Overbought (OB)         : {cfg.strategy.rsi_overbought}")
    print(f"  RSI Oversold (OS)           : {cfg.strategy.rsi_oversold}")
    print(f"  ATR Period                  : {cfg.strategy.atr_period}")
    print(f"  Consolidation Candles       : {cfg.strategy.consolidation_candles}")
    print(f"  Consolidation ATR Mult      : {cfg.strategy.consolidation_atr_mult}")
    print(f"  Swing Lookback              : {cfg.strategy.swing_lookback}")
    print(f"  Volume SMA Period           : {cfg.strategy.volume_sma_period}")
    print(f"  Volume Multiplier           : {cfg.strategy.volume_mult}")
    print(f"  Long Enabled                : {cfg.strategy.long_enabled}")
    print(f"  Short Enabled               : {cfg.strategy.short_enabled}")
    print(f"  Risk-Reward Ratio           : {cfg.strategy.risk_reward_ratio}")
    print(f"  Forward Mode                : {cfg.forward_mode}")
    print(f"  Leverage                    : {cfg.risk.leverage}x")
    print(f"  Risk Per Trade %            : {cfg.risk.risk_per_trade_pct * 100.0:.2f}%")
    print(f"  Max Position Allocation %   : {cfg.risk.max_position_allocation_pct * 100.0:.2f}%")
    print(f"  Commission (Taker Fee)      : {cfg.execution.taker_fee_pct * 100.0:.4f}%")
    print(f"  Slippage (Ticks)            : {cfg.execution.slippage_ticks}")
    print("=" * 80 + "\n")


def run_pipeline(cfg: PipelineConfig, clear_cache_only: bool = False):
    if clear_cache_only:
        data_loader = MarketDataLoader(data_dir=cfg.data_dir)
        data_loader.clear_market_cache(cfg.platform)
        logger.info("[+] Market cache cleared. Exiting immediately (--clear-cache-only).")
        sys.exit(0)

    print_banner(cfg)
    print_effective_strategy_configuration(cfg)
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

    # Config Preset
    parser.add_argument("--config-preset", type=str, default="default", help="Config preset name")

    # Reset & Cache Controls (Defaults MUST be False for safety!)
    parser.add_argument("--reset", action="store_true", default=False, help="Stage-scoped reset")
    parser.add_argument("--clear-cache", action="store_true", default=False, help="Delete market data cache and run stage")
    parser.add_argument("--clear-cache-only", action="store_true", default=False, help="Delete market data cache and exit immediately")
    parser.add_argument("--reset-cache", action="store_true", default=False, help="Alias for --clear-cache")
    parser.add_argument("--reset-forward-state", action="store_true", default=False, help="Reset forward paper state")
    parser.add_argument("--resume", action="store_true", default=True, help="Resume existing forward paper state")
    parser.add_argument("--no-resume", action="store_false", dest="resume", help="Do not resume forward paper state")
    
    parser.add_argument("--execution-mode", type=str, default="REFERENCE", choices=["REFERENCE", "REALISTIC"])
    parser.add_argument("--forward-mode", type=str, default="PAPER")

    args = parser.parse_args()

    cfg = PipelineConfig()

    import json
    config_path = f"configs/{args.config_preset}.json"
    if not os.path.exists(config_path):
        print(f"ERROR: Config preset '{config_path}' does not exist.")
        sys.exit(1)
        
    with open(config_path, "r") as f:
        preset_data = json.load(f)

    cfg.platform.symbol = preset_data.get("symbol", "ETHUSDT")
    cfg.platform.platform = preset_data.get("platform", "BINANCE_FUTURES")
    
    effective_timeframe = preset_data.get("timeframe", "1m")
    tf_source = "preset"
        
    cfg.platform.resolution = effective_timeframe
    cfg.strategy.symbol = cfg.platform.symbol
    cfg.strategy.resolution = effective_timeframe

    s_data = preset_data.get("strategy", {})
    cfg.strategy.ema_period = s_data.get("ema_period", 51)
    cfg.strategy.rsi_period = s_data.get("rsi_period", 14)
    cfg.strategy.rsi_overbought = s_data.get("rsi_overbought", 65.0)
    cfg.strategy.rsi_oversold = s_data.get("rsi_oversold", 35.0)
    cfg.strategy.atr_period = s_data.get("atr_period", 14)
    cfg.strategy.consolidation_candles = s_data.get("consolidation_candles", 8)
    cfg.strategy.consolidation_atr_mult = s_data.get("consolidation_atr_mult", 2.2)
    cfg.strategy.swing_lookback = s_data.get("swing_lookback", 8)
    cfg.strategy.volume_sma_period = s_data.get("volume_sma_period", 20)
    cfg.strategy.use_volume_filter = True if cfg.strategy.volume_sma_period > 0 else False
    cfg.strategy.volume_mult = s_data.get("volume_mult", 1.0)
    cfg.strategy.long_enabled = s_data.get("long_enabled", True)
    cfg.strategy.short_enabled = s_data.get("short_enabled", True)
    cfg.strategy.risk_reward_ratio = s_data.get("risk_reward_ratio", 1.5)

    r_data = preset_data.get("risk", {})
    cfg.risk.initial_capital = r_data.get("initial_capital", 10000.0)
    cfg.risk.leverage = r_data.get("leverage", 1.0)
    cfg.risk.risk_per_trade_pct = r_data.get("risk_per_trade_pct", 1.5) / 100.0
    cfg.risk.max_position_allocation_pct = r_data.get("max_position_allocation_pct", 50.0) / 100.0

    e_data = preset_data.get("execution", {})
    cfg.execution.mode = args.execution_mode
    cfg.execution.taker_fee_pct = e_data.get("commission_pct", 0.05) / 100.0
    cfg.execution.maker_fee_pct = e_data.get("maker_fee_pct", 0.02) / 100.0
    cfg.execution.slippage_pct = e_data.get("slippage_pct", 0.03) / 100.0
    cfg.execution.slippage_ticks = e_data.get("slippage_ticks", 1.0)

    # Startup display
    print("============================================================")
    print(" ACTIVE CONFIG PRESET")
    print(f" Preset: {args.config_preset}")
    print(f" Source: configs/{args.config_preset}.json")
    print(" Timeframe source: preset")
    print("============================================================")

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
    
    if not cfg.run_forward_test:
        cfg.platform.start_date = "2024-01-01"
        cfg.platform.end_date = "2026-08-13"

    cfg.forward_mode = args.forward_mode
    
    run_pipeline(cfg, clear_cache_only=args.clear_cache_only)


if __name__ == "__main__":
    main()
