#!/usr/bin/env bash
# ETH Strategy Pipeline — Execution & Strategy Configuration Wrapper Script

set -e

# ==============================================================================
# STRATEGY & PIPELINE CONFIGURATION KNOBS
# ==============================================================================
DEFAULT_CONFIG="configs/default.json"
CONFIG_ARG="$DEFAULT_CONFIG"
CONFIG_SUPPLIED=false

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

KNOWN_FLAGS=(--backtest --forward-test --historical-replay --robustness
             --config --reset --hard-reset --clear-cache --reset-cache
             --clear-cache-only --resume --help)

# ------------------------------------------------------------------
# Usage — single source of truth (printed by --help and by every error path)
# ------------------------------------------------------------------
usage() {
  cat <<'EOF'
ETH Strategy Pipeline — rule-based ETHUSDT.P research pipeline

USAGE
  ./pipeline.sh --config <config-file> <action> [options]
  ./pipeline.sh <maintenance-action>

ACTIONS (exactly one required; each needs --config)
  --backtest             Historical backtest        -> results/backtest/
  --historical-replay    Candle-by-candle replay    -> results/replay/
  --forward-test         Live paper trading (PAPER) -> results/forward/
  --robustness           Robustness suite

MAINTENANCE ACTIONS (run alone, exit without executing a stage)
  --clear-cache          Delete the market-data cache for the active symbol
                         (alias: --reset-cache)
  --clear-cache-only     Delete only the cache, nothing else
  --reset                Delete current runtime results/logs
  --hard-reset           Delete ALL generated results/logs/cache/archives
                         (cannot be combined with an action)

OPTIONS
  --config <file>        Preset to run. Also accepts --config=<file>.
  --resume               Resume forward state (default for --forward-test)
  --help, -h             Show this help and exit

CONFIG RESOLUTION
  --config is tried in this order, first hit wins:
      <arg>  ->  <arg>.json  ->  configs/<arg>  ->  configs/<arg>.json
  So these are equivalent:
      --config config1-ETHUSDTP15m-long.json
      --config configs/config1-ETHUSDTP15m-long.json
      --config config1-ETHUSDTP15m-long

EXAMPLES
  ./pipeline.sh --config config1-ETHUSDTP15m-long.json --backtest
  ./pipeline.sh --config config2-ETHUSDTP15m-long.json --backtest
  ./pipeline.sh --config default.json --forward-test
  ./pipeline.sh --config config1-ETHUSDTP15m-long.json --historical-replay
  ./pipeline.sh --config default.json --backtest --reset --clear-cache
  ./pipeline.sh --hard-reset
EOF
}

list_configs() {
  echo "Available configs:"
  if ls configs/*.json >/dev/null 2>&1; then
    ls -1 configs/*.json | sed 's|^|  |'
  else
    echo "  (none found in configs/)"
  fi
}

# Suggest the closest known flag for a typo (prefix match in either direction).
suggest_flag() {
  local bad="$1" f
  for f in "${KNOWN_FLAGS[@]}"; do
    if [[ "$f" == "$bad"* || "$bad" == "$f"* ]]; then
      echo "$f"; return 0
    fi
  done
  return 1
}

# Parse CLI arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --help|-h)
      usage
      exit 0
      ;;
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
      if [[ $# -lt 2 ]]; then
        echo "ERROR: --config requires a config file argument, but none was given."
        echo ""
        list_configs
        exit 1
      fi
      if [[ "$2" == -* ]]; then
        CONFIG_GUESS=$(printf '%s' "$2" | sed 's/^-*//')
        echo "ERROR: --config requires a config file argument, but got the flag '$2'."
        echo ""
        echo "  You wrote:    --config $2"
        echo "  Did you mean: --config $CONFIG_GUESS"
        echo ""
        echo "  The config file name must not start with '-'."
        echo ""
        list_configs
        exit 1
      fi
      CONFIG_ARG="$2"
      CONFIG_SUPPLIED=true
      shift 2
      ;;
    --config=*)
      CONFIG_ARG="${1#--config=}"
      CONFIG_SUPPLIED=true
      if [ -z "$CONFIG_ARG" ]; then
        echo "ERROR: --config= requires a config file argument (nothing followed the '=')."
        echo ""
        list_configs
        exit 1
      fi
      shift
      ;;
    *)
      echo "ERROR: Unknown option '$1'"
      if SUGGESTION=$(suggest_flag "$1"); then
        echo ""
        echo "  Did you mean '$SUGGESTION'?"
      fi
      echo ""
      usage
      exit 1
      ;;
  esac
