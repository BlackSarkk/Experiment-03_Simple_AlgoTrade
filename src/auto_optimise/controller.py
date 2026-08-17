"""Campaign controller — runs the stages end to end, unattended.

`./pipeline.sh --optimize --odefault.json --name.json` enters here after the run
plan is printed. The controller owns stage sequencing, the stage toggles, resume,
and the run directory; the stages themselves own their mathematics.

RESUME
------
A run directory is complete for a stage when that stage's terminal artifact
exists. On re-entry the controller finds the newest run directory matching the
preset's symbol/timeframe, skips every completed stage, and continues from the
first incomplete one. Expensive earlier stages are never repeated.

Stage 6 is not implemented; it is reported as such and nothing is written for it.
"""

import json
import os
import time
from typing import Any, Dict, List, Optional

from . import (artifacts, dataprep, phase_a, runplan, stage3, stage4,
               stage5, stage6, ui)

# stage index -> (label, terminal artifact that proves the stage finished)
STAGE_ARTIFACTS = {
    2: "phase_a_manifest.json",
    3: "stage3_manifest.json",
    4: "stage4_manifest.json",
    5: "stage5_manifest.json",
    6: "stage6_manifest.json",
}


def _requested_history(preset) -> Dict[str, Any]:
    hist = preset.history
    return {"mode": hist.mode, "days": hist.days,
            "start_date": str(hist.start_date) if hist.start_date else None,
            "end_date": str(hist.end_date) if hist.end_date else None}


def find_resumable_run(preset) -> Optional[str]:
    """Newest run directory built from THIS symbol, timeframe AND history window.

    Matching on symbol/timeframe alone is not enough: two campaigns over
    different date ranges are different experiments, and resuming one into the
    other would silently reuse the wrong partitions and the wrong candidates.
    """
    root = artifacts.RESULTS_ROOT
    if not os.path.isdir(root):
        return None
    suffix = f"_{preset.symbol}_{preset.timeframe}"
    wanted = _requested_history(preset)
    for name in sorted((d for d in os.listdir(root) if d.endswith(suffix)),
                       reverse=True):
        manifest_path = os.path.join(root, name, STAGE_ARTIFACTS[2])
        if not os.path.isfile(manifest_path):
            continue
        try:
            with open(manifest_path) as fh:
                recorded = json.load(fh).get("requested_history", {})
        except (OSError, ValueError):
            continue
        if all(recorded.get(k) == wanted[k] for k in wanted):
            return os.path.join(root, name)
    return None


def completed_stages(run_path: Optional[str]) -> Dict[int, bool]:
    if not run_path or not os.path.isdir(run_path):
        return {i: False for i in STAGE_ARTIFACTS}
    return {i: os.path.isfile(os.path.join(run_path, name))
            for i, name in STAGE_ARTIFACTS.items()}


