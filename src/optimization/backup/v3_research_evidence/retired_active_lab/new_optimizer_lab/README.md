# new_optimizer_lab — quarantine inspection layer

`src/optimization/multi_tf_optimizer.py` and `src/optimization/deep_15m_optimizer.py` are
**isolated historical research scripts**. Nothing in the project imports or calls either one:
`pipeline.sh` dispatches only to `src/auto_optimise/cli.py` and `src/main.py`, neither of which
touches `src/optimization/`. `src/optimization/` has no `__init__.py`, so it is not even a regular
importable package. Both engines are standalone `__main__` scripts with every setting hardcoded as a
module-level constant — no CLI arguments, no symbol/date/trial overrides. Neither has been run in
this checkout: neither `results/multi_tf_optimization/` nor `results/15m_deep_optimization/` exists.
`deep_15m_optimizer.py` is gitignored and was never committed.

This lab makes them **inspectable without making them reachable**. It is a dispatcher and
inspection layer only:

    PYTHONPATH=src python -m optimization.new_optimizer_lab --engine multi_tf  --plan-only
    PYTHONPATH=src python -m optimization.new_optimizer_lab --engine deep_15m --plan-only

`--plan-only` is the **only** supported mode; any other invocation is refused with exit code 2.
Launching either engine for real is a separate, explicitly authorised decision that this lab does
not make.

## How it stays side-effect free

The lab **never imports the engine modules.** It reads their declarations statically with `ast`, so
a plan run cannot import optuna, cannot construct a `MarketDataLoader`, cannot fetch or load data,
cannot create an output directory and cannot run a backtest. Each plan prints a self-check listing
which of optuna / market_data / pandas / numpy got imported (none) and whether the engine's output
directory exists (no).

Values shown as `<expr: ...>` are not literals in the source — the lab prints the source expression
verbatim rather than guessing. `multi_tf`'s `consolidation_candles` upper bound is one of these: it
is the variable `cons_candles_max`, which differs per timeframe.

## What this lab deliberately does not do

It does not repair, refactor, merge, modernise or re-point either engine. Both keep their original
mathematics, their documented leakage defects and their hardcoded scope. Fixing them is a separate
decision; see `COMPARISON.md` for the defect inventory and the keep / repair / abandon call.

Nothing outside this directory was created or modified. `src/auto_optimise/` is out of scope for
this lab and was not inspected, modified or compared.
