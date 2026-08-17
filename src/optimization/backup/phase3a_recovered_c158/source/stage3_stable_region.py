"""
Phase 3A — Stable-region search over existing Stage-2 results.

#539 failed neighbourhood robustness because it sat on the minimum-trade boundary.
This reuses the 800 Stage-2 trials (no new broad search) and looks for a plateau:

  Step 1  screen accepted Stage-2 trials with a trade-count MARGIN (train>=300, val>=80)
  Step 2  cheap ~36-config neighbourhood per candidate (top 20 distinct)
  Step 3  combined stability ranking (performance + robustness, components visible)
  Step 4  full ~198-neighbour deep test on the top 3 only

Objective, acceptance gates, risk policy, split, seed methodology and engine are unchanged.
TRAIN + VALIDATION only. HOLDOUT is never read.
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
    STAGE2_SPACE, RESULTS_DIR, SEED,
    load_frozen_risk_policy, load_dataset, split_indices,
    run_partition, score_candidate, meets_minimum, passes_acceptance,
)

PARAMS = list(STAGE2_SPACE.keys())
INT_PARAMS = {p for p, s in STAGE2_SPACE.items() if s[0] == "int"}

SCREEN_TRAIN_TRADES = 300      # robustness-screening margin (NOT a change to the gates)
SCREEN_VAL_TRADES = 80
N_CANDIDATES = 20
CHEAP_PAIRS = 14               # 22 single + 14 pairwise = 36 cheap configs
DEEP_PAIRS, DEEP_RANDOM = 49, 128


def clamp(name, value):
    kind, lo, hi, step = STAGE2_SPACE[name]
    value = max(lo, min(hi, value))
    return int(round(value)) if kind == "int" else round(value, 6)


def perturb(base, deltas):
    out = dict(base)
    for p, n in deltas.items():
        out[p] = clamp(p, base[p] + n * STAGE2_SPACE[p][3])
    return None if out == base else out


def neighbourhood(base, n_pairs, n_random, seed=SEED):
    rng = random.Random(seed)
    seen, items = set(), []

    def add(params, kind, label):
        if params is None:
            return
        key = tuple(params[p] for p in PARAMS)
        if key in seen:
            return
        seen.add(key)
        items.append((params, kind, label))

    for p in PARAMS:
        for d in (-1, 1):
            add(perturb(base, {p: d}), "single", f"{p}{d:+d}")

    pairs = list(itertools.combinations(PARAMS, 2))
    rng.shuffle(pairs)
    for a, b in pairs:
        if sum(1 for i in items if i[1] == "pair") >= n_pairs:
            break
        da, db = rng.choice([-1, 1]), rng.choice([-1, 1])
        add(perturb(base, {a: da, b: db}), "pair", f"{a}{da:+d},{b}{db:+d}")

    guard = 0
    while sum(1 for i in items if i[1] == "random") < n_random and guard < n_random * 40:
        guard += 1
        chosen = rng.sample(PARAMS, rng.randint(3, 5))
        deltas = {p: rng.choice([-2, -1, 1, 2]) for p in chosen}
        add(perturb(base, deltas), "random", "multi")

    return items


def evaluate(params, df, idx, risk_cfg):
    tm = run_partition(df, idx["train"], params, risk_cfg)
    vm = run_partition(df, idx["validation"], params, risk_cfg)
    sc = score_candidate(tm, vm)
    ok = meets_minimum(tm, vm)
    return {
        "score": sc["final_score"] if ok else -10.0,
        "meets_minimum": ok,
        "accepted": bool(ok and passes_acceptance(tm, vm, sc)),
        "consistency_gap": sc["consistency_gap"],
        "train": tm, "val": vm,
    }


def profile(base_params, base_score, df, idx, risk_cfg, n_pairs, n_random):
    items = neighbourhood(base_params, n_pairs, n_random)
    scores, accepted = [], 0
    for params, _kind, _label in items:
        r = evaluate(params, df, idx, risk_cfg)
        scores.append(r["score"])
        accepted += int(r["accepted"])
    s = np.array(scores, dtype=float)
    deg = (base_score - s) / abs(base_score) * 100.0
    return {
        "n_neighbours": len(s),
        "median_neighbour_score": float(np.median(s)),
        "median_degradation_pct": float(np.median(deg)),
        "worst_degradation_pct": float(deg.max()),
        "within_15_pct": float((deg <= 15).mean() * 100),
        "within_30_pct": float((deg <= 30).mean() * 100),
        "acceptance_rate_pct": float(accepted / len(s) * 100),
    }


def stability_rank(row):
    """Combined ranking. Components stay visible in the CSV; this is only the sort key.
    Plateau breadth is weighted above peak score, which is the whole point of Stage 3."""
    return (0.40 * (row["within_30_pct"] / 100.0)
            + 0.25 * (row["acceptance_rate_pct"] / 100.0)
            + 0.20 * max(0.0, row["median_neighbour_score"]) / 0.35
            + 0.15 * max(0.0, row["orig_score"]) / 0.35)


def perf_block(m, tag):
    return {
        f"{tag}_return_pct": m["net_return_pct"],
        f"{tag}_net_pnl": round(10000.0 * m["net_return_pct"] / 100.0, 2),
        f"{tag}_pf": m["profit_factor"],
        f"{tag}_expR": m["expectancy_R"],
        f"{tag}_dd": m["max_dd_pct"],
        f"{tag}_trades": m["n_trades"],
    }


def main():
    risk_cfg, risk_hash, risk_raw = load_frozen_risk_policy()
    df, sha = load_dataset()
    idx = split_indices(len(df))

    s2 = pd.read_csv(os.path.join(RESULTS_DIR, "stage2_15m_long_trials.csv"))
    acc = s2[s2["passes_acceptance"]]
    screened = acc[(acc["train_n_trades"] >= SCREEN_TRAIN_TRADES)
                   & (acc["val_n_trades"] >= SCREEN_VAL_TRADES)]
    screened = screened.drop_duplicates(subset=PARAMS).sort_values("score", ascending=False)

    print(f"[StableRegion] Stage-2 accepted {len(acc)} | passing {SCREEN_TRAIN_TRADES}/"
          f"{SCREEN_VAL_TRADES} screen: {len(screened)} distinct")
    print(f"  dataset sha {sha[:16]}… | risk sha {risk_hash[:16]}… | HOLDOUT not read")

    cands = screened.head(N_CANDIDATES)
    if len(cands) == 0:
        print("NO CANDIDATE passes the screening margin.")
        return

    rows, t0 = [], time.time()
    for i, (_, c) in enumerate(cands.iterrows()):
        params = {p: (int(c[p]) if p in INT_PARAMS else float(c[p])) for p in PARAMS}
        base = evaluate(params, df, idx, risk_cfg)
        prof = profile(params, base["score"], df, idx, risk_cfg, CHEAP_PAIRS, 0)
        row = {"trial": int(c["trial"]), "orig_score": base["score"], **prof, **params,
               **perf_block(base["train"], "train"), **perf_block(base["val"], "val"),
               "consistency_gap": base["consistency_gap"]}
        row["stability_rank"] = stability_rank(row)
        rows.append(row)
        print(f"  [{i+1}/{len(cands)}] trial {int(c['trial']):>3} score {base['score']:.3f} "
              f"medDeg {prof['median_degradation_pct']:>6.1f}% w30 {prof['within_30_pct']:>5.1f}% "
              f"acc {prof['acceptance_rate_pct']:>5.1f}%  ({time.time()-t0:.0f}s)")

    cheap = pd.DataFrame(rows).sort_values("stability_rank", ascending=False)
    cheap.to_csv(os.path.join(RESULTS_DIR, "stage3_cheap_screen.csv"), index=False)

    print("\n[StableRegion] DEEP test on top 3")
    deep_rows = []
    for _, c in cheap.head(3).iterrows():
        params = {p: (int(c[p]) if p in INT_PARAMS else float(c[p])) for p in PARAMS}
        base = evaluate(params, df, idx, risk_cfg)
        prof = profile(params, base["score"], df, idx, risk_cfg, DEEP_PAIRS, DEEP_RANDOM)
        row = {"trial": int(c["trial"]), "orig_score": base["score"], **prof, **params,
               **perf_block(base["train"], "train"), **perf_block(base["val"], "val"),
               "consistency_gap": base["consistency_gap"]}
        deep_rows.append(row)
        print(f"  trial {int(c['trial'])}: n={prof['n_neighbours']} medScore "
              f"{prof['median_neighbour_score']:.3f} medDeg {prof['median_degradation_pct']:.1f}% "
              f"w15 {prof['within_15_pct']:.1f}% w30 {prof['within_30_pct']:.1f}% "
              f"acc {prof['acceptance_rate_pct']:.1f}%")

    deep = pd.DataFrame(deep_rows)
    deep.to_csv(os.path.join(RESULTS_DIR, "stage3_deep_top3.csv"), index=False)

    with open(os.path.join(RESULTS_DIR, "stage3_stable_region_manifest.json"), "w") as f:
        json.dump({
            "screen": {"train_trades_min": SCREEN_TRAIN_TRADES, "val_trades_min": SCREEN_VAL_TRADES,
                       "note": "screening margin only; Stage-2 acceptance gates unchanged"},
            "stage2_accepted": int(len(acc)), "screened_distinct": int(len(screened)),
            "cheap_tested": int(len(cheap)), "cheap_neighbours_each": int(cheap.iloc[0]["n_neighbours"]),
            "deep_tested": 3, "deep_neighbours_each": int(deep.iloc[0]["n_neighbours"]),
            "dataset_sha256": sha, "risk_policy_sha256": risk_hash, "risk_policy_values": risk_raw,
            "seed": SEED, "holdout_evaluated": False,
            "elapsed_seconds": round(time.time() - t0, 1),
        }, f, indent=2)

    print(f"\n[StableRegion] done in {time.time()-t0:.0f}s -> {RESULTS_DIR}/stage3_*.csv")


if __name__ == "__main__":
    main()
