# Delta Exchange ETHUSD 1-Hour Algorithmic Trading Strategy

A complete, production-grade algorithmic trading system and backtesting engine built specifically for **Delta Exchange** 1-Hour candles.

---

## 📌 Strategy Overview & Core Features

| Feature | Specification |
| :--- | :--- |
| **Direct Exchange API Data** | Historical 1-hour candles downloaded directly from Delta Exchange API (`https://api.delta.exchange/v2/history/candles`) with pagination and disk caching. |
| **Timeframe** | 1-Hour (`1h` / 60-minute candles) |
| **Trend Filter** | **51 EMA** (Exponential Moving Average) |
| **Momentum Filter** | **14 RSI**: Overbought $\ge 65$, Oversold $\le 35$ |
| **Consolidation Filter** | **8-Candle ATR Consolidation Detector**: Verifies volatility compression before breakout |
| **Crossover Confirmation** | **Completed-Candle confirmation**: Avoids intra-candle false triggers |
| **Stop-Loss** | **Confirmed Swing High / Low**: Dynamic structural support/resistance |
| **Take-Profit** | **Fixed 1:2 Risk:Reward**: $TP = Entry \pm (2.0 \times \|Entry - SL\|)$ |
| **Position Sizing** | **1% Account Risk per Trade**: Dynamic sizing based on SL distance ($Risk = Equity \times 0.01$) |
| **Capital Allocation Cap** | **Maximum 30% Position Allocation**: Protects portfolio from over-leverage |
| **Execution Realism** | **Next-Candle Open Execution**: Strict zero look-ahead bias with slippage & taker fees |
| **Comprehensive Exports** | CSV files for candles, signals, trade log, performance metrics, and an interactive HTML visual report |

---

## 📁 Project Architecture

```
delta_algo/
├── config.py             # Central dataclasses for strategy, risk, fees, and paths
├── data_fetcher.py       # Delta Exchange REST API client (pagination, cache, error handling)
├── indicators.py         # 51 EMA, 14 RSI, 14 ATR, 8-candle consolidation, swing S/R
├── strategy.py           # Signal generator with crossover, RSI bounds, and consolidation
├── risk_manager.py       # 1% risk sizing, 30% position cap, 1:2 RR geometry
├── backtester.py         # Event-driven bar-by-bar engine (Next-Candle Open, fees, slippage)
├── metrics.py            # Quantitative performance scorecard (Sharpe, Drawdown, Win Rate, etc.)
├── exporter.py           # CSV exporters & standalone HTML interactive dashboard
├── main.py               # CLI entrypoint with flexible arguments
├── test_strategy.py      # Automated unit and integration test suite
├── data/                 # Cached raw candles from Delta Exchange
└── output/               # Generated trade logs, signals, metrics, and visual dashboard
```

---

## 📈 TradingView Pine Script (v5)

The entire strategy is also available as a standalone **TradingView Pine Script v5** indicator/strategy:
- File: [`delta_eth_1h_strategy.pine`](file:///C:/Users/Dhruv/.gemini/antigravity/scratch/delta_algo/delta_eth_1h_strategy.pine) (or [`delta_eth_1h_strategy.txt`](file:///C:/Users/Dhruv/.gemini/antigravity/scratch/delta_algo/delta_eth_1h_strategy.txt))

### How to use in TradingView:
1. Open TradingView and select the **ETHUSD** or **ETHUSDT** 1-Hour chart (Delta Exchange or Binance/Bybit).
2. Click **Pine Editor** at the bottom of the screen.
3. Open [`delta_eth_1h_strategy.txt`](file:///C:/Users/Dhruv/.gemini/antigravity/scratch/delta_algo/delta_eth_1h_strategy.txt), copy all code, and paste it into the Pine Editor.
4. Click **Add to chart**.
5. The script will render:
   - **51 EMA** line (colored dynamically).
   - **8-Candle Consolidation** highlighted zones.
   - **Long / Short Entry Triangles**.
   - **Real-time Stop-Loss & Take-Profit Levels**.
   - **On-Chart Performance HUD Table** showing Win Rate, Net Profit, and Active Positions.

---

### 1. Run Strategy Backtest with Live Delta Exchange Data
```bash
python main.py --symbol ETHUSDT --days 180 --capital 10000 --risk-pct 1.0 --max-alloc-pct 30.0
```

### 2. Force Fresh API Download
```bash
python main.py --force-download --days 365
```

### 3. Run Automated Tests
```bash
python -m unittest test_strategy.py
```

---

## 📊 Exported Files (`output/`)

1. **`candles.csv`**: Full OHLCV series with attached indicator values (`ema_51`, `rsi`, `atr`, `is_consolidating`, `swing_high`, `swing_low`).
2. **`signals.csv`**: Every generated trade signal with timestamp, indicator snapshots, trigger reasoning, and target levels.
3. **`trade_log.csv`**: Complete trade lifecycle log (Entry/Exit times, prices, slippage, fees, Net PnL, R-Multiple, updated equity).
4. **`performance_metrics.csv` & `performance_metrics.json`**: Quantitative metrics scorecard.
5. **`dashboard.html`**: Interactive visual web dashboard featuring equity curve, key metric cards, and sortable trade table.

---

## ⚙️ Customization & Parameters

You can customize parameters via CLI or directly in `config.py`:
- `--symbol`: Default `ETHUSDT` (Delta perpetual)
- `--days`: Lookback period in days (e.g. `90`, `180`, `365`)
- `--capital`: Initial capital in USD (e.g. `10000`)
- `--leverage`: Account leverage multiplier (e.g. `1.0`, `2.0`, `3.0`, `5.0`, `10.0`)
- `--risk-pct`: Account equity percentage risked per trade (e.g. `1.0`, `1.5`, `2.0`)
- `--max-alloc-pct`: Maximum position allocation cap (e.g. `50.0`)
- `--maker-fee`: Maker fee percentage (e.g. `0.02`)
- `--taker-fee`: Taker fee percentage (e.g. `0.05`)
- `--slippage`: Slippage per order (e.g. `0.03`)

### Example Commands:
```bash
# 1. Spot / 1x Leverage Backtest (+17.20% Return)
python main.py --symbol ETHUSDT --days 365 --leverage 1.0

# 2. 3x Leverage Backtest (+28.71% Return)
python main.py --symbol ETHUSDT --days 365 --leverage 3.0

# 3. 5x Leverage Backtest
python main.py --symbol ETHUSDT --days 365 --leverage 5.0 --risk-pct 1.5
```
