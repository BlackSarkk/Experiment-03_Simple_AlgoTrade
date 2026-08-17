"""Phase 19 — post-migration V3 reproduction test.

Reruns the exact Phase-16 ETH campaign using imports from `optimization.v3` (the canonical
path after the move) and compares every frozen selection against the Phase-16 ledger on
disk. No fetch, no Pine, no V3 modification.
"""
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

import pandas as pd
import numpy as np
import optuna as _optuna

# ---------------------------------------------------------------- environment gate
_EXPECT = {"python": "3.12.3", "numpy": "2.5.2", "optuna": "4.9.0", "pandas": "3.0.5"}
_ACTUAL = {"python": sys.version.split()[0], "numpy": np.__version__,
           "optuna": _optuna.__version__, "pandas": pd.__version__}
print("PHASE 19 — ENVIRONMENT")
print(f"  interpreter {sys.executable}")
for k, want in _EXPECT.items():
    print(f"  [{'PASS' if _ACTUAL[k] == want else 'FAIL'}] {k:<8} {_ACTUAL[k]} (require {want})")
if _ACTUAL != _EXPECT:
    print("FAIL — environment mismatch: " +
          ", ".join(f"{k}={_ACTUAL[k]} != {v}" for k, v in _EXPECT.items() if _ACTUAL[k] != v))
    sys.exit(1)

# The retired path must not be importable-by-use here.
assert "optimization.new_optimizer_v3" not in sys.modules
from optimization.v3 import optimizer as V3
from optimization.v3 import spec as V3_spec
from strategy.indicators import compute_all_indicators
from backtest.engine import BacktestEngine

for _m in ("optimization.v3", "optimization.v3.optimizer", "optimization.v3.spec"):
    assert _m in sys.modules, _m
assert not any(m.startswith("optimization.new_optimizer_v3") for m in sys.modules), \
    "FAIL — retired module path was imported"

if V3_spec.SEED != 42 or V3_spec.N_JOBS != 1:
    print(f"FAIL — seed/n_jobs mismatch: seed={V3_spec.SEED}, n_jobs={V3_spec.N_JOBS}")
    sys.exit(1)
print(f"  [PASS] TPE seed {V3_spec.SEED}, n_jobs {V3_spec.N_JOBS}")
print(f"  [PASS] V3 imported from {os.path.dirname(V3.__file__)}")

# ---------------------------------------------------------------- no fetching
import common.market_data as _md
def _block(*a, **k):
    raise RuntimeError("PHASE19: fetch blocked")
_md.MarketDataLoader.__init__ = _block

HARD_LOCKED = pd.Timestamp("2026-07-16 00:00:00", tz="UTC")
DEV_LO_TS = pd.Timestamp("2024-07-16 00:00:00", tz="UTC")
DEV_HI_TS = pd.Timestamp("2026-07-15 23:45:00", tz="UTC")
COMP_HI_TS = pd.Timestamp("2026-08-15 23:45:00", tz="UTC")

P16_DIR = os.path.join(ROOT, "src", "optimization", "new_optimizer_lab",
                       "phase16_v3_full_historical")
CSV = os.path.join(ROOT, "src", "optimization", "new_optimizer_lab", "phase12_parity",
                   "data", "ETHUSDT_15m_warmup_dev_test.csv")

TOL = 1e-9


def eval_window(df, cfg, fcfg, lo, hi):
    ind = compute_all_indicators(df.copy(), cfg.strategy)
    frame = ind.iloc[lo:hi].reset_index(drop=True)
    engine = BacktestEngine(cfg)
    strat = V3.SkipHeadStrategy(cfg.strategy, fcfg, V3_spec.EVAL_SKIP_BARS)
    engine.strategy = strat
    return V3.metrics(engine.run(frame), strat.blocked_count, strat.head_dropped)


def fail(msg):
    print(f"\nFAIL — {msg}")
    sys.exit(2)


