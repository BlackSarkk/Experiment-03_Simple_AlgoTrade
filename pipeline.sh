#!/usr/bin/env bash
# ETH Strategy Pipeline — Execution & Strategy Configuration Wrapper Script

set -e

# ==============================================================================
# STRATEGY & PIPELINE CONFIGURATION KNOBS
# ==============================================================================
TIMEFRAME="${TIMEFRAME:-"1m"}"
CONFIG_PRESET="default"

EXECUTION_MODE="REFERENCE"               # "REFERENCE" or "REALISTIC"

# Default Flags (SAFE DEFAULTS: RESET=false, CLEAR_CACHE=false, CLEAR_CACHE_ONLY=false)
CLEAR_CACHE_ONLY=${CLEAR_CACHE_ONLY:-false}
CLEAR_CACHE=${CLEAR_CACHE:-false}
RESET=${RESET:-false}

BACKTEST=${BACKTEST:-false}
ROBUSTNESS=${ROBUSTNESS:-false}
FORWARD_TEST=${FORWARD_TEST:-true}

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
      shift
      ;;
    --robustness)
      ROBUSTNESS=true
      BACKTEST=false
      FORWARD_TEST=false
      shift
      ;;
    --forward-test)
      FORWARD_TEST=true
      BACKTEST=false
      ROBUSTNESS=false
      shift
      ;;
    --default)
      CONFIG_PRESET="default"
      shift
      ;;
    --config*)
      CONFIG_PRESET="${1#--}"
      shift
      ;;
    *)
      shift
      ;;
  esac
done

if [ ! -f "configs/${CONFIG_PRESET}.json" ]; then
    echo "ERROR: Config preset 'configs/${CONFIG_PRESET}.json' does not exist."
    exit 1
fi

# Build Python execution string
CMD=".venv/bin/python3 src/main.py --config-preset $CONFIG_PRESET --forward-mode $FORWARD_MODE --execution-mode $EXECUTION_MODE"

if [ "$CONFIG_PRESET" = "default" ]; then
    CMD="$CMD --timeframe $TIMEFRAME"
fi

if [ "$CLEAR_CACHE_ONLY" = true ]; then
  CMD="$CMD --clear-cache-only"
elif [ "$FORWARD_TEST" = true ]; then
  CMD="$CMD --forward-test"
  if [ "$RESET" = true ]; then
    CMD="$CMD --reset"
  fi
  if [ "$CLEAR_CACHE" = true ]; then
    CMD="$CMD --clear-cache"
  fi
  if [ "$RESUME_FORWARD_STATE" = true ] && [ "$RESET" = false ]; then
    CMD="$CMD --resume"
  fi
elif [ "$ROBUSTNESS" = true ]; then
  CMD="$CMD --robustness"
  if [ "$RESET" = true ]; then
    CMD="$CMD --reset"
  fi
  if [ "$CLEAR_CACHE" = true ]; then
    CMD="$CMD --clear-cache"
  fi
else
  CMD="$CMD --backtest"
  if [ "$RESET" = true ]; then
    CMD="$CMD --reset"
  fi
  if [ "$CLEAR_CACHE" = true ]; then
    CMD="$CMD --clear-cache"
  fi
fi

eval $CMD
