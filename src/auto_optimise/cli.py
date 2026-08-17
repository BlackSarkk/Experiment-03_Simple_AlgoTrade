"""Optimizer entry point.

  ./pipeline.sh --optimize --<preset>.json --<output>.json

One syntax, positional by order: preset first, output second. V1 validates
everything and prints the run plan. The optimization phases themselves are not
implemented yet.
"""

import os
import sys
import time

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from auto_optimise import output_guard, preset as preset_mod, ui, v3_runplan
else:
    from . import output_guard, preset as preset_mod, ui, v3_runplan

USAGE = """\
./pipeline.sh --optimize --<preset>.json --<output>.json

  --<preset>.json   optimizer input, resolved under configs/optimize/
  --<output>.json   runnable strategy config to create in configs/config/
                    MANDATORY. Never auto-generated, never overwritten.

  --plan-only       validate and print the run plan, then stop before stage 1
  --no-resume       ignore any existing run directory and start a fresh campaign

Example:
  ./pipeline.sh --optimize --odefault.json --mywinner.json
"""


def _parse(argv):
    """Return (preset_arg, output_arg, plan_only, no_resume)."""
    names = []
    plan_only = False
    no_resume = False
    for token in argv:
        if token in ("--help", "-h"):
            print(USAGE)
            sys.exit(0)
        if token == "--plan-only":
            plan_only = True
            continue
        if token == "--no-resume":
            no_resume = True
            continue
        if token.startswith("--") and token.endswith(".json") and "=" not in token:
            names.append(token[2:])
        else:
            ui.error_exit(f"unrecognized optimizer argument: {token}", USAGE.strip())

    if not names:
        ui.error_exit("no optimizer preset given.", USAGE.strip())
    if len(names) == 1:
        ui.error_exit(
            "no output config name given.\n"
            "       The optimizer never invents one. Pass it explicitly, e.g.\n"
            "         ./pipeline.sh --optimize --odefault.json --mywinner.json"
        )
    if len(names) > 2:
        ui.error_exit(
            f"expected exactly two names (preset, output), got {len(names)}: "
            + ", ".join(names),
            USAGE.strip(),
        )
    return names[0], names[1], plan_only, no_resume


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    preset_arg, output_arg, plan_only, no_resume = _parse(argv)

    try:
        loaded = preset_mod.load(preset_arg)
    except preset_mod.PresetError as exc:
        ui.error_exit(str(exc))

    try:
        target = output_guard.validate(output_arg)
    except output_guard.OutputNameError as exc:
        ui.error_exit(str(exc))

    if plan_only:
        # Budgets, partition policy and the market-rule RULE are shown without a
        # single network call, Optuna study or file write.
        print(v3_runplan.render(loaded, target))
        print(ui.dim("\n--plan-only: stopping before any stage runs. "
                     "No data was loaded, no trial ran, nothing was written."))
        return 0

    # ---- run the campaign --------------------------------------------------
    # Imported here so preset/output validation errors never pay the cost of
    # loading pandas and the trading stack.
    if __package__ in (None, ""):
        from auto_optimise import dataprep, v3_controller
    else:
        from . import dataprep, v3_controller

    try:
        summary = v3_controller.run_campaign(loaded, target, resume=not no_resume)
    except dataprep.DataPreparationError as exc:
        ui.error_exit(str(exc))
    except FileNotFoundError as exc:
        ui.error_exit(str(exc))
    except AssertionError as exc:
        ui.error_exit(f"pipeline invariant violated: {exc}")

    return 1 if summary.get("failed_at") else 0


if __name__ == "__main__":
    sys.exit(main())
