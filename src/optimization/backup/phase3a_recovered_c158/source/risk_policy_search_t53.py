"""
Phase 3A — quick risk-policy search for Trial #53 (comparison experiment only).

Strategy parameters are FIXED at Trial #53. Only leverage / risk_per_trade_pct /
max_position_allocation_pct are searched. RiskManager formulas, execution rules,
objective and acceptance gates are unchanged. TRAIN + VALIDATION only; HOLDOUT untouched.

Note on ranges: with the corrected margin-based cap, margin = notional/leverage and
notional <= equity*alloc*leverage, so margin <= equity*alloc <= equity for alloc<=1.0.
No combination in the suggested box can produce an impossible margin requirement, and
the RiskManager's own margin guard is the backstop. Ranges are therefore used as given.
"""

import os
import sys
import json
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd
import optuna
from optuna.samplers import TPESampler

from common.config import RiskConfig
from optimization.core_15m_long_optimizer import (
    RESULTS_DIR, SEED, load_frozen_risk_policy, load_dataset, split_indices,
    run_partition, score_candidate, meets_minimum, passes_acceptance,
)

optuna.logging.set_verbosity(optuna.logging.WARNING)

TRIAL_53 = {
    "ema_period": 105, "rsi_period": 18, "rsi_overbought": 80.0, "rsi_oversold": 33.0,
    "atr_period": 11, "consolidation_candles": 14, "consolidation_atr_mult": 3.3,
    "swing_lookback": 8, "volume_sma_period": 32, "volume_mult": 1.5,
    "risk_reward_ratio": 2.7,
}
N_TRIALS = 200


def mk_risk(base: RiskConfig, lev, risk_pct, alloc_pct) -> RiskConfig:
    rc = RiskConfig(**{f.name: getattr(base, f.name) for f in base.__dataclass_fields__.values()})
    rc.leverage = lev
    rc.risk_per_trade_pct = risk_pct / 100.0
    rc.max_position_allocation_pct = alloc_pct / 100.0
    return rc


def perf(m):
    return {"return_pct": m["net_return_pct"], "net_pnl": round(10000.0 * m["net_return_pct"] / 100.0, 2),
            "pf": m["profit_factor"], "expR": m["expectancy_R"], "dd": m["max_dd_pct"],
            "trades": m["n_trades"]}


def main():
    base_risk, risk_hash, risk_raw = load_frozen_risk_policy()
    df, sha = load_dataset()
    idx = split_indices(len(df))

    print(f"[RiskSearch] Trial #53 fixed | dataset sha {sha[:16]}… | HOLDOUT not read")

    # Baseline at the frozen policy
    b_tm = run_partition(df, idx["train"], TRIAL_53, base_risk)
    b_vm = run_partition(df, idx["validation"], TRIAL_53, base_risk)
    b_sc = score_candidate(b_tm, b_vm)
    print(f"  baseline (1.0x / 1.5% / 50%) score {b_sc['final_score']:.5f} "
          f"| train ret {b_tm['net_return_pct']:.2f}% | val ret {b_vm['net_return_pct']:.2f}%")

    rows = []

    def objective(trial):
        lev = trial.suggest_float("leverage", 1.0, 5.0, step=0.1)
        rpt = trial.suggest_float("risk_per_trade_pct", 0.5, 3.0, step=0.1)
        alc = trial.suggest_float("max_position_allocation_pct", 20.0, 80.0, step=1.0)
        rc = mk_risk(base_risk, lev, rpt, alc)
        tm = run_partition(df, idx["train"], TRIAL_53, rc)
        vm = run_partition(df, idx["validation"], TRIAL_53, rc)
        sc = score_candidate(tm, vm)
        ok = meets_minimum(tm, vm)
        rows.append({
            "trial": trial.number, "leverage": lev, "risk_per_trade_pct": rpt,
            "max_position_allocation_pct": alc,
            "score": sc["final_score"] if ok else -10.0,
            "meets_minimum": ok, "accepted": bool(ok and passes_acceptance(tm, vm, sc)),
            "consistency_gap": sc["consistency_gap"],
            **{f"train_{k}": v for k, v in perf(tm).items()},
            **{f"val_{k}": v for k, v in perf(vm).items()},
        })
        for k, v in rows[-1].items():
            if k not in ("trial",):
                trial.set_user_attr(k, v)
        return rows[-1]["score"]

    t0 = time.time()
    study = optuna.create_study(direction="maximize", sampler=TPESampler(seed=SEED))
    study.optimize(objective, n_trials=N_TRIALS, n_jobs=1)
    elapsed = time.time() - t0

    out = pd.DataFrame(rows).sort_values("score", ascending=False)
    out.to_csv(os.path.join(RESULTS_DIR, "risk_search_t53.csv"), index=False)

    with open(os.path.join(RESULTS_DIR, "risk_search_t53_manifest.json"), "w") as f:
        json.dump({"strategy": TRIAL_53, "n_trials": N_TRIALS, "seed": SEED, "n_jobs": 1,
                   "ranges": {"leverage": [1.0, 5.0], "risk_per_trade_pct": [0.5, 3.0],
                              "max_position_allocation_pct": [20.0, 80.0]},
                   "ranges_narrowed": False,
                   "baseline": {"leverage": 1.0, "risk_per_trade_pct": 1.5,
                                "max_position_allocation_pct": 50.0,
                                "score": b_sc["final_score"],
                                "train": perf(b_tm), "val": perf(b_vm)},
                   "dataset_sha256": sha, "holdout_evaluated": False,
                   "elapsed_seconds": round(elapsed, 1)}, f, indent=2)

    print(f"\n[RiskSearch] {N_TRIALS} trials in {elapsed:.0f}s | best {study.best_value:.5f}")


if __name__ == "__main__":
    main()
