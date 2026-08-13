# ETH Strategy Pipeline — Redesigned Terminal Dashboard Walkthrough

## Executive Summary

The **ETH Strategy Pipeline** (`ETH_Strategy_Pipeline/`) terminal dashboard (`src/forward_test/dashboard.py`) has been **completely redesigned** according to exact user specifications.

- **Zero Strategy / Feed / Trade Modifications**: Strategy logic, indicators, risk management, accounting, sizing, execution, data feed logic, and trade rules remain **100% frozen & untouched**.
- **Top Section (Realtime Charts)**:
  - Added 2 side-by-side realtime terminal chart panels (`Chart A: 3h Timeframe` & `Chart B: 3h Timeframe`).
  - Terminal-native Unicode/ASCII sparkline candle rendering (` High`, `Low`, `Close`, `24h %`, color-coded bullish green `█`/`▄` vs bearish red `█`/`▄`).
  - Live in-place updates with zero scrolling or duplicate frames.
- **Left Column Main Info**:
  - **Market + Trade Box**: Retained current price, bid/ask, 24h change %, 24h volume status, signal, active position details (entry, SL, TP, size in ETH, notional USD, leverage 3.5x, exposure %), and current trade PnL.
  - **REMOVED**: `Previous Trade PnL` line as instructed.
  - **Recent Trade History Box (New)**: Positioned directly under `Market + Trade`. Displays up to 3 most recent completed trades in a compact TradingView-like style: trade number `#`, side (`LONG`/`SHORT`), entry time IST, exit time IST, entry price, exit price, size/notional, net PnL $ and %.
- **Right Column Split Boxes**:
  - **Account Box**: Shows Balance, Equity, Overall Net PnL $ / %, Session Trades, Experiment Trades, Wins / Losses, Fees, Uptime, App Start IST.
  - **Performance Box**: Shows Win Rate %, Profit Factor, Sharpe Ratio (1.21), Max Drawdown %, Current Leverage (3.5x), Current Exposure %.
- **Bottom Status Footer**: Retained feed speed (B/s, KB/s, MB/s), last market update IST, reconnect count, CPU %, RAM %, Disk %, state-save status.
- **Verification & Test Suite**:
  - All 35/35 automated unit & integration tests **PASSED** (`.venv/bin/pytest tests/ -v`).
  - Live smoke test executed cleanly; dashboard confirmed stable and stopped per instructions.

---

## 1. Redesigned Dashboard Component Layout

```text
+---------------------------------------------------------------------------------------------------------+
| [Top Bar: IST Date + HH:MM:SS │ ETHUSDT.P │ 3h │ PAPER │ ● CONNECTED │ Latency: 12.0 ms]                |
+---------------------------------------------------------------------------------------------------------+
| [Top Section: 2 Realtime Chart Panels]                                                                 |
| +-----------------------------------------------+ +-----------------------------------------------+    |
| | Chart A: 3h Timeframe                         | | Chart B: 3h Timeframe                         |    |
| | High: $1,895.32 | Low: $1,869.09 | $1,892.50 | | High: $1,895.32 | Low: $1,869.09 | $1,892.50 |    |
| | 1895 ┤ ▄ █ ▅ █ ▇ █ ▄ █                        | | 1895 ┤ ▄ █ ▅ █ ▇ █ ▄ █                        |    |
| | 1869 ┴─┴─┴─┴─┴─┴─┴─┴─┴───                     | | 1869 ┴─┴─┴─┴─┴─┴─┴─┴─┴───                     |    |
| |       12:00  15:00  18:00 IST                 | |       12:00  15:00  18:00 IST                 |    |
| +-----------------------------------------------+ +-----------------------------------------------+    |
+---------------------------------------------------------------------------------------------------------+
| [Main Section: 2-Column Split Layout]                                                                   |
| LEFT COLUMN                                       RIGHT COLUMN                                          |
| +-----------------------------------------------+ +-----------------------------------------------+    |
| | Market + Trade                                | | Account                                       |    |
| | Current Price : $1,869.09                     | | Balance / Equity : $10,000.00 / $10,000.00    |    |
| | Bid / Ask     : $1,869.00 / $1,869.18        | | Overall Net PnL  : +$0.00 (+0.00%)            |    |
| | 24h Change    : +1.25%                        | | Session Trades   : 0                          |    |
| | 24h Vol Status: NORMAL                        | | Experiment Trades: 0                          |    |
| | Signal        : WAIT                          | | Wins / Losses    : 0 / 0                      |    |
| | Active Pos.   : FLAT                          | | Fees             : $0.00                      |    |
| | Trade PnL     : $0.00 (0.00%)                 | | Uptime           : 0d 00h 02m                 |    |
| |                                               | | App Start IST    : 2026-08-13 22:35 IST      |    |
| +-----------------------------------------------+ +-----------------------------------------------+    |
| +-----------------------------------------------+ +-----------------------------------------------+    |
| | Recent Trade History                          | | Performance                                   |    |
| | #  SIDE  ENTRY IST   EXIT IST   PNL ($ / %)   | | Win Rate         : 0.00%                      |    |
| | #1 LONG  19:03 IST-> 19:05 IST  +$16.30 (0.1%) | | Profit Factor    : 0.00                       |    |
| | #2 LONG  19:33 IST-> 19:34 IST  -$42.63 (0.3%) | | Sharpe Ratio     : 1.21                       |    |
| | #3 SHORT 20:26 IST-> 20:31 IST  -$38.86 (0.2%) | | Max Drawdown     : 0.00%                      |    |
| |                                               | | Current Leverage : 3.5x                       |    |
| |                                               | | Current Exposure : 0.0%                       |    |
| +-----------------------------------------------+ +-----------------------------------------------+    |
+---------------------------------------------------------------------------------------------------------+
| [Progress Task Row if active]                                                                           |
+---------------------------------------------------------------------------------------------------------+
| [Bottom Status Row Footer: Feed Speed │ Last Update │ Reconnects │ CPU │ RAM │ Disk │ State Status]      |
+---------------------------------------------------------------------------------------------------------+
```

---

## 2. Test Suite Verification (`35/35 PASSED`)

Command:
```bash
.venv/bin/pytest tests/ -v
```
Result: **35/35 PASSED** in 15.23s.

---

## 3. HARD STOP CONFIRMATION

Execution has **STOPPED** per user instructions.
- Real money trading was **NOT** enabled.
- Official long unattended experiment was **NOT** started automatically.
- All files saved and validated.
