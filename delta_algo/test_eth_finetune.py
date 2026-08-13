"""
Fine-tuning ETH parameters to reach > +20% Net Return.
"""

import numpy as np
import pandas as pd
from config import StrategyConfig
from indicators import compute_all_indicators
from strategy import Delta1HStrategy


def run_eth_fine_tune():
    df_raw = pd.read_csv("data/candles_ETHUSDT_1h.csv")
    df_raw["datetime"] = pd.to_datetime(df_raw["datetime"])
    n = len(df_raw)

    opens = df_raw["open"].to_numpy()
    highs = df_raw["high"].to_numpy()
    lows = df_raw["low"].to_numpy()
    closes = df_raw["close"].to_numpy()

    # Test consolidation candle count: 6 vs 8 vs 10
    # Test consolidation ATR mult: 1.8 vs 2.0 vs 2.2 vs 2.5
    for cons_n in [6, 8]:
        for cons_mult in [1.8, 2.0, 2.2, 2.5]:
            df_ind = compute_all_indicators(
                df=df_raw,
                ema_period=51,
                rsi_period=14,
                atr_period=14,
                consolidation_candles=cons_n,
                consolidation_atr_mult=cons_mult,
                swing_lookback=8,
                trend_ema_period=200,
            )
            strat_cfg = StrategyConfig(risk_reward_ratio=1.5, volume_mult=1.0)
            strategy = Delta1HStrategy(strat_cfg)
            signals = strategy.generate_signals(df_ind)
            signals_by_idx = {s.candle_idx: s for s in signals}

            capital = 10000.0
            equity = capital
            trades = []
            active_trade = None
            peak_equity = capital
            max_dd_pct = 0.0
            taker_fee = 0.0005
            slippage = 0.0003

            for i in range(n):
                c_open = opens[i]
                c_high = highs[i]
                c_low = lows[i]
                c_close = closes[i]

                if active_trade is not None:
                    is_closed = False
                    exit_price = None
                    if active_trade["type"] == "LONG":
                        if c_low <= active_trade["sl"]:
                            exit_price = min(c_open, active_trade["sl"]) * (1.0 - slippage)
                            is_closed = True
                        elif c_high >= active_trade["tp"]:
                            exit_price = max(c_open, active_trade["tp"]) * (1.0 - slippage)
                            is_closed = True
                    elif active_trade["type"] == "SHORT":
                        if c_high >= active_trade["sl"]:
                            exit_price = max(c_open, active_trade["sl"]) * (1.0 + slippage)
                            is_closed = True
                        elif c_low <= active_trade["tp"]:
                            exit_price = min(c_open, active_trade["tp"]) * (1.0 + slippage)
                            is_closed = True

                    if is_closed and exit_price is not None:
                        gross = (exit_price - active_trade["entry"]) * active_trade["size"] if active_trade["type"] == "LONG" else (active_trade["entry"] - exit_price) * active_trade["size"]
                        fees = (active_trade["entry"] + exit_price) * active_trade["size"] * taker_fee
                        trade_pnl = gross - fees
                        equity += trade_pnl
                        trades.append(trade_pnl)
                        active_trade = None

                if active_trade is None and (i - 1) in signals_by_idx:
                    sig = signals_by_idx[i - 1]
                    if sig.signal_type == "LONG":
                        entry_p = c_open * (1.0 + slippage)
                        unit_risk = entry_p - sig.sl_price
                        tp_p = entry_p + (1.5 * unit_risk)
                    else:
                        entry_p = c_open * (1.0 - slippage)
                        unit_risk = sig.sl_price - entry_p
                        tp_p = entry_p - (1.5 * unit_risk)

                    if unit_risk > 0:
                        risk_dollars = equity * 0.015  # 1.5% compounding
                        raw_size = risk_dollars / unit_risk
                        max_alloc_cap = (equity * 0.50) / entry_p
                        final_size = min(raw_size, max_alloc_cap)
                        active_trade = {
                            "type": sig.signal_type,
                            "entry": entry_p,
                            "sl": sig.sl_price,
                            "tp": tp_p,
                            "unit_risk": unit_risk,
                            "size": final_size,
                        }

                if equity > peak_equity:
                    peak_equity = equity
                dd = (peak_equity - equity) / peak_equity * 100.0
                if dd > max_dd_pct:
                    max_dd_pct = dd

            wins = [p for p in trades if p > 0]
            win_rate = (len(wins) / len(trades) * 100.0) if trades else 0.0
            gross_w = sum(wins)
            gross_l = abs(sum([p for p in trades if p <= 0]))
            pf = (gross_w / gross_l) if gross_l > 0 else 99.0
            net_profit = equity - capital
            ret_pct = (net_profit / capital) * 100.0
            print(f"ConsN: {cons_n} | ConsMult: {cons_mult:.1f} | Net: ${net_profit:+,.2f} ({ret_pct:+.2f}%) | WR: {win_rate:.1f}% | PF: {pf:.2f} | MaxDD: {max_dd_pct:.2f}% | Trades: {len(trades)}")


if __name__ == "__main__":
    run_eth_fine_tune()
