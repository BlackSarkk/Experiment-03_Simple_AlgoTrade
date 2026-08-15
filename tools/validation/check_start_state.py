import pandas as pd
import dataclasses
import sys
sys.path.append('src')
from backtest.engine import BacktestEngine
from common.config import PipelineConfig
from strategy.indicators import compute_all_indicators

pipe_cfg = PipelineConfig()
pipe_cfg.platform.symbol = 'ETHUSDT'
pipe_cfg.platform.resolution = '1m'
pipe_cfg.risk.initial_capital = 10000.0
pipe_cfg.risk.leverage = 3.5
pipe_cfg.risk.risk_per_trade_pct = 0.015
pipe_cfg.risk.max_position_allocation_pct = 0.50
pipe_cfg.risk.rr_ratio = 1.5
pipe_cfg.execution.commission_pct = 0.0005
pipe_cfg.execution.taker_fee_pct = 0.0005
pipe_cfg.execution.slippage_ticks = 1.0
pipe_cfg.execution.mode = 'REFERENCE'
pipe_cfg.strategy.ema_period = 51
pipe_cfg.strategy.rsi_period = 14
pipe_cfg.strategy.rsi_overbought = 65.0
pipe_cfg.strategy.rsi_oversold = 35.0
pipe_cfg.strategy.atr_period = 14
pipe_cfg.strategy.consolidation_candles = 8
pipe_cfg.strategy.consolidation_atr_mult = 2.2
pipe_cfg.strategy.volume_filter = True
pipe_cfg.strategy.volume_sma_period = 20
pipe_cfg.strategy.volume_mult = 1.0
pipe_cfg.strategy.swing_lookback = 8
pipe_cfg.strategy.long_enabled = True
pipe_cfg.strategy.short_enabled = True

df = pd.read_csv('data/candles_futures_binance_futures_ETHUSDT_1m.csv')
warmup_period = 300
df_eval = df.iloc[warmup_period:].copy()

bt_engine = BacktestEngine(pipe_cfg)
df_bt_ind = compute_all_indicators(df.copy(), pipe_cfg.strategy)
bt_res = bt_engine.run(df_bt_ind)

df_bt_trades = pd.DataFrame([dataclasses.asdict(t) for t in bt_res['trades']])
df_bt_trades.rename(columns={
    'signal_type': 'side',
    'signal_time': 'signal_timestamp',
    'entry_time': 'entry_timestamp',
    'exit_time': 'exit_timestamp',
    'size': 'quantity',
    'total_fees': 'fees',
    'equity_after': 'balance_after',
}, inplace=True)

comp_start_ts = df_eval.iloc[0].datetime

warmup_trades = df_bt_trades[df_bt_trades['entry_timestamp'] < comp_start_ts]
if warmup_trades.empty:
    bt_start_balance = 10000.0
else:
    bt_start_balance = warmup_trades.iloc[-1]['balance_after']

print(f"Comparison start timestamp: {comp_start_ts}")
print(f"Backtest balance at comparison start: {bt_start_balance:.2f}")
print(f"Forward balance at comparison start: 10000.00")
print(f"Difference: {abs(bt_start_balance - 10000.0):.2f}")
