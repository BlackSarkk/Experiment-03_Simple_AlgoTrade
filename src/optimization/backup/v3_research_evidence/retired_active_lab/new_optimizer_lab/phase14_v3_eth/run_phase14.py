"""Phase 14 — ETH 15m V3 Optimization Campaign + Parity/TEST evaluation.
"""
import argparse
import contextlib
import io
import json
import os
import sys
import time

ROOT = "/home/rahul/Documents/Claude/Experiment-03_Simple_AlgoTrade"
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "src", "optimization", "recovered_phase3a"))

import pandas as pd
import numpy as np
from strategy.indicators import compute_all_indicators
from backtest.engine import BacktestEngine
from filters.stage_1_bollinger.filter import BollingerFilterConfig, BollingerFilteredStrategy

# Import recovered code for parity check
import campaign_2y_15m as REC
# Import V3 optimizer
from optimization.v3 import optimizer as V3
from optimization.v3 import spec as V3_spec
from optimization.v3 import scoring as V3_scoring

# 1. MarketDataLoader cannot fetch during optimization preflight check
import common.market_data as _md
def block_fetch(*args, **kwargs):
    raise RuntimeError("PHASE14: fetch blocked")
_md.MarketDataLoader.__init__ = block_fetch

HARD = pd.Timestamp("2026-08-17 00:00:00", tz="UTC")
DEV_LO_TS = pd.Timestamp("2024-07-16 00:00:00", tz="UTC")
TEST_LO_TS = pd.Timestamp("2026-06-01 00:00:00", tz="UTC")

TEMPLATE = {
    "strategy": {"long_enabled": True, "short_enabled": False},
    "risk": {
        "sizing_mode": "RISK_BASED", "initial_capital": 10000.0,
        "quantity_step": 0.001, "leverage": 1.0, "risk_per_trade_pct": 1.5,
        "max_position_allocation_pct": 50.0
    },
    "execution": {"commission_pct": 0.05, "slippage_ticks": 1, "tick_size": 0.01}
}
for k in V3_spec.STRATEGY_KEYS:
    TEMPLATE["strategy"][k] = 0

