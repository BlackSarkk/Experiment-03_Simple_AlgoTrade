"""
Robustness showdown: Candidate #5 vs Candidate #158.

Same deep neighbourhood methodology for both (single +/-1, pairwise, seeded random local),
same seed, same partitions, TRAIN + VALIDATION only, HOLDOUT never read.

Perturbation bounds use the ORIGINAL Stage-1 SEARCH_SPACE, not STAGE2_SPACE: Candidate #5
sits outside the Stage-2 box on several dimensions (ema 51, rsi_oversold 45, volume_sma 20),
so clamping to Stage-2 would cripple it and make the comparison unfair. SEARCH_SPACE contains
both candidates. Each candidate keeps its OWN leverage/risk/allocation fixed — only strategy
parameters are perturbed.
"""

import os
import sys
import json
import time
import random
import itertools

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pandas as pd

from optimization.core_15m_long_optimizer import (
    SEARCH_SPACE, RESULTS_DIR, SEED,
    load_frozen_risk_policy, load_dataset, split_indices,
    run_partition, score_candidate, meets_minimum, passes_acceptance,
)
from optimization.risk_policy_search_t53 import mk_risk

SPACE = SEARCH_SPACE
PARAMS = list(SPACE.keys())
INT_PARAMS = {p for p, s in SPACE.items() if s[0] == "int"}
N_PAIRS, N_RANDOM = 49, 128

CAND = {
    "C5": {
        "label": "Candidate #5",
        "strategy": {"ema_period": 51, "rsi_period": 21, "rsi_overbought": 65.0,
                     "rsi_oversold": 45.0, "atr_period": 21, "consolidation_candles": 8,
                     "consolidation_atr_mult": 2.8, "swing_lookback": 12,
                     "volume_sma_period": 20, "volume_mult": 1.6, "risk_reward_ratio": 3.0},
        "risk": (3.5, 1.5, 50.0),
        "expect": {"train_ret": 64.93, "train_pf": 1.116, "train_dd": 31.64, "train_n": 405,
                   "val_ret": 89.78, "val_pf": 1.443, "val_dd": 15.61, "val_n": 142},
    },
    "C158": {
        "label": "Candidate #158",
        "strategy": {"ema_period": 105, "rsi_period": 18, "rsi_overbought": 80.0,
                     "rsi_oversold": 33.0, "atr_period": 11, "consolidation_candles": 14,
                     "consolidation_atr_mult": 3.3, "swing_lookback": 8,
                     "volume_sma_period": 32, "volume_mult": 1.5, "risk_reward_ratio": 2.7},
        "risk": (5.0, 1.7, 28.0),
        "expect": {"train_ret": 100.28, "train_pf": 1.218, "train_dd": 19.83, "train_n": 323,
                   "val_ret": 45.97, "val_pf": 1.425, "val_dd": 20.27, "val_n": 101},
    },
}


def clamp(name, value):
    kind, lo, hi, step = SPACE[name]
    value = max(lo, min(hi, value))
    return int(round(value)) if kind == "int" else round(value, 6)


def perturb(base, deltas):
    out = dict(base)
    for p, n in deltas.items():
        out[p] = clamp(p, base[p] + n * SPACE[p][3])
    return None if out == base else out


def neighbourhood(base, seed=SEED):
    rng = random.Random(seed)
    seen, items = set(), []

    def add(params, kind):
        if params is None:
            return
        key = tuple(params[p] for p in PARAMS)
        if key in seen:
            return
        seen.add(key)
        items.append((params, kind))

    for p in PARAMS:
        for d in (-1, 1):
            add(perturb(base, {p: d}), "single")
    pairs = list(itertools.combinations(PARAMS, 2))
    rng.shuffle(pairs)
    for a, b in pairs:
        if sum(1 for i in items if i[1] == "pair") >= N_PAIRS:
            break
        add(perturb(base, {a: rng.choice([-1, 1]), b: rng.choice([-1, 1])}), "pair")
    guard = 0
    while sum(1 for i in items if i[1] == "random") < N_RANDOM and guard < N_RANDOM * 40:
        guard += 1
        chosen = rng.sample(PARAMS, rng.randint(3, 5))
        add(perturb(base, {p: rng.choice([-2, -1, 1, 2]) for p in chosen}), "random")
    return items


def full(m):
    n = m["n_trades"]
    w = round(m["win_rate"] * n)
    return {"return_pct": m["net_return_pct"], "net_pnl": round(100.0 * m["net_return_pct"], 2),
            "dd": m["max_dd_pct"], "pf": m["profit_factor"], "sharpe": m["sharpe"],
            "expR": m["expectancy_R"], "trades": n, "wins": w, "losses": n - w,
            "win_rate": round(m["win_rate"] * 100, 2), "fees": m["total_fees"]}


def evaluate(strategy, rc, df, idx):
    tm = run_partition(df, idx["train"], strategy, rc)
    vm = run_partition(df, idx["validation"], strategy, rc)
    sc = score_candidate(tm, vm)
    ok = meets_minimum(tm, vm)
    return {"score": sc["final_score"] if ok else -10.0,
            "accepted": bool(ok and passes_acceptance(tm, vm, sc)),
            "train": full(tm), "val": full(vm)}


