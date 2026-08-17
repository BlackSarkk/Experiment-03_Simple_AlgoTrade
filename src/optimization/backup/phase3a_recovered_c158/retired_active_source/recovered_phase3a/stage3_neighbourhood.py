"""
Phase 3A — Stage 3 neighbourhood robustness around the Stage-2 winner.

Answers one question: is #539 a stable region or a lucky exact combination?

Sampling (deterministic, seed 42) — not the 3^11 Cartesian space:
  1. every single-parameter +/-1 step perturbation          (22)
  2. representative pairwise +/-1 combinations              (100, sampled)
  3. random local combinations, 3-5 params, +/-1..2 steps   (128)

Reuses the frozen Stage-2 machinery unchanged: same objective, same acceptance gates,
same risk policy from configs/riskmanager.json, same 60/20/20 split, LONG only.
TRAIN + VALIDATION only. HOLDOUT is never read.
"""

import os
import sys
import json
import time
import random
import itertools

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd

from optimization.core_15m_long_optimizer import (
    STAGE2_SPACE, RESULTS_DIR, SEED,
    load_frozen_risk_policy, load_dataset, split_indices,
    run_partition, score_candidate, meets_minimum, passes_acceptance,
)

INCUMBENT = {
    "ema_period": 104, "rsi_period": 15, "rsi_overbought": 76.0, "rsi_oversold": 42.0,
    "atr_period": 15, "consolidation_candles": 14, "consolidation_atr_mult": 3.0,
    "swing_lookback": 8, "volume_sma_period": 36, "volume_mult": 1.7,
    "risk_reward_ratio": 3.0,
}
BASE_SCORE = 0.32823          # Stage-2 reported score for #539

N_PAIRS = 100
N_RANDOM = 128
PARAMS = list(INCUMBENT.keys())


def clamp(name, value):
    kind, lo, hi, step = STAGE2_SPACE[name]
    value = max(lo, min(hi, value))
    return int(round(value)) if kind == "int" else round(value, 6)


def perturb(base, deltas):
    """deltas: {param: n_steps}. Returns None if nothing actually moved (hit a bound)."""
    out = dict(base)
    for p, n in deltas.items():
        step = STAGE2_SPACE[p][3]
        out[p] = clamp(p, base[p] + n * step)
    return None if out == base else out


def build_neighbourhood():
    rng = random.Random(SEED)
    seen, items = set(), []

    def add(params, kind, label):
        if params is None:
            return
        key = tuple(params[p] for p in PARAMS)
        if key in seen:
            return
        seen.add(key)
        items.append((params, kind, label))

    add(dict(INCUMBENT), "incumbent", "base")

    for p in PARAMS:                                   # 1. single +/-1
        for d in (-1, 1):
            add(perturb(INCUMBENT, {p: d}), "single", f"{p}{d:+d}")

    pairs = list(itertools.combinations(PARAMS, 2))     # 2. pairwise +/-1
    rng.shuffle(pairs)
    for a, b in pairs:
        if len([i for i in items if i[1] == "pair"]) >= N_PAIRS:
            break
        da, db = rng.choice([-1, 1]), rng.choice([-1, 1])
        add(perturb(INCUMBENT, {a: da, b: db}), "pair", f"{a}{da:+d},{b}{db:+d}")

    while len([i for i in items if i[1] == "random"]) < N_RANDOM:   # 3. random local
        k = rng.randint(3, 5)
        chosen = rng.sample(PARAMS, k)
        deltas = {p: rng.choice([-2, -1, 1, 2]) for p in chosen}
        add(perturb(INCUMBENT, deltas), "random",
            ",".join(f"{p}{d:+d}" for p, d in deltas.items()))

    return items


def main():
    risk_cfg, risk_hash, risk_raw = load_frozen_risk_policy()
    df, sha = load_dataset()
    idx = split_indices(len(df))
    items = build_neighbourhood()

    print(f"[Stage3] incumbent #539 | base score {BASE_SCORE}")
    print(f"  dataset sha {sha[:16]}… | risk sha {risk_hash[:16]}… | risk {risk_raw}")
    print(f"  train {idx['train'][1]-idx['train'][0]:,} | val {idx['validation'][1]-idx['validation'][0]:,}")
    print("  HOLDOUT not read.")
    print(f"  neighbours: {len(items)-1} (+ incumbent re-evaluation)")

    rows, t0 = [], time.time()
    for i, (params, kind, label) in enumerate(items):
        tm = run_partition(df, idx["train"], params, risk_cfg)
        vm = run_partition(df, idx["validation"], params, risk_cfg)
        sc = score_candidate(tm, vm)
        ok_min = meets_minimum(tm, vm)
        rows.append({
            "n": i, "kind": kind, "label": label,
            "score": sc["final_score"] if ok_min else -10.0,
            "raw_score": sc["final_score"],
            "meets_minimum": ok_min,
            "passes_acceptance": bool(ok_min and passes_acceptance(tm, vm, sc)),
            "consistency_gap": sc["consistency_gap"],
            **{f"train_{k}": v for k, v in tm.items()},
            **{f"val_{k}": v for k, v in vm.items()},
            **params,
        })
        if (i + 1) % 50 == 0:
            print(f"    {i+1}/{len(items)}  ({time.time()-t0:.0f}s)")

    out = pd.DataFrame(rows)
    out["degradation_pct"] = (BASE_SCORE - out["score"]) / abs(BASE_SCORE) * 100.0
    os.makedirs(RESULTS_DIR, exist_ok=True)
    csv = os.path.join(RESULTS_DIR, "stage3_neighbourhood.csv")
    out.sort_values("score", ascending=False).to_csv(csv, index=False)

    manifest = {
        "stage": 3, "incumbent_trial": 539, "incumbent_params": INCUMBENT,
        "base_score": BASE_SCORE, "neighbours": len(out) - 1,
        "sampling": {"single": 22, "pairwise": N_PAIRS, "random_local": N_RANDOM, "seed": SEED},
        "dataset_sha256": sha, "risk_policy_sha256": risk_hash, "risk_policy_values": risk_raw,
        "split": {k: {"rows": v[1] - v[0]} for k, v in idx.items()},
        "holdout_evaluated": False,
        "elapsed_seconds": round(time.time() - t0, 1),
    }
    with open(os.path.join(RESULTS_DIR, "stage3_manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"\n[Stage3] {len(out)} configs in {time.time()-t0:.0f}s -> {csv}")


if __name__ == "__main__":
    main()