def quiet(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        return fn(*a, **k)

def preset_of(p):
    t = json.loads(json.dumps(TEMPLATE))
    for k in V3_spec.STRATEGY_KEYS:
        t["strategy"][k] = p[k]
    t["risk"]["leverage"] = p["leverage"]
    # V3 specifies risk_per_trade_pct and max_position_allocation_pct as FRACTIONS in spec.py/optimizer.py
    # but the backtest engine expects percent values, so we convert them here.
    t["risk"]["risk_per_trade_pct"] = p["risk_per_trade_pct"] * 100.0
    t["risk"]["max_position_allocation_pct"] = p["max_position_allocation_pct"] * 100.0
    return t

def eval_window(df, cfg, fcfg, lo, hi, ind=None):
    """Shared evaluation logic: indicators computed once on full frame, then sliced by index."""
    ind = compute_all_indicators(df.copy(), cfg.strategy) if ind is None else ind
    frame = ind.iloc[lo:hi].reset_index(drop=True)
    engine = BacktestEngine(cfg)
    # Use SkipHeadStrategy to drop early signals
    strat = V3.SkipHeadStrategy(cfg.strategy, fcfg, V3_spec.EVAL_SKIP_BARS)
    engine.strategy = strat
    return V3.metrics(engine.run(frame), strat.blocked_count, strat.head_dropped), ind

def main():
    print("PHASE 14 — PREFLIGHT CHECKS")
    
    # Read the exact CSV data file
    csv_path = os.path.join(ROOT, "src", "optimization", "new_optimizer_lab", "phase12_parity", "data", "ETHUSDT_15m_warmup_dev_test.csv")
    df = pd.read_csv(csv_path)
    dt = pd.to_datetime(df["datetime"], utc=True)
    
    # Prove boundaries
    warm = int((dt < DEV_LO_TS).sum())
    test_lo = int((dt >= TEST_LO_TS).to_numpy().argmax())
    dev_hi = test_lo
    
    checks = [
        ("zero rows at/after 2026-08-17", int((dt >= HARD).sum()) == 0, int((dt >= HARD).sum())),
        ("warmup rows == 1000", warm == 1000, warm),
        ("DEV rows == 65760", dev_hi - warm == 65760, dev_hi - warm),
        ("TEST rows == 7392", len(df) - test_lo == 7392, len(df) - test_lo),
        ("DEV starts 2024-07-16 00:00", str(dt.iloc[warm]) == "2024-07-16 00:00:00+00:00", str(dt.iloc[warm])),
        ("DEV ends 2026-05-31 23:45", str(dt.iloc[dev_hi - 1]) == "2026-05-31 23:45:00+00:00", str(dt.iloc[dev_hi - 1])),
        ("TEST 2026-06-01..2026-08-16", (str(dt.iloc[test_lo]) == "2026-06-01 00:00:00+00:00" and str(dt.iloc[-1]) == "2026-08-16 23:45:00+00:00"), ""),
    ]
    
    # Separate DF construction for optimizer input (TEST rows physically absent)
    df_dev = df.iloc[:dev_hi].reset_index(drop=True)
    checks.append(("optimization frame excludes TEST", (len(df_dev) == dev_hi and str(pd.to_datetime(df_dev['datetime'], utc=True).iloc[-1]) == "2026-05-31 23:45:00+00:00"), len(df_dev)))
    
    # Verify budgets and seed/n_jobs
    checks.append(("budgets: 1a=400, 1b=800, 1c=200, 2a=300, 2b=150", 
                   (V3_spec.BROAD_TRIALS == 400 and V3_spec.NARROW_TRIALS == 800 and V3_spec.RISK_SEED_TRIALS == 200 and V3_spec.FINAL_TRIALS == 300 and V3_spec.BOLL_TRIALS == 150), 
                   f"1a={V3_spec.BROAD_TRIALS}, 1b={V3_spec.NARROW_TRIALS}, 1c={V3_spec.RISK_SEED_TRIALS}, 2a={V3_spec.FINAL_TRIALS}, 2b={V3_spec.BOLL_TRIALS}"))
    checks.append(("seed = 42, n_jobs = 1", (V3_spec.SEED == 42 and V3_spec.N_JOBS == 1), f"seed={V3_spec.SEED}, n_jobs={V3_spec.N_JOBS}"))
    
    # Parity check on recovered engine vs shared evaluator structure using standard probe
    probe = {
        "ema_period": 104, "rsi_period": 20, "rsi_overbought": 64.0, "rsi_oversold": 23.0,
        "atr_period": 7, "consolidation_candles": 7, "consolidation_atr_mult": 2.8,
        "swing_lookback": 17, "volume_sma_period": 12, "volume_mult": 1.8,
        "risk_reward_ratio": 3.6, "leverage": 4.0, "risk_per_trade_pct": 0.026,
        "max_position_allocation_pct": 0.70
    }
    
    # Build standard V2 config for testing parity of BacktestEngine
    from new_optimizer_v2 import optimizer as V2
    REC.DEV_HI = len(df_dev)
    rec_tr = quiet(REC.run, df_dev, REC.build_cfg(preset_of(probe), probe), REC.OFF, warm, warm + int(len(df_dev) * 0.70))
    # V2 eval_window parity
    v2_cfg = V2.build_cfg("ETHUSDT", "15m", probe)
    # Using V2.OFF and SkipHeadStrategy with V2 skip_bars of V2.RANGES_KEYS. V2 uses default skip calculation.
    # In V2, evaluator uses default backtest strategy. Let's run a custom check comparing V2 and recovered run().
    engine_v2 = BacktestEngine(v2_cfg)
    engine_v2.strategy = BollingerFilteredStrategy(v2_cfg.strategy, V2.OFF)
    ind_v2 = compute_all_indicators(df_dev.copy(), v2_cfg.strategy)
    res_v2 = V2.metrics(engine_v2.run(ind_v2.iloc[warm:warm + int(len(df_dev) * 0.70)].reset_index(drop=True)), 0)
    
    checks.append(("evaluator parity (recovered vs BacktestEngine)", 
                   (rec_tr["trades"] == res_v2["trades"] and abs(rec_tr["return_pct"] - res_v2["return_pct"]) < 1e-6), 
                   f"rec n={rec_tr['trades']} ret={rec_tr['return_pct']:.6f} | v2 n={res_v2['trades']} ret={res_v2['return_pct']:.6f}"))

    # Print results of preflight
    fails = []
    for name, ok, d in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:<45} {d}")
        if not ok:
            fails.append(name)
            
    if fails:
        print(f"\nPREFLIGHT FAIL -> {', '.join(fails)}")
        sys.exit(1)
    else:
        print("\nPREFLIGHT PASS\n")
        
    # 2. Run Campaign
    print("Starting V3 Campaign on DEV data...")
    t_start = time.time()
    
    campaign = V3.Campaign("ETHUSDT", "15m", df_dev, warm)
    
    # Run Stage 1 (1a broad, 1b narrow, 1c risk)
    print("Running Stage 1...")
    seed_meta, s1_dfs, narrow_space = campaign.stage1()
    
    broad_df = s1_dfs["1a_broad"]
    narrow_df = s1_dfs["1b_narrow"]
    risk_df = s1_dfs["1c_risk"]
    
    # Print Stage 1 winners
    gated_broad = broad_df[broad_df.gated].sort_values(["score", "trial"], ascending=[False, True])
    win_1a = gated_broad.iloc[0]
    
    gated_narrow = narrow_df[narrow_df.gated].sort_values(["score", "trial"], ascending=[False, True])
    win_1b = gated_narrow.iloc[0]
    
    gated_risk = risk_df[risk_df.gated].sort_values(["score", "trial"], ascending=[False, True])
    win_1c = gated_risk.iloc[0]
    
    print(f"Stage 1a Broad Winner: Trial {win_1a.trial}, Score {win_1a.score:.4f}")
    print(f"Stage 1b Narrow Winner: Trial {win_1b.trial}, Score {win_1b.score:.4f}")
    print(f"Stage 1c Risk Winner: Trial {win_1c.trial}, Score {win_1c.score:.4f}")
    print(f"Discovered Seed: {seed_meta['seed']}")
    
    # Run Stage 2a
    print("Running Stage 2a...")
    s2a_df, s2a_meta = campaign.stage2_config(seed_meta["seed"])
    win_2a = s2a_meta["params"]
    print(f"Stage 2a Winner: Trial {s2a_meta['trial']}, Score {s2a_meta['score']:.4f}")
    print(f"Params: {win_2a}")
    
    # Run Stage 2b Bollinger
    print("Running Stage 2b...")
    s2b_df, s2b_meta, dev_off_metrics = campaign.stage2_bollinger(win_2a)
    
    if s2b_meta:
        bwin = s2b_meta["cfg"]
        print(f"Stage 2b Bollinger Winner: Trial {s2b_meta['trial']}, Score {s2b_meta['score']:.4f}")
        print(f"Bollinger Config: {bwin.to_dict()}")
    else:
        bwin = V3.OFF
        print("Stage 2b Bollinger Winner: NONE (Bollinger disabled)")
        
    t_end = time.time()
    print(f"Campaign completed in {t_end - t_start:.2f} seconds.")
    
    # Save optimizer output files to target folder
    target_dir = os.path.join(ROOT, "src", "optimization", "new_optimizer_lab", "phase14_v3_eth")
    os.makedirs(target_dir, exist_ok=True)
    
    broad_df.to_csv(os.path.join(target_dir, "v3_stage1a_broad.csv"), index=False)
    narrow_df.to_csv(os.path.join(target_dir, "v3_stage1b_narrow.csv"), index=False)
    risk_df.to_csv(os.path.join(target_dir, "v3_stage1c_risk.csv"), index=False)
    s2a_df.to_csv(os.path.join(target_dir, "v3_stage2a_final.csv"), index=False)
    s2b_df.to_csv(os.path.join(target_dir, "v3_stage2b_bollinger.csv"), index=False)
    
    # Evaluate winners on DEV train and valid (BB OFF vs BB ON)
    cfg_winner = V3.build_cfg("ETHUSDT", "15m", win_2a)
    
    dev_results_off = campaign.evaluate(cfg_winner, V3.OFF)
    dev_results_on = campaign.evaluate(cfg_winner, bwin)
    
    # 3. TEST evaluation (physically once at the very end after campaign matches are frozen)
    print("Running TEST evaluation...")
    test_results_off, _ = eval_window(df, cfg_winner, V3.OFF, test_lo, len(df))
    test_results_on, _ = eval_window(df, cfg_winner, bwin, test_lo, len(df))
    
    # Format and save JSON output
    out_data = {
        "preflight": [dict(check=c[0], pass_ok=bool(c[1]), detail=str(c[2])) for c in checks],
        "stages": {
            "1a_broad": {
                "trial": int(win_1a.trial),
                "score": float(win_1a.score),
                "params": {k: float(win_1a[k]) if V3_spec.STRATEGY_RANGES[k][0] == "float" else int(win_1a[k]) for k in V3_spec.STRATEGY_KEYS}
            },
            "1b_narrow": {
                "trial": int(win_1b.trial),
                "score": float(win_1b.score),
                "params": {k: float(win_1b[k]) if V3_spec.STRATEGY_RANGES[k][0] == "float" else int(win_1b[k]) for k in V3_spec.STRATEGY_KEYS}
            },
            "1c_risk": {
                "trial": int(win_1c.trial),
                "score": float(win_1c.score),
                "params": {k: float(win_1c[k]) for k in V3_spec.RISK_KEYS}
            },
            "seed": seed_meta["seed"],
            "2a_final": {
                "trial": s2a_meta["trial"],
                "score": s2a_meta["score"],
                "params": win_2a
            },
            "2b_boll": {
                "trial": s2b_meta["trial"] if s2b_meta else None,
                "score": s2b_meta["score"] if s2b_meta else None,
                "cfg": bwin.to_dict()
            }
        },
        "dev_metrics": {
            "off": {
                "train": dev_results_off["train"],
                "valid": dev_results_off["valid"]
            },
            "on": {
                "train": dev_results_on["train"],
                "valid": dev_results_on["valid"]
            }
        },
        "test_metrics": {
            "off": test_results_off,
            "on": test_results_on
        }
    }
    
    with open(os.path.join(target_dir, "phase14_results.json"), "w") as f:
        json.dump(out_data, f, indent=2)
        
    print("Phase 14 run completed successfully.")

if __name__ == "__main__":
    main()
