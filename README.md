# ETH Strategy Pipeline

Rule-based ETHUSDT perpetual trading pipeline with exact Pine Script reproduction, backtesting, robustness evaluation, and unattended paper forward testing.

## Overview

This project provides a robust engine for testing and live-simulating crypto trading strategies. The current workflow relies on a **Configuration Preset System**. Instead of passing individual parameters via CLI, full strategy configurations are loaded from JSON files located in `configs/`.

- **`configs/default.json`**: Baseline defaults. The timeframe is inherited from the `pipeline.sh` script or environment variables.
- **`configs/default.json`**: Validated Phase 6 baseline (Candidate 5). Named presets explicitly override symbol, timeframe, strategy inputs, risk, and execution settings.

## Project Structure

- `configs/` - Strategy configuration presets (JSON)
- `data/` - Cached historical market data (CSV)
- `src/` - Core engine (Backtest, Paper Forward, Strategy Logic, Config Loader)
- `tests/` - Integration and unit tests
- `pipeline.sh` - Main entrypoint wrapper

### Historical Replay
Execute the historical replay engine using a specific configuration preset:
```bash
./pipeline.sh --historical-replay --default
```

## Installation

```bash
git clone https://github.com/BlackSarkk/Experiment-03_Simple_AlgoTrade.git
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
chmod +x pipeline.sh
```

## Usage Commands

### Backtesting
Run a backtest using a specific configuration preset:
```bash
./pipeline.sh --backtest --default
```

### Forward Testing (Paper Trading)
Start a fresh forward test, pulling the latest data and resetting state with custom config:
```bash
./pipeline.sh --forward-test --reset --clear-cache --default
```

Start a fresh forward test, pulling the latest data and resetting state with default settings:
```bash
./pipeline.sh --forward-test --reset --clear-cache
./pipeline.sh --forward-test --reset --clear-cache --deafult
```
*Tip: For long-running forward tests (e.g., on a Raspberry Pi), it is highly recommended to run the pipeline inside a `tmux` session to prevent termination when your SSH connection drops.*

### Run Automated Tests
Execute the full integration and unit test suite:
```bash
PYTHONPATH=src .venv/bin/pytest tests/ -q
```
