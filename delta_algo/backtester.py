"""
Event-Driven Bar-by-Bar Backtesting Engine.
Ultra-fast numpy vectorized implementation.
"""

from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional
import numpy as np
import pandas as pd

from config import AppConfig, ExecutionConfig, RiskConfig
from strategy import Signal, Delta1HStrategy
from risk_manager import RiskManager


@dataclass
class Trade:
    trade_id: int
    signal_type: str                      # 'LONG' or 'SHORT'
    signal_time: str
    signal_price: float
    entry_bar_idx: int
    entry_time: str
    entry_price: float                    # Realized entry price with slippage
    size: float                           # In base asset (e.g. ETH)
    nominal_value: float                  # size * entry_price
    margin_required: float                # Margin locked in trade
    effective_leverage: float             # nominal_value / equity
    capital_allocation_pct: float         # % of portfolio allocated
    risk_budget: float                    # 1% - 1.5% risk dollar amount
    sl_price: float                       # Active Stop-Loss
    tp_price: float                       # Active Take-Profit
    exit_bar_idx: Optional[int] = None
    exit_time: Optional[str] = None
    exit_price: Optional[float] = None    # Realized exit price with slippage
    exit_reason: Optional[str] = None     # 'TP', 'BE_SL', 'SL', or 'END_OF_DATA'
    duration_bars: int = 0
    gross_pnl: float = 0.0
    entry_fee: float = 0.0
    exit_fee: float = 0.0
    total_fees: float = 0.0
    slippage_cost: float = 0.0
    net_pnl: float = 0.0
    net_return_pct: float = 0.0           # Net PnL / Account Equity before trade
    r_multiple: float = 0.0               # Net PnL / Risk Budget
    equity_after: float = 0.0             # Account balance following trade


