"""New-optimizer lab — dispatcher / inspection layer. Quarantine only.

    python -m optimization.new_optimizer_lab --engine multi_tf  --plan-only
    python -m optimization.new_optimizer_lab --engine deep_15m --plan-only

Both engines remain untouched historical research scripts. This module NEVER imports them:
declarations are read statically with `ast`, so `--plan-only` cannot import optuna, cannot
construct a MarketDataLoader, cannot fetch or load data, cannot create an output directory
and cannot run a backtest. Nothing here repairs, refactors, merges or modernises either
engine — it only reports what each one declares about itself.

Anything other than --plan-only is refused: actually launching these engines is a separate,
explicitly authorised decision, not something this lab does.
"""
import argparse
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OPTDIR = os.path.dirname(HERE)

ENGINES = {
    "multi_tf": {
        "file": "multi_tf_optimizer.py",
        "title": "Multi-timeframe parameter optimizer (Phase 5 header; Candidate-#5-era infrastructure)",
        "entry": "main() -> optimize_timeframe(tf) for each tf in TIMEFRAMES",
        "suggest_fn": "suggest_params",
        "consts": ["SEED", "DATA_DIR", "OUT_DIR", "INITIAL_CAPITAL", "START_DATE", "END_DATE",
                   "TRIALS_PER_TF", "TIMEFRAMES"],
        "split": "50 / 25 / 25 by ROW INDEX on the raw frame (split_data, L278-284): "
                 "train=iloc[:50%], val=iloc[50%:75%], hold=iloc[75%:]",
        "partitions_used": "TRAIN and VALIDATION are BOTH scored inside the objective "
                           "(objective L306-319). HOLDOUT is evaluated after the search for the "
                           "single best trial only (L344) and feeds the printed `robust` flag.",
        "objective": "generalization_score = 0.6*robust_score(TRAIN) + 0.4*robust_score(VALID); "
                     "robust_score = ret*0.3 + min(PF,5)*15 - dd*1.5 + clip(sharpe)*10 + "
                     "win_rate*20 + clip(expectancy)*0.05, with trade penalties (-30 if n<15, "
                     "-10 if n<30) and DD cliffs (-20 if dd>25, -50 if dd>40); returns -1000 if n<5",
        "gates": "no hard rejection — only score penalties. Post-hoc `robust` flag requires "
                 "val/hold trades >= 5, val/hold PF >= 1.0, val/hold DD < 40%",
        "robustness": "none inside the search (no perturbation, no regime testing)",
        "bollinger": "not searched, not applied — no filter anywhere in this engine",
        "limits": [
            "Indicators are computed on the ALREADY-SLICED partition (run_backtest_on_slice L122), "
            "so every rolling window restarts at the partition edge — no warmup. Contradicts "
            "main.py, which computes indicators on the full frame before slicing.",
            "leverage / risk_per_trade_pct / max_position_allocation_pct are sampled in the SAME "
            "study as the strategy parameters, so a candidate can out-rank a better signal purely "
            "by sizing harder.",
            "`side_choice` (both / long_only / short_only) is a search dimension, so direction is "
            "decided by the sampler rather than fixed by the campaign.",
            "VALIDATION is inside the objective, so TPE optimises against it directly — it is a "
            "second training set, not a generalisation check.",
            "Objective is dollar-return-weighted and hand-tuned; sizing is not neutralised.",
            "Runs all 10 timeframes sequentially in one invocation; no CLI arguments at all "
            "(no --symbol, --dates, --trials) — every setting is a module-level constant.",
            "Requires data pre-downloaded by fetch_data.py; 2m is resampled from 1m.",
            "consolidation_candles' upper bound is the variable cons_candles_max (L211): 15 for "
            "1m/2m/3m/5m, 20 for every other timeframe — so the space silently differs per "
            "timeframe, which is why --plan-only shows it as an expression rather than a number.",
        ],
    },
    "deep_15m": {
        "file": "deep_15m_optimizer.py",
        "title": "Deep 15m 5-stage optimizer (gitignored scratch script, never committed)",
        "entry": "main() -> stage1_search -> stage2_stability -> stage3_regimes -> "
                 "stage4_holdout -> stage5_risk",
        "suggest_fn": "_objective",
        "consts": ["SEED", "OUT_DIR", "DATA_DIR", "SYMBOL", "TIMEFRAME", "START_DATE", "END_DATE",
                   "INITIAL_CAPITAL", "COMMISSION_PCT", "SLIPPAGE_TICKS", "TRIALS"],
        "split": "50 / 25 / 25 by ROW INDEX on the raw frame (L135-140): "
                 "train=iloc[:50%], val=iloc[50%:75%], hold=iloc[75%:]",
        "partitions_used": "Stage 1 scores TRAIN+VALID jointly. Stage 2 (stability) evaluates on "
                           "VALIDATION. Stage 3 (regimes) evaluates on the FULL frame incl. "
                           "holdout. Stage 4 reports TRAIN/VAL/HOLDOUT and SELECTS on holdout PF. "
                           "Stage 5 (risk) optimises on `self.df` = the FULL frame incl. holdout.",
        "objective": "stage1: score = TRAIN net_return_pct + VALID net_return_pct, then "
                     "-1000 if train n<50 or val n<20; -500 if either DD>30%; "
                     "-500 if train PF<1.1 or val PF<1.0. "
                     "stage5 (risk): score = net_return_pct on the full frame, "
                     "minus 1000*(DD-35) when DD>35%",
        "gates": "stage1 penalties above; stage2 requires avg perturbation PF>1.05 and "
                 "min perturbation PF>0.95; stage3 requires >=3 of 5 calendar regimes with PF>1.0",
        "robustness": "stage2 perturbs 4 of 11 parameters one at a time (EMA +/-5, RSI period "
                      "+/-2, RSI bounds +/-2, RR +/-0.2) and gates on the MEAN perturbation PF; "
                      "stage3 scores 5 hardcoded calendar windows (H1/H2 2024, H1/H2 2025, 2026 YTD)",
        "bollinger": "not searched, not applied — no filter anywhere in this engine",
        "limits": [
            "Indicators are computed on the already-sliced partition (run_backtest L106, with the "
            "comment 'In a perfect world we compute once and slice') — no warmup, rolling windows "
            "restart at every partition and regime edge.",
            "study.optimize(..., n_jobs=4) under TPESampler(seed=42): parallel trials make the "
            "seeded sampler NON-REPRODUCIBLE. Same for the 150-trial stage5 risk search.",
            "Stage 3 and Stage 5 both read the HOLDOUT slice, and Stage 4 selects the winner by "
            "holdout PF — the holdout is used for selection, so it is not a holdout.",
            "Stage 5 optimises the risk policy on the full frame including the data later used to "
            "validate it: direct leakage.",
            "ema_period range is 10-100, so it cannot emit Candidate #158 (EMA 104) or Trial #53 "
            "(EMA 105) at all.",
            "max_alloc grid is 20-100 step 10, so 28% (historical risk trial #158) is unreachable.",
            "`side` (both / long_only / short_only) is a search dimension.",
            "A CANDIDATE_5 dict is prepared for enqueueing at L192 with the comment '# enqueue "
            "cand 5', but study.enqueue_trial is never called — the incumbent seed is dead code.",
            "File ends with a duplicated `if __name__ == '__main__': main()` block.",
            "No CLI arguments; every setting is a module-level constant.",
        ],
    },
}


