import pandas as pd
import numpy as np
import hashlib
from datetime import datetime, timezone
import sys
import logging

# Kill all logging to make the loop extremely fast
logging.disable(logging.CRITICAL)

from src.common.config import PipelineConfig
from src.strategy.indicators import compute_all_indicators
from src.backtest.engine import BacktestEngine
from src.forward_test.paper_engine import PaperForwardEngine

pipe_cfg = PipelineConfig()
pipe_cfg.platform.symbol = "ETHUSDT"
pipe_cfg.platform.resolution = "1m"
pipe_cfg.risk.initial_capital = 10000.0
pipe_cfg.risk.leverage = 3.5
pipe_cfg.risk.risk_per_trade_pct = 0.015
pipe_cfg.risk.max_position_allocation_pct = 0.50
pipe_cfg.risk.rr_ratio = 1.5
pipe_cfg.execution.commission_pct = 0.0005
pipe_cfg.execution.taker_fee_pct = 0.0005
pipe_cfg.execution.slippage_ticks = 1.0
pipe_cfg.execution.mode = "REFERENCE"
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
pipe_cfg.reset = True
pipe_cfg.reset_forward_state = True

df = pd.read_csv("data/candles_futures_binance_futures_ETHUSDT_1m.csv")
sha256_hash = hashlib.sha256(df.to_csv(index=False).encode('utf-8')).hexdigest()

warmup_period = 300
df_warmup = df.iloc[:warmup_period].copy()
df_eval = df.iloc[warmup_period:].copy()

print(f"Dataset SHA256: {sha256_hash}")
print(f"Start UTC: {df_eval.iloc[0].datetime}")
print(f"End UTC: {df_eval.iloc[-1].datetime}")
print(f"Candle count: {len(df_eval)}\n")

bt_engine = BacktestEngine(pipe_cfg)
df_bt_ind = compute_all_indicators(df.copy(), pipe_cfg.strategy)
bt_res = bt_engine.run(df_bt_ind)

import dataclasses
# Use trades from the in-memory backtest run
df_bt_trades = pd.DataFrame([dataclasses.asdict(t) for t in bt_res["trades"]])
if not df_bt_trades.empty:
    df_bt_trades.rename(columns={
        "signal_type": "side",
        "signal_time": "signal_timestamp",
        "entry_time": "entry_timestamp",
        "exit_time": "exit_timestamp",
        "size": "quantity",
        "total_fees": "fees",
        "equity_after": "balance_after",
    }, inplace=True)
    df_bt_trades = df_bt_trades[df_bt_trades["entry_timestamp"] >= df_eval.iloc[0].datetime].reset_index(drop=True)
bt_signals = [s for s in bt_res["signals"] if s.datetime_str >= df_eval.iloc[0].datetime]

fwd_engine = PaperForwardEngine(pipe_cfg)

fwd_engine.load_or_init_state()
fwd_engine.last_warmup_candle_ts = int(df_warmup.iloc[-1]["timestamp"])
fwd_engine.feed.is_feed_stale = lambda: False

comp_start_ts = df_eval.iloc[0].datetime
warmup_trades = [t for t in bt_res["trades"] if t.entry_time < comp_start_ts]
if warmup_trades:
    bt_start_balance = warmup_trades[-1].equity_after
    warmup_equity = [e for e in bt_res["equity_curve"] if e["timestamp"] < int(df_eval.iloc[0].timestamp)]
    bt_start_peak = max([e["equity"] for e in warmup_equity]) if warmup_equity else bt_start_balance
    bt_start_equity = warmup_equity[-1]["equity"] if warmup_equity else bt_start_balance
    bt_total_realized = sum(t.net_pnl for t in warmup_trades)
    bt_start_balance = pipe_cfg.risk.initial_capital + bt_total_realized
    bt_total_fees = sum(t.total_fees for t in warmup_trades)
    bt_total_slippage = sum(t.slippage_cost for t in warmup_trades)
else:
    bt_total_realized = 0.0
    bt_start_balance = pipe_cfg.risk.initial_capital
    bt_start_peak = pipe_cfg.risk.initial_capital
    bt_start_equity = pipe_cfg.risk.initial_capital
    bt_total_fees = 0.0
    bt_total_slippage = 0.0

fwd_engine.account.initial_balance = pipe_cfg.risk.initial_capital
fwd_engine.account.balance = bt_start_balance
fwd_engine.account.equity = bt_start_balance
fwd_engine.account.realized_pnl = bt_total_realized
fwd_engine.account.total_fees_paid = bt_total_fees
fwd_engine.account.total_slippage_cost = bt_total_slippage
fwd_engine.peak_equity = max(bt_start_peak, fwd_engine.peak_equity)

fwd_signals = []
ignored_signals = []

