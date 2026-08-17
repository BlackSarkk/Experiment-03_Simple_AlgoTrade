"""Phase 12 — ETH 15m DEV optimization (2 arms) + sealed TEST evaluation + C158 reference.

Optimization sees ONLY df_dev = df.iloc[:dev_hi] — the TEST rows are physically absent from
the frame handed to either optimizer, so no gate can be bypassed. TEST is measured once, at
the end, through a single shared evaluator for all three arms.
"""
import argparse, contextlib, io, json, os, sys, time

ROOT = "/home/rahul/Documents/Claude/Experiment-03_Simple_AlgoTrade"
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "src", "optimization", "recovered_phase3a"))

import pandas as pd, optuna
from optuna.samplers import TPESampler
from strategy.indicators import compute_all_indicators
from backtest.engine import BacktestEngine
from filters.stage_1_bollinger.filter import BollingerFilterConfig, BollingerFilteredStrategy

import campaign_2y_15m as REC
from new_optimizer_v2 import optimizer as V2
import common.market_data as _md
_md.MarketDataLoader.__init__ = lambda *a, **k: (_ for _ in ()).throw(
    RuntimeError("PHASE12: fetch blocked"))

HARD = pd.Timestamp("2026-08-17 00:00:00", tz="UTC")
DEV_LO_TS = pd.Timestamp("2024-07-16 00:00:00", tz="UTC")
TEST_LO_TS = pd.Timestamp("2026-06-01 00:00:00", tz="UTC")
C158 = os.path.join(ROOT, "src/optimization/recovered_phase3a/quarantine/frozen_challengers",
                    "trial285_candidate158_bollinger_on_shadow.json")
TEMPLATE = {"strategy": {"long_enabled": True, "short_enabled": False},
            "risk": {"sizing_mode": "RISK_BASED", "initial_capital": 10000.0,
                     "quantity_step": 0.001, "leverage": 1.0, "risk_per_trade_pct": 1.5,
                     "max_position_allocation_pct": 50.0},
            "execution": {"commission_pct": 0.05, "slippage_ticks": 1, "tick_size": 0.01}}
for k in V2.RANGES_KEYS[:11]:
    TEMPLATE["strategy"][k] = 0


