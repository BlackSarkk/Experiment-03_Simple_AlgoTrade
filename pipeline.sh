#!/usr/bin/env bash
# ETH Strategy Pipeline — Execution & Strategy Configuration Wrapper Script

set -e

# ==============================================================================
# STRATEGY & PIPELINE CONFIGURATION KNOBS
# ==============================================================================
CONFIG_PRESET="default"

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
    --default)
      CONFIG_PRESET="default"
      shift
      ;;
    --config1)
      CONFIG_PRESET="config1-ETHUSDTP15m"
      shift
      ;;
    --config*)
      CONFIG_PRESET="${1#--}"
      shift
      ;;
    *)
      echo "ERROR: Unknown option '$1'"
      echo ""
      echo "Usage:"
      echo "  ./pipeline.sh --backtest [options]"
      echo "  ./pipeline.sh --forward-test [options]"
      echo "  ./pipeline.sh --historical-replay [options]"
      echo "  ./pipeline.sh --robustness [options]"
      echo "  ./pipeline.sh --reset"
      echo "  ./pipeline.sh --hard-reset"
      echo "  ./pipeline.sh --clear-cache"
      echo "  ./pipeline.sh --reset --clear-cache"
      exit 1
      ;;
  esac
done

if [ ! -f "configs/${CONFIG_PRESET}.json" ] && [ ! -f "configs/${CONFIG_PRESET}.config" ]; then
    echo "ERROR: Config preset 'configs/${CONFIG_PRESET}' (.json or .config) does not exist."
    exit 1
fi

MAINTENANCE_ONLY=false
if [ "$EXECUTION_SUPPLIED" = false ]; then
  if [ "$HARD_RESET" = true ] || [ "$RESET" = true ] || [ "$CLEAR_CACHE" = true ] || [ "$CLEAR_CACHE_ONLY" = true ]; then
    MAINTENANCE_ONLY=true
  else
    echo "ERROR: No action specified."
    echo ""
    echo "Usage:"
    echo "  ./pipeline.sh --backtest [options]"
    echo "  ./pipeline.sh --forward-test [options]"
    echo "  ./pipeline.sh --historical-replay [options]"
    echo "  ./pipeline.sh --robustness [options]"
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
CMD=".venv/bin/python3 src/main.py --config-preset $CONFIG_PRESET --forward-mode $FORWARD_MODE --execution-mode $EXECUTION_MODE"

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
