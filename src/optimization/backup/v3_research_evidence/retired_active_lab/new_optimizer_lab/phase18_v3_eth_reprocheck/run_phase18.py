"""Phase 18 — V3 ETH Phase-16 Reproduction Verification.

Independently reproduce the completed ETH Phase-16 campaign to verify that V3/harness
can recover expected fingerprints exactly. This is NOT a full historical run; it uses
Phase-16 data boundaries only.

Expected results from known Phase-16 run:
  Stage 1a: trial 324, score 0.3820
  Stage 1b: trial 457, score 0.4179
  Stage 1c: trial 47, score 0.6736, risk 3.5x / 3.0% / 65%
  Stage 2a: trial 0 (seed enqueued), score 0.6736
  Bollinger: trial 143, score 0.1693, params 11/3.0/1.1/17/0.1/0.05

  Locked-window (BB OFF):  -0.19% ret, 0.985 PF, 10.99% DD, 10 trades, -$18.55
  Locked-window (BB ON):   +2.64% ret, 1.412 PF, 7.36% DD, 5 trades, +$264.46
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
    raise RuntimeError("PHASE18: fetch blocked")
_md.MarketDataLoader.__init__ = block_fetch

# Phase-16 exact boundaries
HARD_LOCKED = pd.Timestamp("2026-07-16 00:00:00", tz="UTC")
DEV_LO_TS = pd.Timestamp("2024-07-16 00:00:00", tz="UTC")
DEV_HI_TS = pd.Timestamp("2026-07-15 23:45:00", tz="UTC")
COMP_HI_TS = pd.Timestamp("2026-08-15 23:45:00", tz="UTC")

# Expected Phase-16 fingerprints
EXPECTED = {
    "stage_1a": {"trial": 324, "score": 0.3820},
    "stage_1b": {"trial": 457, "score": 0.4179},
    "stage_1c": {"trial": 47, "score": 0.6736, "risk": (3.5, 0.030, 0.65)},
    "stage_2a": {"trial": 0, "score": 0.6736},
    "bollinger": {"trial": 143, "score": 0.1693, "params": (11, 3.0, 1.1, 17, 0.1, 0.05)},
    "locked_off": {
        "return_pct": -0.19, "pf": 0.985, "max_dd": 10.99,
        "trades": 10, "net_pnl": -18.55
    },
    "locked_on": {
        "return_pct": 2.64, "pf": 1.412, "max_dd": 7.36,
        "trades": 5, "net_pnl": 264.46
    }
}

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

def check_match(name, got, expected, tolerance=1e-2):
    """Check if got value matches expected within tolerance."""
    if isinstance(expected, (int, float)):
        diff = abs(got - expected)
        ok = diff <= tolerance
        return ok, f"{got:.4f} (expected {expected:.4f}, diff {diff:.4f})"
    return got == expected, f"{got} (expected {expected})"

def main():
    print("=" * 88)
    print("PHASE 18 — V3 ETH PHASE-16 REPRODUCTION VERIFICATION")
    print("=" * 88)

    # Read data
    csv_path = os.path.join(ROOT, "src", "optimization", "new_optimizer_lab", "phase12_parity",
                            "data", "ETHUSDT_15m_warmup_dev_test.csv")
    if not os.path.exists(csv_path):
        print(f"FATAL: data file not found at {csv_path}")
        sys.exit(1)

    df = pd.read_csv(csv_path)
    dt = pd.to_datetime(df["datetime"], utc=True)

    # PREFLIGHT CHECKS
    print("\nPREFLIGHT CHECKS")
    warm = int((dt < DEV_LO_TS).sum())
    dev_hi = int((dt <= DEV_HI_TS).sum())
    comp_hi = int((dt <= COMP_HI_TS).sum())

    checks = [
        ("warmup rows == 1000", warm == 1000, warm),
        ("DEV rows == 70080", dev_hi - warm == 70080, dev_hi - warm),
        ("DEV starts 2024-07-16 00:00", str(dt.iloc[warm]) == "2024-07-16 00:00:00+00:00", str(dt.iloc[warm])),
        ("DEV ends 2026-07-15 23:45", str(dt.iloc[dev_hi - 1]) == "2026-07-15 23:45:00+00:00", str(dt.iloc[dev_hi - 1])),
    ]

    train_rows = int((dev_hi - warm) * 0.70)
    valid_rows = (dev_hi - warm) - train_rows
    checks.append(("70/30 split inside DEV (TRAIN=49056, VALID=21024)",
                   train_rows == 49056 and valid_rows == 21024,
                   f"TRAIN={train_rows}, VALID={valid_rows}"))
    checks.append(("VALID starts 2025-12-09 00:00",
                   str(dt.iloc[warm + train_rows]) == "2025-12-09 00:00:00+00:00",
                   str(dt.iloc[warm + train_rows])))

    # Exclude locked comparison rows from optimization frame
    df_dev = df.iloc[:dev_hi].reset_index(drop=True)
    locked_count = int((pd.to_datetime(df_dev['datetime'], utc=True) >= HARD_LOCKED).sum())
    checks.append(("optimization frame excludes locked window",
                   locked_count == 0,
                   f"rows >= HARD_LOCKED = {locked_count}"))

    # Config checks
    checks.append(("budgets: 1a=400, 1b=800, 1c=200, 2a=300, 2b=150",
                   (V3_spec.BROAD_TRIALS == 400 and V3_spec.NARROW_TRIALS == 800 and
                    V3_spec.RISK_SEED_TRIALS == 200 and V3_spec.FINAL_TRIALS == 300 and
                    V3_spec.BOLL_TRIALS == 150),
                   f"1a={V3_spec.BROAD_TRIALS}, 1b={V3_spec.NARROW_TRIALS}, "
                   f"1c={V3_spec.RISK_SEED_TRIALS}, 2a={V3_spec.FINAL_TRIALS}, "
                   f"2b={V3_spec.BOLL_TRIALS}"))

    checks.append(("seed = 42, n_jobs = 1",
                   V3_spec.SEED == 42 and V3_spec.N_JOBS == 1,
                   f"seed={V3_spec.SEED}, n_jobs={V3_spec.N_JOBS}"))

    checks.append(("long_only (long_enabled=True, short_enabled=False)",
                   V3_spec.LONG_ENABLED and not V3_spec.SHORT_ENABLED,
                   f"long={V3_spec.LONG_ENABLED}, short={V3_spec.SHORT_ENABLED}"))

    # Parity check: recovered vs BacktestEngine
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
                   (rec_tr["trades"] == res_v2["trades"] and
                    abs(rec_tr["return_pct"] - res_v2["return_pct"]) < 1e-6),
                   f"rec n={rec_tr['trades']} ret={rec_tr['return_pct']:.6f} | "
                   f"v2 n={res_v2['trades']} ret={res_v2['return_pct']:.6f}"))

    # Print preflight results
    fails = []
    for name, ok, detail in checks:
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] {name:<50} {detail}")
        if not ok:
            fails.append(name)

    if fails:
        print(f"\nPREFLIGHT FAIL: {', '.join(fails)}")
        return 1

    print("\nPREFLIGHT PASS\n")

    # RUN CAMPAIGN
    print("=" * 88)
    print("CAMPAIGN EXECUTION")
    print("=" * 88)
    t_start = time.time()

    campaign = V3.Campaign("ETHUSDT", "15m", df_dev, warm)

    print("\nStage 1 (1a broad, 1b narrow, 1c risk)...")
    seed_meta, s1_dfs, narrow_space = campaign.stage1()

    broad_df = s1_dfs["1a_broad"]
    narrow_df = s1_dfs["1b_narrow"]
    risk_df = s1_dfs["1c_risk"]

    # Get stage 1 winners
    gated_broad = broad_df[broad_df.gated].sort_values(["score", "trial"], ascending=[False, True])
    win_1a = gated_broad.iloc[0]

    gated_narrow = narrow_df[narrow_df.gated].sort_values(["score", "trial"], ascending=[False, True])
    win_1b = gated_narrow.iloc[0]

    gated_risk = risk_df[risk_df.gated].sort_values(["score", "trial"], ascending=[False, True])
    win_1c = gated_risk.iloc[0]

    print(f"  1a: trial {int(win_1a.trial)}, score {win_1a.score:.4f}")
    print(f"  1b: trial {int(win_1b.trial)}, score {win_1b.score:.4f}")
    print(f"  1c: trial {int(win_1c.trial)}, score {win_1c.score:.4f}")

    print("\nStage 2a (seeded final config)...")
    s2a_df, s2a_meta = campaign.stage2_config(seed_meta["seed"])
    win_2a = s2a_meta["params"]
    print(f"  2a: trial {s2a_meta['trial']}, score {s2a_meta['score']:.4f}")

    print("\nStage 2b (Bollinger)...")
    s2b_df, s2b_meta, dev_off_metrics = campaign.stage2_bollinger(win_2a)

    if s2b_meta:
        bwin = s2b_meta["cfg"]
        print(f"  2b: trial {s2b_meta['trial']}, score {s2b_meta['score']:.4f}")
    else:
        bwin = V3.OFF
        print(f"  2b: NO WINNER (Bollinger disabled)")

    t_elapsed = time.time() - t_start
    print(f"\nCampaign completed in {t_elapsed:.1f}s")

    # COMPARISON
    print("\n" + "=" * 88)
    print("REPRODUCTION VERIFICATION")
    print("=" * 88)

    mismatches = []

    # Stage 1a
    print(f"\nStage 1a: trial {int(win_1a.trial)} vs expected {EXPECTED['stage_1a']['trial']}")
    ok, detail = check_match("score", win_1a.score, EXPECTED['stage_1a']['score'], tolerance=0.001)
    if int(win_1a.trial) != EXPECTED['stage_1a']['trial']:
        mismatches.append(f"Stage 1a trial: got {int(win_1a.trial)}, expected {EXPECTED['stage_1a']['trial']}")
    if not ok:
        mismatches.append(f"Stage 1a score: {detail}")
    print(f"  trial: {int(win_1a.trial)}, score: {win_1a.score:.4f} {detail}")

    # Stage 1b
    print(f"\nStage 1b: trial {int(win_1b.trial)} vs expected {EXPECTED['stage_1b']['trial']}")
    ok, detail = check_match("score", win_1b.score, EXPECTED['stage_1b']['score'], tolerance=0.001)
    if int(win_1b.trial) != EXPECTED['stage_1b']['trial']:
        mismatches.append(f"Stage 1b trial: got {int(win_1b.trial)}, expected {EXPECTED['stage_1b']['trial']}")
    if not ok:
        mismatches.append(f"Stage 1b score: {detail}")
    print(f"  trial: {int(win_1b.trial)}, score: {win_1b.score:.4f} {detail}")

    # Stage 1c
    print(f"\nStage 1c: trial {int(win_1c.trial)} vs expected {EXPECTED['stage_1c']['trial']}")
    ok_score, detail_score = check_match("score", win_1c.score, EXPECTED['stage_1c']['score'], tolerance=0.001)
    lev = win_1c["leverage"]
    risk = win_1c["risk_per_trade_pct"]
    alloc = win_1c["max_position_allocation_pct"]
    ok_risk = (abs(lev - 3.5) < 0.1 and abs(risk - 0.03) < 0.001 and abs(alloc - 0.65) < 0.01)
    if int(win_1c.trial) != EXPECTED['stage_1c']['trial']:
        mismatches.append(f"Stage 1c trial: got {int(win_1c.trial)}, expected {EXPECTED['stage_1c']['trial']}")
    if not ok_score:
        mismatches.append(f"Stage 1c score: {detail_score}")
    if not ok_risk:
        mismatches.append(f"Stage 1c risk: got {lev:.1f}x/{risk:.3f}/{alloc:.2f}, expected 3.5x/0.030/0.65")
    print(f"  trial: {int(win_1c.trial)}, score: {win_1c.score:.4f} {detail_score}")
    print(f"  risk: {lev:.1f}x / {risk:.3f} / {alloc:.2f} ({'MATCH' if ok_risk else 'MISMATCH'})")

    # Stage 2a
    print(f"\nStage 2a: trial {s2a_meta['trial']} vs expected {EXPECTED['stage_2a']['trial']}")
    ok_score, detail_score = check_match("score", s2a_meta['score'], EXPECTED['stage_2a']['score'], tolerance=0.001)
    if s2a_meta['trial'] != EXPECTED['stage_2a']['trial']:
        mismatches.append(f"Stage 2a trial: got {s2a_meta['trial']}, expected {EXPECTED['stage_2a']['trial']}")
    if not ok_score:
        mismatches.append(f"Stage 2a score: {detail_score}")
    print(f"  trial: {s2a_meta['trial']}, score: {s2a_meta['score']:.4f} {detail_score}")

    # Stage 2b Bollinger
    if s2b_meta:
        print(f"\nStage 2b: trial {s2b_meta['trial']} vs expected {EXPECTED['bollinger']['trial']}")
        ok_score, detail_score = check_match("score", s2b_meta['score'], EXPECTED['bollinger']['score'], tolerance=0.001)
        cfg = s2b_meta["cfg"]
        got_params = (cfg.length, cfg.std, cfg.min_bandwidth_pct, cfg.expansion_lookback,
                      cfg.expansion_min_ratio, cfg.min_mid_distance)
        exp_params = EXPECTED['bollinger']['params']
        ok_params = all(abs(g - e) < 0.1 for g, e in zip(got_params, exp_params))
        if s2b_meta['trial'] != EXPECTED['bollinger']['trial']:
            mismatches.append(f"Stage 2b trial: got {s2b_meta['trial']}, expected {EXPECTED['bollinger']['trial']}")
        if not ok_score:
            mismatches.append(f"Stage 2b score: {detail_score}")
        if not ok_params:
            mismatches.append(f"Stage 2b params: got {got_params}, expected {exp_params}")
        print(f"  trial: {s2b_meta['trial']}, score: {s2b_meta['score']:.4f} {detail_score}")
        print(f"  params: {got_params} ({'MATCH' if ok_params else 'MISMATCH'})")
    else:
        mismatches.append("Stage 2b: no winner (Bollinger disabled)")
        print(f"\nStage 2b: NO WINNER (Bollinger disabled)")

    # Locked-window evaluation
    print("\n" + "-" * 88)
    print("LOCKED-WINDOW COMPARISON (2026-07-16 00:00 to 2026-08-15 23:45)")
    print("-" * 88)

    cfg_winner = V3.build_cfg("ETHUSDT", "15m", win_2a)
    comp_results_off, _ = eval_window(df, cfg_winner, V3.OFF, dev_hi, comp_hi)
    comp_results_on, _ = eval_window(df, cfg_winner, bwin, dev_hi, comp_hi)

    print("\nBB OFF:")
    print(f"  return: {comp_results_off['return_pct']:+.2f}% (expected {EXPECTED['locked_off']['return_pct']:+.2f}%)")
    print(f"  PF:     {comp_results_off['pf']:.3f} (expected {EXPECTED['locked_off']['pf']:.3f})")
    print(f"  DD:     {comp_results_off['max_dd']:.2f}% (expected {EXPECTED['locked_off']['max_dd']:.2f}%)")
    print(f"  trades: {comp_results_off['trades']} (expected {EXPECTED['locked_off']['trades']})")
    print(f"  net:    ${comp_results_off['net_pnl']:+.2f} (expected ${EXPECTED['locked_off']['net_pnl']:+.2f})")

    off_ret_ok = abs(comp_results_off['return_pct'] - EXPECTED['locked_off']['return_pct']) < 0.5
    off_pf_ok = abs(comp_results_off['pf'] - EXPECTED['locked_off']['pf']) < 0.05
    off_dd_ok = abs(comp_results_off['max_dd'] - EXPECTED['locked_off']['max_dd']) < 0.5
    off_trades_ok = comp_results_off['trades'] == EXPECTED['locked_off']['trades']
    off_net_ok = abs(comp_results_off['net_pnl'] - EXPECTED['locked_off']['net_pnl']) < 50

    if not all([off_ret_ok, off_pf_ok, off_dd_ok, off_trades_ok, off_net_ok]):
        if not off_ret_ok:
            mismatches.append(f"Locked BB OFF return: {comp_results_off['return_pct']:.2f}% vs {EXPECTED['locked_off']['return_pct']:.2f}%")
        if not off_pf_ok:
            mismatches.append(f"Locked BB OFF PF: {comp_results_off['pf']:.3f} vs {EXPECTED['locked_off']['pf']:.3f}")
        if not off_dd_ok:
            mismatches.append(f"Locked BB OFF DD: {comp_results_off['max_dd']:.2f}% vs {EXPECTED['locked_off']['max_dd']:.2f}%")
        if not off_trades_ok:
            mismatches.append(f"Locked BB OFF trades: {comp_results_off['trades']} vs {EXPECTED['locked_off']['trades']}")
        if not off_net_ok:
            mismatches.append(f"Locked BB OFF net: ${comp_results_off['net_pnl']:.2f} vs ${EXPECTED['locked_off']['net_pnl']:.2f}")

    print("\nBB ON:")
    print(f"  return: {comp_results_on['return_pct']:+.2f}% (expected {EXPECTED['locked_on']['return_pct']:+.2f}%)")
    print(f"  PF:     {comp_results_on['pf']:.3f} (expected {EXPECTED['locked_on']['pf']:.3f})")
    print(f"  DD:     {comp_results_on['max_dd']:.2f}% (expected {EXPECTED['locked_on']['max_dd']:.2f}%)")
    print(f"  trades: {comp_results_on['trades']} (expected {EXPECTED['locked_on']['trades']})")
    print(f"  net:    ${comp_results_on['net_pnl']:+.2f} (expected ${EXPECTED['locked_on']['net_pnl']:+.2f})")

    on_ret_ok = abs(comp_results_on['return_pct'] - EXPECTED['locked_on']['return_pct']) < 0.5
    on_pf_ok = abs(comp_results_on['pf'] - EXPECTED['locked_on']['pf']) < 0.05
    on_dd_ok = abs(comp_results_on['max_dd'] - EXPECTED['locked_on']['max_dd']) < 0.5
    on_trades_ok = comp_results_on['trades'] == EXPECTED['locked_on']['trades']
    on_net_ok = abs(comp_results_on['net_pnl'] - EXPECTED['locked_on']['net_pnl']) < 50

    if not all([on_ret_ok, on_pf_ok, on_dd_ok, on_trades_ok, on_net_ok]):
        if not on_ret_ok:
            mismatches.append(f"Locked BB ON return: {comp_results_on['return_pct']:.2f}% vs {EXPECTED['locked_on']['return_pct']:.2f}%")
        if not on_pf_ok:
            mismatches.append(f"Locked BB ON PF: {comp_results_on['pf']:.3f} vs {EXPECTED['locked_on']['pf']:.3f}")
        if not on_dd_ok:
            mismatches.append(f"Locked BB ON DD: {comp_results_on['max_dd']:.2f}% vs {EXPECTED['locked_on']['max_dd']:.2f}%")
        if not on_trades_ok:
            mismatches.append(f"Locked BB ON trades: {comp_results_on['trades']} vs {EXPECTED['locked_on']['trades']}")
        if not on_net_ok:
            mismatches.append(f"Locked BB ON net: ${comp_results_on['net_pnl']:.2f} vs ${EXPECTED['locked_on']['net_pnl']:.2f}")

    # Save results
    print("\n" + "=" * 88)
    print("RESULTS SUMMARY")
    print("=" * 88)

    target_dir = HERE
    os.makedirs(target_dir, exist_ok=True)

    broad_df.to_csv(os.path.join(target_dir, "v3_stage1a_broad.csv"), index=False)
    narrow_df.to_csv(os.path.join(target_dir, "v3_stage1b_narrow.csv"), index=False)
    risk_df.to_csv(os.path.join(target_dir, "v3_stage1c_risk.csv"), index=False)
    s2a_df.to_csv(os.path.join(target_dir, "v3_stage2a_final.csv"), index=False)
    s2b_df.to_csv(os.path.join(target_dir, "v3_stage2b_bollinger.csv"), index=False)

    out_data = {
        "preflight": [dict(check=c[0], pass_ok=bool(c[1]), detail=str(c[2])) for c in checks],
        "stages": {
            "1a_broad": {"trial": int(win_1a.trial), "score": float(win_1a.score),
                         "expected_trial": EXPECTED['stage_1a']['trial'], "expected_score": EXPECTED['stage_1a']['score']},
            "1b_narrow": {"trial": int(win_1b.trial), "score": float(win_1b.score),
                          "expected_trial": EXPECTED['stage_1b']['trial'], "expected_score": EXPECTED['stage_1b']['score']},
            "1c_risk": {"trial": int(win_1c.trial), "score": float(win_1c.score),
                        "expected_trial": EXPECTED['stage_1c']['trial'], "expected_score": EXPECTED['stage_1c']['score']},
            "2a_final": {"trial": s2a_meta['trial'], "score": s2a_meta['score'],
                         "expected_trial": EXPECTED['stage_2a']['trial'], "expected_score": EXPECTED['stage_2a']['score']},
            "2b_boll": {"trial": s2b_meta['trial'] if s2b_meta else None, "score": s2b_meta['score'] if s2b_meta else None,
                        "expected_trial": EXPECTED['bollinger']['trial'], "expected_score": EXPECTED['bollinger']['score']}
        },
        "locked_metrics": {
            "off": {k: float(v) for k, v in comp_results_off.items()},
            "on": {k: float(v) for k, v in comp_results_on.items()}
        },
        "expected_locked": {
            "off": EXPECTED['locked_off'],
            "on": EXPECTED['locked_on']
        },
        "mismatches": mismatches
    }

    with open(os.path.join(target_dir, "phase18_results.json"), "w") as f:
        json.dump(out_data, f, indent=2)

    if mismatches:
        print(f"\nMISMATCHES FOUND: {len(mismatches)}")
        for i, m in enumerate(mismatches, 1):
            print(f"  {i}. {m}")
        print("\nFIRST MISMATCH:")
        print(f"  {mismatches[0]}")
        return 1
    else:
        print("\nALL STAGE WINNERS AND LOCKED-WINDOW METRICS MATCH EXPECTED FINGERPRINTS")
        print("SAFE TO PROCEED TO BTC CAMPAIGN")
        return 0

if __name__ == "__main__":
    sys.exit(main())