class DeltaBacktester:
    """Simulates realistic execution of Delta 1H Strategy."""

    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or AppConfig()
        self.strategy = Delta1HStrategy(self.config.strategy)
        self.risk_manager = RiskManager(self.config.risk, self.config.strategy)
        self.exec_cfg = self.config.execution

    def run(self, df: pd.DataFrame) -> Dict[str, Any]:
        if df.empty:
            raise ValueError("Candle DataFrame is empty.")

        signals = self.strategy.generate_signals(df)
        signals_by_idx = {s.candle_idx: s for s in signals}

        current_equity = self.config.risk.initial_capital
        equity_curve = []
        trades: List[Trade] = []
        active_trade: Optional[Trade] = None
        trade_counter = 0

        n = len(df)
        opens = df["open"].to_numpy()
        highs = df["high"].to_numpy()
        lows = df["low"].to_numpy()
        closes = df["close"].to_numpy()
        timestamps = df["timestamp"].to_numpy()
        datetimes = df["datetime"].astype(str).to_numpy()

        for i in range(n):
            c_open = float(opens[i])
            c_high = float(highs[i])
            c_low = float(lows[i])
            c_close = float(closes[i])
            dt_str = datetimes[i]
            ts = int(timestamps[i])

            # -------------------------------------------------------------
            # 1. Manage Active Position on Candle i
            # -------------------------------------------------------------
            if active_trade is not None:
                active_trade.duration_bars += 1
                is_closed = False
                exit_price = None
                exit_reason = None

                # 1.1 Breakeven Stop Check at +1R
                if self.config.strategy.use_breakeven_at_1r and active_trade.size > 0:
                    unit_risk = active_trade.risk_budget / active_trade.size
                    if active_trade.signal_type == "LONG":
                        if c_high >= (active_trade.entry_price + unit_risk):
                            breakeven_sl = active_trade.entry_price * (1.0 + self.exec_cfg.taker_fee_pct * 2.5)
                            active_trade.sl_price = max(active_trade.sl_price, round(breakeven_sl, 2))
                    elif active_trade.signal_type == "SHORT":
                        if c_low <= (active_trade.entry_price - unit_risk):
                            breakeven_sl = active_trade.entry_price * (1.0 - self.exec_cfg.taker_fee_pct * 2.5)
                            active_trade.sl_price = min(active_trade.sl_price, round(breakeven_sl, 2))

                # 1.2 Intra-bar SL / TP Execution
                if active_trade.signal_type == "LONG":
                    sl_hit = c_low <= active_trade.sl_price
                    tp_hit = c_high >= active_trade.tp_price

                    if sl_hit and tp_hit:
                        exit_price = min(c_open, active_trade.sl_price) * (1.0 - self.exec_cfg.slippage_pct)
                        exit_reason = "BE_SL" if active_trade.sl_price >= active_trade.entry_price else "SL"
                        is_closed = True
                    elif sl_hit:
                        base_exit = min(c_open, active_trade.sl_price)
                        exit_price = base_exit * (1.0 - self.exec_cfg.slippage_pct)
                        exit_reason = "BE_SL" if active_trade.sl_price >= active_trade.entry_price else "SL"
                        is_closed = True
                    elif tp_hit:
                        base_exit = max(c_open, active_trade.tp_price)
                        exit_price = base_exit * (1.0 - self.exec_cfg.slippage_pct)
                        exit_reason = "TP"
                        is_closed = True

                elif active_trade.signal_type == "SHORT":
                    sl_hit = c_high >= active_trade.sl_price
                    tp_hit = c_low <= active_trade.tp_price

                    if sl_hit and tp_hit:
                        exit_price = max(c_open, active_trade.sl_price) * (1.0 + self.exec_cfg.slippage_pct)
                        exit_reason = "BE_SL" if active_trade.sl_price <= active_trade.entry_price else "SL"
                        is_closed = True
                    elif sl_hit:
                        base_exit = max(c_open, active_trade.sl_price)
                        exit_price = base_exit * (1.0 + self.exec_cfg.slippage_pct)
                        exit_reason = "BE_SL" if active_trade.sl_price <= active_trade.entry_price else "SL"
                        is_closed = True
                    elif tp_hit:
                        base_exit = min(c_open, active_trade.tp_price)
                        exit_price = base_exit * (1.0 + self.exec_cfg.slippage_pct)
                        exit_reason = "TP"
                        is_closed = True

                # End of dataset exit
                if not is_closed and i == n - 1:
                    exit_price = c_close * (1.0 - self.exec_cfg.slippage_pct if active_trade.signal_type == "LONG" else 1.0 + self.exec_cfg.slippage_pct)
                    exit_reason = "END_OF_DATA"
                    is_closed = True

                if is_closed:
                    active_trade.exit_bar_idx = i
                    active_trade.exit_time = dt_str
                    active_trade.exit_price = round(exit_price, 2)
                    active_trade.exit_reason = exit_reason

                    if active_trade.signal_type == "LONG":
                        gross_pnl = (active_trade.exit_price - active_trade.entry_price) * active_trade.size
                    else:
                        gross_pnl = (active_trade.entry_price - active_trade.exit_price) * active_trade.size

                    exit_nominal = active_trade.exit_price * active_trade.size
                    exit_fee = exit_nominal * self.exec_cfg.taker_fee_pct
                    total_fees = active_trade.entry_fee + exit_fee
                    
                    entry_slip = abs(active_trade.entry_price * self.exec_cfg.slippage_pct * active_trade.size)
                    exit_slip = abs(active_trade.exit_price * self.exec_cfg.slippage_pct * active_trade.size)
                    total_slip = entry_slip + exit_slip

                    net_pnl = gross_pnl - total_fees
                    equity_before = current_equity
                    current_equity += net_pnl

                    active_trade.gross_pnl = round(gross_pnl, 2)
                    active_trade.exit_fee = round(exit_fee, 2)
                    active_trade.total_fees = round(total_fees, 2)
                    active_trade.slippage_cost = round(total_slip, 2)
                    active_trade.net_pnl = round(net_pnl, 2)
                    active_trade.net_return_pct = round((net_pnl / equity_before) * 100, 3)
                    active_trade.r_multiple = round(net_pnl / max(active_trade.risk_budget, 1e-6), 2)
                    active_trade.equity_after = round(current_equity, 2)

                    trades.append(active_trade)
                    active_trade = None

            # -------------------------------------------------------------
            # 2. Next-Candle Open Execution
            # -------------------------------------------------------------
            if active_trade is None and (i - 1) in signals_by_idx:
                sig = signals_by_idx[i - 1]
                
                if sig.signal_type == "LONG":
                    realized_entry = c_open * (1.0 + self.exec_cfg.slippage_pct)
                else:
                    realized_entry = c_open * (1.0 - self.exec_cfg.slippage_pct)

                sizing = self.risk_manager.calculate_position(
                    equity=current_equity,
                    entry_price=realized_entry,
                    sl_price=sig.sl_price,
                    signal_type=sig.signal_type
                )

                if sizing.is_valid and sizing.position_size > 0:
                    trade_counter += 1
                    entry_nominal = realized_entry * sizing.position_size
                    entry_fee = entry_nominal * self.exec_cfg.taker_fee_pct

                    active_trade = Trade(
                        trade_id=trade_counter,
                        signal_type=sig.signal_type,
                        signal_time=sig.datetime_str,
                        signal_price=sig.close_price,
                        entry_bar_idx=i,
                        entry_time=dt_str,
                        entry_price=round(realized_entry, 2),
                        size=sizing.position_size,
                        nominal_value=sizing.nominal_position_value,
                        margin_required=sizing.margin_required,
                        effective_leverage=sizing.effective_leverage,
                        capital_allocation_pct=sizing.capital_allocation_pct,
                        risk_budget=sizing.risk_amount,
                        sl_price=sizing.sl_price,
                        tp_price=sizing.tp_price,
                        entry_fee=round(entry_fee, 2),
                    )

            equity_curve.append({
                "bar_idx": i,
                "timestamp": ts,
                "datetime": dt_str,
                "equity": round(current_equity, 2),
                "in_position": active_trade is not None
            })

        return {
            "trades": trades,
            "signals": signals,
            "equity_curve": equity_curve,
            "final_equity": current_equity,
            "total_trades": len(trades)
        }