done

# ------------------------------------------------------------------
# Decide the action FIRST, so "no action" is reported before anything
# config-related is printed.
# ------------------------------------------------------------------
MAINTENANCE_ONLY=false
if [ "$EXECUTION_SUPPLIED" = false ]; then
  if [ "$HARD_RESET" = true ] || [ "$RESET" = true ] || [ "$CLEAR_CACHE" = true ] || [ "$CLEAR_CACHE_ONLY" = true ]; then
    MAINTENANCE_ONLY=true
  else
    echo "ERROR: No action specified."
    echo ""
    echo "  You must pass exactly one action, e.g. --backtest."
    echo ""
    usage
    exit 1
  fi
fi

if [ "$HARD_RESET" = true ] && [ "$EXECUTION_SUPPLIED" = true ]; then
    echo "ERROR: --hard-reset cannot be combined with an action. Run it separately:"
    echo "         ./pipeline.sh --hard-reset"
    exit 1
fi

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
  echo "       Looked for, in order:"
  for cand in "$CONFIG_ARG" "$CONFIG_ARG.json" "configs/$CONFIG_ARG" "configs/$CONFIG_ARG.json"; do
    echo "         - $cand"
  done
  echo ""
  list_configs
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

if [ "$CONFIG_SUPPLIED" = false ]; then
  echo "NOTE: no --config given; falling back to the default preset."
fi
echo "Config: $CONFIG_PATH"

# ------------------------------------------------------------------
# Build the Python command as an ARRAY (no eval): paths containing spaces or
# shell metacharacters are passed through safely as single arguments.
# ------------------------------------------------------------------
CMD=(.venv/bin/python3 src/main.py
     --config-preset "$CONFIG_PATH"
     --forward-mode "$FORWARD_MODE"
     --execution-mode "$EXECUTION_MODE")

if [ "$HARD_RESET" = true ]; then
  CMD+=(--hard-reset --maintenance-only)
elif [ "$MAINTENANCE_ONLY" = true ]; then
  CMD+=(--maintenance-only)
  if [ "$RESET" = true ]; then CMD+=(--reset); fi
  if [ "$CLEAR_CACHE" = true ] || [ "$CLEAR_CACHE_ONLY" = true ]; then CMD+=(--clear-cache); fi
elif [ "$FORWARD_TEST" = true ]; then
  CMD+=(--forward-test)
  if [ "$RESET" = true ]; then CMD+=(--reset); fi
  if [ "$CLEAR_CACHE" = true ]; then CMD+=(--clear-cache); fi
  if [ "$RESUME_FORWARD_STATE" = true ] && [ "$RESET" = false ]; then CMD+=(--resume); fi
elif [ "$ROBUSTNESS" = true ]; then
  CMD+=(--robustness)
  if [ "$RESET" = true ]; then CMD+=(--reset); fi
  if [ "$CLEAR_CACHE" = true ]; then CMD+=(--clear-cache); fi
else
  CMD+=(--backtest)
  if [ "$RESET" = true ]; then CMD+=(--reset); fi
  if [ "$CLEAR_CACHE" = true ]; then CMD+=(--clear-cache); fi
fi

"${CMD[@]}"
