"""Run plan — the resolved campaign, printed for human confirmation.

The six-stage view is the campaign skeleton the full optimizer will execute.
Disabled stages are shown as SKIPPED and are excluded from ETA estimation.
Partition ratios are fixed policy (chronological 60/20/20 by calendar date, with
a separate warmup block before TRAIN); they are not preset inputs.
"""

from dataclasses import dataclass
from typing import List

from . import ui

TRAIN_PCT, VALID_PCT, UNSEEN_PCT = 60, 20, 20

# (key, label). `key` is None for stages that always run.
STAGE_DEFS = (
    (None, "Data Preparation"),
    ("strategy_optimization", "Strategy Optimization"),
    ("strategy_optimization", "Strategy Robustness"),
    ("risk_management", "Risk Management"),
    ("bollinger", "Bollinger"),
    (None, "Top-10 + UNSEEN"),
)

STAGE_LABEL_WIDTH = max(len(label) for _, label in STAGE_DEFS)


@dataclass(frozen=True)
class Stage:
    index: int
    total: int
    label: str
    enabled: bool

    def render(self, status: str = "") -> str:
        tag = "[%d/%d]" % (self.index, self.total)
        label = self.label.ljust(STAGE_LABEL_WIDTH)
        if not self.enabled:
            return ui.dim(f"{tag} {label}  SKIPPED")
        if status == "PASS":
            return f"{ui.info(tag)} {label}  {ui.ok('PASS')}"
        if status:
            return f"{ui.info(tag)} {label}  {ui.dim(status)}"
        return f"{ui.info(tag)} {label}"


def build_stages(stages) -> List[Stage]:
    total = len(STAGE_DEFS)
    out = []
    for i, (key, label) in enumerate(STAGE_DEFS, start=1):
        enabled = True if key is None else bool(getattr(stages, key))
        out.append(Stage(index=i, total=total, label=label, enabled=enabled))
    return out


def _flag(enabled: bool) -> str:
    return ui.ok("ON") if enabled else ui.warn("OFF")


def render_stage_report(preset, prepared, elapsed: float) -> str:
    """Post-run stage view: stage 1 executed, the rest declared not implemented."""
    stages = build_stages(preset.stages)
    lines = []
    add = lines.append

    add("")
    add(stages[0].render("PASS"))
    add(f"      Requested: {_fmt(prepared.requested_start)} -> {_fmt(prepared.requested_end)}"
        f"   ({prepared.train.n_candles + prepared.validation.n_candles + prepared.unseen_candles} candles)")
    add(f"      Warmup:    {_fmt(prepared.warmup_start)} -> {_fmt(prepared.warmup_end)}"
        f"   ({prepared.warmup_candles} candles, excluded from every partition)")
    add(f"      TRAIN:     {_fmt(prepared.train.start)} -> {_fmt(prepared.train.end)}"
        f"   ({prepared.train.n_candles} candles)")
    add(f"      VALID:     {_fmt(prepared.validation.start)} -> {_fmt(prepared.validation.end)}"
        f"   ({prepared.validation.n_candles} candles)")
    add(f"      UNSEEN:    {_fmt(prepared.unseen_start)} -> {_fmt(prepared.unseen_end)}"
        f"   ({prepared.unseen_candles} candles) {ui.warn('[LOCKED]')}")
    add(f"      Checksum:  {prepared.checksum}")
    add(f"      Elapsed:   {elapsed:.1f}s")

    for stage in stages[1:]:
        add(stage.render("NOT IMPLEMENTED"))

    add("")
    add(ui.warn("No output config written — no optimization winner exists yet."))
    return "\n".join(lines)


def _fmt(ts) -> str:
    return ts.strftime("%Y-%m-%d %H:%M")


def render(preset, output) -> str:
    trial_count, was_auto = preset.resolved_trials()
    trials_text = f"AUTO -> {trial_count}" if was_auto else str(trial_count)

    lines = []
    add = lines.append

    add(ui.info("OPTIMIZER RUN PLAN"))
    add("")
    add(f"  {preset.symbol} | {preset.timeframe} | {preset.platform}")
    add(f"  History:  {preset.history.describe()}")
    add(f"  Balance:  ${preset.initial_balance:,.0f}")
    add(f"  Mode:     {preset.optimization_mode}")
    add("")
    add(f"  LONG:   {_flag(preset.direction.long_enabled)}")
    add(f"  SHORT:  {_flag(preset.direction.short_enabled)}")
    if preset.direction.long_enabled and preset.direction.short_enabled:
        add(ui.dim("          both sides active in ONE mixed campaign — one backtest"))
        add(ui.dim("          per trial, one combined score, shared parameters"))
    add("")
    add("  STAGES")
    for stage in build_stages(preset.stages):
        add("  " + stage.render())
    add("")
    add(f"  Trials:   {trials_text}   " + ui.dim("(Phase-A strategy search budget only;"))
    add(ui.dim("            risk and Bollinger stages derive their own smaller budgets)"))
    add(f"  Split:    TRAIN {TRAIN_PCT} / VALID {VALID_PCT} / UNSEEN {UNSEEN_PCT}"
        + ui.dim("  (chronological, + warmup block)"))
    add("")
    add("  Preset:   " + preset.path)
    add("  Output:   " + output.path)
    add("")
    add("  Input validation:  " + ui.ok("PASS"))
    add("  Output available:  " + ui.ok("PASS"))
    return "\n".join(lines)
