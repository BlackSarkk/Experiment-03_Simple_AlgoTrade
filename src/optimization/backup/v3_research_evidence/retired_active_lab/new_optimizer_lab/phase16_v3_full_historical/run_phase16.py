"""Phase 16 — V3 Full Historical-Window Campaign.
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
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "src", "optimization", "recovered_phase3a"))

import pandas as pd
import numpy as np
from strategy.indicators import compute_all_indicators
from backtest.engine import BacktestEngine
from filters.stage_1_bollinger.filter import BollingerFilterConfig, BollingerFilteredStrategy

import campaign_2y_15m as REC
from optimization.v3 import optimizer as V3
from optimization.v3 import spec as V3_spec
from optimization.v3 import scoring as V3_scoring
import tools.generate_pine as gp

# 1. Block fetching
import common.market_data as _md
def block_fetch(*args, **kwargs):
    raise RuntimeError("PHASE16: fetch blocked")
_md.MarketDataLoader.__init__ = block_fetch

HARD_LOCKED = pd.Timestamp("2026-07-16 00:00:00", tz="UTC")
DEV_LO_TS = pd.Timestamp("2024-07-16 00:00:00", tz="UTC")
DEV_HI_TS = pd.Timestamp("2026-07-15 23:45:00", tz="UTC")
COMP_HI_TS = pd.Timestamp("2026-08-15 23:45:00", tz="UTC")

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
    t["risk"]["risk_per_trade_pct"] = p["risk_per_trade_pct"] * 100.0
    t["risk"]["max_position_allocation_pct"] = p["max_position_allocation_pct"] * 100.0
    return t

def eval_window(df, cfg, fcfg, lo, hi, ind=None):
    """Shared evaluation logic: indicators computed once on full frame, then sliced by index."""
    ind = compute_all_indicators(df.copy(), cfg.strategy) if ind is None else ind
    frame = ind.iloc[lo:hi].reset_index(drop=True)
    engine = BacktestEngine(cfg)
    strat = V3.SkipHeadStrategy(cfg.strategy, fcfg, V3_spec.EVAL_SKIP_BARS)
    engine.strategy = strat
    return V3.metrics(engine.run(frame), strat.blocked_count, strat.head_dropped), ind

def main():
    print("PHASE 16 — PREFLIGHT CHECKS")
    
    # Read the exact CSV data file
    csv_path = os.path.join(ROOT, "src", "optimization", "new_optimizer_lab", "phase12_parity", "data", "ETHUSDT_15m_warmup_dev_test.csv")
    df = pd.read_csv(csv_path)
    dt = pd.to_datetime(df["datetime"], utc=True)
    
    # Prove boundaries
    warm = int((dt < DEV_LO_TS).sum())
    dev_hi = int((dt <= DEV_HI_TS).sum())
    comp_hi = int((dt <= COMP_HI_TS).sum())
    
    checks = [
        ("warmup rows == 1000", warm == 1000, warm),
        ("DEV rows == 70080", dev_hi - warm == 70080, dev_hi - warm),
        ("DEV starts 2024-07-16 00:00", str(dt.iloc[warm]) == "2024-07-16 00:00:00+00:00", str(dt.iloc[warm])),
        ("DEV ends 2026-07-15 23:45", str(dt.iloc[dev_hi - 1]) == "2026-07-15 23:45:00+00:00", str(dt.iloc[dev_hi - 1])),
        ("70/30 split inside DEV (TRAIN=49056, VALID=21024)", 
         (train_rows := int((dev_hi - warm) * 0.70)) == 49056 and (valid_rows := (dev_hi - warm) - train_rows) == 21024,
         f"TRAIN={train_rows}, VALID={valid_rows}"),
        ("VALID starts 2025-12-09 00:00", str(dt.iloc[warm + train_rows]) == "2025-12-09 00:00:00+00:00", str(dt.iloc[warm + train_rows])),
    ]
    
    # Exclude locked comparison rows from the optimization frame
    df_dev = df.iloc[:dev_hi].reset_index(drop=True)
    checks.append(("optimization frame excludes comparison window", 
                   (len(df_dev) == dev_hi and int((pd.to_datetime(df_dev['datetime'], utc=True) >= HARD_LOCKED).sum()) == 0), 
                   f"dev_frame_len={len(df_dev)}, rows >= HARD_LOCKED = {int((pd.to_datetime(df_dev['datetime'], utc=True) >= HARD_LOCKED).sum())}"))
    
    # Parity check on recovered engine vs shared evaluator structure using standard probe
    probe = {
        "ema_period": 104, "rsi_period": 20, "rsi_overbought": 64.0, "rsi_oversold": 23.0,
        "atr_period": 7, "consolidation_candles": 7, "consolidation_atr_mult": 2.8,
        "swing_lookback": 17, "volume_sma_period": 12, "volume_mult": 1.8,
        "risk_reward_ratio": 3.6, "leverage": 4.0, "risk_per_trade_pct": 0.026,
        "max_position_allocation_pct": 0.70
    }
    
    from new_optimizer_v2 import optimizer as V2
    REC.DEV_HI = len(df_dev)
    rec_tr = quiet(REC.run, df_dev, REC.build_cfg(preset_of(probe), probe), REC.OFF, warm, warm + train_rows)
    v2_cfg = V2.build_cfg("ETHUSDT", "15m", probe)
    engine_v2 = BacktestEngine(v2_cfg)
    engine_v2.strategy = BollingerFilteredStrategy(v2_cfg.strategy, V2.OFF)
    ind_v2 = compute_all_indicators(df_dev.copy(), v2_cfg.strategy)
    res_v2 = V2.metrics(engine_v2.run(ind_v2.iloc[warm:warm + train_rows].reset_index(drop=True)), 0)
    
    checks.append(("evaluator parity (recovered vs BacktestEngine)", 
                   (rec_tr["trades"] == res_v2["trades"] and abs(rec_tr["return_pct"] - res_v2["return_pct"]) < 1e-6), 
                   f"rec n={rec_tr['trades']} ret={rec_tr['return_pct']:.6f} | v2 n={res_v2['trades']} ret={res_v2['return_pct']:.6f}"))

    # Print results of preflight
    fails = []
    for name, ok, d in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:<50} {d}")
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
    target_dir = os.path.join(ROOT, "src", "optimization", "new_optimizer_lab", "phase16_v3_full_historical")
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
    
    # 3. Locked-window comparison evaluation (once only, after freezing)
    print("Running Locked comparison window evaluation...")
    comp_results_off, _ = eval_window(df, cfg_winner, V3.OFF, dev_hi, comp_hi)
    comp_results_on, _ = eval_window(df, cfg_winner, bwin, dev_hi, comp_hi)
    
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
        "locked_metrics": {
            "off": comp_results_off,
            "on": comp_results_on
        }
    }
    
    with open(os.path.join(target_dir, "phase16_results.json"), "w") as f:
        json.dump(out_data, f, indent=2)
        
    print("Phase 16 run completed successfully.")
    
    # 4. Pine Export
    print("Exporting Pine script...")
    pine_path = os.path.join(ROOT, "pine", "v3_fullhistorical_eth15m.pine")
    if os.path.exists(pine_path):
        print(f"FAIL: Target Pine file {pine_path} already exists!")
        sys.exit(1)
        
    # Construct base dictionary matching generate_pine render layout
    # strategy params
    s_cfg = {
        "ema_period": int(win_2a["ema_period"]),
        "rsi_period": int(win_2a["rsi_period"]),
        "rsi_overbought": float(win_2a["rsi_overbought"]),
        "rsi_oversold": float(win_2a["rsi_oversold"]),
        "atr_period": int(win_2a["atr_period"]),
        "consolidation_candles": int(win_2a["consolidation_candles"]),
        "consolidation_atr_mult": float(win_2a["consolidation_atr_mult"]),
        "swing_lookback": int(win_2a["swing_lookback"]),
        "volume_sma_period": int(win_2a["volume_sma_period"]),
        "volume_mult": float(win_2a["volume_mult"]),
        "risk_reward_ratio": float(win_2a["risk_reward_ratio"]),
        "long_enabled": True,
        "short_enabled": False
    }

    # risk params (converted to percentages for pine generator)
    r_cfg = {
        "initial_capital": 10000.0,
        "leverage": float(win_2a["leverage"]),
        "risk_per_trade_pct": float(win_2a["risk_per_trade_pct"]) * 100.0,
        "max_position_allocation_pct": float(win_2a["max_position_allocation_pct"]) * 100.0,
        "quantity_step": 0.001
    }

    # execution params
    e_cfg = {
        "commission_pct": 0.05,
        "slippage_ticks": 1,
        "tick_size": 0.01
    }

    b_cfg = {
        # Default Bollinger state should be OFF per instructions
        "enabled": False,
        "length": int(bwin.length),
        "std": float(bwin.std),
        "min_bandwidth_pct": float(bwin.min_bandwidth_pct),
        "expansion_lookback": int(bwin.expansion_lookback),
        "expansion_min_ratio": float(bwin.expansion_min_ratio),
        "min_mid_distance": float(bwin.min_mid_distance)
    }

    cfg_export = {
        "strategy": s_cfg,
        "risk": r_cfg,
        "filters": {"bollinger": b_cfg},
        "execution": e_cfg,
        "_source": "Phase 16 V3 Full Historical export",
        "_optimizer_architecture": "new_optimizer_v3",
        "_train_start": "2024-07-16 00:00:00+00:00",
        "_validation_end": "2026-07-15 23:45:00+00:00",
        "_reference_metrics": {
            "development_return_pct": dev_results_on["train"]["return_pct"] + dev_results_on["valid"]["return_pct"],
            "development_pf": dev_results_on["valid"]["pf"],
            "development_max_dd_pct": dev_results_on["valid"]["max_dd"],
            "development_trades": dev_results_on["train"]["trades"] + dev_results_on["valid"]["trades"]
        }
    }

    # Render Pine using gp.TEMPLATE.format
    rendered = gp.TEMPLATE.format(
        title="ETHUSDT 15m V3 Full Historical", short="V3-ETH15m-FullHist", cfgfile="v3_fullhistorical_eth15m",
        source=cfg_export["_source"],
        arch=cfg_export["_optimizer_architecture"],
        dev_start="2024-07-16", dev_end="2026-07-15",
        uns_start="2026-07-16", uns_end="2026-08-15",
        capital=int(r_cfg["initial_capital"]), commission=e_cfg["commission_pct"],
        slippage=int(e_cfg["slippage_ticks"]), tick=e_cfg["tick_size"],
        qstep=r_cfg["quantity_step"],
        ema=int(s_cfg["ema_period"]), rsi=int(s_cfg["rsi_period"]),
        ob=round(float(s_cfg["rsi_overbought"]), 1), os=round(float(s_cfg["rsi_oversold"]), 1),
        atr=int(s_cfg["atr_period"]), cons=int(s_cfg["consolidation_candles"]),
        cmult=round(float(s_cfg["consolidation_atr_mult"]), 2),
        swing=int(s_cfg["swing_lookback"]), vsma=int(s_cfg["volume_sma_period"]),
        vmult=round(float(s_cfg["volume_mult"]), 2),
        rr=round(float(s_cfg["risk_reward_ratio"]), 2),
        lev=round(float(r_cfg["leverage"]), 1),
        risk=round(float(r_cfg["risk_per_trade_pct"]), 2),
        alloc=round(float(r_cfg["max_position_allocation_pct"]), 1),
        bb_enabled="false", # Default Bollinger state should be OFF
        bb_len=int(b_cfg["length"]),
        bb_std=round(float(b_cfg["std"]), 2),
        bb_minbw=round(float(b_cfg["min_bandwidth_pct"]), 2),
        bb_explb=int(b_cfg["expansion_lookback"]),
        bb_expratio=round(float(b_cfg["expansion_min_ratio"]), 2),
        bb_middist=round(float(b_cfg["min_mid_distance"]), 2),
        ref_dev_ret=gp._fmt(cfg_export["_reference_metrics"].get("development_return_pct")),
        ref_dev_pf=gp._fmt(cfg_export["_reference_metrics"].get("development_pf")),
        ref_dev_dd=gp._fmt(cfg_export["_reference_metrics"].get("development_max_dd_pct")),
        ref_dev_n=gp._fmt(cfg_export["_reference_metrics"].get("development_trades")),
        ref_uoff_ret=gp._fmt(comp_results_off.get("return_pct") if comp_results_off else None),
        ref_uoff_pf=gp._fmt(comp_results_off.get("pf") if comp_results_off else None),
        ref_uoff_dd=gp._fmt(comp_results_off.get("max_dd") if comp_results_off else None),
        ref_uoff_n=gp._fmt(comp_results_off.get("trades") if comp_results_off else None),
        ref_uon_ret=gp._fmt(comp_results_on.get("return_pct") if comp_results_on else None),
        ref_uon_pf=gp._fmt(comp_results_on.get("pf") if comp_results_on else None),
        ref_uon_dd=gp._fmt(comp_results_on.get("max_dd") if comp_results_on else None),
        ref_uon_n=gp._fmt(comp_results_on.get("trades") if comp_results_on else None),
    )

    # Check long-only in the generated string
    assert "enable_long  = input.bool(true" in rendered
    assert "enable_short = input.bool(false" in rendered
    assert "short_signal =" not in rendered or "enable_short and" in rendered
    
    with open(pine_path, "w") as f:
        f.write(rendered)
        
    print(f"Pine script written to {pine_path}")

if __name__ == "__main__":
    main()