def main():
    base_risk, _, _ = load_frozen_risk_policy()
    df, sha = load_dataset()
    idx = split_indices(len(df))
    print(f"[Showdown] dataset sha {sha[:16]}… | HOLDOUT not read\n")

    # ---- Step 1: reproduce baselines, STOP on mismatch -------------------
    baselines = {}
    for key, c in CAND.items():
        rc = mk_risk(base_risk, *c["risk"])
        b = evaluate(c["strategy"], rc, df, idx)
        baselines[key] = b
        e = c["expect"]
        checks = [("train_ret", b["train"]["return_pct"], e["train_ret"], 0.05),
                  ("train_pf", b["train"]["pf"], e["train_pf"], 0.002),
                  ("train_dd", b["train"]["dd"], e["train_dd"], 0.05),
                  ("train_n", b["train"]["trades"], e["train_n"], 0),
                  ("val_ret", b["val"]["return_pct"], e["val_ret"], 0.05),
                  ("val_pf", b["val"]["pf"], e["val_pf"], 0.002),
                  ("val_dd", b["val"]["dd"], e["val_dd"], 0.05),
                  ("val_n", b["val"]["trades"], e["val_n"], 0)]
        bad = [(n, got, exp) for n, got, exp, tol in checks if abs(got - exp) > tol]
        print(f"{c['label']}: train {b['train']['return_pct']:.2f}% PF {b['train']['pf']:.3f} "
              f"DD {b['train']['dd']:.2f}% n {b['train']['trades']} | "
              f"val {b['val']['return_pct']:.2f}% PF {b['val']['pf']:.3f} "
              f"DD {b['val']['dd']:.2f}% n {b['val']['trades']}")
        if bad:
            print(f"  BASELINE MISMATCH -> {bad}")
            print("STOPPING: baselines do not reproduce.")
            sys.exit(1)
        print("  baseline reproduced OK")

    # ---- Step 2: deep neighbourhood ------------------------------------
    summary, t0 = {}, time.time()
    for key, c in CAND.items():
        rc = mk_risk(base_risk, *c["risk"])
        base = baselines[key]
        items = neighbourhood(c["strategy"])
        print(f"\n[{c['label']}] {len(items)} neighbours…")
        rows = []
        for i, (params, kind) in enumerate(items):
            r = evaluate(params, rc, df, idx)
            rows.append({"kind": kind, "score": r["score"], "accepted": r["accepted"],
                         **{f"train_{k}": v for k, v in r["train"].items()},
                         **{f"val_{k}": v for k, v in r["val"].items()}, **params})
            if (i + 1) % 60 == 0:
                print(f"    {i+1}/{len(items)} ({time.time()-t0:.0f}s)")
        d = pd.DataFrame(rows)
        d.to_csv(os.path.join(RESULTS_DIR, f"showdown_{key}_neighbours.csv"), index=False)

        bs, btr, bvr = base["score"], base["train"]["return_pct"], base["val"]["return_pct"]
        deg = (bs - d["score"]) / abs(bs) * 100.0
        summary[key] = {
            "label": c["label"], "n": len(d),
            "profitable_pct": float(((d["train_return_pct"] > 0) & (d["val_return_pct"] > 0)).mean() * 100),
            "pf_gt1_pct": float(((d["train_pf"] > 1) & (d["val_pf"] > 1)).mean() * 100),
            "ret70_pct": float(((d["train_return_pct"] >= 0.7 * btr) & (d["val_return_pct"] >= 0.7 * bvr)).mean() * 100),
            "within30_pct": float((deg <= 30).mean() * 100),
            "acceptance_pct": float(d["accepted"].mean() * 100),
            "median_train_return": float(d["train_return_pct"].median()),
            "median_val_return": float(d["val_return_pct"].median()),
            "median_train_pf": float(d["train_pf"].median()),
            "median_val_pf": float(d["val_pf"].median()),
            "median_train_dd": float(d["train_dd"].median()),
            "median_val_dd": float(d["val_dd"].median()),
            "worst_deg_pct": float(deg[d["score"] > -9].max()) if (d["score"] > -9).any() else float("nan"),
            "base": {"score": bs, "train": base["train"], "val": base["val"]},
        }

    with open(os.path.join(RESULTS_DIR, "showdown_summary.json"), "w") as f:
        json.dump({"dataset_sha256": sha, "seed": SEED, "space": "SEARCH_SPACE (Stage-1 bounds)",
                   "holdout_evaluated": False, "summary": summary,
                   "elapsed_seconds": round(time.time() - t0, 1)}, f, indent=2)

    print(f"\n[Showdown] done in {time.time()-t0:.0f}s")
    for k, s in summary.items():
        print(f"\n{s['label']}: n={s['n']} profit {s['profitable_pct']:.1f}% PF>1 {s['pf_gt1_pct']:.1f}% "
              f"ret70 {s['ret70_pct']:.1f}% w30 {s['within30_pct']:.1f}% acc {s['acceptance_pct']:.1f}%")
        print(f"  median train ret {s['median_train_return']:.2f}% PF {s['median_train_pf']:.3f} DD {s['median_train_dd']:.2f}%")
        print(f"  median val   ret {s['median_val_return']:.2f}% PF {s['median_val_pf']:.3f} DD {s['median_val_dd']:.2f}%")
        print(f"  worst meaningful degradation {s['worst_deg_pct']:.1f}%")


if __name__ == "__main__":
    main()