def run_campaign(preset, output, resume: bool = True,
                 show_dashboard: bool = True, progress=None) -> Dict[str, Any]:
    """Execute stages 1-5. Returns a summary; writes no strategy config."""

    def emit(text):
        if progress is None:
            print(text)
        else:
            progress(text)

    campaign_started = time.time()
    run_path = find_resumable_run(preset) if resume else None
    done = completed_stages(run_path)
    resumed_from = None
    if run_path and any(done.values()):
        resumed_from = min(i for i in sorted(STAGE_ARTIFACTS) if not done[i]) \
            if not all(done.values()) else 7
        emit(ui.info(f"Resuming {os.path.basename(run_path)} — "
                     f"stages {', '.join(str(i) for i in sorted(STAGE_ARTIFACTS) if done[i])} "
                     "already complete"))

    summary: Dict[str, Any] = {"run_path": run_path, "resumed_from": resumed_from,
                               "stages": {}, "failed_at": None}

    # ---- [1/6] data preparation -------------------------------------------
    emit("")
    emit(ui.info("[1/6] Data Preparation") + ui.dim("  running..."))
    started = time.time()
    prepared = dataprep.prepare(preset,
                                progress=lambda m: emit("      " + ui.dim(m)))
    emit(runplan.render_stage_report(preset, prepared, time.time() - started))
    summary["stages"][1] = "PASS"
    summary["prepared"] = prepared

    # ---- [2/6] strategy optimization --------------------------------------
    if not preset.stages.strategy_optimization:
        emit(ui.dim("[2/6] Strategy Optimization   SKIPPED (disabled in preset)"))
        emit(ui.dim("[3/6] Strategy Robustness     SKIPPED (needs stage 2)"))
        summary["stages"][2] = summary["stages"][3] = "SKIPPED"
        return summary

    if run_path and done[2]:
        emit(ui.info("[2/6] Strategy Optimization") + ui.ok("   PASS")
             + ui.dim("  (resumed from artifacts)"))
        summary["stages"][2] = "PASS (resumed)"
    else:
        run_path = os.path.join(artifacts.RESULTS_ROOT,
                                artifacts.new_run_id(preset))
        emit("")
        emit(ui.info("[2/6] Strategy Optimization") + ui.dim("  running..."))
        result = phase_a.run(preset, prepared,
                             run_id=os.path.basename(run_path),
                             show_dashboard=show_dashboard,
                             campaign_started=campaign_started)
        emit(runplan.render_phase_a_report(preset, result))
        summary["stages"][2] = "PASS"
        summary["phase_a"] = result
    summary["run_path"] = run_path

    # ---- [3/6] strategy robustness ----------------------------------------
    if done.get(3) and os.path.isfile(os.path.join(run_path, STAGE_ARTIFACTS[3])):
        emit(ui.info("[3/6] Strategy Robustness") + ui.ok("     PASS")
             + ui.dim("  (resumed from artifacts)"))
        summary["stages"][3] = "PASS (resumed)"
    else:
        emit("")
        emit(ui.info("[3/6] Strategy Robustness") + ui.dim("  running..."))
        result = stage3.run(preset, prepared, run_path,
                            progress=lambda m: emit("      " + ui.dim(m)),
                            show_dashboard=show_dashboard,
                            campaign_started=campaign_started)
        if result["failed"]:
            emit(ui.err("[3/6] Strategy Robustness    FAILED — no candidate was robust"))
            summary["stages"][3] = "FAILED"
            summary["failed_at"] = 3
            return summary
        emit(f"      {len(result['survivors'])} survivors, "
             f"{len(result['advancing'])} advancing")
        summary["stages"][3] = "PASS"
        summary["stage3"] = result

    # ---- [4/6] risk management --------------------------------------------
    if done.get(4) and os.path.isfile(os.path.join(run_path, STAGE_ARTIFACTS[4])):
        emit(ui.info("[4/6] Risk Management") + ui.ok("        PASS")
             + ui.dim("  (resumed from artifacts)"))
        summary["stages"][4] = "PASS (resumed)"
    elif not preset.stages.risk_management:
        emit(ui.dim("[4/6] Risk Management        SKIPPED (disabled in preset)"))
        stage4.skipped(preset, run_path)
        summary["stages"][4] = "SKIPPED"
    else:
        emit("")
        emit(ui.info("[4/6] Risk Management") + ui.dim("  running..."))
        result = stage4.run(preset, prepared, run_path,
                            progress=lambda m: emit("      " + ui.dim(m)),
                            show_dashboard=show_dashboard,
                            campaign_started=campaign_started)
        if result["failed"]:
            emit(ui.err("[4/6] Risk Management        FAILED — no risk policy generalised"))
            summary["stages"][4] = "FAILED"
            summary["failed_at"] = 4
            return summary
        emit(f"      {len(result['survivors'])} survivors, "
             f"{len(result['advancing'])} advancing")
        summary["stages"][4] = "PASS"
        summary["stage4"] = result

    # ---- [5/6] Bollinger ---------------------------------------------------
    # Resume takes precedence over the toggle: a completed real Stage 5 must not
    # be overwritten by a later run whose preset happens to disable the filter.
    if done.get(5) and os.path.isfile(os.path.join(run_path, STAGE_ARTIFACTS[5])):
        emit(ui.info("[5/6] Bollinger") + ui.ok("              PASS")
             + ui.dim("  (resumed from artifacts)"))
        summary["stages"][5] = "PASS (resumed)"
    elif not preset.stages.bollinger:
        emit(ui.dim("[5/6] Bollinger              SKIPPED (disabled in preset)"))
        result = stage5.skipped(preset, run_path)
        emit(ui.dim(f"      {len(result['advancing'])} candidates forwarded "
                    "with Bollinger OFF"))
        summary["stages"][5] = "SKIPPED"
        summary["stage5"] = result
    else:
        emit("")
        emit(ui.info("[5/6] Bollinger") + ui.dim("  running..."))
        result = stage5.run(preset, prepared, run_path,
                            progress=lambda m: emit("      " + ui.dim(m)),
                            show_dashboard=show_dashboard,
                            campaign_started=campaign_started)
        on = result["manifest"]["bollinger_on"]
        off = result["manifest"]["bollinger_off"]
        emit(f"      Bollinger ON: {on or 'none'} · OFF: {off or 'none'}")
        emit(f"      {len(result['advancing'])} advancing")
        summary["stages"][5] = "PASS"
        summary["stage5"] = result

    # ---- [6/6] final selection ---------------------------------------------
    # A completed stage 6 must never re-open UNSEEN: the vault may be unlocked
    # exactly once per campaign, and the decision is already on disk.
    if done.get(6) and os.path.isfile(os.path.join(run_path, STAGE_ARTIFACTS[6])):
        emit(ui.info("[6/6] Final Selection") + ui.ok("        PASS")
             + ui.dim("  (resumed from artifacts — UNSEEN not re-opened)"))
        summary["stages"][6] = "PASS (resumed)"
        with open(os.path.join(run_path, STAGE_ARTIFACTS[6])) as fh:
            summary["stage6"] = json.load(fh)
    else:
        emit("")
        emit(ui.info("[6/6] Final Selection") + ui.dim("  running..."))
        result = stage6.run(preset, prepared, run_path, output,
                            progress=lambda m: emit("      " + ui.dim(m)),
                            show_dashboard=show_dashboard,
                            campaign_started=campaign_started)
        if result["failed"]:
            emit(ui.err("[6/6] Final Selection        NO WINNER — "
                        + "; ".join(result["reasons"])))
            emit(ui.warn("No strategy survived unseen confirmation; "
                         "no config was created."))
            summary["stages"][6] = "NO WINNER"
            summary["failed_at"] = 6
            summary["stage6"] = result
            return summary
        emit(f"      WINNER: TRAIN rank {result['winner']['train_rank']} "
             f"({result['status']})"
             + ("  [fallback activated]" if result["fallback_activated"] else ""))
        emit(f"      Config: {result['config']['path']}")
        summary["stages"][6] = "PASS"
        summary["stage6"] = result

    emit("")
    emit(f"Artifacts: {run_path}/")
    summary["run_path"] = run_path
    summary["seconds"] = time.time() - campaign_started
    return summary
