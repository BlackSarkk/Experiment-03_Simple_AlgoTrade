"""Fair 15m bakeoff — recovered Scenario-4 recipe (unseeded) vs New Optimizer V2, ETH and BTC.

Four campaigns, identical data policy. Preflight must pass for a campaign to run; a parity
failure stops that campaign and reports the first mismatch.
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
import optuna
from optuna.samplers import TPESampler

import campaign_2y_15m as REC                     # recovered recipe (never edited)
from new_optimizer_v2 import optimizer as V2
import common.market_data as _md
_md.MarketDataLoader.__init__ = lambda *a, **k: (_ for _ in ()).throw(
    RuntimeError("BAKEOFF: fetch blocked — datasets are pre-built and bounded"))

LOCK = pd.Timestamp("2026-07-16 00:00:00", tz="UTC")
DEV_LO = pd.Timestamp("2024-07-16 00:00:00", tz="UTC")
WARM = 1000
SYMS = ["ETHUSDT", "BTCUSDT"]
TEMPLATE = {"strategy": {"long_enabled": True, "short_enabled": False},
            "risk": {"sizing_mode": "RISK_BASED", "initial_capital": 10000.0,
                     "quantity_step": 0.001, "leverage": 1.0, "risk_per_trade_pct": 1.5,
                     "max_position_allocation_pct": 50.0},
            "execution": {"commission_pct": 0.05, "slippage_ticks": 1, "tick_size": 0.01}}
for k in V2.RANGES_KEYS[:11]:
    TEMPLATE["strategy"][k] = 0


def load(sym):
    p = os.path.join(HERE, "data", f"{sym}_15m_warmup1000_dev.csv")
    df = pd.read_csv(p)
    dt = pd.to_datetime(df["datetime"], utc=True)
    assert int((dt >= LOCK).sum()) == 0, f"{sym}: locked row present"
    warm = int((dt < DEV_LO).sum())
    return df, dt, warm


def quiet(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        return fn(*a, **k)


# ------------------------------------------------------------------ preflight
def preflight():
    checks, frames = [], {}
    for sym in SYMS:
        df, dt, warm = load(sym)
        dev = len(df) - warm
        tr_hi = warm + int(dev * 0.70)
        frames[sym] = (df, dt, warm, tr_hi)
        checks += [
            (f"{sym} zero rows at/after 2026-07-16", int((dt >= LOCK).sum()) == 0, int((dt >= LOCK).sum())),
            (f"{sym} warmup rows == 1000", warm == WARM, warm),
            (f"{sym} DEV rows == 70080", dev == 70080, dev),
            (f"{sym} DEV starts 2024-07-16 00:00", str(dt.iloc[warm]) == "2024-07-16 00:00:00+00:00", str(dt.iloc[warm])),
            (f"{sym} DEV ends 2026-07-15 23:45", str(dt.iloc[-1]) == "2026-07-15 23:45:00+00:00", str(dt.iloc[-1])),
            (f"{sym} split at 2025-12-09 00:00", str(dt.iloc[tr_hi]) == "2025-12-09 00:00:00+00:00", str(dt.iloc[tr_hi])),
            (f"{sym} TRAIN/VALID rows 49056/21024", (tr_hi - warm, len(df) - tr_hi) == (49056, 21024),
             (tr_hi - warm, len(df) - tr_hi)),
        ]
    checks += [
        ("V2 budgets 300 / 150", (V2.STRAT_TRIALS, V2.BOLL_TRIALS) == (300, 150),
         (V2.STRAT_TRIALS, V2.BOLL_TRIALS)),
        ("recovered budgets 300 / 150", (REC.STRAT_TRIALS, REC.BOLL_TRIALS) == (300, 150),
         (REC.STRAT_TRIALS, REC.BOLL_TRIALS)),
        ("both seed 42 / n_jobs 1", (V2.SEED, V2.N_JOBS, REC.SEED) == (42, 1, 42), ""),
        ("14-dim spaces identical", V2.RANGES == REC.RANGES, ""),
        ("V2 long-only, no direction dim", "side_choice" not in V2.suggest.__doc__ if V2.suggest.__doc__ else True, ""),
        ("no fetch path can execute", _fetch_blocked(), ""),
        ("recovered unlock_unseen not called", REC._UNLOCKED is False, REC._UNLOCKED),
    ]
    # parity: V2's full-frame-then-slice evaluator must equal the recovered run() bar for bar
    sym = "ETHUSDT"
    df, dt, warm, tr_hi = frames[sym]
    probe = {"ema_period": 104, "rsi_period": 20, "rsi_overbought": 64.0, "rsi_oversold": 23.0,
             "atr_period": 7, "consolidation_candles": 7, "consolidation_atr_mult": 2.8,
             "swing_lookback": 17, "volume_sma_period": 12, "volume_mult": 1.8,
             "risk_reward_ratio": 3.6, "leverage": 4.0, "risk_per_trade_pct": 0.026,
             "max_position_allocation_pct": 0.70}
    REC.DEV_HI = len(df)
    rec_cfg = REC.build_cfg(_preset(probe), probe)
    rec_tr = quiet(REC.run, df, rec_cfg, REC.OFF, warm, tr_hi)
    rec_va = quiet(REC.run, df, rec_cfg, REC.OFF, tr_hi, len(df))
    camp = V2.Campaign(sym, "15m", df, warm)
    v2m = quiet(camp.evaluate, V2.build_cfg(sym, "15m", probe), V2.OFF)
    for part, r, v in (("TRAIN", rec_tr, v2m["train"]), ("VALID", rec_va, v2m["valid"])):
        same = (r["trades"] == v["trades"] and abs(r["return_pct"] - v["return_pct"]) < 1e-6
                and abs(r["pf"] - v["pf"]) < 1e-9 and abs(r["max_dd"] - v["max_dd"]) < 1e-9)
        checks.append((f"evaluator parity {part} (V2 vs recovered run())", same,
                       f"rec n={r['trades']} ret={r['return_pct']:.6f} | "
                       f"v2 n={v['trades']} ret={v['return_pct']:.6f}"))
    print("PREFLIGHT")
    for n, ok, d in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {n:<52} {d}")
    fails = [n for n, ok, _ in checks if not ok]
    print(f"\n  PREFLIGHT: {'PASS' if not fails else 'FAIL -> ' + ', '.join(fails)}\n")
    return (not fails), frames, checks


def _fetch_blocked():
    try:
        _md.MarketDataLoader(data_dir="x")
        return False
    except RuntimeError:
        return True


def _preset(p):
    t = json.loads(json.dumps(TEMPLATE))
    for k in V2.RANGES_KEYS[:11]:
        t["strategy"][k] = p[k]
    t["risk"]["leverage"] = p["leverage"]
    t["risk"]["risk_per_trade_pct"] = p["risk_per_trade_pct"] * 100.0
    t["risk"]["max_position_allocation_pct"] = p["max_position_allocation_pct"] * 100.0
    return t


# ------------------------------------------------------- recovered arm, unseeded
def run_recovered(sym, df, warm, tr_hi):
    REC.DEV_HI = len(df)
    prev_sym = REC.SYMBOL
    REC.SYMBOL = sym
    rows = []

    def objective(trial):
        p = REC.suggest(trial, "NEW")
        cfg = REC.build_cfg(_preset(p), p)
        tm = quiet(REC.run, df, cfg, REC.OFF, warm, tr_hi)
        vm = quiet(REC.run, df, cfg, REC.OFF, tr_hi, len(df))
        s = REC.profit_first(tm, vm)
        rows.append({"trial": trial.number, "score": s, **p,
                     **{f"tr_{k}": v for k, v in (tm or {}).items()},
                     **{f"va_{k}": v for k, v in (vm or {}).items()}})
        return s

    st = optuna.create_study(direction="maximize", sampler=TPESampler(seed=REC.SEED))
    st.optimize(objective, n_trials=REC.STRAT_TRIALS, n_jobs=1)      # UNSEEDED: no enqueue_trial
    d = pd.DataFrame(rows).sort_values(["score", "trial"], ascending=[False, True])
    cred = d[(d["tr_trades"] >= REC.MIN_TRAIN_TRADES) & (d["va_trades"] >= REC.MIN_VAL_TRADES)]
    if cred.empty:
        REC.SYMBOL = prev_sym
        return d, None, None, None
    b = cred.iloc[0]
    best = {k: (int(b[k]) if k in V2.RANGES_KEYS[:11][:0] + ("ema_period", "rsi_period", "atr_period",
                "consolidation_candles", "swing_lookback", "volume_sma_period") else float(b[k]))
            for k in V2.RANGES_KEYS}
    preset = _preset(best)
    bf, dev_off, dev_on = quiet(REC.optimize_bollinger, f"{sym}_rec", preset, best, df, warm, len(df))
    REC.SYMBOL = prev_sym
    return d, {"trial": int(b.trial), "score": float(b.score), "params": best,
               "credible": int(len(cred)), "total": int(len(d))}, bf, (dev_off, dev_on)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preflight-only", action="store_true")
    a = ap.parse_args()
    ok, frames, checks = preflight()
    json.dump([{"check": n, "pass": bool(p), "detail": str(d)} for n, p, d in checks],
              open(os.path.join(HERE, "preflight.json"), "w"), indent=2)
    if a.preflight_only or not ok:
        print("STOPPED." if not ok else "Preflight-only.")
        return 1 if not ok else 0

    os.makedirs(os.path.join(HERE, "runs"), exist_ok=True)
    # The recovered optimize_bollinger() writes to its module-level relative OUT path and does
    # not create the directory itself (optimize_scenario did). Stage a cwd under this folder so
    # nothing lands in the project's results/ tree.
    STAGE = os.path.join(HERE, "stage")
    for sym in SYMS:
        os.makedirs(os.path.join(STAGE, REC.OUT, f"{sym}_rec"), exist_ok=True)
    os.chdir(STAGE)
    results = []
    for sym in SYMS:
        df, dt, warm, tr_hi = frames[sym]
        # ---------------- recovered, unseeded
        t0 = time.time()
        d, win, bf, dev = run_recovered(sym, df, warm, tr_hi)
        rt = time.time() - t0
        d.to_csv(os.path.join(HERE, "runs", f"recovered_{sym}_strategy_trials.csv"), index=False)
        if win is None:
            results.append({"optimizer": "recovered", "symbol": sym, "status": "NO CREDIBLE TRIAL",
                            "runtime_s": round(rt, 1)})
            print(f"recovered x {sym}: NO CREDIBLE TRIAL")
            continue
        REC.DEV_HI = len(df)
        REC.SYMBOL = sym
        cfg = REC.build_cfg(_preset(win["params"]), win["params"])
        tr_off = quiet(REC.run, df, cfg, REC.OFF, warm, tr_hi)
        va_off = quiet(REC.run, df, cfg, REC.OFF, tr_hi, len(df))
        d_off, d_on = dev
        REC.SYMBOL = "ETHUSDT"
        results.append({"optimizer": "recovered", "symbol": sym, "status": "OK",
                        "selected_trial": win["trial"], "score": win["score"],
                        "credible": f"{win['credible']}/{win['total']}",
                        "params": win["params"], "bollinger": bf.to_dict(),
                        "boll_trial": None,
                        "train": tr_off, "valid": va_off, "dev_off": d_off, "dev_on": d_on,
                        "runtime_s": round(rt, 1)})
        print(f"recovered x {sym}: trial {win['trial']} score {win['score']:.4f} "
              f"credible {win['credible']}/{win['total']} ({rt:.0f}s)")

        # ---------------- V2
        t0 = time.time()
        camp = V2.Campaign(sym, "15m", df, warm)
        sd, w2 = camp.run_stage_a()
        sd.to_csv(os.path.join(HERE, "runs", f"v2_{sym}_strategy_trials.csv"), index=False)
        if w2 is None:
            results.append({"optimizer": "v2", "symbol": sym, "status": "NO GATED TRIAL",
                            "runtime_s": round(time.time() - t0, 1)})
            print(f"v2 x {sym}: NO GATED TRIAL")
            continue
        bd, wb, off_m = camp.run_stage_b(w2["params"])
        bd.to_csv(os.path.join(HERE, "runs", f"v2_{sym}_bollinger_trials.csv"), index=False)
        rt = time.time() - t0
        cfg2 = V2.build_cfg(sym, "15m", w2["params"])
        ind = camp._indicators(cfg2)
        bcfg = wb["cfg"] if wb else V2.OFF
        on_m = camp.evaluate(cfg2, bcfg, ind=ind) if wb else off_m
        REC.DEV_HI = len(df)
        REC.SYMBOL = sym
        rcfg = REC.build_cfg(_preset(w2["params"]), w2["params"])
        dev_off2 = quiet(REC.run, df, rcfg, REC.OFF, warm, len(df))
        dev_on2 = quiet(REC.run, df, rcfg,
                        REC.BollingerFilterConfig.from_dict(dict(bcfg.to_dict(), enabled=bool(wb))),
                        warm, len(df))
        REC.SYMBOL = "ETHUSDT"
        results.append({"optimizer": "v2", "symbol": sym, "status": "OK",
                        "selected_trial": w2["trial"], "score": w2["score"],
                        "credible": f"{w2['gated_count']}/{w2['total']}",
                        "params": w2["params"],
                        "bollinger": bcfg.to_dict(),
                        "boll_trial": (wb["trial"] if wb else None),
                        "boll_score": (wb["score"] if wb else None),
                        "train": off_m["train"], "valid": off_m["valid"],
                        "dev_off": dev_off2, "dev_on": dev_on2, "runtime_s": round(rt, 1)})
        print(f"v2 x {sym}: trial {w2['trial']} score {w2['score']:.4f} "
              f"gated {w2['gated_count']}/{w2['total']} | boll trial "
              f"{wb['trial'] if wb else 'NONE (gate failed -> filter disabled)'} ({rt:.0f}s)")

    assert REC._UNLOCKED is False
    json.dump({"scope": "reused-historical-DEV architecture bakeoff; not a final winner claim",
               "dev": ["2024-07-16 00:00:00+00:00", "2026-07-15 23:45:00+00:00"],
               "warmup_candles": 1000, "split": "70/30 chronological, boundary 2025-12-09 00:00",
               "locked_rows_used": 0, "unlock_unseen_called": REC._UNLOCKED,
               "results": results},
              open(os.path.join(HERE, "bakeoff_results.json"), "w"), indent=2, default=str)
    print("\n-> bakeoff_results.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