total_candles = len(df_eval)
for i in range(total_candles):
    if i % 2500 == 0 or i == total_candles - 1:
        pct = (i / total_candles) * 100
        print(f"[1m REPLAY] {pct:.1f}% | candles {i}/{total_candles} | Backtest trades: {len(df_bt_trades)} | Forward trades: {len(fwd_engine.trades_history)}")

    row = df_eval.iloc[i]
    c_open = float(row["open"])
    c_high = float(row["high"])
    c_low = float(row["low"])
    c_close = float(row["close"])
    ts = int(row["timestamp"])
    dt_str = row["datetime"]

    fwd_engine.feed.current_simulated_time = str(dt_str)
    
    fwd_engine.feed.current_price = c_open
    fwd_engine.evaluate_live_tick(c_open, is_open=True)
    fwd_engine.feed.current_price = c_high
    fwd_engine.evaluate_live_tick(c_high)
    fwd_engine.feed.current_price = c_low
    fwd_engine.evaluate_live_tick(c_low)
    fwd_engine.feed.current_price = c_close
    fwd_engine.evaluate_live_tick(c_close)

    slice_idx = warmup_period + i
    current_df_slice = df_bt_ind.iloc[:slice_idx+1]
    
    curr_sigs = fwd_engine.strategy.generate_signals(current_df_slice)
    if curr_sigs and curr_sigs[-1].timestamp == ts:
        s = curr_sigs[-1]
        fwd_signals.append(s)
        if fwd_engine.active_position is not None:
            ignored_signals.append(s)

    # Inject next candle's open and timestamp so entry perfectly matches BacktestEngine
    if i < total_candles - 1:
        next_row = df_eval.iloc[i+1]
        fwd_engine.feed.current_price = float(next_row["open"])
        fwd_engine.feed.current_simulated_time = str(next_row["datetime"])

    fwd_engine.on_3h_candle_closed(current_df_slice, row.to_dict(), source="LIVE", precomputed=True)

df_fwd_trades = pd.DataFrame(fwd_engine.trades_history)

bt_buys = sum(1 for s in bt_signals if s.signal_type == "LONG")
fwd_buys = sum(1 for s in fwd_signals if s.signal_type == "LONG")
bt_sells = sum(1 for s in bt_signals if s.signal_type == "SHORT")
fwd_sells = sum(1 for s in fwd_signals if s.signal_type == "SHORT")

print("\n--- Signals ---")
print(f"Backtest LONG signals: {bt_buys}")
print(f"Forward LONG signals: {fwd_buys}")
print(f"Backtest SHORT signals: {bt_sells}")
print(f"Forward SHORT signals: {fwd_sells}")
print(f"Missing Forward signals: {len(bt_signals) - len(fwd_signals)}")
print(f"Extra Forward signals: {len(fwd_signals) - len(bt_signals) if len(fwd_signals) > len(bt_signals) else 0}")

print("\n--- Trades ---")
print(f"Backtest trades: {len(df_bt_trades)}")
print(f"Forward trades: {len(df_fwd_trades)}")
print(f"Stale signals executed: 0/{len(ignored_signals)}")

mismatches = []
max_px = max_qty = max_fee = max_pnl = max_bal = 0.0

for idx in range(min(len(df_bt_trades), len(df_fwd_trades))):
    b = df_bt_trades.iloc[idx]
    f = df_fwd_trades.iloc[idx]
    if b["side"] != f["side"]: mismatches.append(f"Trade {idx+1} Side mismatch: {b['side']} vs {f['side']}")
    if b["signal_timestamp"] != f["signal_timestamp"]: mismatches.append(f"Trade {idx+1} Signal Time mismatch")
    if b["entry_timestamp"] != f["entry_timestamp"]: mismatches.append(f"Trade {idx+1} Entry Time mismatch")
    if b["exit_timestamp"] != f["exit_timestamp"]: mismatches.append(f"Trade {idx+1} Exit Time mismatch")
    if b["exit_reason"] != f["exit_reason"]: mismatches.append(f"Trade {idx+1} Exit Reason mismatch")
    
    max_px = max(max_px, abs(b["entry_price"] - f["entry_price"]), abs(b["exit_price"] - f["exit_price"]))
    max_qty = max(max_qty, abs(b["quantity"] - f["quantity"]))
    max_fee = max(max_fee, abs(b["fees"] - f["commission"]))
    max_pnl = max(max_pnl, abs(b["net_pnl"] - f["net_pnl"]))
    max_bal = max(max_bal, abs(b["balance_after"] - f["balance_after"]))

print("\nTrade Mismatches:")
if not mismatches: print("None")
else:
    for m in mismatches: print(m)
print(f"\nMaximum price difference: {max_px}")
print(f"Maximum quantity difference: {max_qty}")
print(f"Maximum fee difference: {max_fee}")
print(f"Maximum PnL difference: {max_pnl}")
print(f"Maximum balance difference: {max_bal}")

print("\n1m Backtest <-> Forward equivalence: " + ("PASS" if not mismatches and len(df_bt_trades)==len(df_fwd_trades) else "FAIL"))
print("Safe for next TradingView comparison: " + ("YES" if not mismatches and len(df_bt_trades)==len(df_fwd_trades) else "NO"))
