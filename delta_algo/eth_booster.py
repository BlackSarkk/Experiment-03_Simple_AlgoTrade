"""
ETH Profit Booster & Multi-Strategy Analysis.
Accurately models:
- Fixed 1.5R with Compounding Sizing
- Dynamic Trailing Stop
- Profit Scaling across 1.5%, 2.0%, 2.5% Risk allocations
"""

import numpy as np
import pandas as pd
from config import StrategyConfig
from indicators import compute_all_indicators
from strategy import Delta1HStrategy


def run_eth_booster():
    df_raw = pd.read_csv("data/candles_ETHUSDT_1h.csv")
    df_raw["datetime"] = pd.to_datetime(df_raw["datetime"])
    n = len(df_raw)

    df_ind = compute_all_indicators(
        df=df_raw,
        ema_period=51,
        rsi_period=14,
        atr_period=14,
        consolidation_candles=8,
        consolidation_atr_mult=2.2,
        swing_lookback=8,
        trend_ema_period=200,
    )

    opens = df_ind["open"].to_numpy()
    highs = df_ind["high"].to_numpy()
    lows = df_ind["low"].to_numpy()
    closes = df_ind["close"].to_numpy()
    ema_51 = df_ind["ema_51"].to_numpy()
    timestamps = df_ind["timestamp"].to_numpy()
    datetimes = df_ind["datetime"].astype(str).to_numpy()

    taker_fee = 0.0005
    slippage = 0.0003

    print("=" * 90)
    print("               ETH 1-YEAR PROFIT ACCELERATION MATRIX (8,760 CANDLES)")
    print("=" * 90)

    for risk_pct in [1.5, 2.0, 2.5]:
        for mode in ["Fixed_1.5R", "Fixed_2.0R", "Trailing_EMA51_Runner"]:
            capital = 10000.0
            equity = capital
            trades = []
            active_trade = None
            peak_equity = capital
            max_dd_pct = 0.0

            strat_cfg = StrategyConfig(risk_reward_ratio=1.5, volume_mult=1.0)
            strategy = Delta1HStrategy(strat_cfg)
            signals = strategy.generate_signals(df_ind)
            signals_by_idx = {s.candle_idx: s for s in signals}

            for i in range(n):
                c_open = opens[i]
                c_high = highs[i]
                c_low = lows[i]
                c_close = closes[i]

                # 1. Manage Active Trade
                if active_trade is not None:
                    is_closed = False
                    exit_price = None
                    exit_reason = None
                    trade_pnl = 0.0

                    if active_trade["type"] == "LONG":
                        if mode == "Fixed_1.5R":
                            if c_low <= active_trade["sl"]:
                                exit_price = min(c_open, active_trade["sl"]) * (1.0 - slippage)
                                exit_reason = "SL"
                                is_closed = True
                            elif c_high >= active_trade["tp"]:
                                exit_price = max(c_open, active_trade["tp"]) * (1.0 - slippage)
                                exit_reason = "TP"
                                is_closed = True
                        elif mode == "Fixed_2.0R":
                            tp2 = active_trade["entry"] + 2.0 * active_trade["unit_risk"]
                            if c_low <= active_trade["sl"]:
                                exit_price = min(c_open, active_trade["sl"]) * (1.0 - slippage)
                                exit_reason = "SL"
                                is_closed = True
                            elif c_high >= tp2:
                                exit_price = max(c_open, tp2) * (1.0 - slippage)
                                exit_reason = "TP"
                                is_closed = True
                        elif mode == "Trailing_EMA51_Runner":
                            # If not hit 1.5R yet, standard SL
                            if not active_trade.get("tp1_hit", False):
                                if c_low <= active_trade["sl"]:
                                    exit_price = min(c_open, active_trade["sl"]) * (1.0 - slippage)
                                    exit_reason = "SL"
                                    is_closed = True
                                elif c_high >= active_trade["tp"]:
                                    # Move SL to Entry + fee buffer
                                    active_trade["tp1_hit"] = True
                                    active_trade["sl"] = active_trade["entry"] * (1.0 + taker_fee * 2.5)
                            else:
                                # Runner phase: exit when price drops below 51 EMA or hits breakeven SL
                                if c_low <= active_trade["sl"]:
                                    exit_price = min(c_open, active_trade["sl"]) * (1.0 - slippage)
                                    exit_reason = "BE_SL"
                                    is_closed = True
                                elif c_close < ema_51[i]:
                                    exit_price = c_close * (1.0 - slippage)
                                    exit_reason = "EMA_Trail"
                                    is_closed = True

                    elif active_trade["type"] == "SHORT":
                        if mode == "Fixed_1.5R":
                            if c_high >= active_trade["sl"]:
                                exit_price = max(c_open, active_trade["sl"]) * (1.0 + slippage)
                                exit_reason = "SL"
                                is_closed = True
                            elif c_low <= active_trade["tp"]:
                                exit_price = min(c_open, active_trade["tp"]) * (1.0 + slippage)
                                exit_reason = "TP"
                                is_closed = True
                        elif mode == "Fixed_2.0R":
                            tp2 = active_trade["entry"] - 2.0 * active_trade["unit_risk"]
                            if c_high >= active_trade["sl"]:
                                exit_price = max(c_open, active_trade["sl"]) * (1.0 + slippage)
                                exit_reason = "SL"
                                is_closed = True
                            elif c_low <= tp2:
                                exit_price = min(c_open, tp2) * (1.0 + slippage)
                                exit_reason = "TP"
                                is_closed = True
                        elif mode == "Trailing_EMA51_Runner":
                            if not active_trade.get("tp1_hit", False):
                                if c_high >= active_trade["sl"]:
                                    exit_price = max(c_open, active_trade["sl"]) * (1.0 + slippage)
                                    exit_reason = "SL"
                                    is_closed = True
                                elif c_low <= active_trade["tp"]:
                                    active_trade["tp1_hit"] = True
                                    active_trade["sl"] = active_trade["entry"] * (1.0 - taker_fee * 2.5)
                            else:
                                if c_high >= active_trade["sl"]:
                                    exit_price = max(c_open, active_trade["sl"]) * (1.0 + slippage)
                                    exit_reason = "BE_SL"
                                    is_closed = True
                                elif c_close > ema_51[i]:
                                    exit_price = c_close * (1.0 + slippage)
                                    exit_reason = "EMA_Trail"
                                    is_closed = True

                    if is_closed and exit_price is not None:
                        if active_trade["type"] == "LONG":
                            gross_pnl = (exit_price - active_trade["entry"]) * active_trade["size"]
                        else:
                            gross_pnl = (active_trade["entry"] - exit_price) * active_trade["size"]
                        
                        fees = (active_trade["entry"] + exit_price) * active_trade["size"] * taker_fee
                        trade_pnl = gross_pnl - fees
                        equity += trade_pnl
                        trades.append(trade_pnl)
                        active_trade = None

                # 2. Next-Candle Open Execution
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
                        # Dynamic Compounding Sizing based on current equity
                        risk_dollars = equity * (risk_pct / 100.0)
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
            losses = [p for p in trades if p <= 0]
            win_rate = (len(wins) / len(trades) * 100.0) if trades else 0.0
            gross_w = sum(wins)
            gross_l = abs(sum(losses))
            pf = (gross_w / gross_l) if gross_l > 0 else 99.0
            net_profit = equity - capital
            ret_pct = (net_profit / capital) * 100.0

            print(f"Risk: {risk_pct:.1f}% | Mode: {mode:<22} | Net Profit: ${net_profit:+,.2f} ({ret_pct:+.2f}%) | WR: {win_rate:.1f}% | PF: {pf:.2f} | MaxDD: {max_dd_pct:.2f}% | Trades: {len(trades)}")

    print("=" * 90)


if __name__ == "__main__":
    run_eth_booster()
