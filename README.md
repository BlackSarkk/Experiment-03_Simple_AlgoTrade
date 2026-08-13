# ETH Strategy Pipeline

Rule-based 3h ETHUSDT perpetual trading pipeline with exact Pine Script reproduction, backtesting, robustness evaluation, and unattended paper forward testing.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
chmod +x pipeline.sh
```

## Usage Commands

### Start Fresh Paper Test
```bash
./pipeline.sh --forward-test --reset --clear-cache
```

### Resume Paper Test
```bash
./pipeline.sh --forward-test --resume
```

### Run Backtest
```bash
./pipeline.sh --backtest
```

### Run Robustness Suite
```bash
./pipeline.sh --robustness
```

### Clear Market Cache Only
```bash
./pipeline.sh --clear-cache-only
```

### Reset Paper Experiment (Without Clearing Market Cache)
```bash
./pipeline.sh --forward-test --reset
```

## Changing Settings

Edit the configuration section at the top of `pipeline.sh`:

- `SYMBOL` ("ETHUSDT")
- `PLATFORM` ("BINANCE_FUTURES")
- `TIMEFRAME` ("3h")
- `INITIAL_BALANCE` (10000)
- `LEVERAGE` (3.5)
- `RISK_PER_TRADE_PCT` (1.5)
- `MAX_POSITION_ALLOCATION_PCT` (50)
- `RR_RATIO` (1.5)
- `COMMISSION_PCT` (0.05)
- `SLIPPAGE_TICKS` (1)

## Raspberry Pi Service Management

```bash
# Install and start service:
./deploy/install_service.sh

# Check service status:
systemctl status eth-paper-forward.service

# View live journal logs:
journalctl -u eth-paper-forward.service -f -o cat

# Restart service:
sudo systemctl restart eth-paper-forward.service

# Stop service:
sudo systemctl stop eth-paper-forward.service
```
