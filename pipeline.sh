#!/usr/bin/env bash
# ETH Strategy Pipeline — Execution & Strategy Configuration Wrapper Script

set -e

# ==============================================================================
# STRATEGY & PIPELINE CONFIGURATION KNOBS
# ==============================================================================
CONFIG_ARG="configs/default.json"

EXECUTION_MODE="REFERENCE"               # "REFERENCE" or "REALISTIC"

EXECUTION_SUPPLIED=false

# Default Flags (SAFE DEFAULTS: RESET=false, CLEAR_CACHE=false, CLEAR_CACHE_ONLY=false)
CLEAR_CACHE_ONLY=${CLEAR_CACHE_ONLY:-false}
CLEAR_CACHE=${CLEAR_CACHE:-false}
RESET=${RESET:-false}
HARD_RESET=${HARD_RESET:-false}

BACKTEST=${BACKTEST:-false}
ROBUSTNESS=${ROBUSTNESS:-false}
FORWARD_TEST=${FORWARD_TEST:-false}

FORWARD_MODE=${FORWARD_MODE:-"PAPER"}
RESUME_FORWARD_STATE=${RESUME_FORWARD_STATE:-true}

# Parse CLI arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --clear-cache-only)
      CLEAR_CACHE_ONLY=true
      shift
      ;;
    --clear-cache|--reset-cache)
      CLEAR_CACHE=true
      shift
      ;;
    --hard-reset)
      HARD_RESET=true
      RESET=true
      CLEAR_CACHE=true
      RESUME_FORWARD_STATE=false
      shift
      ;;
    --reset)
      RESET=true
      RESUME_FORWARD_STATE=false
      shift
      ;;
    --resume)
      RESET=false
      RESUME_FORWARD_STATE=true
      shift
      ;;
    --backtest)
      BACKTEST=true
      ROBUSTNESS=false
      FORWARD_TEST=false
      EXECUTION_SUPPLIED=true
      shift
      ;;
    --robustness)
      ROBUSTNESS=true
      BACKTEST=false
      FORWARD_TEST=false
      EXECUTION_SUPPLIED=true
      shift
      ;;
    --forward-test)
      FORWARD_TEST=true
      BACKTEST=false
      ROBUSTNESS=false
      EXECUTION_SUPPLIED=true
      FORWARD_MODE="PAPER"
      shift
      ;;
    --historical-replay)
      FORWARD_TEST=true
      BACKTEST=false
      ROBUSTNESS=false
      EXECUTION_SUPPLIED=true
      FORWARD_MODE="HISTORICAL_REPLAY"
      shift
      ;;
    --config)
      if [[ $# -lt 2 || "$2" == --* ]]; then
        echo "ERROR: --config requires a config file argument."
        echo "       e.g. --config config1-ETHUSDTP15m-long.json"
        exit 1
      fi
      CONFIG_ARG="$2"
      shift 2
      ;;
    --config=*)
      CONFIG_ARG="${1#--config=}"
      shift
      ;;
    *)
      echo "ERROR: Unknown option '$1'"
      echo ""
      echo "Usage:"
      echo "  ./pipeline.sh --config <config-file> --backtest"
      echo "  ./pipeline.sh --config <config-file> --forward-test"
      echo "  ./pipeline.sh --config <config-file> --historical-replay"
      echo "  ./pipeline.sh --config <config-file> --robustness"
      echo "  ./pipeline.sh --reset"
      echo "  ./pipeline.sh --hard-reset"
      echo "  ./pipeline.sh --clear-cache"
      echo "  ./pipeline.sh --reset --clear-cache"
      exit 1
      ;;
  esac
done

# ------------------------------------------------------------------
# Resolve --config to an actual file. Accepted forms:
#   configs/name.json | name.json | name   (searched in ./ then ./configs/)
# ------------------------------------------------------------------
CONFIG_PATH=""
for cand in "$CONFIG_ARG" "$CONFIG_ARG.json" "configs/$CONFIG_ARG" "configs/$CONFIG_ARG.json"; do
  if [ -f "$cand" ]; then CONFIG_PATH="$cand"; break; fi
done

