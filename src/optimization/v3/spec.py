"""V3 declarations — stdlib only.

Imported by the plan-only CLI, so this module must never import pandas, numpy, optuna,
or anything under common/ strategy/ backtest/. Keep it dependency-free.
"""

VERSION = "v3-seed-then-config-1.0"

# ---------------------------------------------------------------- budgets
BROAD_TRIALS = 400          # stage 1a: 11 strategy dims, neutral risk
NARROW_TRIALS = 800         # stage 1b: 11 strategy dims, narrowed ranges
RISK_SEED_TRIALS = 200      # stage 1c: strategy frozen, 3 risk dims
FINAL_TRIALS = 300          # stage 2a: 14 dims jointly, seed enqueued as trial 0
BOLL_TRIALS = 150           # stage 2b: 6 Bollinger dims, strategy+risk frozen

SEED = 42
N_JOBS = 1
INITIAL_CAPITAL = 10000.0
TRAIN_FRAC = 0.70

# ---------------------------------------------------------------- direction
LONG_ENABLED = True
SHORT_ENABLED = False       # hardcoded; never a search dimension

# ---------------------------------------------------------------- neutral risk (stage 1a/1b)
NEUTRAL_RISK = {"leverage": 1.0, "risk_per_trade_pct": 0.015,
                "max_position_allocation_pct": 0.50}

# ---------------------------------------------------------------- execution
COMMISSION_PCT = 0.05       # taker, charged on entry and exit notional
SLIPPAGE_TICKS = 1          # always adverse
QUANTITY_STEP = 0.001
TICK_SIZE = {"ETHUSDT": 0.01, "BTCUSDT": 0.1}   # per symbol; unknown symbol raises

# ---------------------------------------------------------------- search space (11 + 3)
STRATEGY_RANGES = {
    "ema_period":             ("int",   20,   150,  1),
    "rsi_period":             ("int",    7,    21,  1),
    "rsi_overbought":         ("float", 55.0, 80.0, 1.0),
    "rsi_oversold":           ("float", 20.0, 45.0, 1.0),
    "atr_period":             ("int",    7,    21,  1),
    "consolidation_candles":  ("int",    4,    20,  1),
    "consolidation_atr_mult": ("float",  1.0,  4.0, 0.1),
    "swing_lookback":         ("int",    4,    20,  1),
    "volume_sma_period":      ("int",   10,    50,  1),
    "volume_mult":            ("float",  0.5,  2.0, 0.1),
    "risk_reward_ratio":      ("float",  1.0,  4.0, 0.1),
}
RISK_RANGES = {
    "leverage":                    ("float", 1.0,   5.0,   0.5),
    "risk_per_trade_pct":          ("float", 0.005, 0.030, 0.001),   # FRACTION
    "max_position_allocation_pct": ("float", 0.25,  0.75,  0.05),    # FRACTION
}
STRATEGY_KEYS = tuple(STRATEGY_RANGES)
RISK_KEYS = tuple(RISK_RANGES)
ALL_KEYS = STRATEGY_KEYS + RISK_KEYS

BOLLINGER_RANGES = {
    "length":              ("int",   10,   50,   1),
    "std":                 ("float", 1.5,  3.0,  0.1),
    "min_bandwidth_pct":   ("float", 0.0,  6.0,  0.1),
    "expansion_lookback":  ("int",    2,   20,   1),
    "expansion_min_ratio": ("float", 0.0,  1.6,  0.05),
    "min_mid_distance":    ("float", 0.0,  0.45, 0.01),
}

# ---------------------------------------------------------------- evaluation window
# The frozen BaselineStrategy skips max(ema_period + 10, 60) leading bars of whatever frame
# it is handed. That makes the evaluable window EMA-dependent, so candidates would be ranked
# over different amounts of data. V3 drops every signal below a FIXED index instead, chosen
# above the largest possible strategy skip, so every candidate is scored on identical rows.
EVAL_SKIP_BARS = 170        # > max(ema_max + 10, 60) = 160 for ema_max = 150

# ---------------------------------------------------------------- narrowing rule (stage 1b)
NARROW_TOP_FRACTION = 0.15  # of gated broad trials, by score
NARROW_MIN_CANDIDATES = 20
NARROW_WIDEN_STEPS = 1      # widen the observed [min, max] by one step per side, then clip

# ---------------------------------------------------------------- gates
# Minimum trade counts scale with partition length so the same rule fits any window.
MIN_TRADES_PER_ROWS = 500   # one trade per 500 candles
MIN_TRADES_FLOOR = 30
GATE = {"valid_return_pct_gt": 0.0, "train_return_pct_gt": 0.0,
        "valid_profit_factor_ge": 1.05, "valid_max_dd_pct_le": 35.0}

# ---------------------------------------------------------------- score weights
SCORE_VERSION = "v3_score_v1"
W = {"va_ret": 0.30, "va_pf": 0.25, "va_dd": 0.20, "va_sample": 0.10,
     "tr_ret": 0.10, "tr_pf": 0.05, "consistency": 0.15}
CAPS = {"va_ret_cap_pct": 100.0, "va_pf_cap": 2.0, "va_dd_free_pct": 15.0,
        "va_dd_span_pct": 35.0, "va_sample_target_x": 3.0,
        "tr_ret_cap_pct": 100.0, "tr_pf_cap": 2.0}
FAIL_BASE = -1.0            # graded failures live in [-2, -1]; passes live above -1
FAIL_SPAN = 1.0

BOLL_SCORE_VERSION = "v3_boll_score_v1"
BW = {"va_pf": 0.25, "va_netpnl": 0.20, "va_dd": 0.15,
      "tr_pf": 0.15, "tr_netpnl": 0.15, "tr_dd": 0.10}
BOLL_MIN_TRADE_RETENTION = 0.40   # of the unfiltered VALID trade count

DATA_CONTRACT = (
    "The caller supplies ONE frame containing exactly [warmup rows][DEV rows] for ONE symbol "
    "and ONE timeframe. V3 asserts that no holdout/test partition is present: it splits DEV "
    "70/30 into TRAIN/VALID and can address nothing else. Indicators are computed once per "
    "candidate on the FULL warmup+DEV frame and sliced by index, so every evaluated partition "
    "has complete warmup. There is no unlock path and no unseen partition to leak."
)