def main():
    df = pd.read_csv(CSV)
    dt = pd.to_datetime(df["datetime"], utc=True)
    warm = int((dt < DEV_LO_TS).sum())
    dev_hi = int((dt <= DEV_HI_TS).sum())
    comp_hi = int((dt <= COMP_HI_TS).sum())
    train_rows = int((dev_hi - warm) * 0.70)

    print("\nPHASE 19 — PREFLIGHT")
    checks = [
        ("warmup rows == 1000", warm == 1000, warm),
        ("DEV rows == 70080", dev_hi - warm == 70080, dev_hi - warm),
        ("DEV starts 2024-07-16 00:00", str(dt.iloc[warm]) == "2024-07-16 00:00:00+00:00", str(dt.iloc[warm])),
        ("DEV ends 2026-07-15 23:45", str(dt.iloc[dev_hi - 1]) == "2026-07-15 23:45:00+00:00", str(dt.iloc[dev_hi - 1])),
        ("TRAIN 49056 / VALID 21024", train_rows == 49056 and (dev_hi - warm) - train_rows == 21024,
         f"{train_rows}/{(dev_hi - warm) - train_rows}"),
        ("VALID starts 2025-12-09 00:00", str(dt.iloc[warm + train_rows]) == "2025-12-09 00:00:00+00:00",
         str(dt.iloc[warm + train_rows])),
        ("locked window 2026-07-16..2026-08-15", str(dt.iloc[dev_hi]) == "2026-07-16 00:00:00+00:00"
         and str(dt.iloc[comp_hi - 1]) == "2026-08-15 23:45:00+00:00", f"{dt.iloc[dev_hi]}..{dt.iloc[comp_hi-1]}"),
        ("long-only", V3_spec.LONG_ENABLED and not V3_spec.SHORT_ENABLED,
         f"long={V3_spec.LONG_ENABLED} short={V3_spec.SHORT_ENABLED}"),
        ("budgets total 1850", (V3_spec.BROAD_TRIALS, V3_spec.NARROW_TRIALS, V3_spec.RISK_SEED_TRIALS,
                                V3_spec.FINAL_TRIALS, V3_spec.BOLL_TRIALS) == (400, 800, 200, 300, 150), "1850"),
    ]
    df_dev = df.iloc[:dev_hi].reset_index(drop=True)
    locked_in_frame = int((pd.to_datetime(df_dev["datetime"], utc=True) >= HARD_LOCKED).sum())
    checks.append(("locked rows excluded from optimization frame", locked_in_frame == 0, locked_in_frame))
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:<46} {detail}")
        if not ok:
            fail(f"preflight: {name} ({detail})")

    print("\nRunning 1,850 trials…")
    t0 = time.time()
    campaign = V3.Campaign("ETHUSDT", "15m", df_dev, warm)
    seed_meta, s1, _ = campaign.stage1()
    broad, narrow, risk = s1["1a_broad"], s1["1b_narrow"], s1["1c_risk"]

    def best(d):
        return d[d.gated].sort_values(["score", "trial"], ascending=[False, True]).iloc[0]

    w1a, w1b, w1c = best(broad), best(narrow), best(risk)
    s2a_df, s2a = campaign.stage2_config(seed_meta["seed"])
    win = s2a["params"]
    s2b_df, s2b, _ = campaign.stage2_bollinger(win)
    bwin = s2b["cfg"] if s2b else V3.OFF
    print(f"  done in {time.time() - t0:.0f}s")

    out = HERE
    broad.to_csv(os.path.join(out, "v3_stage1a_broad.csv"), index=False)
    narrow.to_csv(os.path.join(out, "v3_stage1b_narrow.csv"), index=False)
    risk.to_csv(os.path.join(out, "v3_stage1c_risk.csv"), index=False)
    s2a_df.to_csv(os.path.join(out, "v3_stage2a_final.csv"), index=False)
    s2b_df.to_csv(os.path.join(out, "v3_stage2b_bollinger.csv"), index=False)

    cfg_win = V3.build_cfg("ETHUSDT", "15m", win)
    comp_off = eval_window(df, cfg_win, V3.OFF, dev_hi, comp_hi)
    comp_on = eval_window(df, cfg_win, bwin, dev_hi, comp_hi)

    results = {
        "stages": {
            "1a_broad": {"trial": int(w1a.trial), "score": float(w1a.score),
                         "params": {k: float(w1a[k]) for k in V3_spec.STRATEGY_KEYS}},
            "1b_narrow": {"trial": int(w1b.trial), "score": float(w1b.score),
                          "params": {k: float(w1b[k]) for k in V3_spec.STRATEGY_KEYS}},
            "1c_risk": {"trial": int(w1c.trial), "score": float(w1c.score),
                        "params": {k: float(w1c[k]) for k in V3_spec.RISK_KEYS}},
            "seed": seed_meta["seed"],
            "2a_final": {"trial": s2a["trial"], "score": s2a["score"], "params": win},
            "2b_boll": {"trial": s2b["trial"] if s2b else None,
                        "score": s2b["score"] if s2b else None, "cfg": bwin.to_dict()},
        },
        "locked_metrics": {"off": comp_off, "on": comp_on},
    }
    with open(os.path.join(out, "phase19_results.json"), "w") as f:
        json.dump(results, f, indent=2)

    # ------------------------------------------------------------ comparison
    ref = json.load(open(os.path.join(P16_DIR, "phase16_results.json")))
    print("\nCOMPARISON vs Phase-16 ledger")

    for stage, keys in (("1a_broad", V3_spec.STRATEGY_KEYS),
                        ("1b_narrow", V3_spec.STRATEGY_KEYS),
                        ("1c_risk", V3_spec.RISK_KEYS),
                        ("2a_final", V3_spec.ALL_KEYS)):
        g, r = results["stages"][stage], ref["stages"][stage]
        if int(g["trial"]) != int(r["trial"]):
            fail(f"{stage} trial: got {g['trial']}, Phase-16 {r['trial']}")
        if abs(float(g["score"]) - float(r["score"])) > TOL:
            fail(f"{stage} score: got {g['score']!r}, Phase-16 {r['score']!r}")
        for k in keys:
            if abs(float(g["params"][k]) - float(r["params"][k])) > TOL:
                fail(f"{stage} param {k}: got {g['params'][k]}, Phase-16 {r['params'][k]}")
        print(f"  [PASS] {stage:<10} trial {g['trial']:<4} score {g['score']:.10f}  +{len(keys)} params")

    for k in V3_spec.ALL_KEYS:
        if abs(float(results["stages"]["seed"][k]) - float(ref["stages"]["seed"][k])) > TOL:
            fail(f"seed param {k}: got {results['stages']['seed'][k]}, Phase-16 {ref['stages']['seed'][k]}")
    print("  [PASS] seed       14 params")

    g, r = results["stages"]["2b_boll"], ref["stages"]["2b_boll"]
    if g["trial"] != r["trial"]:
        fail(f"2b_boll trial: got {g['trial']}, Phase-16 {r['trial']}")
    if abs(float(g["score"]) - float(r["score"])) > TOL:
        fail(f"2b_boll score: got {g['score']!r}, Phase-16 {r['score']!r}")
    for k, v in r["cfg"].items():
        gv = g["cfg"][k]
        same = gv == v if isinstance(v, bool) else abs(float(gv) - float(v)) <= TOL
        if not same:
            fail(f"2b_boll cfg {k}: got {gv}, Phase-16 {v}")
    print(f"  [PASS] 2b_boll    trial {g['trial']:<4} score {g['score']:.10f}  +{len(r['cfg'])} cfg fields")

    for arm in ("off", "on"):
        g, r = results["locked_metrics"][arm], ref["locked_metrics"][arm]
        if int(g["trades"]) != int(r["trades"]):
            fail(f"locked {arm.upper()} trades: got {g['trades']}, Phase-16 {r['trades']}")
        for k, v in r.items():
            if abs(float(g[k]) - float(v)) > TOL:
                fail(f"locked {arm.upper()} {k}: got {g[k]!r}, Phase-16 {v!r}")
        print(f"  [PASS] locked {arm.upper():<3} {int(g['trades'])} trades, "
              f"ret {g['return_pct']:+.10f}%, PF {g['pf']:.10f}, DD {g['max_dd']:.10f}%, "
              f"net ${g['net_pnl']:+.2f}  ({len(r)} fields)")

    print("\nPASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
