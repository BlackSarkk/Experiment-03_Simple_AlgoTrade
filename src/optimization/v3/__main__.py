"""V3 CLI. `--plan-only` is declaration printing only.

It imports ONLY `spec` and `scoring`, both stdlib-only, so a plan run cannot import pandas,
numpy, optuna, the production engine or the data loader, and cannot create a directory.
"""
import argparse
import sys

from . import scoring, spec


def _rng(d):
    for name, (kind, lo, hi, step) in d.items():
        yield f"    {name:<26} {kind:<6} {lo} .. {hi}  step {step}"


def plan() -> int:
    print("=" * 88)
    print(f"NEW OPTIMIZER V3 — PLAN ONLY   version {spec.VERSION}")
    print("  nothing executed · no market data · no Optuna · no pandas · no directory created")
    print("=" * 88)

    print("\nDIRECTION")
    print(f"  long_enabled  {spec.LONG_ENABLED}   short_enabled {spec.SHORT_ENABLED}"
          "   (hardcoded, never a search dimension)")

    print("\nBUDGETS")
    print(f"  stage 1a broad strategy      {spec.BROAD_TRIALS} trials   11 dims, neutral risk")
    print(f"  stage 1b narrowed strategy   {spec.NARROW_TRIALS} trials   11 dims, derived ranges")
    print(f"  stage 1c risk seed           {spec.RISK_SEED_TRIALS} trials    3 dims, strategy frozen")
    print(f"  stage 2a seeded final config {spec.FINAL_TRIALS} trials   14 dims, SEED enqueued as trial 0")
    print(f"  stage 2b Bollinger           {spec.BOLL_TRIALS} trials    6 dims, strategy+risk frozen")
    print(f"  sampler TPE seed {spec.SEED} · n_jobs {spec.N_JOBS} · initial capital "
          f"{spec.INITIAL_CAPITAL:,.0f} · TRAIN/VALID {int(spec.TRAIN_FRAC*100)}/{int((1-spec.TRAIN_FRAC)*100)}")

    print("\nNEUTRAL RISK (stages 1a and 1b — identical for every trial)")
    print(f"  leverage {spec.NEUTRAL_RISK['leverage']}x · risk/trade "
          f"{spec.NEUTRAL_RISK['risk_per_trade_pct']*100:.1f}% · allocation "
          f"{spec.NEUTRAL_RISK['max_position_allocation_pct']*100:.0f}%")

    print("\nEXECUTION")
    print(f"  commission {spec.COMMISSION_PCT}% taker (entry and exit) · slippage "
          f"{spec.SLIPPAGE_TICKS} tick adverse · quantity step {spec.QUANTITY_STEP}")
    print("  tick size per symbol: " + " · ".join(f"{k}={v}" for k, v in spec.TICK_SIZE.items())
          + "   (undeclared symbol raises)")

    print("\nSEARCH SPACE — 11 strategy dimensions")
    print("\n".join(_rng(spec.STRATEGY_RANGES)))
    print("  3 risk dimensions (fractions, not percent)")
    print("\n".join(_rng(spec.RISK_RANGES)))
    print("  6 Bollinger dimensions (stage 2b)")
    print("\n".join(_rng(spec.BOLLINGER_RANGES)))

    print("\nSEED FLOW (stage 1)")
    print("  1a broad 11-dim search at neutral risk, scored on TRAIN+VALID")
    print(f"  1b narrow: top {spec.NARROW_TOP_FRACTION:.0%} of gated 1a trials (min "
          f"{spec.NARROW_MIN_CANDIDATES}) -> per-dimension observed [min,max], widened "
          f"{spec.NARROW_WIDEN_STEPS} step each side, clipped to the 1a bounds")
    print("  1b re-searches the narrowed space; best gated strategy across 1a+1b is FROZEN")
    print("  1c searches leverage / risk_per_trade_pct / max_position_allocation_pct only")
    print("  -> output: exactly ONE complete 14-dimension seed")

    print("\nFINAL FLOW (stage 2)")
    print("  2a enqueue_trial(seed) -> becomes trial 0, then "
          f"{spec.FINAL_TRIALS} joint 14-dim trials (seed included in the budget)")
    print("  2a winner = highest score among gated trials, ties by lower trial number")
    print("  2b 150 Bollinger trials on the frozen winner; TRAIN and VALID both scored")
    print("  2b if no filter clears the gate, V3 ships Bollinger DISABLED and reports it")

    print("\nDATA BOUNDARIES")
    for line in spec.DATA_CONTRACT.split(". "):
        if line.strip():
            print(f"  {line.strip().rstrip('.')}.")
    print(f"  fixed evaluation skip: {spec.EVAL_SKIP_BARS} leading bars of every partition are")
    print("    non-tradeable for EVERY candidate, so the evaluated window does not move with")
    print(f"    ema_period (the frozen strategy's own skip maxes at "
          f"{max(spec.STRATEGY_RANGES['ema_period'][2] + 10, 60)})")

    print("\nGATE — graded, never a flat sentinel")
    print(f"  minimum trades: max({spec.MIN_TRADES_FLOOR}, partition_rows // {spec.MIN_TRADES_PER_ROWS})"
          " per partition")
    for k, v in spec.GATE.items():
        print(f"  {k:<30} {v}")
    print(f"  each violated requirement contributes up to 1.0 of shortfall (6 requirements);")
    print(f"  failing score = {spec.FAIL_BASE} - {spec.FAIL_SPAN} * shortfall/6  ->  "
          f"[{spec.FAIL_BASE - spec.FAIL_SPAN}, {spec.FAIL_BASE}]")
    print("  passing scores are bounded strictly above that band, so TPE always has ordering")

    print(f"\nSCORE — {spec.SCORE_VERSION}  (weights sum shown as signed contribution caps)")
    for k, v in spec.W.items():
        sign = "-" if k in ("va_dd", "consistency") else "+"
        print(f"  {sign}{v:<5} {k}")
    print("  caps: " + " · ".join(f"{k}={v}" for k, v in spec.CAPS.items()))
    lo, hi = _score_bounds()
    print(f"  reachable passing range approximately [{lo:+.3f}, {hi:+.3f}]  (fail band tops out at "
          f"{spec.FAIL_BASE:+.3f})")
    print(f"  VALID weight {spec.W['va_ret']+spec.W['va_pf']+spec.W['va_dd']+spec.W['va_sample']:.2f} "
          f"vs TRAIN {spec.W['tr_ret']+spec.W['tr_pf']:.2f}; VALID return alone is only "
          f"{spec.W['va_ret']:.2f}")

    print(f"\nBOLLINGER SCORE — {spec.BOLL_SCORE_VERSION}")
    for k, v in spec.BW.items():
        print(f"  +{v:<5} {k}")
    print(f"  gate: VALID trades >= the same stage-1 floor · TRAIN trades >= the stage-1 floor ·"
          f" VALID trade retention >= {spec.BOLL_MIN_TRADE_RETENTION:.0%}")
    print(f"  net P&L carries {spec.BW['va_netpnl']+spec.BW['tr_netpnl']:.2f} across both partitions,"
          f" versus {spec.BW['va_pf']+spec.BW['tr_pf']:.2f} for profit factor")
    print("=" * 88)
    print("PLAN ONLY — no campaign, no backtest, no Optuna trial, no fetch.")
    loaded = [m for m in ("pandas", "numpy", "optuna", "common.market_data",
                          "backtest.engine", "strategy.indicators") if m in sys.modules]
    print(f"heavy modules imported by this run: {loaded if loaded else 'none'}")
    return 0


def _score_bounds():
    """Analytic bounds of the passing score, from the declared weights and caps."""
    w = spec.W
    hi = w["va_ret"] + w["va_pf"] + w["va_sample"] + w["tr_ret"] + w["tr_pf"]
    lo = -(w["va_dd"] + w["consistency"])
    return lo, hi


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m optimization.new_optimizer_v3",
                                 description="New Optimizer V3 — two-stage seed -> config. "
                                             "--plan-only is the only implemented mode.")
    ap.add_argument("--plan-only", action="store_true")
    a = ap.parse_args(argv)
    if not a.plan_only:
        print("REFUSED: --plan-only is the only mode this CLI implements.\n"
              "Running a V3 campaign requires a prepared warmup+DEV frame and an explicit "
              "authorisation; drive optimizer.Campaign from a harness when that time comes.")
        return 2
    return plan()


if __name__ == "__main__":
    sys.exit(main())
