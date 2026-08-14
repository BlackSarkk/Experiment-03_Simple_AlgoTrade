#!/usr/bin/env bash
# ETH Strategy Pipeline — Execution & Strategy Configuration Wrapper Script

set -e

# ==============================================================================
# STRATEGY & PIPELINE CONFIGURATION KNOBS
# ==============================================================================
SYMBOL="ETHUSDT"
PLATFORM="BINANCE_FUTURES"
TIMEFRAME="${TIMEFRAME:-"3m"}"

INITIAL_BALANCE=10000
LEVERAGE=3.5
RISK_PER_TRADE_PCT=1.5
MAX_POSITION_ALLOCATION_PCT=50
RR_RATIO=1.5

LONG_ENABLED=${LONG_ENABLED:-true}
SHORT_ENABLED=${SHORT_ENABLED:-true}

EMA_PERIOD=${EMA_PERIOD:-51}
RSI_PERIOD=${RSI_PERIOD:-14}
RSI_OVERBOUGHT=${RSI_OVERBOUGHT:-65.0}
RSI_OVERSOLD=${RSI_OVERSOLD:-35.0}
ATR_PERIOD=${ATR_PERIOD:-14}
CONSOLIDATION_CANDLES=${CONSOLIDATION_CANDLES:-8}
CONSOLIDATION_ATR_MULT=${CONSOLIDATION_ATR_MULT:-2.2}
SWING_LOOKBACK=${SWING_LOOKBACK:-8}
VOLUME_SMA_PERIOD=${VOLUME_SMA_PERIOD:-20}
VOLUME_MULT=${VOLUME_MULT:-1.0}

COMMISSION_PCT=0.05
SLIPPAGE_TICKS=1
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
    *)
      shift
      ;;
  esac
done

# Build Python execution string
CMD=".venv/bin/python3 src/main.py --symbol $SYMBOL --platform $PLATFORM --timeframe $TIMEFRAME --initial-capital $INITIAL_BALANCE --leverage $LEVERAGE --risk-pct $RISK_PER_TRADE_PCT --max-alloc-pct $MAX_POSITION_ALLOCATION_PCT --rr-ratio $RR_RATIO --commission-pct $COMMISSION_PCT --slippage-ticks $SLIPPAGE_TICKS --execution-mode $EXECUTION_MODE --long-enabled $LONG_ENABLED --short-enabled $SHORT_ENABLED --forward-mode $FORWARD_MODE --ema-period $EMA_PERIOD --rsi-period $RSI_PERIOD --rsi-overbought $RSI_OVERBOUGHT --rsi-oversold $RSI_OVERSOLD --atr-period $ATR_PERIOD --consolidation-candles $CONSOLIDATION_CANDLES --consolidation-atr-mult $CONSOLIDATION_ATR_MULT --swing-lookback $SWING_LOOKBACK --volume-sma-period $VOLUME_SMA_PERIOD --volume-mult $VOLUME_MULT"

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
