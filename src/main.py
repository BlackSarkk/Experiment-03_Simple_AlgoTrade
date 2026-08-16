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
from filters.stage_1_bollinger.filter import (BollingerFilterConfig, compute_bollinger,
                                              allow_mask as bb_allow_mask)
from filters.masked_strategy import MaskedStrategy
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


def evaluation_mask(df, platform_cfg):
    """Boolean mask of rows inside the requested evaluation window (same rule as
    slice_evaluation_window). Exposed so signal-filter masks computed on the FULL
    frame can be sliced identically — filters need pre-window warmup just like
    indicators do."""
    import pandas as pd
    if df.empty or "datetime" not in df.columns:
        return pd.Series(True, index=df.index)
    dt = pd.to_datetime(df["datetime"], utc=True)
    mask = pd.Series(True, index=df.index)
    if platform_cfg.start_date:
        mask &= (dt >= pd.Timestamp(platform_cfg.start_date, tz="UTC"))
    if platform_cfg.end_date:
        req_end = pd.Timestamp(platform_cfg.end_date, tz="UTC")
        if len(str(platform_cfg.end_date)) == 10:
            req_end = req_end + pd.Timedelta(hours=23, minutes=59, seconds=59)
        mask &= (dt <= req_end)
    return mask


def slice_evaluation_window(df, platform_cfg):
    """Restrict an indicator-attached frame to the requested evaluation window.

    The market-data cache is allowed to be wider than the requested range (extra candles
    before `start_date` seed indicator warmup, and reuse across runs may leave a longer
    file). The *evaluation* window must never be wider than requested.

    `end_date` given as a bare date (YYYY-MM-DD) is treated as inclusive end-of-day
    (23:59:59 UTC), matching how a "2024-01-01 -> 2026-08-15" range reads.

    Returns the sliced frame; indicator columns are preserved unchanged because they were
    computed on the full frame before slicing.
    """
    import pandas as pd

    if df.empty or "datetime" not in df.columns:
        return df

    dt = pd.to_datetime(df["datetime"], utc=True)
    total = len(df)
    mask = pd.Series(True, index=df.index)

    start_str = platform_cfg.start_date
    end_str = platform_cfg.end_date

    if start_str:
        req_start = pd.Timestamp(start_str, tz="UTC")
        mask &= (dt >= req_start)
    if end_str:
        req_end = pd.Timestamp(end_str, tz="UTC")
        if len(str(end_str)) == 10:  # bare date -> inclusive end of day
            req_end = req_end + pd.Timedelta(hours=23, minutes=59, seconds=59)
        mask &= (dt <= req_end)

    if not start_str and not end_str:
        return df

    df_eval = df[mask].reset_index(drop=True)
    warmup = total - len(df_eval)

    if df_eval.empty:
        raise ValueError(
            f"Evaluation window [{start_str} .. {end_str}] selected 0 candles from a "
            f"{total}-candle cache. Check the configured dates."
        )

    logger.info(
        f"Evaluation window: {df_eval['datetime'].iloc[0]} -> {df_eval['datetime'].iloc[-1]} "
        f"| {len(df_eval)} evaluation candles | {warmup} warmup/out-of-range candles excluded"
    )
    return df_eval


