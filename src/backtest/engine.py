"""
Event-Driven Bar-by-Bar Backtesting Engine.
Supports REFERENCE_MODE (exact Pine Script semantics with leveraged effective equity) and REALISTIC_MODE.
Integrates live Rich terminal dashboard and tqdm progress monitoring.
"""

from dataclasses import dataclass
from typing import List, Dict, Any, Optional
import time
import numpy as np
import pandas as pd
from tqdm import tqdm
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.layout import Layout
from rich.live import Live
from rich.text import Text

from common.config import PipelineConfig
from common.accounting import AccountState, AccountingEngine
from strategy.baseline_strategy import Signal, BaselineStrategy
from risk_management.baseline import BaselineRiskManager


@dataclass
class TradeRecord:
    trade_id: int
    signal_type: str                      # 'LONG' or 'SHORT'
    signal_time: str
    signal_price: float
    entry_bar_idx: int
    entry_time: str
    entry_price: float                    # Realized entry price with slippage
    size: float                           # Base asset quantity
    nominal_value: float                  # size * entry_price
    margin_required: float                # Margin locked
    effective_leverage: float             # nominal_value / equity
    capital_allocation_pct: float         # % of account equity allocated
    risk_budget: float                    # Risk amount in dollars (effective_equity * risk_pct)
    sl_price: float                       # Active Stop-Loss
    tp_price: float                       # Active Take-Profit
    cap_activated: bool = False           # Whether max allocation cap was triggered
    # Entry Indicator Snapshots
    ema_51: float = 0.0
    rsi: float = 0.0
    atr: float = 0.0
    consolidation_range: float = 0.0
    volume: float = 0.0
    vol_sma_20: float = 0.0
    swing_high: float = 0.0
    swing_low: float = 0.0
    # Exit details
    exit_bar_idx: Optional[int] = None
    exit_time: Optional[str] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    duration_bars: int = 0
    gross_pnl: float = 0.0
    entry_fee: float = 0.0
    exit_fee: float = 0.0
    total_fees: float = 0.0
    slippage_cost: float = 0.0
    net_pnl: float = 0.0
    net_return_pct: float = 0.0
    r_multiple: float = 0.0
    equity_after: float = 0.0