def literal(node):
    """Literal value if it is one, otherwise the source expression verbatim."""
    try:
        return ast.literal_eval(node)
    except Exception:
        try:
            return f"<expr: {ast.unparse(node)}>"
        except Exception:
            return "<non-literal>"


def read_declarations(path, want):
    """Static read of module-level constants and trial.suggest_* calls. Never imports."""
    tree = ast.parse(open(path).read())
    consts = {}
    for n in tree.body:
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id in want:
                    consts[t.id] = literal(n.value)
                elif isinstance(t, ast.Tuple):  # e.g. A, B = 1, 2
                    names = [e.id for e in t.elts if isinstance(e, ast.Name)]
                    vals = literal(n.value)
                    if isinstance(vals, tuple) and len(vals) == len(names):
                        for k, v in zip(names, vals):
                            if k in want:
                                consts[k] = v
    dims = []
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr.startswith("suggest_")):
            name = literal(n.args[0]) if n.args else "?"
            rest = [literal(a) for a in n.args[1:]]
            kw = {k.arg: literal(k.value) for k in n.keywords}
            dims.append((n.lineno, n.func.attr, name, rest, kw))
    return consts, dims


RISK_DIMS = {"leverage", "risk_per_trade_pct", "max_position_allocation_pct", "max_alloc"}
DIR_DIMS = {"side", "side_choice", "long_enabled", "short_enabled"}