def run_pipeline(cfg: PipelineConfig, clear_cache_only: bool = False, maintenance_only: bool = False):

    print_banner(cfg)
    print_effective_strategy_configuration(cfg)
    print_run_configuration_panel(cfg)

    # Handle RESET / HARD_RESET if requested
    if cfg.hard_reset:
        print("\n" + "=" * 70)
        print("                  HARD_RESET: deleting all generated cache, results, logs, runtime state, archives, and optimization outputs.")
        print("=" * 70)
        import glob
        
        results_cleared = 0
        for root, dirs, files in os.walk(cfg.results_dir):
            for f in files:
                if f.endswith((".csv", ".json", ".log", ".txt", ".md")):
                    os.remove(os.path.join(root, f))
                    results_cleared += 1
                    
        logs_cleared = 0
        for root, dirs, files in os.walk(cfg.logs_dir):
            for f in files:
                if f.endswith((".log", ".json", ".txt", ".md")):
                    os.remove(os.path.join(root, f))
                    logs_cleared += 1
                    
        print(f"  [-] Removed {results_cleared} generated files in results/")
        print(f"  [-] Removed {logs_cleared} generated files in logs/")
        print("  [+] Preserved configs/, src/, tests/")
        print("======================================================================\n")
    elif cfg.reset:
        print("\n" + "=" * 70)
        print("                  RESET=true RUN ARTIFACT CLEANUP")
        print("=" * 70)
        import glob
        
        files_to_delete = [
            os.path.join(cfg.logs_dir, "forward_state.json"),
            os.path.join(cfg.logs_dir, "replay_state.json"),
            os.path.join(cfg.results_dir, "forward", "trades.csv"),
            os.path.join(cfg.results_dir, "forward", "events.csv"),
            os.path.join(cfg.results_dir, "forward", "equity_curve.csv"),
            os.path.join(cfg.results_dir, "replay", "trades.csv"),
            os.path.join(cfg.results_dir, "replay", "events.csv"),
            os.path.join(cfg.results_dir, "replay", "equity_curve.csv"),
            os.path.join(cfg.results_dir, "backtest", "trades.csv"),
            os.path.join(cfg.results_dir, "backtest", "events.csv"),
            os.path.join(cfg.results_dir, "backtest", "equity_curve.csv"),
        ]
        
        for f in glob.glob(os.path.join(cfg.logs_dir, "session_*.log")):
            files_to_delete.append(f)
            
        deleted = 0
        for f in files_to_delete:
            if os.path.exists(f):
                os.remove(f)
                deleted += 1
                
        print(f"  [-] Removed {deleted} generated current runtime/log files")
        print("  [+] Preserved historical archives, optimization results, tracker data")
        print("  [+] Preserved market-data cache")
        print("  [+] Preserved configs/, src/, tests/")
        print("======================================================================\n")

    # Handle CLEAR_CACHE if requested
    data_loader = MarketDataLoader(data_dir=cfg.data_dir)
    if cfg.clear_cache or cfg.reset_cache or clear_cache_only:
        data_loader.clear_market_cache(cfg.platform, hard_reset=cfg.hard_reset)

    if maintenance_only or clear_cache_only:
        logger.info("[+] Maintenance tasks completed. Exiting immediately.")
        sys.exit(0)

    # 1. Market Data Loading
    df_raw = data_loader.load_ohlcv(cfg.platform, reset_cache=cfg.clear_cache)

    # 2. Indicator Calculation
    # Indicators are computed on the FULL cached frame so that candles before the requested
    # start date can seed EMA/RSI/ATR/volume warmup. They are then dropped from the evaluation
    # frame below, so warmup candles can never produce trades, PnL, or drawdown.
    logger.info("Computing technical indicators (51 EMA, 14 RSI, 14 ATR, 8 Consolidation, 8 Swing S/R, 20 Vol SMA)...")
    df_indicators = compute_all_indicators(df_raw, cfg.strategy)
    logger.info(f"Indicators attached to {len(df_indicators)} candles (full cache incl. warmup).")

    # 2b. Signal-filter masks are computed on the FULL indicator frame (pre-slice) so
    #     that filter indicators get the same pre-window warmup the strategy indicators
    #     get. Computing a filter on the sliced frame would restart its rolling windows
    #     at the window edge and change which signals are blocked.
    bb_cfg = BollingerFilterConfig.from_dict(cfg.filters.get("bollinger"))
    _filter_mask_full = None
    if bb_cfg.enabled:
        import numpy as _np
        _filter_mask_full = _np.ones(len(df_indicators), dtype=bool)
        _active = []
        _filter_mask_full &= bb_allow_mask(compute_bollinger(df_indicators, bb_cfg), bb_cfg)
        _active.append(f"bollinger(len={bb_cfg.length}, std={bb_cfg.std})")
        print("=" * 60)
        print(" ACTIVE SIGNAL FILTERS  (computed on full history, then window-sliced)")
        for a in _active:
            print(f"   - {a}")
        print("=" * 60)
    else:
        logger.info("Signal filters: none enabled (baseline signal set).")

    # 2c. Strict evaluation-window slicing.
    #     The cache MAY be wider than the requested range; the evaluation window MAY NOT.
    _eval_mask = evaluation_mask(df_indicators, cfg.platform)
    if _filter_mask_full is not None:
        _filter_mask = _filter_mask_full[_eval_mask.to_numpy()]
    else:
        _filter_mask = None
    df_indicators = slice_evaluation_window(df_indicators, cfg.platform)

    def _apply_filters(engine):
        """Inject the precomputed filter mask. Engine/strategy source is never modified."""
        if _filter_mask is not None:
            engine.strategy = MaskedStrategy(cfg.strategy, _filter_mask)
        return engine

    # 3. Backtest Execution
    if cfg.run_backtest and not cfg.run_robustness and not cfg.run_forward_test:
        logger.info(f"Executing Backtest Engine in {cfg.execution_mode} Mode...")
        engine = _apply_filters(BacktestEngine(cfg))
        res = engine.run(df_indicators)
        _st = getattr(engine.strategy, "blocked_count", 0)
        if _st:
            logger.info(f"Signal filters blocked {_st} signals")

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
        if cfg.forward_mode == "HISTORICAL_REPLAY":
            from forward_test.replay_engine import HistoricalReplayEngine
            logger.info(f"Executing Historical Replay Forward Engine (Mode: {cfg.forward_mode})...")
            # Pass the SAME indicator-attached, evaluation-window-sliced frame the backtest
            # uses, so the two paths cannot drift on range or warmup handling.
            forward_engine = HistoricalReplayEngine(cfg, df_indicators)
            forward_engine.run_replay()
        else:
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
    parser.add_argument("--config-preset", type=str, default="configs/config/default.json",
                        help="Path to a config JSON (or a bare preset name resolved under configs/config/)")

    # Reset & Cache Controls (Defaults MUST be False for safety!)
    parser.add_argument("--hard-reset", action="store_true", default=False, help="Complete destruction of all generated files")
    parser.add_argument("--reset", action="store_true", default=False, help="Stage-scoped reset")
    parser.add_argument("--clear-cache", action="store_true", default=False, help="Delete market data cache and run stage")
    parser.add_argument("--clear-cache-only", action="store_true", default=False, help="Delete market data cache and exit immediately")
    parser.add_argument("--maintenance-only", action="store_true", default=False, help="Perform maintenance tasks (reset/clear-cache) and exit immediately")
    parser.add_argument("--reset-cache", action="store_true", default=False, help="Alias for --clear-cache")
    parser.add_argument("--reset-forward-state", action="store_true", default=False, help="Reset forward paper state")
    parser.add_argument("--resume", action="store_true", default=True, help="Resume existing forward paper state")
    parser.add_argument("--no-resume", action="store_false", dest="resume", help="Do not resume forward paper state")
    
    parser.add_argument("--execution-mode", type=str, default="REFERENCE", choices=["REFERENCE", "REALISTIC"])
    parser.add_argument("--forward-mode", type=str, default="PAPER")

    args = parser.parse_args()

    cfg = PipelineConfig()

    import json
    # --config-preset accepts a path (pipeline.sh resolves and passes one) or a bare
    # preset name. Candidates are tried in order; first hit wins.
    _raw = args.config_preset
    config_path = None
    for cand in (_raw, f"{_raw}.json",
                 os.path.join("configs", "config", _raw),
                 os.path.join("configs", "config", f"{_raw}.json")):
        if os.path.isfile(cand):
            config_path = cand
            break
    if config_path is None:
        print(f"ERROR: Config file not found: '{_raw}'")
        sys.exit(1)

    try:
        with open(config_path, "r") as f:
            preset_data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"ERROR: '{config_path}' is not valid JSON: {e}")
        sys.exit(1)
    if not isinstance(preset_data, dict):
        print(f"ERROR: '{config_path}' must contain a JSON object.")
        sys.exit(1)
    _missing = [k for k in ("symbol", "timeframe", "strategy", "risk", "execution")
                if k not in preset_data]
    if _missing:
        print(f"ERROR: '{config_path}' is missing required fields: {', '.join(_missing)}")
        sys.exit(1)

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

    # --- Risk policy source of truth -------------------------------------------------
    # configs/riskmanager.json is AUTHORITATIVE for all execution stages (Backtest,
    # Historical Replay, Forward/Paper). A legacy "risk" block inside a strategy preset
    # is kept for compatibility but is overridden field-by-field by riskmanager.json.
    # Precedence:  riskmanager.json  >  preset "risk" block  >  RiskConfig dataclass
    r_data = dict(preset_data.get("risk", {}))
    risk_policy_path = "src/risk_management/riskmanager.json"
    risk_policy_loaded = False
    # A preset may declare "_risk_policy": "preset" to own its risk block outright
    # (candidate presets config1-4 carry their own validated leverage/risk/allocation).
    preset_owns_risk = str(preset_data.get("_risk_policy", "")).lower() == "preset"
    if preset_owns_risk:
        print("============================================================")
        print(" ACTIVE RISK POLICY (preset-owned)")
        print(f" Source: {config_path}  (riskmanager.json bypassed)")
        print("============================================================")
    if os.path.exists(risk_policy_path) and not preset_owns_risk:
        with open(risk_policy_path, "r") as f:
            policy = json.load(f)
        overrides = policy.get("risk", {})
        overridden = sorted(k for k in overrides if k in r_data and r_data[k] != overrides[k])
        r_data.update(overrides)
        risk_policy_loaded = True
        print("============================================================")
        print(" ACTIVE RISK POLICY (authoritative)")
        print(f" Source: {risk_policy_path}")
        if overridden:
            print(f" Overrode preset 'risk' fields: {', '.join(overridden)}")
        print("============================================================")
    elif not preset_owns_risk:
        logger.warning(
            f"{risk_policy_path} not found — falling back to the preset 'risk' block. "
            "Production runs should define the authoritative risk policy."
        )
    cfg.risk_policy_source = f"preset:{config_path}" if preset_owns_risk else risk_policy_path if risk_policy_loaded else f"preset:{config_path}"

    cfg.risk.initial_capital = r_data.get("initial_capital", 10000.0)
    # RiskConfig dataclass defaults are the single source of truth for fallbacks.
    cfg.risk.leverage = r_data.get("leverage", cfg.risk.leverage)
    cfg.risk.quantity_step = r_data.get("quantity_step", cfg.risk.quantity_step)
    cfg.risk.sizing_mode = r_data.get("sizing_mode", cfg.risk.sizing_mode)
    cfg.risk.fixed_notional = r_data.get("fixed_notional", cfg.risk.fixed_notional)
    cfg.risk.risk_per_trade_pct = r_data.get("risk_per_trade_pct", 1.5) / 100.0
    cfg.risk.max_position_allocation_pct = r_data.get("max_position_allocation_pct", 50.0) / 100.0

    e_data = preset_data.get("execution", {})
    cfg.execution.mode = args.execution_mode
    cfg.execution.taker_fee_pct = e_data.get("commission_pct", 0.05) / 100.0
    cfg.execution.maker_fee_pct = e_data.get("maker_fee_pct", 0.02) / 100.0
    cfg.execution.slippage_pct = e_data.get("slippage_pct", 0.03) / 100.0
    cfg.execution.slippage_ticks = e_data.get("slippage_ticks", 1.0)
    cfg.execution.tick_size = e_data.get("tick_size", 0.01)

    # Signal-filter blocks (removal-only gates). Absent block == disabled.
    cfg.filters = preset_data.get("filters", {}) or {}

    # Startup display
    print("============================================================")
    print(" ACTIVE CONFIG PRESET")
    print(f" Config: {config_path}")
    print(f" Source: {config_path}")
    print(" Timeframe source: preset")
    print("============================================================")

    # Determine execution stage
    if args.forward_test:
        cfg.run_forward_test = True
        cfg.run_backtest = False
        cfg.run_robustness = False
        if args.forward_mode != "HISTORICAL_REPLAY":
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
    cfg.hard_reset = args.hard_reset
    cfg.reset = args.reset
    cfg.clear_cache = args.clear_cache or args.reset_cache or args.clear_cache_only or args.hard_reset
    cfg.reset_cache = cfg.clear_cache
    cfg.reset_forward_state = args.reset_forward_state or args.reset
    cfg.resume_forward_state = args.resume and not args.reset
    
    if not cfg.run_forward_test or args.forward_mode == "HISTORICAL_REPLAY":
        cfg.platform.start_date = preset_data.get("start_date", "2024-01-01")
        cfg.platform.end_date = preset_data.get("end_date", "2026-08-15")

    cfg.forward_mode = args.forward_mode
    
    run_pipeline(cfg, clear_cache_only=args.clear_cache_only, maintenance_only=args.maintenance_only)


if __name__ == "__main__":
    main()
