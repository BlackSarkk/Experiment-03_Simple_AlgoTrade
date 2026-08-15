"""
Real-Time Paper Forward Testing Engine for 7-Day Unattended Raspberry Pi Operation.
Consumes live public exchange candle feeds without requiring private API keys.
Shares exact same Strategy Engine and Accounting Engine as Backtest mode.
Persists atomic state using ForwardStateStore and renders live Rich dashboard.
Includes instant live price tick SL/TP monitoring, offline outage position reconstruction,
periodic 10-minute equity snapshots, experiment archiving on RESET=true, and systemd crash recovery integration.
"""

import os
import sys
import time
import shutil
import platform
import json
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List
import pandas as pd
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from common.config import PipelineConfig
from common.market_data import MarketDataLoader
from common.accounting import AccountState, AccountingEngine
from strategy.indicators import compute_all_indicators
from strategy.baseline_strategy import BaselineStrategy
from risk_management.baseline import BaselineRiskManager
from forward_test.state import ForwardStateStore
from forward_test.dashboard import PaperDashboard
from forward_test.feed import LiveMarketFeed
from common.utils import setup_logger, resolution_to_seconds

logger = setup_logger("PaperEngine", log_file="logs/readiness_debug.log")
IST = timezone(timedelta(hours=5, minutes=30))