class BacktestEngine:
    """Bar-by-bar event-driven backtesting execution engine."""

    def __init__(self, config: PipelineConfig):
        self.config = config
        self.strategy = BaselineStrategy(config.strategy)
        self.risk_manager = BaselineRiskManager(config.risk, config.strategy)
        self.exec_cfg = config.execution
        self.console = Console()

    def run(self, df: pd.DataFrame) -> Dict[str, Any]:
        if df.empty:
            raise ValueError("Candle DataFrame is empty.")

        signals = self.strategy.generate_signals(df)
        signals_by_idx = {s.candle_idx: s for s in signals}

        account = AccountState(
            initial_balance=self.config.risk.initial_capital,
            balance=self.config.risk.initial_capital,
            equity=self.config.risk.initial_capital
        )

        equity_curve: List[Dict[str, Any]] = []
        trades: List[TradeRecord] = []
        active_trade: Optional[TradeRecord] = None
        trade_counter = 0

        n = len(df)
        opens = df["open"].to_numpy()
        highs = df["high"].to_numpy()
        lows = df["low"].to_numpy()
        closes = df["close"].to_numpy()
        timestamps = df["timestamp"].to_numpy()
        datetimes = df["datetime"].astype(str).to_numpy()

        peak_equity = account.initial_balance
        max_dd_pct = 0.0
        is_ref_mode = (self.exec_cfg.mode.upper() == "REFERENCE")
        latest_event = "Engine Started"
        in_position_at_bar_close: Dict[int, bool] = {}

        pbar = tqdm(total=n, desc=f"Backtesting {self.config.platform.symbol} ({self.exec_cfg.mode})", unit="bar")

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

                if active_trade.signal_type == "LONG":
                    active_trade.gross_pnl = (c_close - active_trade.entry_price) * active_trade.size
                else:
                    active_trade.gross_pnl = (active_trade.entry_price - c_close) * active_trade.size

                # Intrabar SL / TP execution check
                if active_trade.signal_type == "LONG":
                    sl_hit = c_low <= active_trade.sl_price
                    tp_hit = c_high >= active_trade.tp_price

                    if sl_hit and tp_hit:
                        base_exit = min(c_open, active_trade.sl_price)
                        exit_price = (base_exit - self.exec_cfg.slippage_ticks * 0.1) if is_ref_mode else base_exit * (1.0 - self.exec_cfg.slippage_pct)
                        exit_reason = "SL"
                        is_closed = True
                    elif sl_hit:
                        base_exit = min(c_open, active_trade.sl_price)
                        exit_price = (base_exit - self.exec_cfg.slippage_ticks * 0.1) if is_ref_mode else base_exit * (1.0 - self.exec_cfg.slippage_pct)
                        exit_reason = "SL"
                        is_closed = True
                    elif tp_hit:
                        base_exit = max(c_open, active_trade.tp_price)
                        exit_price = (base_exit - self.exec_cfg.slippage_ticks * 0.1) if is_ref_mode else base_exit * (1.0 - self.exec_cfg.slippage_pct)
                        exit_reason = "TP"
                        is_closed = True

                elif active_trade.signal_type == "SHORT":
                    sl_hit = c_high >= active_trade.sl_price
                    tp_hit = c_low <= active_trade.tp_price

                    if sl_hit and tp_hit:
                        base_exit = max(c_open, active_trade.sl_price)
                        exit_price = (base_exit + self.exec_cfg.slippage_ticks * 0.1) if is_ref_mode else base_exit * (1.0 + self.exec_cfg.slippage_pct)
                        exit_reason = "SL"
                        is_closed = True
                    elif sl_hit:
                        base_exit = max(c_open, active_trade.sl_price)
                        exit_price = (base_exit + self.exec_cfg.slippage_ticks * 0.1) if is_ref_mode else base_exit * (1.0 + self.exec_cfg.slippage_pct)
                        exit_reason = "SL"
                        is_closed = True
                    elif tp_hit:
                        base_exit = min(c_open, active_trade.tp_price)
                        exit_price = (base_exit + self.exec_cfg.slippage_ticks * 0.1) if is_ref_mode else base_exit * (1.0 + self.exec_cfg.slippage_pct)
                        exit_reason = "TP"
                        is_closed = True

                if not is_closed and i == n - 1:
                    exit_price = c_close
                    exit_reason = "END_OF_DATA"
                    is_closed = True

                if is_closed and exit_price is not None:
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

                    if is_ref_mode:
                        total_slip = (self.exec_cfg.slippage_ticks * 0.1) * 2.0 * active_trade.size
                    else:
                        entry_slip = abs(active_trade.entry_price * self.exec_cfg.slippage_pct * active_trade.size)
                        exit_slip = abs(active_trade.exit_price * self.exec_cfg.slippage_pct * active_trade.size)
                        total_slip = entry_slip + exit_slip

                    net_pnl = gross_pnl - total_fees
                    balance_before = account.balance

                    AccountingEngine.update_account_on_trade_close(
                        account=account,
                        net_pnl=net_pnl,
                        total_fees=total_fees,
                        total_slippage=total_slip
                    )

                    active_trade.gross_pnl = round(gross_pnl, 2)
                    active_trade.exit_fee = round(exit_fee, 2)
                    active_trade.total_fees = round(total_fees, 2)
                    active_trade.slippage_cost = round(total_slip, 2)
                    active_trade.net_pnl = round(net_pnl, 2)
                    active_trade.net_return_pct = round((net_pnl / balance_before) * 100.0, 3)
                    active_trade.r_multiple = round(net_pnl / max(active_trade.risk_budget, 1e-6), 2)
                    active_trade.equity_after = round(account.balance, 2)

                    latest_event = f"Trade #{active_trade.trade_id} {active_trade.signal_type} Closed via {exit_reason} (Net PnL: ${net_pnl:+.2f})"
                    trades.append(active_trade)
                    active_trade = None

            # -------------------------------------------------------------
            # 2. Next-Candle Open Execution (State-Aware Signal Guard)
            # -------------------------------------------------------------
            sig_idx = i - 1
            sig_valid_state = (sig_idx >= 0) and not in_position_at_bar_close.get(sig_idx, False)

            if active_trade is None and sig_idx in signals_by_idx and sig_valid_state:
                sig = signals_by_idx[i - 1]

                # Side isolation filter check
                is_long_allowed = (sig.signal_type == "LONG" and self.config.strategy.long_enabled)
                is_short_allowed = (sig.signal_type == "SHORT" and self.config.strategy.short_enabled)

                if is_long_allowed or is_short_allowed:
                    if is_ref_mode:
                        realized_entry = c_open + (self.exec_cfg.slippage_ticks * 0.1 if sig.signal_type == "LONG" else -self.exec_cfg.slippage_ticks * 0.1)
                    else:
                        realized_entry = c_open * (1.0 + self.exec_cfg.slippage_pct if sig.signal_type == "LONG" else 1.0 - self.exec_cfg.slippage_pct)

                    sizing = self.risk_manager.calculate_position(
                        equity=account.balance,
                        entry_price=realized_entry,
                        sl_price=sig.sl_price,
                        signal_type=sig.signal_type
                    )

                    if sizing.is_valid and sizing.position_size > 0:
                        trade_counter += 1
                        entry_nominal = realized_entry * sizing.position_size
                        entry_fee = entry_nominal * self.exec_cfg.taker_fee_pct

                        active_trade = TradeRecord(
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
                            cap_activated=False,
                            ema_51=sig.ema_51,
                            rsi=sig.rsi,
                            atr=sig.atr,
                            consolidation_range=sig.risk_per_unit,
                            volume=sig.volume,
                            vol_sma_20=sig.vol_sma_20,
                            swing_high=sig.swing_high,
                            swing_low=sig.swing_low,
                            entry_fee=round(entry_fee, 2),
                        )
                        latest_event = f"Trade #{trade_counter} {sig.signal_type} Opened @ ${realized_entry:.2f} (Size: {sizing.position_size:.4f} ETH)"
                    in_position_at_bar_close[i] = True    # -------------------------------------------------------------
            # 3. Track Equity Curve & Peak Drawdown
            # -------------------------------------------------------------
            current_unrealized = active_trade.gross_pnl if active_trade else 0.0
            current_equity = account.balance + current_unrealized
            account.equity = current_equity

            if current_equity > peak_equity:
                peak_equity = current_equity
            dd = (peak_equity - current_equity) / peak_equity * 100.0 if peak_equity > 0 else 0.0
            if dd > max_dd_pct:
                max_dd_pct = dd

            equity_curve.append({
                "bar_idx": i,
                "timestamp": ts,
                "datetime": dt_str,
                "balance": round(account.balance, 2),
                "equity": round(current_equity, 2),
                "open_pnl": round(current_unrealized, 2),
                "drawdown_pct": round(dd, 2),
                "in_position": active_trade is not None,
                "current_price": c_close,
            })

            in_position_at_bar_close[i] = (active_trade is not None)
            pbar.update(1)

        pbar.close()

        net_ret_pct = ((account.balance - account.initial_balance) / account.initial_balance) * 100.0

        return {
            "trades": trades,
            "signals": signals,
            "equity_curve": equity_curve,
            "account": account,
            "final_balance": account.balance,
            "net_return_pct": net_ret_pct,
            "total_trades": len(trades),
            "max_drawdown_pct": max_dd_pct
        }