def plan(key):
    e = ENGINES[key]
    path = os.path.join(OPTDIR, e["file"])
    if not os.path.isfile(path):
        print(f"ENGINE SOURCE MISSING: {path}")
        return 1
    consts, dims = read_declarations(path, set(e["consts"]))
    seen, uniq = set(), []
    for ln, fn, name, rest, kw in dims:
        if name in seen:
            continue
        seen.add(name)
        uniq.append((ln, fn, name, rest, kw))
    strat = [d for d in uniq if d[2] not in RISK_DIMS | DIR_DIMS]
    risk = [d for d in uniq if d[2] in RISK_DIMS]
    direc = [d for d in uniq if d[2] in DIR_DIMS]

    print("=" * 88)
    print(f"NEW-OPTIMIZER LAB — PLAN ONLY (nothing executed, nothing loaded)")
    print(f"engine        {key}")
    print(f"source        src/optimization/{e['file']}   (untouched, never imported)")
    print(f"title         {e['title']}")
    print(f"entry point   {e['entry']}")
    print("=" * 88)
    print("\nDECLARED SCOPE (read statically from the source)")
    for k in e["consts"]:
        if k in consts:
            print(f"  {k:<18} {consts[k]!r}")
    print(f"\nSEARCH DIMENSIONS via {e['suggest_fn']} — "
          f"{len(strat)} strategy, {len(risk)} risk, {len(direc)} direction")
    for label, group in (("strategy", strat), ("risk", risk), ("direction", direc)):
        if not group:
            continue
        print(f"  [{label}]")
        for ln, fn, name, rest, kw in group:
            step = f" step={kw['step']}" if "step" in kw else ""
            rng = f"{rest}" if rest else ""
            print(f"    L{ln:<5} {fn:<22} {name:<28} {rng}{step}")
    print(f"\nSPLIT\n  {e['split']}")
    print(f"\nPARTITION USAGE\n  {e['partitions_used']}")
    print(f"\nOBJECTIVE\n  {e['objective']}")
    print(f"\nGATES\n  {e['gates']}")
    print(f"\nROBUSTNESS\n  {e['robustness']}")
    print(f"\nBOLLINGER\n  {e['bollinger']}")
    print(f"\nOUTPUT LOCATION\n  {consts.get('OUT_DIR', '<not declared>')}  "
          f"(NOT created by --plan-only)")
    print("\nKNOWN LIMITATIONS")
    for i, l in enumerate(e["limits"], 1):
        print(f"  {i:>2}. {l}")
    print("\nPLAN ONLY — no data loaded, no MarketDataLoader constructed, no Optuna study "
          "created,\n             no output directory created, no backtest run.")
    # prove it
    leaked = [m for m in ("optuna", "common.market_data", "pandas", "numpy") if m in sys.modules]
    print(f"\nside-effect check: modules imported by this run: "
          f"{leaked if leaked else 'none of optuna / market_data / pandas / numpy'}")
    print(f"                   output dir exists: "
          f"{os.path.isdir(os.path.join(os.getcwd(), consts.get('OUT_DIR', 'x')))}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m optimization.new_optimizer_lab",
                                 description="Inspection layer for the new-optimizer attempt. "
                                             "Quarantine only; --plan-only is the only mode.")
    ap.add_argument("--engine", required=True, choices=sorted(ENGINES))
    ap.add_argument("--plan-only", action="store_true")
    a = ap.parse_args(argv)
    if not a.plan_only:
        print("REFUSED: --plan-only is the only supported mode.\n"
              "This lab inspects and dispatches; it does not launch these engines. Running either "
              "one is a separate, explicitly authorised decision — and note both write to "
              "results/ paths of their own and both have documented leakage defects "
              "(see COMPARISON.md).")
        return 2
    return plan(a.engine)


if __name__ == "__main__":
    sys.exit(main())
