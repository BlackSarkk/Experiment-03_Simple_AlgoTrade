"""Campaign controller for the canonical V3 flow.

    resolve preset / history / exchange metadata
      -> load exact history + warmup
      -> full-frame indicators BEFORE any partition slice
      -> chronological TRAIN / VALID / UNSEEN split
      -> lock UNSEEN structurally
      -> 1a broad -> 1b narrowed -> 1c risk -> 2a joint -> 2b Bollinger
      -> freeze the winner
      -> evaluate UNSEEN once
      -> write the requested output config

No strategy config is written unless every ENABLED required stage produced a
result. A failure returns without touching `configs/config/`.
"""

import json
import os
import platform
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict

from . import artifacts, dataprep, market_rules, ui, v3_runplan

RUN_MANIFEST = "v3_manifest.json"

# Reproduction escape hatch. Normal runs read the project cache at `data/`. A
# controlled reproduction of a historical campaign must read that campaign's
# frozen dataset instead, and must not write into the project cache to do it.
# The directory actually used is recorded in the manifest and the run plan.
DATA_DIR_ENV = "AUTO_OPTIMISE_DATA_DIR"
LEDGER_FILES = {
    "1a_broad": "v3_stage1a_broad.csv",
    "1b_narrow": "v3_stage1b_narrow.csv",
    "1c_risk": "v3_stage1c_risk.csv",
    "2a_final": "v3_stage2a_final.csv",
    "2b_bollinger": "v3_stage2b_bollinger.csv",
}


def environment() -> Dict[str, str]:
    import numpy, optuna, pandas
    return {
        "python": platform.python_version(),
        "numpy": numpy.__version__,
        "optuna": optuna.__version__,
        "pandas": pandas.__version__,
        "interpreter": sys.executable,
    }


