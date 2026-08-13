"""
Unit tests for AccountingEngine and AccountState using unittest.
"""

import unittest
import math
import sys
import os
sys.path.insert(0, os.path.abspath("src"))

from common.accounting import AccountState, AccountingEngine, Position


class TestAccounting(unittest.TestCase):

    def test_account_reconciliation_success(self):
        account = AccountState(initial_balance=10000.0, balance=10000.0, equity=10000.0)
        AccountingEngine.update_account_on_trade_close(account, net_pnl=150.0, total_fees=5.0, total_slippage=2.0)

        self.assertEqual(account.balance, 10150.0)
        self.assertEqual(account.realized_pnl, 150.0)
        self.assertEqual(account.total_fees_paid, 5.0)
        self.assertEqual(account.total_slippage_cost, 2.0)
        self.assertEqual(account.total_trades, 1)
        self.assertEqual(account.winning_trades, 1)
        self.assertTrue(account.reconcile())

    def test_account_reconciliation_failure(self):
        account = AccountState(initial_balance=10000.0, balance=10000.0, equity=10000.0)
        account.balance = 10500.0  # Manually corrupt balance without realized_pnl
        with self.assertRaises(ValueError):
            account.reconcile()

    def test_long_winning_trade_accounting(self):
        gross_pnl, total_fees, net_pnl = AccountingEngine.calculate_realized_trade_pnl(
            side="LONG", size=2.0, entry_price=3000.0, exit_price=3100.0, entry_fee=3.0, exit_fee=3.1
        )
        self.assertEqual(gross_pnl, 200.0)
        self.assertEqual(total_fees, 6.1)
        self.assertEqual(round(net_pnl, 1), 193.9)

    def test_short_winning_trade_accounting(self):
        gross_pnl, total_fees, net_pnl = AccountingEngine.calculate_realized_trade_pnl(
            side="SHORT", size=2.0, entry_price=3000.0, exit_price=2900.0, entry_fee=3.0, exit_fee=2.9
        )
        self.assertEqual(gross_pnl, 200.0)
        self.assertEqual(total_fees, 5.9)
        self.assertEqual(round(net_pnl, 1), 194.1)

    def test_short_losing_trade_accounting(self):
        gross_pnl, total_fees, net_pnl = AccountingEngine.calculate_realized_trade_pnl(
            side="SHORT", size=2.0, entry_price=3000.0, exit_price=3100.0, entry_fee=3.0, exit_fee=3.1
        )
        self.assertEqual(gross_pnl, -200.0)
        self.assertEqual(total_fees, 6.1)
        self.assertEqual(round(net_pnl, 1), -206.1)


if __name__ == "__main__":
    unittest.main()
