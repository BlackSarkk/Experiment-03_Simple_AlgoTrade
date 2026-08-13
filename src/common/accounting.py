"""
Shared Accounting Engine for Backtesting and Paper Forward Testing.
Enforces strict balance reconciliation, margin calculations, PnL tracking, fee/slippage deduction.
"""

from dataclasses import dataclass
from typing import Optional
import math


@dataclass
class AccountState:
    initial_balance: float
    balance: float
    equity: float
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    total_fees_paid: float = 0.0
    total_slippage_cost: float = 0.0
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0

    def reconcile(self) -> bool:
        """Verify that balance == initial_balance + realized_pnl."""
        expected_balance = self.initial_balance + self.realized_pnl
        if not math.isclose(self.balance, expected_balance, abs_tol=1e-4):
            raise ValueError(f"Account Balance Discrepancy! Actual: ${self.balance:.4f}, Expected: ${expected_balance:.4f}")
        return True


@dataclass
class Position:
    symbol: str
    side: str                          # 'LONG' or 'SHORT'
    size: float                        # In base asset (e.g., ETH)
    entry_price: float                 # Realized entry price with slippage
    signal_price: float                # Signal close price
    entry_timestamp: int
    entry_time_str: str
    sl_price: float                    # Active Stop-Loss
    tp_price: float                    # Active Take-Profit
    risk_budget: float                 # Absolute dollar risk budgeted
    leverage: float
    nominal_value: float               # size * entry_price
    margin_required: float             # nominal_value / leverage
    entry_fee: float
    entry_slippage_cost: float

    def calculate_unrealized_pnl(self, current_price: float) -> float:
        """Calculate gross unrealized PnL at current market price."""
        if self.side == "LONG":
            return (current_price - self.entry_price) * self.size
        else:
            return (self.entry_price - current_price) * self.size


class AccountingEngine:
    """Calculates position sizing, margin, order fees, and executes trade accounting."""

    @staticmethod
    def calculate_margin_required(nominal_value: float, leverage: float) -> float:
        return nominal_value / max(leverage, 1.0)

    @staticmethod
    def calculate_realized_trade_pnl(
        side: str,
        size: float,
        entry_price: float,
        exit_price: float,
        entry_fee: float,
        exit_fee: float,
    ) -> tuple[float, float, float]:
        """
        Returns (gross_pnl, total_fees, net_pnl).
        """
        if side == "LONG":
            gross_pnl = (exit_price - entry_price) * size
        else:
            gross_pnl = (entry_price - exit_price) * size

        total_fees = entry_fee + exit_fee
        net_pnl = gross_pnl - total_fees
        return gross_pnl, total_fees, net_pnl

    @staticmethod
    def update_account_on_trade_close(
        account: AccountState,
        net_pnl: float,
        total_fees: float,
        total_slippage: float,
    ) -> None:
        """Atomically updates account balance and metrics upon trade closure."""
        account.balance += net_pnl
        account.equity = account.balance
        account.realized_pnl += net_pnl
        account.total_fees_paid += total_fees
        account.total_slippage_cost += total_slippage
        account.total_trades += 1
        if net_pnl > 0:
            account.winning_trades += 1
        elif net_pnl < 0:
            account.losing_trades += 1

        # Reconcile balance integrity
        account.reconcile()
