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
    from auto_optimise import output_guard, preset as preset_mod, runplan, ui
else:
    from . import output_guard, preset as preset_mod, runplan, ui

USAGE = """\
./pipeline.sh --optimize --<preset>.json --<output>.json

  --<preset>.json   optimizer input, resolved under configs/optimize/
  --<output>.json   runnable strategy config to create in configs/config/
                    MANDATORY. Never auto-generated, never overwritten.

Example:
  ./pipeline.sh --optimize --odefault.json --mywinner.json
"""


def _parse(argv):
    """Return (preset_arg, output_arg). Extra positional .json args are an error."""
    names = []
    for token in argv:
        if token in ("--help", "-h"):
            print(USAGE)
            sys.exit(0)
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
    return names[0], names[1]


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    preset_arg, output_arg = _parse(argv)

    try:
        loaded = preset_mod.load(preset_arg)
    except preset_mod.PresetError as exc:
        ui.error_exit(str(exc))

    try:
        target = output_guard.validate(output_arg)
    except output_guard.OutputNameError as exc:
        ui.error_exit(str(exc))

    print(runplan.render(loaded, target))

    # ---- stage [1/6] -------------------------------------------------------
    # Imported here so preset/output validation errors never pay the cost of
    # loading pandas and the trading stack.
    if __package__ in (None, ""):
        from auto_optimise import dataprep
    else:
        from . import dataprep

    print("")
    print(ui.info("[1/6] Data Preparation") + ui.dim("  running..."))
    started = time.time()
    try:
        prepared = dataprep.prepare(loaded, progress=lambda m: print("      " + ui.dim(m)))
    except dataprep.DataPreparationError as exc:
        ui.error_exit(str(exc))
    except AssertionError as exc:
        ui.error_exit(f"data preparation invariant violated: {exc}")

    print(runplan.render_stage_report(loaded, prepared, time.time() - started))
    return 0


if __name__ == "__main__":
    sys.exit(main())