if [ -z "$CONFIG_PATH" ]; then
  echo "ERROR: Config file not found: '$CONFIG_ARG'"
  echo "       Looked for: $CONFIG_ARG, $CONFIG_ARG.json, configs/$CONFIG_ARG, configs/$CONFIG_ARG.json"
  echo "       Available configs:"
  ls -1 configs/*.json 2>/dev/null | sed 's|^|         |' || echo "         (none)"
  exit 1
fi

# JSON parse + required-schema validation (clear error, non-zero exit)
if ! .venv/bin/python3 - "$CONFIG_PATH" <<'PYCHK'
import json, sys
path = sys.argv[1]
try:
    with open(path) as fh:
        cfg = json.load(fh)
except json.JSONDecodeError as e:
    print(f"ERROR: '{path}' is not valid JSON: {e}"); sys.exit(1)
except OSError as e:
    print(f"ERROR: cannot read '{path}': {e}"); sys.exit(1)
if not isinstance(cfg, dict):
    print(f"ERROR: '{path}' must contain a JSON object."); sys.exit(1)
REQUIRED = {
    None:        ["symbol", "timeframe", "strategy", "risk", "execution"],
    "strategy":  ["ema_period", "rsi_period", "rsi_overbought", "rsi_oversold",
                  "atr_period", "consolidation_candles", "consolidation_atr_mult",
                  "swing_lookback", "volume_sma_period", "volume_mult",
                  "risk_reward_ratio", "long_enabled", "short_enabled"],
    "risk":      ["initial_capital", "leverage", "risk_per_trade_pct",
                  "max_position_allocation_pct"],
    "execution": ["commission_pct", "slippage_ticks", "tick_size"],
}
missing = [k for k in REQUIRED[None] if k not in cfg]
for section, keys in REQUIRED.items():
    if section is None or section in missing:
        continue
    block = cfg.get(section)
    if not isinstance(block, dict):
        missing.append(f"{section} (must be a JSON object)"); continue
    missing += [f"{section}.{k}" for k in keys if k not in block]
if missing:
    print(f"ERROR: '{path}' is missing required config fields:")
    for m in missing:
        print(f"         - {m}")
    sys.exit(1)
PYCHK
then
  exit 1
fi

echo "Config: $CONFIG_PATH"

MAINTENANCE_ONLY=false
if [ "$EXECUTION_SUPPLIED" = false ]; then
  if [ "$HARD_RESET" = true ] || [ "$RESET" = true ] || [ "$CLEAR_CACHE" = true ] || [ "$CLEAR_CACHE_ONLY" = true ]; then
    MAINTENANCE_ONLY=true
  else
    echo "ERROR: No action specified."
    echo ""
    echo "Usage:"
    echo "  ./pipeline.sh --config <config-file> --backtest"
    echo "  ./pipeline.sh --config <config-file> --forward-test"
    echo "  ./pipeline.sh --config <config-file> --historical-replay"
    echo "  ./pipeline.sh --config <config-file> --robustness"
    echo "  ./pipeline.sh --reset"
    echo "  ./pipeline.sh --hard-reset"
    echo "  ./pipeline.sh --clear-cache"
    echo "  ./pipeline.sh --reset --clear-cache"
    exit 1
  fi
fi

if [ "$HARD_RESET" = true ] && [ "$EXECUTION_SUPPLIED" = true ]; then
    echo "ERROR: --hard-reset cannot be combined with execution modes. Run it separately."
    exit 1
fi

# Build Python execution string
CMD=".venv/bin/python3 src/main.py --config-preset "$CONFIG_PATH" --forward-mode $FORWARD_MODE --execution-mode $EXECUTION_MODE"

if [ "$HARD_RESET" = true ]; then
  CMD="$CMD --hard-reset --maintenance-only"
elif [ "$MAINTENANCE_ONLY" = true ]; then
  CMD="$CMD --maintenance-only"
  if [ "$RESET" = true ]; then CMD="$CMD --reset"; fi
  if [ "$CLEAR_CACHE" = true ] || [ "$CLEAR_CACHE_ONLY" = true ]; then CMD="$CMD --clear-cache"; fi
elif [ "$FORWARD_TEST" = true ]; then
  CMD="$CMD --forward-test"
  if [ "$RESET" = true ]; then CMD="$CMD --reset"; fi
  if [ "$CLEAR_CACHE" = true ]; then CMD="$CMD --clear-cache"; fi
  if [ "$RESUME_FORWARD_STATE" = true ] && [ "$RESET" = false ]; then CMD="$CMD --resume"; fi
elif [ "$ROBUSTNESS" = true ]; then
  CMD="$CMD --robustness"
  if [ "$RESET" = true ]; then CMD="$CMD --reset"; fi
  if [ "$CLEAR_CACHE" = true ]; then CMD="$CMD --clear-cache"; fi
else
  CMD="$CMD --backtest"
  if [ "$RESET" = true ]; then CMD="$CMD --reset"; fi
  if [ "$CLEAR_CACHE" = true ]; then CMD="$CMD --clear-cache"; fi
fi

eval $CMD