def quiet(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        return fn(*a, **k)


def preset_of(p):
    t = json.loads(json.dumps(TEMPLATE))
    for k in V2.RANGES_KEYS[:11]:
        t["strategy"][k] = p[k]
    t["risk"]["leverage"] = p["leverage"]
    t["risk"]["risk_per_trade_pct"] = p["risk_per_trade_pct"] * 100.0
    t["risk"]["max_position_allocation_pct"] = p["max_position_allocation_pct"] * 100.0
    return t


def eval_window(df, cfg, fcfg, lo, hi, ind=None):
    """Shared measurement: indicators ONCE on the full frame, then slice by index."""
    ind = compute_all_indicators(df.copy(), cfg.strategy) if ind is None else ind
    frame = ind.iloc[lo:hi].reset_index(drop=True)
    engine = BacktestEngine(cfg)
    strat = BollingerFilteredStrategy(cfg.strategy, fcfg)
    engine.strategy = strat
    return V2.metrics(engine.run(frame), strat.blocked_count), ind


df = pd.read_csv(os.path.join(HERE, "data", "ETHUSDT_15m_warmup_dev_test.csv"))
dt = pd.to_datetime(df["datetime"], utc=True)
assert int((dt >= HARD).sum()) == 0
warm = int((dt < DEV_LO_TS).sum())
test_lo = int((dt >= TEST_LO_TS).to_numpy().argmax())
dev_hi = test_lo
tr_hi = warm + int((dev_hi - warm) * 0.70)
df_dev = df.iloc[:dev_hi].reset_index(drop=True)          # TEST rows absent by construction

checks = [
    ("zero rows at/after 2026-08-17", int((dt >= HARD).sum()) == 0, 0),
    ("warmup rows == 1000", warm == 1000, warm),
    ("DEV rows == 65760", dev_hi - warm == 65760, dev_hi - warm),
    ("TEST rows == 7392", len(df) - test_lo == 7392, len(df) - test_lo),
    ("DEV starts 2024-07-16 00:00", str(dt.iloc[warm]) == "2024-07-16 00:00:00+00:00", str(dt.iloc[warm])),
    ("DEV ends 2026-05-31 23:45", str(dt.iloc[dev_hi - 1]) == "2026-05-31 23:45:00+00:00", str(dt.iloc[dev_hi - 1])),
    ("TEST 2026-06-01..2026-08-16", (str(dt.iloc[test_lo]) == "2026-06-01 00:00:00+00:00"
                                     and str(dt.iloc[-1]) == "2026-08-16 23:45:00+00:00"), ""),
    ("70/30 split inside DEV", (tr_hi - warm, dev_hi - tr_hi) == (46032, 19728), (tr_hi - warm, dev_hi - tr_hi)),
    ("optimization frame excludes TEST", len(df_dev) == dev_hi and
     str(pd.to_datetime(df_dev['datetime'], utc=True).iloc[-1]) == "2026-05-31 23:45:00+00:00", len(df_dev)),
    ("budgets 300/150 both arms", (V2.STRAT_TRIALS, V2.BOLL_TRIALS, REC.STRAT_TRIALS, REC.BOLL_TRIALS)
     == (300, 150, 300, 150), ""),
    ("seed 42 / n_jobs 1", (V2.SEED, V2.N_JOBS, REC.SEED) == (42, 1, 42), ""),
    ("14-dim ranges identical", V2.RANGES == REC.RANGES, ""),
    ("C158 shadow: long-only + BB on", (lambda c: c["strategy"]["long_enabled"] and
     not c["strategy"]["short_enabled"] and c["filters"]["bollinger"]["enabled"])(json.load(open(C158))), ""),
]
# evaluator parity on a DEV window
probe = {"ema_period": 104, "rsi_period": 20, "rsi_overbought": 64.0, "rsi_oversold": 23.0,
         "atr_period": 7, "consolidation_candles": 7, "consolidation_atr_mult": 2.8,
         "swing_lookback": 17, "volume_sma_period": 12, "volume_mult": 1.8,
         "risk_reward_ratio": 3.6, "leverage": 4.0, "risk_per_trade_pct": 0.026,
         "max_position_allocation_pct": 0.70}
REC.DEV_HI = len(df_dev)
rec_tr = quiet(REC.run, df_dev, REC.build_cfg(preset_of(probe), probe), REC.OFF, warm, tr_hi)
own_tr, _ = quiet(eval_window, df_dev, V2.build_cfg("ETHUSDT", "15m", probe), V2.OFF, warm, tr_hi)
checks.append(("evaluator parity (shared vs recovered run())",
               rec_tr["trades"] == own_tr["trades"] and abs(rec_tr["return_pct"] - own_tr["return_pct"]) < 1e-6,
               f"rec n={rec_tr['trades']} ret={rec_tr['return_pct']:.6f} | shared n={own_tr['trades']} ret={own_tr['return_pct']:.6f}"))

print("PREFLIGHT")
for n, ok, d in checks:
    print(f"  [{'PASS' if ok else 'FAIL'}] {n:<44} {d}")
fails = [n for n, ok, _ in checks if not ok]
print(f"\n  PREFLIGHT: {'PASS' if not fails else 'FAIL -> ' + ', '.join(fails)}\n")
json.dump([{"check": n, "pass": bool(p), "detail": str(d)} for n, p, d in checks],
          open(os.path.join(HERE, "preflight.json"), "w"), indent=2)
ap = argparse.ArgumentParser(); ap.add_argument("--preflight-only", action="store_true")
if ap.parse_args().preflight_only or fails:
    sys.exit(1 if fails else 0)

os.makedirs(os.path.join(HERE, "runs"), exist_ok=True)
STAGE = os.path.join(HERE, "stage")
os.makedirs(os.path.join(STAGE, REC.OUT, "eth_rec"), exist_ok=True)
results = {}

# ---------------------------------------------------------------- arm 1: recovered, unseeded
t0 = time.time()
REC.DEV_HI = len(df_dev); REC.SYMBOL = "ETHUSDT"
rows = []
def rec_obj(trial):
    p = REC.suggest(trial, "NEW")
    cfg = REC.build_cfg(preset_of(p), p)
    tm = quiet(REC.run, df_dev, cfg, REC.OFF, warm, tr_hi)
    vm = quiet(REC.run, df_dev, cfg, REC.OFF, tr_hi, dev_hi)
    s = REC.profit_first(tm, vm)
    rows.append({"trial": trial.number, "score": s, **p,
                 **{f"tr_{k}": v for k, v in (tm or {}).items()},
                 **{f"va_{k}": v for k, v in (vm or {}).items()}})
    return s
st = optuna.create_study(direction="maximize", sampler=TPESampler(seed=REC.SEED))
st.optimize(rec_obj, n_trials=REC.STRAT_TRIALS, n_jobs=1)      # unseeded: no enqueue_trial
d = pd.DataFrame(rows).sort_values(["score", "trial"], ascending=[False, True])
d.to_csv(os.path.join(HERE, "runs", "recovered_strategy_trials.csv"), index=False)
cred = d[(d["tr_trades"] >= REC.MIN_TRAIN_TRADES) & (d["va_trades"] >= REC.MIN_VAL_TRADES)]
b = cred.iloc[0]
INTS = ("ema_period", "rsi_period", "atr_period", "consolidation_candles", "swing_lookback", "volume_sma_period")
rec_best = {k: (int(b[k]) if k in INTS else float(b[k])) for k in V2.RANGES_KEYS}
cwd = os.getcwd(); os.chdir(STAGE)
bf, dev_off, dev_on = quiet(REC.optimize_bollinger, "eth_rec", preset_of(rec_best), rec_best,
                            df_dev, warm, dev_hi)
os.chdir(cwd)
bd = pd.read_csv(os.path.join(STAGE, REC.OUT, "eth_rec", "bollinger_trials.csv"))
bwin = bd.sort_values(["score"], ascending=False).iloc[0]
results["recovered"] = {"trial": int(b.trial), "score": float(b.score),
                        "credible": f"{len(cred)}/{len(d)}", "params": rec_best,
                        "bollinger": bf.to_dict(), "boll_trial": int(bwin["trial"]),
                        "boll_score": float(bwin["score"]),
                        "dev_off": dev_off, "dev_on": dev_on, "runtime_s": round(time.time() - t0, 1)}
print(f"recovered: trial {int(b.trial)} score {b.score:.4f} credible {len(cred)}/{len(d)} | "
      f"boll trial {int(bwin['trial'])} ({time.time()-t0:.0f}s)")

# ---------------------------------------------------------------- arm 2: V2 unchanged
t0 = time.time()
camp = V2.Campaign("ETHUSDT", "15m", df_dev, warm)
sd, w2 = camp.run_stage_a()
sd.to_csv(os.path.join(HERE, "runs", "v2_strategy_trials.csv"), index=False)
bd2, wb, off_m = camp.run_stage_b(w2["params"])
bd2.to_csv(os.path.join(HERE, "runs", "v2_bollinger_trials.csv"), index=False)
v2_bcfg = wb["cfg"] if wb else V2.OFF
results["v2"] = {"trial": w2["trial"], "score": w2["score"],
                 "credible": f"{w2['gated_count']}/{w2['total']}", "params": w2["params"],
                 "bollinger": v2_bcfg.to_dict(),
                 "boll_trial": (wb["trial"] if wb else None),
                 "boll_score": (wb["score"] if wb else None),
                 "runtime_s": round(time.time() - t0, 1)}
print(f"v2: trial {w2['trial']} score {w2['score']:.4f} gated {w2['gated_count']}/{w2['total']} | "
      f"boll trial {wb['trial'] if wb else 'NONE'} ({time.time()-t0:.0f}s)")

# ---------------------------------------------------------------- sealed TEST, measured once
c158 = json.load(open(C158))
c158_p = {k: float(c158["strategy"][k]) for k in V2.RANGES_KEYS[:11]}
c158_p.update(leverage=float(c158["risk"]["leverage"]),
              risk_per_trade_pct=float(c158["risk"]["risk_per_trade_pct"]) / 100.0,
              max_position_allocation_pct=float(c158["risk"]["max_position_allocation_pct"]) / 100.0)
ARMS = [("recovered", rec_best, bf.to_dict()),
        ("v2", w2["params"], v2_bcfg.to_dict()),
        ("c158_reference", c158_p, c158["filters"]["bollinger"])]
print("\nSEALED TEST 2026-06-01 .. 2026-08-16")
for name, p, bdict in ARMS:
    cfg = V2.build_cfg("ETHUSDT", "15m", p)
    ind = None
    for state, f in (("off", BollingerFilterConfig(enabled=False)),
                     ("on", BollingerFilterConfig.from_dict(dict(bdict, enabled=True)))):
        m, ind = quiet(eval_window, df, cfg, f, test_lo, len(df), ind=ind)
        results.setdefault(name, {})[f"test_{state}"] = m
        if m:
            print(f"  {name:<16}{state.upper():<4} ret {m['return_pct']:+8.2f}% pf {m['pf']:.3f} "
                  f"dd {m['max_dd']:6.2f}% n {m['trades']:3d} netP&L ${m['net_pnl']:,.0f}")
        else:
            print(f"  {name:<16}{state.upper():<4} NO TRADES")
    results[name]["params"] = p
    results[name]["bollinger"] = bdict

json.dump({"scope": "DEV-optimized, sealed-TEST-evaluated ETH 15m parity experiment; not a winner claim",
           "dev": ["2024-07-16 00:00:00+00:00", "2026-05-31 23:45:00+00:00"],
           "test": ["2026-06-01 00:00:00+00:00", "2026-08-16 23:45:00+00:00"],
           "warmup_candles": 1000, "split": "70/30 inside DEV",
           "rows_at_or_after_2026_08_17": 0, "test_rows_seen_by_optimization": 0,
           "results": results},
          open(os.path.join(HERE, "phase12_results.json"), "w"), indent=2, default=str)
print("\n-> phase12_results.json")