def run_campaign(preset, output, resume: bool = True, show_dashboard: bool = True,
                 progress=None) -> Dict[str, Any]:
    def emit(text):
        (print if progress is None else progress)(text)

    started = time.time()
    started_utc = datetime.now(timezone.utc).isoformat()
    total, allocation, was_auto, note = preset.resolved_budgets()
    summary: Dict[str, Any] = {"stages": {}, "failed_at": None, "config": None}

    # ---- market rules (exchange metadata) ---------------------------------
    emit("")
    emit(ui.info("[1/4] Market rules") + ui.dim("  resolving from the exchange..."))
    try:
        rules = market_rules.resolve(preset.platform, preset.symbol,
                                     preset.execution.tick_size)
    except market_rules.MarketRuleError as exc:
        ui.error_exit(str(exc))
    emit(f"      tick {rules.tick_size:g} ({rules.tick_source}) · "
         f"step {rules.quantity_step:g} · {rules.source}")

    # ---- data preparation --------------------------------------------------
    emit("")
    emit(ui.info("[2/4] Data preparation") + ui.dim("  running..."))
    data_dir = os.environ.get(DATA_DIR_ENV) or "data"
    if data_dir != "data":
        emit(ui.warn(f"      dataset override: {data_dir}  ({DATA_DIR_ENV})"))
    prepared = dataprep.prepare(preset, data_dir=data_dir,
                                progress=lambda m: emit("      " + ui.dim(m)))

    from . import v3_stages
    facts = v3_stages.partition_facts(prepared)
    emit(v3_runplan.render(preset, output, rules=rules, facts=facts))

    run_id = artifacts.new_run_id(preset)
    run_path = artifacts.run_dir(run_id)

    manifest = {
        "run_id": run_id, "started_utc": started_utc,
        "preset_path": preset.path, "preset_snapshot": artifacts.preset_snapshot(preset),
        "environment": environment(),
        "market_rules": rules.to_dict(),
        "data_dir": data_dir,
        "data_checksum": prepared.checksum,
        "partitions": {k: (str(v) if hasattr(v, "strftime") else v)
                       for k, v in facts.items()},
        "trial_budget": {"total": total, "per_stage": allocation,
                         "resolved_from": note, "was_auto": was_auto},
        "partition_policy": {
            "policy": ("reserve the final share as sealed UNSEEN, then V3 splits DEV "
                       f"{facts['v3_dev_split']}"),
            "unseen_reserved_first": True,
            "unseen_boundary_source": facts["unseen_boundary_source"],
            "dev_local_split": {"train_pct": facts["dev_train_pct"],
                                "valid_pct": facts["dev_valid_pct"]},
            "effective_full_history_split": {"train_pct": facts["train_pct"],
                                             "valid_pct": facts["valid_pct"],
                                             "unseen_pct": facts["unseen_pct"]},
        },
        "direction": {"long_enabled": preset.direction.long_enabled,
                      "short_enabled": preset.direction.short_enabled,
                      "campaign": "single combined simulation per trial"},
        "stages_requested": {
            "strategy_optimization": preset.stages.strategy_optimization,
            "risk_management": preset.stages.risk_management,
            "bollinger": preset.stages.bollinger,
        },
        "output_target": output.path,
    }
    artifacts.write_manifest(run_path, manifest)
    with open(os.path.join(run_path, RUN_MANIFEST), "w") as fh:
        json.dump(manifest, fh, indent=2, default=str)

    # ---- the five V3 stages ------------------------------------------------
    emit("")
    emit(ui.info("[3/4] V3 optimization") + ui.dim(f"  {total} trials..."))
    try:
        result = v3_stages.run(preset, prepared, allocation,
                               progress=lambda m: emit("      " + ui.dim(m)),
                               show_dashboard=show_dashboard)
    except v3_stages.StageFailure as exc:
        emit(ui.err(f"      FAILED — {exc}"))
        emit(ui.warn("No output config written: a required stage produced no result."))
        summary["failed_at"] = "v3_stages"
        summary["run_path"] = run_path
        return summary

    for key, frame in result.ledgers.items():
        name = LEDGER_FILES.get(key)
        if name is not None and frame is not None:
            frame.to_csv(os.path.join(run_path, name), index=False)
    with open(os.path.join(run_path, "v3_seed.json"), "w") as fh:
        json.dump({"seed": result.seed, "stage_meta": result.stage_meta},
                  fh, indent=2, default=str)

    # ---- UNSEEN, once, after the winner is frozen -------------------------
    emit("")
    emit(ui.info("[4/4] UNSEEN confirmation")
         + ui.dim("  opening the locked partition once..."))
    from . import v3_confirm
    unseen_report = v3_confirm.confirm(preset, prepared, result.winner, result.bollinger)
    with open(os.path.join(run_path, "v3_unseen_confirmation.json"), "w") as fh:
        json.dump(unseen_report, fh, indent=2, default=str)

    # ---- emit the config ---------------------------------------------------
    from . import v3_config_writer
    stage_status = {k: (v or {}).get("status", "SELECTED")
                    for k, v in result.stage_meta.items() if k.startswith("stage_")}
    for key in result.skipped:
        stage_status[key] = "SKIPPED"
    provenance = {
        "output_name": output.name, "run_id": run_id, "started_utc": started_utc,
        "environment": manifest["environment"],
        "trial_total": total, "allocation": allocation, "trial_source": note,
        "stages": stage_status, "skipped": result.skipped,
    }
    payload = v3_config_writer.build(preset, prepared, rules, result,
                                     unseen_report, provenance, facts)
    config_info = v3_config_writer.write(payload, output)
    with open(os.path.join(run_path, "v3_final_config.json"), "w") as fh:
        json.dump(payload, fh, indent=2, default=str)

    manifest["result"] = {
        "stage_winners": result.stage_meta, "winner": result.winner,
        "bollinger_enabled": result.bollinger_enabled,
        "skipped": result.skipped,
        "unseen_confirmation": unseen_report,
        "config": config_info, "seconds": time.time() - started,
    }
    with open(os.path.join(run_path, RUN_MANIFEST), "w") as fh:
        json.dump(manifest, fh, indent=2, default=str)

    emit(v3_runplan.render_result(preset, result, unseen_report, config_info, facts))
    emit("")
    emit(f"Artifacts: {run_path}/")
    summary.update({"run_path": run_path, "config": config_info,
                    "result": result, "seconds": time.time() - started})
    return summary