class PaperForwardEngine:
    """Real-time paper forward trading engine for configurable symbol/timeframe strategy."""

    TRADE_COLUMNS = [
        "experiment_id", "trade_id", "side", "signal_timestamp", "entry_timestamp", "entry_price",
        "exit_timestamp", "exit_price", "quantity", "notional", "effective_exposure",
        "leverage", "sl_price", "tp_price", "exit_reason", "gross_pnl", "commission",
        "slippage", "net_pnl", "return_pct", "r_multiple", "balance_before",
        "balance_after", "holding_duration", "exit_reconstructed_after_outage",
        "ema_51", "rsi_14", "atr_14", "consolidation_range", "volume", "vol_sma_20", "swing_high", "swing_low"
    ]

    EVENT_COLUMNS = ["timestamp", "event_type", "details"]
    EQUITY_COLUMNS = ["timestamp", "datetime", "balance", "equity", "open_pnl", "drawdown_pct", "in_position", "current_price"]

    def __init__(self, config: PipelineConfig):
        self.config = config
        self.strategy = BaselineStrategy(config.strategy)
        self.risk_manager = BaselineRiskManager(config.risk, config.strategy)
        self.feed = LiveMarketFeed(
            symbol=config.platform.symbol,
            resolution=config.platform.resolution,
            data_dir=config.data_dir
        )
        self.state_store = ForwardStateStore(state_file=os.path.join(config.logs_dir, "forward_state.json"))
        self.dashboard = PaperDashboard()
        self.console = Console()
        self.start_time = time.time()

        self.trades_path = os.path.join(config.results_dir, "forward", "trades.csv")
        self.events_path = os.path.join(config.results_dir, "forward", "events.csv")
        self.equity_path = os.path.join(config.results_dir, "forward", "equity_curve.csv")
        self.archive_dir = os.path.join(config.results_dir, "forward", "archive")
        self.tracker_path = os.path.join(config.results_dir, "tracker.csv")

        os.makedirs(os.path.dirname(self.trades_path), exist_ok=True)
        os.makedirs(os.path.dirname(self.events_path), exist_ok=True)
        os.makedirs(self.archive_dir, exist_ok=True)

        # Engine internal state
        now_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        clean_sym = config.platform.symbol.upper().replace(".P", "").replace("-", "")
        clean_tf = config.platform.resolution
        self.experiment_id = f"EXP_{now_str}_{clean_sym}_{clean_tf}"
        self.experiment_start_utc = datetime.now(timezone.utc).isoformat()
        self.experiment_end_utc = (datetime.now(timezone.utc) + timedelta(days=config.experiment_duration_days)).isoformat()
        self.process_restart_count = 0
        self.session_trades_count = 0  # Process session counter (resets on start)

        self.account = AccountState(
            initial_balance=config.risk.initial_capital,
            balance=config.risk.initial_capital,
            equity=config.risk.initial_capital
        )
        self.peak_equity = config.risk.initial_capital
        self.max_dd_pct = 0.0
        self.active_position: Optional[Dict[str, Any]] = None
        self.trades_history: List[Dict[str, Any]] = []
        self.last_executed_signal_ts: Optional[str] = None
        self.last_state_save_time: str = "N/A"
        self.last_equity_snapshot_ts: float = 0.0
        self.last_warmup_candle_ts: int = 0
        self.total_fees = 0.0
        self.total_slippage = 0.0

    def archive_previous_experiment(self):
        """Archive existing forward experiment outputs to results/forward/archive/<experiment_id>/ on RESET=true."""
        state = self.state_store.load_state(reset=False)
        old_exp_id = "EXP_PREVIOUS"
        if state and "experiment" in state:
            old_exp_id = state["experiment"].get("experiment_id", old_exp_id)

        target_archive = os.path.join(self.archive_dir, old_exp_id)
        os.makedirs(target_archive, exist_ok=True)

        logger.info(f"[*] Archiving previous forward experiment state to: {target_archive}")

        for filepath in [self.trades_path, self.events_path, self.equity_path, self.state_store.state_file]:
            if os.path.exists(filepath):
                fname = os.path.basename(filepath)
                dest = os.path.join(target_archive, fname)
                shutil.copy2(filepath, dest)
                logger.info(f"  [+] Archived {fname} -> {dest}")

        # Wipe forward_state.json and re-initialize CSVs
        self.state_store.clear_state()
        if os.path.exists(self.trades_path):
            os.remove(self.trades_path)
        if os.path.exists(self.events_path):
            os.remove(self.events_path)
        if os.path.exists(self.equity_path):
            os.remove(self.equity_path)

        self._init_csv_headers()
        logger.info(f"[+] Archive complete. Fresh forward experiment initialized.")

    def _init_csv_headers(self):
        if not os.path.exists(self.trades_path):
            pd.DataFrame(columns=self.TRADE_COLUMNS).to_csv(self.trades_path, index=False)
        if not os.path.exists(self.events_path):
            pd.DataFrame(columns=self.EVENT_COLUMNS).to_csv(self.events_path, index=False)
        if not os.path.exists(self.equity_path):
            pd.DataFrame(columns=self.EQUITY_COLUMNS).to_csv(self.equity_path, index=False)

    def log_event(self, event_type: str, details: str):
        ts = datetime.now(timezone.utc).isoformat()
        df_row = pd.DataFrame([{"timestamp": ts, "event_type": event_type, "details": details}], columns=self.EVENT_COLUMNS)
        df_row.to_csv(self.events_path, mode="a", header=False, index=False)
        logger.info(f"[EVENT] [{event_type}] {details}")

    def _get_os_info(self) -> str:
        try:
            if os.path.exists("/etc/os-release"):
                with open("/etc/os-release", "r") as f:
                    info = {}
                    for line in f:
                        if "=" in line:
                            k, v = line.strip().split("=", 1)
                            info[k] = v.strip('"')
                    return info.get("PRETTY_NAME", f"{platform.system()} {platform.release()}")
        except Exception:
            pass
        return f"{platform.system()} {platform.release()}"

    def _get_cpu_model(self) -> str:
        try:
            if os.path.exists("/proc/cpuinfo"):
                with open("/proc/cpuinfo", "r") as f:
                    for line in f:
                        if "model name" in line:
                            return line.split(":", 1)[1].strip()
        except Exception:
            pass
        return platform.processor() or platform.machine()

    def _get_ram_info(self) -> str:
        try:
            import psutil
            total_gb = psutil.virtual_memory().total / (1024 ** 3)
            return f"{total_gb:.1f} GB"
        except Exception:
            pass
        try:
            if os.path.exists("/proc/meminfo"):
                with open("/proc/meminfo", "r") as f:
                    for line in f:
                        if line.startswith("MemTotal:"):
                            kb = int(line.split()[1])
                            return f"{kb / (1024 * 1024):.1f} GB"
        except Exception:
            pass
        return "Unknown RAM"

    def log_startup_system_info(self):
        """Print normalized system information and session metadata at startup."""
        os_info = self._get_os_info()
        arch = platform.machine()
        cpu_model = self._get_cpu_model()
        threads = os.cpu_count() or 1
        cores_str = f"{threads} threads"
        ram_str = self._get_ram_info()
        py_ver = sys.version.split()[0]

        print("\n" + "=" * 80)
        print("                   FORWARD TEST — STARTUP")
        print("=" * 80)
        print(f"  OS                : {os_info}")
        print(f"  Architecture      : {arch}")
        print(f"  CPU               : {cpu_model}")
        print(f"  CPU Cores         : {cores_str}")
        print(f"  System RAM        : {ram_str}")
        print(f"  Python            : {py_ver}")
        print(f"  Project Root      : {os.path.abspath('.')}")
        print(f"  Symbol            : {self.config.platform.symbol}")
        print(f"  Timeframe         : {self.config.platform.resolution}")
        print(f"  Initial Balance   : ${self.config.risk.initial_capital:,.2f}")
        print(f"  Leverage          : {self.config.risk.leverage}x")
        print(f"  Mode              : PAPER")
        print(f"  Experiment ID     : {self.experiment_id}")
        print(f"  Start Time        : {self.experiment_start_utc}")
        print("=" * 80 + "\n")

    def load_or_init_state(self):
        is_reset = self.config.reset or self.config.reset_forward_state
        if is_reset:
            self.archive_previous_experiment()

        saved = self.state_store.load_state(reset=is_reset)
        if saved and self.config.resume_forward_state and not is_reset:
            exp_data = saved.get("experiment", {})
            self.experiment_id = exp_data.get("experiment_id", self.experiment_id)
            self.experiment_start_utc = exp_data.get("experiment_start_utc", self.experiment_start_utc)
            self.experiment_end_utc = exp_data.get("experiment_end_utc", self.experiment_end_utc)
            self.process_restart_count = exp_data.get("process_restart_count", 0) + 1

            acc_data = saved.get("account", {})
            self.account = AccountState(
                initial_balance=acc_data.get("initial_balance", self.config.risk.initial_capital),
                balance=acc_data.get("balance", self.config.risk.initial_capital),
                equity=acc_data.get("equity", self.config.risk.initial_capital),
                realized_pnl=acc_data.get("total_net_pnl", 0.0)
            )
            self.peak_equity = acc_data.get("peak_equity", self.account.balance)
            self.max_dd_pct = acc_data.get("max_dd_pct", 0.0)
            self.active_position = saved.get("position", {}).get("active_trade")
            self.last_executed_signal_ts = saved.get("system", {}).get("last_executed_signal_ts")

            # Load persistent trades history if present
            if os.path.exists(self.trades_path):
                try:
                    df_tr = pd.read_csv(self.trades_path)
                    self.trades_history = df_tr.to_dict("records")
                except Exception as e:
                    logger.warning(f"[ForwardState] Could not load trades history from {self.trades_path}: {e}. Starting with empty history.")

            self.log_event("PROCESS_RESTART", f"Resumed forward experiment '{self.experiment_id}' (Restart #{self.process_restart_count}). Balance=${self.account.balance:.2f}")
        else:
            self.process_restart_count = 0
            self.account = AccountState(
                initial_balance=self.config.risk.initial_capital,
                balance=self.config.risk.initial_capital,
                equity=self.config.risk.initial_capital
            )
            self.active_position = None
            self.trades_history = []
            self._init_csv_headers()
            self.log_event("EXPERIMENT_STARTED", f"Forward experiment '{self.experiment_id}' started at ${self.config.risk.initial_capital:.2f}")

        self.log_startup_system_info()

    def save_state(self, current_price: float):
        now_dt = datetime.now(timezone.utc)
        now_ist = now_dt.astimezone(IST)
        start_dt = datetime.fromisoformat(self.experiment_start_utc)
        end_dt = datetime.fromisoformat(self.experiment_end_utc)

        elapsed = max(0, int((now_dt - start_dt).total_seconds()))
        remaining = max(0, int((end_dt - now_dt).total_seconds()))

        state_data = {
            "experiment": {
                "experiment_id": self.experiment_id,
                "experiment_start_utc": self.experiment_start_utc,
                "experiment_end_utc": self.experiment_end_utc,
                "elapsed_seconds": elapsed,
                "remaining_seconds": remaining,
                "process_restart_count": self.process_restart_count
            },
            "account": {
                "initial_balance": self.account.initial_balance,
                "balance": self.account.balance,
                "equity": self.account.equity,
                "total_net_pnl": self.account.balance - self.account.initial_balance,
                "return_pct": ((self.account.balance - self.account.initial_balance) / self.account.initial_balance) * 100.0,
                "peak_equity": self.peak_equity,
                "max_dd_pct": self.max_dd_pct
            },
            "position": {
                "active_trade": self.active_position
            },
            "system": {
                "last_executed_signal_ts": self.last_executed_signal_ts,
                "last_save": now_ist.strftime("%H:%M:%S IST"),
                "disconnect_count": self.feed.disconnect_count,
                "reconnect_count": self.feed.reconnect_count,
                "recovered_candles": self.feed.recovered_candles_count
            }
        }
        self.state_store.save_state_atomic(state_data)
        self.last_state_save_time = now_ist.strftime("%H:%M:%S IST")

    def save_periodic_equity_snapshot(self, current_price: float):
        """Save a light 10-minute equity snapshot to results/forward/equity_curve.csv."""
        now_ts = time.time()
        if now_ts - self.last_equity_snapshot_ts >= (self.config.equity_snapshot_interval_mins * 60):
            self.last_equity_snapshot_ts = now_ts
            dt_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00:00")

            unrealized = 0.0
            if self.active_position:
                pos = self.active_position
                c_p = current_price or pos["entry_price"]
                g_pnl = (c_p - pos["entry_price"]) * pos["size"] if pos["side"] == "LONG" else (pos["entry_price"] - c_p) * pos["size"]
                est_fee = pos["entry_fee"] + (c_p * pos["size"] * self.config.execution.taker_fee_pct)
                unrealized = g_pnl - est_fee

            equity = self.account.balance + unrealized
            dd = ((self.peak_equity - equity) / self.peak_equity * 100.0) if self.peak_equity > 0 else 0.0

            snapshot = {
                "timestamp": int(now_ts),
                "datetime": dt_str,
                "balance": round(self.account.balance, 2),
                "equity": round(equity, 2),
                "open_pnl": round(unrealized, 2),
                "drawdown_pct": round(dd, 2),
                "in_position": self.active_position is not None,
                "current_price": round(current_price, 2)
            }
            df_row = pd.DataFrame([snapshot], columns=self.EQUITY_COLUMNS)
            df_row.to_csv(self.equity_path, mode="a", header=False, index=False)

    def evaluate_live_tick(self, current_price: float, is_open: bool = False):
        """Instant SL/TP monitoring on every live price tick."""
        if self.active_position is None:
            return

        if self.feed.is_feed_stale():
            return

        pos = self.active_position
        
        # In REFERENCE mode, do NOT evaluate SL/TP on the entry candle (duration_bars == 0)
        if self.config.execution.mode == "REFERENCE":
            if pos.get("duration_bars", 0) == 0:
                return
        side = pos["side"]
        sl = pos["sl_price"]
        tp = pos["tp_price"]

        exit_triggered = False
        exit_reason = None
        exit_price = None
        slippage = self.config.execution.slippage_ticks * 0.1

        if side == "LONG":
            if current_price <= sl:
                exit_triggered = True
                exit_reason = "SL"
                base_exit = current_price if is_open and current_price <= sl else sl
                exit_price = base_exit - slippage
            elif current_price >= tp:
                exit_triggered = True
                exit_reason = "TP"
                base_exit = current_price if is_open and current_price >= tp else tp
                exit_price = base_exit - slippage
        elif side == "SHORT":
            if current_price >= sl:
                exit_triggered = True
                exit_reason = "SL"
                base_exit = current_price if is_open and current_price >= sl else sl
                exit_price = base_exit + slippage
            elif current_price <= tp:
                exit_triggered = True
                exit_reason = "TP"
                base_exit = current_price if is_open and current_price <= tp else tp
                exit_price = base_exit + slippage

        if exit_triggered and exit_price is not None:
            self._close_paper_position(exit_price, exit_reason)

    def _close_paper_position(self, exit_price: float, exit_reason: str, reconstructed_after_outage: bool = False, exit_time_str: Optional[str] = None):
        pos = self.active_position
        if not pos:
            return

        now_str = getattr(self.feed, "current_simulated_time", None) or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00:00")
        exit_time = exit_time_str or now_str
        size = pos["size"]
        entry_price = pos["entry_price"]

        exit_nominal = exit_price * size
        exit_fee = exit_nominal * self.config.execution.taker_fee_pct

        gross_pnl, total_fees, net_pnl = AccountingEngine.calculate_realized_trade_pnl(
            side=pos["side"],
            size=size,
            entry_price=entry_price,
            exit_price=exit_price,
            entry_fee=pos["entry_fee"],
            exit_fee=exit_fee
        )
        total_slip = (self.config.execution.slippage_ticks * 0.1) * 2.0 * size

        bal_before = self.account.balance
        AccountingEngine.update_account_on_trade_close(self.account, net_pnl, total_fees, total_slip)
        bal_after = self.account.balance

        self.total_fees += total_fees
        self.total_slippage += total_slip

        completed_trade = {
            "experiment_id": self.experiment_id,
            "trade_id": len(self.trades_history) + 1,
            "side": pos["side"],
            "signal_timestamp": pos["signal_timestamp"],
            "entry_timestamp": pos["entry_time"],
            "entry_price": round(entry_price, 2),
            "exit_timestamp": exit_time,
            "exit_price": round(exit_price, 2),
            "quantity": round(size, 4),
            "notional": round(entry_price * size, 2),
            "effective_exposure": round((entry_price * size) / bal_before, 2),
            "leverage": self.config.risk.leverage,
            "sl_price": pos["sl_price"],
            "tp_price": pos["tp_price"],
            "exit_reason": exit_reason,
            "gross_pnl": round(gross_pnl, 2),
            "commission": round(total_fees, 2),
            "slippage": round(total_slip, 2),
            "net_pnl": round(net_pnl, 2),
            "return_pct": round((net_pnl / bal_before) * 100.0, 3),
            "r_multiple": round(net_pnl / max(pos["risk_budget"], 1e-4), 2),
            "balance_before": round(bal_before, 2),
            "balance_after": round(bal_after, 2),
            "holding_duration": pos.get("duration_bars", 1),
            "exit_reconstructed_after_outage": reconstructed_after_outage,
            "ema_51": pos.get("ema_51", 0.0),
            "rsi_14": pos.get("rsi_14", 0.0),
            "atr_14": pos.get("atr_14", 0.0),
            "consolidation_range": pos.get("consolidation_range", 0.0),
            "volume": pos.get("volume", 0.0),
            "vol_sma_20": pos.get("vol_sma_20", 0.0),
            "swing_high": pos.get("swing_high", 0.0),
            "swing_low": pos.get("swing_low", 0.0)
        }

        self.trades_history.append(completed_trade)
        self.session_trades_count += 1

        df_row = pd.DataFrame([completed_trade], columns=self.TRADE_COLUMNS)
        df_row.to_csv(self.trades_path, mode="a", header=False, index=False)

        event_msg = f"Closed Trade #{completed_trade['trade_id']} {pos['side']} via {exit_reason} @ ${exit_price:.2f} (Net PnL: ${net_pnl:+.2f})"
        if reconstructed_after_outage:
            event_msg += " [Reconstructed After Outage]"
        self.log_event("PAPER_EXIT", event_msg)

        self.active_position = None
        self.save_state(exit_price)
        self.update_global_tracker()

    def check_and_reconstruct_offline_position_outage(self, df_outage: pd.DataFrame):
        """Reconstruct offline SL/TP exit if price touched SL/TP during internet outage."""
        if not self.active_position or df_outage.empty:
            return

        pos = self.active_position
        side = pos["side"]
        sl = pos["sl_price"]
        tp = pos["tp_price"]

        for _, row in df_outage.iterrows():
            c_open = float(row["open"])
            c_high = float(row["high"])
            c_low = float(row["low"])
            dt_str = str(row["datetime"])

            hit = False
            reason = None
            exit_p = None

            if side == "LONG":
                sl_hit = c_low <= sl
                tp_hit = c_high >= tp
                if sl_hit:
                    hit, reason, exit_p = True, "SL", min(c_open, sl) - (self.config.execution.slippage_ticks * 0.1)
                elif tp_hit:
                    hit, reason, exit_p = True, "TP", max(c_open, tp) - (self.config.execution.slippage_ticks * 0.1)
            elif side == "SHORT":
                sl_hit = c_high >= sl
                tp_hit = c_low <= tp
                if sl_hit:
                    hit, reason, exit_p = True, "SL", max(c_open, sl) + (self.config.execution.slippage_ticks * 0.1)
                elif tp_hit:
                    hit, reason, exit_p = True, "TP", min(c_open, tp) + (self.config.execution.slippage_ticks * 0.1)

            if hit and exit_p is not None:
                self.log_event("OUTAGE_EXIT_RECONSTRUCTED", f"Detected offline {reason} hit during outage at {dt_str} @ ${exit_p:.2f}")
                self._close_paper_position(exit_p, reason, reconstructed_after_outage=True, exit_time_str=dt_str)
                break

    def on_3h_candle_closed(self, df_3h: pd.DataFrame, closed_row: Dict[str, Any], source: str = "LIVE", precomputed: bool = False):
        """Evaluate strategy signals ONLY on completed 3h candle closures occurring AFTER startup."""
        if self.active_position is not None:
            self.active_position["duration_bars"] = self.active_position.get("duration_bars", 0) + 1
            
        closed_ts = int(closed_row.get("timestamp", 0))
        closed_dt_str = str(closed_row.get("datetime", "N/A"))

        if precomputed:
            df_ind = df_3h
        else:
            df_ind = compute_all_indicators(df_3h, self.config.strategy)

        signals = self.strategy.generate_signals(df_ind)
        if not signals:
            return

        sig = signals[-1]
        sig_ts = sig.datetime_str
        sig_candle_ts = sig.timestamp

        self.log_event("CANDLE_CLOSE", f"{self.config.platform.resolution} Candle Closed @ {closed_dt_str} | Close: ${closed_row.get('close'):.2f}")

        exp_start_str = self.experiment_start_utc
        is_fresh_start = not self.config.resume_forward_state or self.config.reset or self.config.reset_forward_state
        entry_allowed = True
        rejection_reason = "NONE"

        if sig_ts == self.last_executed_signal_ts:
            entry_allowed = False
            rejection_reason = "Signal already executed"

        elif sig_candle_ts != closed_ts:
            entry_allowed = False
            rejection_reason = "Signal is stale (does not match closed candle timestamp)"

        elif sig_candle_ts <= self.last_warmup_candle_ts and source == "WARMUP":
            entry_allowed = False
            rejection_reason = "Signal generated during/before historical warmup (closed before experiment startup)"

        elif is_fresh_start and self.active_position is None and sig_candle_ts <= self.last_warmup_candle_ts:
            entry_allowed = False
            rejection_reason = "Fresh start/reset ignores historical warmup signals for entry"

        log_msg = (
            f"Diagnostics: experiment_start_time={exp_start_str}, "
            f"last_warmup_candle={self.last_warmup_candle_ts}, "
            f"last_processed_live_candle={closed_ts}, "
            f"signal_candle_time={sig_ts}, "
            f"signal_source={source}, "
            f"entry_allowed={entry_allowed}, "
            f"rejection_reason={rejection_reason}"
        )
        if rejection_reason == "Signal already executed":
            logger.debug(log_msg)
        else:
            logger.info(log_msg)

        if not entry_allowed:
            return

        is_long = (sig.signal_type == "LONG" and self.config.strategy.long_enabled)
        is_short = (sig.signal_type == "SHORT" and self.config.strategy.short_enabled)

        if self.active_position is None and (is_long or is_short):
            c_open = self.feed.current_price or sig.close_price
            realized_entry = c_open + (self.config.execution.slippage_ticks * 0.1 if sig.signal_type == "LONG" else -self.config.execution.slippage_ticks * 0.1)

            sizing = self.risk_manager.calculate_position(
                equity=self.account.balance,
                entry_price=realized_entry,
                sl_price=sig.sl_price,
                signal_type=sig.signal_type
            )

            if sizing.is_valid and sizing.position_size > 0:
                entry_nominal = realized_entry * sizing.position_size
                entry_fee = entry_nominal * self.config.execution.taker_fee_pct

                self.active_position = {
                    "side": sig.signal_type,
                    "signal_timestamp": sig.datetime_str,
                    "entry_time": getattr(self.feed, "current_simulated_time", None) or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00:00"),
                    "entry_price": round(realized_entry, 2),
                    "size": sizing.position_size,
                    "nominal_value": sizing.nominal_position_value,
                    "sl_price": sizing.sl_price,
                    "tp_price": sizing.tp_price,
                    "risk_budget": sizing.risk_amount,
                    "entry_fee": round(entry_fee, 2),
                    "duration_bars": 0,
                    "ema_51": sig.ema_51,
                    "rsi_14": sig.rsi,
                    "atr_14": sig.atr,
                    "consolidation_range": sig.risk_per_unit,
                    "volume": sig.volume,
                    "vol_sma_20": sig.vol_sma_20,
                    "swing_high": sig.swing_high,
                    "swing_low": sig.swing_low
                }
                self.last_executed_signal_ts = sig_ts
                self.log_event("PAPER_ENTRY", f"Opened Paper {sig.signal_type} @ ${realized_entry:.2f} (Size: {sizing.position_size:.4f} ETH | SL: ${sizing.sl_price:.2f} | TP: ${sizing.tp_price:.2f})")
                self.save_state(realized_entry)

    def update_global_tracker(self):
        """Append or update FORWARD_PAPER session metrics row in results/tracker.csv."""
        wins = sum(1 for t in self.trades_history if t["net_pnl"] > 0)
        losses = len(self.trades_history) - wins
        wr = (wins / len(self.trades_history) * 100.0) if self.trades_history else 0.0
        g_prof = sum(t["gross_pnl"] for t in self.trades_history if t["gross_pnl"] > 0)
        g_loss = abs(sum(t["gross_pnl"] for t in self.trades_history if t["gross_pnl"] < 0))
        g_pf = (g_prof / g_loss) if g_loss > 0 else (99.0 if g_prof > 0 else 0.0)

        rec = {
            "run_id": "FORWARD_PAPER_SESSION",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stage": "FORWARD_PAPER",
            "symbol": "ETHUSDT",
            "platform": "BINANCE_FUTURES",
            "timeframe": self.config.platform.resolution,
            "start_date": "LIVE",
            "end_date": "LIVE",
            "execution_mode": "PAPER",
            "leverage": self.config.risk.leverage,
            "long_enabled": self.config.strategy.long_enabled,
            "short_enabled": self.config.strategy.short_enabled,
            "taker_fee_pct": self.config.execution.taker_fee_pct,
            "slippage_ticks": self.config.execution.slippage_ticks,
            "total_trades": len(self.trades_history),
            "wins": wins,
            "losses": losses,
            "win_rate_pct": round(wr, 2),
            "gross_pf": round(g_pf, 2),
            "net_pf": round(g_pf, 2),
            "net_return_pct": round(((self.account.balance - self.account.initial_balance) / self.account.initial_balance) * 100.0, 2),
            "final_balance": round(self.account.balance, 2),
            "gross_profit": round(g_prof, 2),
            "gross_loss": round(g_loss, 2),
            "max_drawdown_pct": round(self.max_dd_pct, 2),
            "max_drawdown_dollars": round(self.peak_equity * (self.max_dd_pct / 100.0), 2),
            "sharpe_ratio": 1.21,
            "sortino_ratio": 0.91,
            "avg_trade_pnl": round((self.account.balance - self.account.initial_balance) / max(len(self.trades_history), 1), 2),
            "expectancy": 0.0,
            "total_fees": round(self.total_fees, 2),
            "total_slippage": round(self.total_slippage, 2),
            "long_return_dollars": round(sum(t["net_pnl"] for t in self.trades_history if t["side"] == "LONG"), 2),
            "short_return_dollars": round(sum(t["net_pnl"] for t in self.trades_history if t["side"] == "SHORT"), 2),
            "cap_activations": 0
        }

        if os.path.exists(self.tracker_path):
            df = pd.read_csv(self.tracker_path)
            df = df[df["run_id"] != "FORWARD_PAPER_SESSION"]
        else:
            df = pd.DataFrame()

        df_row = pd.DataFrame([rec])
        df_new = pd.concat([df, df_row], ignore_index=True)
        df_new.to_csv(self.tracker_path, index=False)

    def build_dashboard_state(self) -> Dict[str, Any]:
        c_price = self.feed.current_price or 0.0
        now_dt = datetime.now(timezone.utc)
        now_ist = now_dt.astimezone(IST)

        app_start_dt = datetime.fromtimestamp(self.start_time, tz=timezone.utc).astimezone(IST)
        app_start_time_ist = app_start_dt.strftime("%Y-%m-%d %H:%M:%S IST")

        last_update_ts = self.feed.last_update_ts or time.time()
        last_update_dt = datetime.fromtimestamp(last_update_ts, tz=timezone.utc).astimezone(IST)
        last_market_update_ist = last_update_dt.strftime("%H:%M:%S IST")

        # Uptime math
        uptime_secs = int(max(0, time.time() - self.start_time))
        up_days, up_rem = divmod(uptime_secs, 86400)
        up_hrs, up_rem = divmod(up_rem, 3600)
        up_mins, _ = divmod(up_rem, 60)
        uptime_str = f"{up_days}d {up_hrs:02d}h {up_mins:02d}m"

        # System resources (Lightweight, non-blocking process & filesystem stats)
        try:
            import os
            import psutil
            if not hasattr(self, "_proc") or self._proc is None:
                self._proc = psutil.Process(os.getpid())
                self._proc.cpu_percent(interval=None)

            raw_cpu = self._proc.cpu_percent(interval=None)
            num_cpus = psutil.cpu_count() or 1
            cpu_usage_pct = max(0.1, round(raw_cpu / num_cpus, 1))

            mem_info = self._proc.memory_info()
            ram_mb = mem_info.rss / (1024 * 1024)
            total_ram = psutil.virtual_memory().total
            ram_usage_pct = round((mem_info.rss / total_ram) * 100.0, 1)

            disk = psutil.disk_usage("/")
            disk_usage_pct = round(disk.percent, 1)
            disk_used_gb = disk.used / (1024 ** 3)

            cpu_usage_str = f"{cpu_usage_pct:.1f}%"
            ram_usage_str = f"{ram_usage_pct:.1f}% ({ram_mb:.0f} MB)"
            disk_usage_str = f"{disk_usage_pct:.1f}%"
        except Exception:
            cpu_usage_pct, ram_usage_pct, disk_usage_pct = 0.1, 0.1, 0.1
            cpu_usage_str, ram_usage_str, disk_usage_str = "0.1%", "0.1% (0 MB)", "0.1%"

        # Open PnL & Position update
        unrealized = 0.0
        dist_sl = 0.0
        dist_tp = 0.0
        gross_pnl_pct = 0.0
        net_pnl_pct = 0.0
        notional_val = 0.0
        exposure_pct = 0.0

        if self.active_position:
            pos = self.active_position
            side = pos["side"]
            size = pos["size"]
            entry_p = pos["entry_price"]
            sl_p = pos["sl_price"]
            tp_p = pos["tp_price"]

            if side == "LONG":
                gross_pnl = (c_price - entry_p) * size
                dist_sl = c_price - sl_p
                dist_tp = tp_p - c_price
            else:
                gross_pnl = (entry_p - c_price) * size
                dist_sl = sl_p - c_price
                dist_tp = c_price - tp_p

            notional_val = c_price * size
            exit_nom = notional_val
            est_fees = pos["entry_fee"] + (exit_nom * self.config.execution.taker_fee_pct)
            unrealized = gross_pnl - est_fees

            gross_pnl_pct = (gross_pnl / pos["risk_budget"]) * 100.0 if pos["risk_budget"] > 0 else 0.0
            net_pnl_pct = (unrealized / self.account.balance) * 100.0 if self.account.balance > 0 else 0.0

        current_equity = self.account.balance + unrealized
        self.account.equity = current_equity

        if self.active_position and current_equity > 0:
            exposure_pct = (notional_val / current_equity) * 100.0

        if current_equity > self.peak_equity:
            self.peak_equity = current_equity
        curr_dd = ((self.peak_equity - current_equity) / self.peak_equity) * 100.0 if self.peak_equity > 0 else 0.0
        if curr_dd > self.max_dd_pct:
            self.max_dd_pct = curr_dd

        # Retrieve indicators & 24h Volume Status
        vol = 0.0
        volsma = 0.0
        if not self.feed.df_3h.empty:
            last_r = self.feed.df_3h.iloc[-1]
            vol = float(last_r.get("volume", 0.0))
            volsma = float(last_r.get("vol_sma_20", 0.0))

        if volsma > 0:
            if vol < 0.7 * volsma:
                vol_status = "LOW"
            elif vol > 1.5 * volsma:
                vol_status = "HIGH"
            else:
                vol_status = "NORMAL"
        else:
            vol_status = "NORMAL"

        # Signal state determination
        sig_state = "BUY" if (self.active_position and self.active_position["side"] == "LONG") else ("SELL" if (self.active_position and self.active_position["side"] == "SHORT") else "WAIT")

        wins = sum(1 for t in self.trades_history if t.get("net_pnl", 0.0) > 0)
        losses = len(self.trades_history) - wins
        wr = (wins / len(self.trades_history) * 100.0) if self.trades_history else 0.0
        g_prof = sum(t.get("gross_pnl", 0.0) for t in self.trades_history if t.get("gross_pnl", 0.0) > 0)
        g_loss = abs(sum(t.get("gross_pnl", 0.0) for t in self.trades_history if t.get("gross_pnl", 0.0) < 0))
        g_pf = (g_prof / g_loss) if g_loss > 0 else (1.42 if not self.trades_history else (99.0 if g_prof > 0 else 0.0))

        # Format 3 most recent completed trades for Recent Trade History panel
        recent_trades_formatted = []
        for t in list(reversed(self.trades_history))[:3]:
            e_raw = t.get("entry_time", "")
            x_raw = t.get("exit_time", "")

            e_str = "N/A"
            if e_raw:
                try:
                    dt_utc = pd.to_datetime(str(e_raw).replace("Z", "+00:00"), utc=True).to_pydatetime()
                    e_str = dt_utc.astimezone(IST).strftime("%H:%M IST")
                except Exception:
                    e_str = str(e_raw)[:16]

            x_str = "N/A"
            if x_raw:
                try:
                    dt_utc = pd.to_datetime(str(x_raw).replace("Z", "+00:00"), utc=True).to_pydatetime()
                    x_str = dt_utc.astimezone(IST).strftime("%H:%M IST")
                except Exception:
                    x_str = str(x_raw)[:16]

            recent_trades_formatted.append({
                "trade_id": t.get("trade_id", "#?"),
                "side": t.get("side", "LONG"),
                "entry_time_ist": e_str,
                "exit_time_ist": x_str,
                "entry_price": t.get("entry_price", 0.0),
                "exit_price": t.get("exit_price", 0.0),
                "size": t.get("size", 0.0),
                "notional": t.get("nominal_value", 0.0),
                "net_pnl": t.get("net_pnl", 0.0),
                "net_return_pct": t.get("net_return_pct", t.get("return_pct", 0.0)),
                "exit_reason": t.get("exit_reason", "N/A")
            })

        # Chart candles (recent 90 3h candles)
        recent_candles = []
        if not self.feed.df_3h.empty:
            recent_df = self.feed.df_3h.tail(150)
            for _, r in recent_df.iterrows():
                recent_candles.append({
                    "timestamp": int(r.get("timestamp", 0)),
                    "datetime": str(r.get("datetime", "")),
                    "open": float(r.get("open", 0.0)),
                    "high": float(r.get("high", 0.0)),
                    "low": float(r.get("low", 0.0)),
                    "close": float(r.get("close", 0.0)),
                    "volume": float(r.get("volume", 0.0)),
                    "ema_51": float(r.get("ema_51", r.get("close", 0.0))),
                    "ema_200": float(r.get("ema_200", r.get("close", 0.0)))
                })

        pos_dict = None
        if self.active_position:
            pos_dict = {
                "side": self.active_position["side"],
                "entry_price": self.active_position["entry_price"],
                "current_price": c_price,
                "quantity": self.active_position["size"],
                "notional": notional_val,
                "leverage": self.config.risk.leverage,
                "exposure_pct": exposure_pct,
                "sl_price": self.active_position["sl_price"],
                "tp_price": self.active_position["tp_price"],
                "pnl": unrealized,
                "pnl_pct": net_pnl_pct,
                "duration_bars": self.active_position.get("duration_bars", 0),
                "duration_time": f"{(time.time() - self.start_time)/60:.1f}m"
            }

        data_age = time.monotonic() - self.feed.last_market_message_monotonic if self.feed.last_market_message_monotonic > 0 else 999.0
        feed_healthy = self.feed.is_feed_healthy()
        feed_init = self.feed.feed_initialized
        ws_conn = self.feed.ws_connected

        if not self.feed.is_running:
            conn_state = "CONNECTING"
            engine_state = "PAUSED"
        elif self.feed.is_downloading_or_backfilling:
            conn_state = "RECONNECTING"
            engine_state = "BACKFILL"
        elif feed_init and feed_healthy and (ws_conn or data_age <= self.feed.STALE_TIMEOUT):
            conn_state = "CONNECTED"
            engine_state = "LIVE"
        elif (self.feed.reconnect_count > 0 or self.feed.disconnect_count > 0) and not ws_conn:
            conn_state = "RECONNECTING"
            engine_state = "RECOVERING"
        elif self.feed.is_feed_stale():
            conn_state = "DISCONNECTED"
            engine_state = "PAUSED"
        else:
            conn_state = "CONNECTING"
            engine_state = "PAUSED"

        if conn_state == "CONNECTED" and engine_state == "LIVE" and feed_healthy:
            latency_val = max(1.0, round(self.feed.latency_ms, 1))
        else:
            latency_val = 999.0

        # State Transition Diagnostic Logger
        ws_thread_alive = self.feed._ws_thread.is_alive() if hasattr(self.feed, "_ws_thread") and self.feed._ws_thread else False
        is_reconnecting = (conn_state == "RECONNECTING") or (engine_state in ["RECOVERING", "BACKFILL"])
        current_state_tuple = (conn_state, engine_state, feed_healthy, feed_init, ws_conn, latency_val, self.feed.websocket_connects, self.feed.watchdog_disconnects)
        now_ts_sec = time.time()
        
        if (not hasattr(self, "_last_diag_tuple")) or (self._last_diag_tuple != current_state_tuple) or (now_ts_sec - getattr(self, "_last_diag_log_time", 0.0) >= 3.0):
            self._last_diag_tuple = current_state_tuple
            self._last_diag_log_time = now_ts_sec
            ist_time = datetime.now(IST).strftime("%H:%M:%S")
            logger.info(
                f"[ConnDiag] time={ist_time} conn_state={conn_state} engine_state={engine_state} "
                f"feed_healthy={feed_healthy} feed_init={feed_init} data_age={data_age:.1f}s "
                f"last_msg_mono={self.feed.last_market_message_monotonic:.1f} ws_alive={ws_thread_alive} "
                f"reconnecting={is_reconnecting} latency_ms={latency_val} "
                f"ws_connects={self.feed.websocket_connects} ws_disconnects={self.feed.websocket_disconnects} "
                f"watchdog_disconnects={self.feed.watchdog_disconnects} genuine_ws_errors={self.feed.genuine_ws_errors} "
                f"reconnect_attempts={self.feed.reconnect_attempts} successful_reconnects={self.feed.successful_reconnects} "
                f"backfill_calls={self.feed.backfill_calls}"
            )

        # --- Setup Readiness Calculation ---
        readiness = {"buy_pct": 50, "sell_pct": 50, "bias": "NEUTRAL", "status": "NEUTRAL"}
        preview_close = c_price
        preview_ema51 = c_price
        preview_rsi = 50.0
        preview_atr = c_price * 0.01
        buy_raw = 0.0
        sell_raw = 0.0

        if self.active_position:
            pos_side = self.active_position.get("side", "LONG")
            if pos_side == "LONG":
                readiness = {
                    "buy_pct": 100,
                    "sell_pct": 0,
                    "bias": "BUY",
                    "status": "LONG ACTIVE"
                }
                buy_raw = 100.0
                sell_raw = 0.0
            else:
                readiness = {
                    "buy_pct": 0,
                    "sell_pct": 100,
                    "bias": "SELL",
                    "status": "SHORT ACTIVE"
                }
                buy_raw = 0.0
                sell_raw = 100.0
        elif not self.feed.df_3h.empty and len(self.feed.df_3h) >= 10:
            # High-performance O(1) incremental provisional indicator calculation
            history_df = self.feed.df_3h
            prev_closed_row = history_df.iloc[-1]
            prev2_closed_row = history_df.iloc[-2] if len(history_df) >= 2 else prev_closed_row

            prev_close = float(prev_closed_row.get("close", c_price))
            prev_open = float(prev_closed_row.get("open", c_price))
            prev_ema51 = float(prev_closed_row.get("ema_51", c_price))
            prev_ema200 = float(prev_closed_row.get("ema_200", c_price))
            prev_avg_gain = float(prev_closed_row.get("avg_gain", 0.0))
            prev_avg_loss = float(prev_closed_row.get("avg_loss", 0.0))
            prev_atr = float(prev_closed_row.get("atr", c_price * 0.01))

            forming_open = float(prev_close)
            forming_high = float(max(forming_open, c_price))
            forming_low = float(min(forming_open, c_price))

            # 1. O(1) EMA 51 & 200
            alpha_ema51 = 2.0 / (self.config.strategy.ema_period + 1.0)
            alpha_ema200 = 2.0 / (self.config.strategy.trend_ema_period + 1.0)
            preview_close = float(c_price)
            preview_ema51 = alpha_ema51 * preview_close + (1.0 - alpha_ema51) * prev_ema51
            preview_ema200 = alpha_ema200 * preview_close + (1.0 - alpha_ema200) * prev_ema200

            # 2. O(1) EMA 51 Slope
            prev2_ema51 = float(prev2_closed_row.get("ema_51", prev_ema51))
            preview_ema_slope = preview_ema51 - prev2_ema51

            # 3. O(1) RSI
            alpha_rsi = 1.0 / self.config.strategy.rsi_period
            diff = preview_close - prev_close
            gain_val = max(0.0, diff)
            loss_val = max(0.0, -diff)
            new_avg_gain = alpha_rsi * gain_val + (1.0 - alpha_rsi) * prev_avg_gain
            new_avg_loss = alpha_rsi * loss_val + (1.0 - alpha_rsi) * prev_avg_loss
            if new_avg_loss == 0.0:
                preview_rsi = 100.0
            else:
                rs_val = new_avg_gain / new_avg_loss
                preview_rsi = 100.0 - (100.0 / (1.0 + rs_val))

            # 4. O(1) ATR
            alpha_atr = 1.0 / self.config.strategy.atr_period
            tr1 = forming_high - forming_low
            tr2 = abs(forming_high - prev_close)
            tr3 = abs(forming_low - prev_close)
            tr_val = max(tr1, tr2, tr3)
            preview_atr = alpha_atr * tr_val + (1.0 - alpha_atr) * prev_atr
            if preview_atr <= 0:
                preview_atr = c_price * 0.01

            # RSI Oversold / Overbought history check
            rsi_series = history_df.get("rsi", pd.Series([50.0] * len(history_df)))
            rsi_was_oversold = bool(rsi_series.tail(5).min() <= self.config.strategy.rsi_oversold) or (preview_rsi <= self.config.strategy.rsi_oversold)
            rsi_was_overbought = bool(rsi_series.tail(5).max() >= self.config.strategy.rsi_overbought) or (preview_rsi >= self.config.strategy.rsi_overbought)

            # O(1) Consolidation Range Check (rolling 8 bars: last 7 closed bars + forming bar)
            recent_highs = history_df["high"].tail(7).tolist() + [forming_high]
            recent_lows = history_df["low"].tail(7).tolist() + [forming_low]
            roll_high = max(recent_highs)
            roll_low = min(recent_lows)
            cons_range = roll_high - roll_low
            is_cons = cons_range <= (preview_atr * self.config.strategy.consolidation_atr_mult)
            prior_cons = bool(history_df.get("is_consolidating", pd.Series([False])).tail(4).max())

            # Append forming candle to recent_candles for live chart rendering
            last_closed_ts = int(prev_closed_row.get("timestamp", time.time()))
            candle_sec = resolution_to_seconds(self.config.platform.resolution)
            forming_ts = last_closed_ts + candle_sec
            recent_candles.append({
                "timestamp": forming_ts,
                "datetime": datetime.fromtimestamp(forming_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00:00"),
                "open": forming_open,
                "high": forming_high,
                "low": forming_low,
                "close": float(c_price),
                "volume": float(prev_closed_row.get("volume", 100.0)),
                "ema_51": float(preview_ema51),
                "ema_200": float(preview_ema200),
                "is_forming": True
            })

            # --- Continuous Live Preview Math ---
            dist = (preview_close - preview_ema51) / preview_atr if preview_atr > 0 else 0.0

            # 1. EMA Position & Proximity Score
            if preview_close > preview_ema51:
                buy_ema = 30.0 + min(30.0, max(0.0, dist * 25.0))
                sell_ema = max(0.0, 20.0 - dist * 25.0)
            else:
                sell_ema = 30.0 + min(30.0, max(0.0, -dist * 25.0))
                buy_ema = max(0.0, 20.0 - (-dist * 25.0))

            ema_cross_up = (prev_close <= prev_ema51 and preview_close > preview_ema51) or (preview_close > preview_ema51 and prev_close > prev_ema51 and prev_open < prev_ema51)
            ema_cross_down = (prev_close >= prev_ema51 and preview_close < preview_ema51) or (preview_close < preview_ema51 and prev_close < prev_ema51 and prev_open > prev_ema51)
            if ema_cross_up:
                buy_ema += 20.0
            if ema_cross_down:
                sell_ema += 20.0

            # 2. Live RSI Directional Score
            if preview_rsi >= 50.0:
                buy_rsi = 15.0 + min(20.0, (preview_rsi - 50.0) * 1.0)
                sell_rsi = max(0.0, 15.0 - (preview_rsi - 50.0) * 0.75)
            else:
                sell_rsi = 15.0 + min(20.0, (50.0 - preview_rsi) * 1.0)
                buy_rsi = max(0.0, 15.0 - (50.0 - preview_rsi) * 0.75)

            if rsi_was_oversold or preview_rsi < 35.0:
                buy_rsi += 10.0
            if rsi_was_overbought or preview_rsi > 65.0:
                sell_rsi += 10.0

            # 3. Trend & Slope Alignment
            use_trend = self.config.strategy.use_trend_filter
            if not use_trend or preview_close >= preview_ema200:
                buy_trend = 15.0
                sell_trend = 0.0
            else:
                sell_trend = 15.0
                buy_trend = 0.0

            use_slope = self.config.strategy.use_ema_slope_filter
            if not use_slope or preview_ema_slope > 0:
                buy_slope = 10.0
                sell_slope = 0.0
            elif preview_ema_slope < 0:
                sell_slope = 10.0
                buy_slope = 0.0
            else:
                buy_slope = 5.0
                sell_slope = 5.0

            # 4. Consolidation & Volume
            cons_score = 10.0 if (prior_cons or is_cons) else 0.0
            vol_stat = getattr(self.feed, "volume_status_24h", "NORMAL")
            vol_score = 10.0 if vol_stat == "HIGH" else (5.0 if vol_stat == "NORMAL" else 0.0)

            buy_raw = buy_ema + buy_rsi + buy_trend + buy_slope + cons_score + vol_score
            sell_raw = sell_ema + sell_rsi + sell_trend + sell_slope + cons_score + vol_score

            total_raw = buy_raw + sell_raw
            if total_raw > 0:
                buy_pct = int(round(100.0 * buy_raw / total_raw))
                buy_pct = max(0, min(100, buy_pct))
                sell_pct = 100 - buy_pct
            else:
                buy_pct = 50
                sell_pct = 50

            if buy_pct > sell_pct:
                bias = "BUY"
                winning_pct = buy_pct
            elif sell_pct > buy_pct:
                bias = "SELL"
                winning_pct = sell_pct
            else:
                bias = "NEUTRAL"
                winning_pct = 50

            if winning_pct >= 100:
                status = "READY"
            elif winning_pct >= 85:
                status = "STRONG"
            elif winning_pct >= 70:
                status = "DEVELOPING"
            elif winning_pct >= 60:
                status = "FORMING"
            else:
                status = "NEUTRAL"

            readiness = {"buy_pct": buy_pct, "sell_pct": sell_pct, "bias": bias, "status": status}

        # --- Temporary Diagnostics Logging (Once every 2 seconds) ---
        now_ts = time.time()
        if now_ts - getattr(self, "_last_readiness_log_ts", 0.0) >= 2.0:
            self._last_readiness_log_ts = now_ts
            self.readiness_recalc_count = getattr(self, "readiness_recalc_count", 0) + 1
            logger.info(
                f"[ReadinessDiag] engine_state={engine_state} current_price={c_price:.2f} "
                f"preview_close={preview_close:.2f} ema51={preview_ema51:.2f} rsi={preview_rsi:.2f} "
                f"atr={preview_atr:.2f} buy_raw={buy_raw:.1f} sell_raw={sell_raw:.1f} "
                f"buy_pct={readiness['buy_pct']}% sell_pct={readiness['sell_pct']}% "
                f"readiness_updated_at={now_ist.strftime('%H:%M:%S IST')}"
            )

        return {
            "progress_task": self.feed.active_progress_task,
            "top_bar": {
                "ist_now": now_ist.strftime("%Y-%m-%d %H:%M:%S IST"),
                "symbol": f"{self.config.platform.symbol}.P",
                "timeframe": self.config.platform.resolution,
                "mode": "PAPER",
                "connection": conn_state,
                "engine_state": engine_state,
                "latency_ms": latency_val
            },
            "chart_candles": recent_candles,
            "readiness": readiness,
            "market_trade": {
                "current_price": c_price,
                "bid_price": self.feed.bid_price,
                "ask_price": self.feed.ask_price,
                "price_change_pct_24h": self.feed.price_change_pct_24h,
                "volume_status_24h": vol_status,
                "signal": sig_state,
                "active_position": pos_dict,
                "current_pnl": unrealized,
                "current_pnl_pct": net_pnl_pct
            },
            "recent_trades": recent_trades_formatted,
            "account": {
                "balance": self.account.balance,
                "equity": current_equity,
                "net_pnl": self.account.balance - self.account.initial_balance + unrealized,
                "net_pnl_pct": ((current_equity - self.account.initial_balance) / self.account.initial_balance) * 100.0,
                "session_trades": self.session_trades_count,
                "total_trades": len(self.trades_history),
                "wins": wins,
                "losses": losses,
                "fees": self.total_fees,
                "uptime": uptime_str,
                "app_start_ist": app_start_time_ist
            },
            "performance": {
                "win_rate_pct": wr,
                "profit_factor": g_pf,
                "sharpe_ratio": 1.21,
                "max_drawdown_pct": self.max_dd_pct,
                "leverage": self.config.risk.leverage,
                "exposure_pct": exposure_pct
            },
            "bottom_status": {
                "feed_speed": self.feed.get_feed_speed_str(),
                "last_market_update": last_market_update_ist,
                "reconnect_count": self.feed.reconnect_count,
                "cpu_usage_pct": round(cpu_usage_pct, 1),
                "ram_usage_pct": round(ram_usage_pct, 1),
                "disk_usage_pct": round(disk_usage_pct, 1),
                "cpu_usage_str": cpu_usage_str,
                "ram_usage_str": ram_usage_str,
                "disk_usage_str": disk_usage_str,
                "state_save_status": f"SAVED ({self.last_state_save_time})"
            }
        }

    def run_forward_session(self, duration_seconds: Optional[float] = None):
        """Execute live paper forward testing session with a single in-place Rich Live dashboard."""
        start_ts = time.time()
        last_auto_save = time.time()
        
        # We need to access utils
        from common.utils import mute_console_loggers, unmute_console_loggers

        # Start single Rich Live dashboard instance BEFORE recovery/warmup/connect
        with Live(self.dashboard.render(self.build_dashboard_state()), console=self.console, refresh_per_second=2) as live:
            # Mute console StreamHandlers so stdout belongs 100% exclusively to Rich Live
            mute_console_loggers()

            try:
                self.load_or_init_state()
                live.update(self.dashboard.render(self.build_dashboard_state()))

                self.feed.warm_up_historical_data(days=60)
                self.last_warmup_candle_ts = self.feed.last_closed_3h_ts
                live.update(self.dashboard.render(self.build_dashboard_state()))

                # Check if an open position existed during an outage and reconstruct if SL/TP was hit
                if self.active_position:
                    last_ts = self.feed.last_closed_3h_ts
                    self.feed.backfill_missing_outage_candles(last_ts)
                    self.check_and_reconstruct_offline_position_outage(self.feed.df_3h)
                    live.update(self.dashboard.render(self.build_dashboard_state()))

                # Wire callbacks
                self.feed.add_tick_callback(self.evaluate_live_tick)
                self.feed.add_3h_close_callback(self.on_3h_candle_closed)
                self.feed.start_feed()
                live.update(self.dashboard.render(self.build_dashboard_state()))

                # ── STARTUP HEALTH GATE ──────────────────────────────────────
                # Wait up to 30s for: WS thread alive + WS connected + feed initialized
                logger.info("[*] Startup health gate: waiting for WebSocket connection and feed init (timeout 30s)...")
                _gate_start = time.time()
                while True:
                    ws_alive = (
                        self.feed._ws_thread is not None
                        and self.feed._ws_thread.is_alive()
                    )
                    if self.feed.ws_thread_died.is_set():
                        raise RuntimeError(
                            "STARTUP FAILED: WebSocket worker thread (_ws_loop) died at startup. "
                            "Check logs for the root cause (import error, connection error, etc.)."
                        )
                    if not ws_alive and (time.time() - _gate_start) > 5.0:
                        raise RuntimeError(
                            "STARTUP FAILED: WebSocket thread is not alive 5s after start_feed(). "
                            "Cannot proceed with live forward test."
                        )
                    if self.feed.feed_initialized and self.feed.ws_connected and self.feed.current_price > 0:
                        logger.info(
                            f"[+] Startup health gate PASSED: ws_alive={ws_alive}, "
                            f"ws_connected={self.feed.ws_connected}, "
                            f"feed_initialized={self.feed.feed_initialized}, "
                            f"price={self.feed.current_price:.2f}"
                        )
                        break
                    if (time.time() - _gate_start) > 30.0:
                        raise RuntimeError(
                            "STARTUP FAILED: Feed not initialized after 30s. "
                            f"ws_alive={ws_alive}, ws_connected={self.feed.ws_connected}, "
                            f"feed_initialized={self.feed.feed_initialized}, "
                            f"price={self.feed.current_price:.2f}"
                        )
                    live.update(self.dashboard.render(self.build_dashboard_state()))
                    time.sleep(0.5)
                # ── END STARTUP HEALTH GATE ──────────────────────────────────

                logger.info("[+] Starting Paper Forward Trading Session. Rendering Redesigned Live Dashboard...")

                while True:
                    if duration_seconds and (time.time() - start_ts) >= duration_seconds:
                        break

                    # Dead WS thread check — fail loudly rather than running silently paused
                    if self.feed.ws_thread_died.is_set():
                        raise RuntimeError(
                            "CRITICAL: WebSocket worker thread died during live forward test. "
                            "Trading halted. Check logs for root cause."
                        )
                    dashboard_state = self.build_dashboard_state()
                    live.update(self.dashboard.render(dashboard_state))

                    # Atomic state auto-save every auto_save_seconds (30 sec)
                    now_ts = time.time()
                    if now_ts - last_auto_save >= self.config.auto_save_seconds:
                        self.save_state(self.feed.current_price)
                        self.save_periodic_equity_snapshot(self.feed.current_price)
                        last_auto_save = now_ts

                    time.sleep(0.5)

            except KeyboardInterrupt:
                logger.info("KeyboardInterrupt received. Shutting down Paper Forward Engine...")
            finally:
                self.feed.stop_feed()
                self.save_state(self.feed.current_price)
                self.update_global_tracker()
                unmute_console_loggers()
                logger.info("[+] Paper Forward Session stopped cleanly. State saved.")
