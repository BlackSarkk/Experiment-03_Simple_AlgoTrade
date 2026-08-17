"""Run plan — the resolved campaign, printed for human confirmation.

The six-stage view is the campaign skeleton the full optimizer will execute.
Disabled stages are shown as SKIPPED and are excluded from ETA estimation.
Partition ratios are fixed policy: UNSEEN is reserved first (default 20%), and V3
splits the remaining DEV 70/30, giving an effective 56/24/20. Not preset inputs.
"""

from dataclasses import dataclass
from typing import List

from . import ui

from . import dataprep as _dp
TRAIN_PCT, VALID_PCT, UNSEEN_PCT = (_dp.effective_ratios()["train_pct"],
                                   _dp.effective_ratios()["valid_pct"],
                                   _dp.effective_ratios()["unseen_pct"])

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

    return "\n".join(lines)


def _fmt(ts) -> str:
    return ts.strftime("%Y-%m-%d %H:%M")


def render_phase_a_report(preset, result) -> str:
    stages = build_stages(preset.stages)
    lines = []
    add = lines.append

    add("")
    add(stages[1].render("PASS"))
    rate = result.completed / max(1e-9, result.seconds)
    add(f"      Trials:    {result.completed} completed, {result.rejected} rejected "
        f"(min {result.min_trades} trades on TRAIN)")
    add(f"      Runtime:   {result.seconds:.1f}s  ({rate:.2f} trials/sec)")

    if result.best:
        b = result.best
        add("      Best TRAIN candidate:")
        add(f"        score {b['score']:.2f} | return {b.get('net_return_pct', 0):.2f}% "
            f"| PF {b.get('profit_factor', 0):.3f} | Sharpe {b.get('sharpe', 0):.2f} "
            f"| DD {b.get('max_dd_pct', 0):.2f}% | {b.get('trades', 0)} trades")
        add(f"        EMA {b['ema_period']} · RSI {b['rsi_period']} "
            f"({b['rsi_oversold']:.0f}/{b['rsi_overbought']:.0f}) · ATR {b['atr_period']} "
            f"· cons {b['consolidation_candles']}@{b['consolidation_atr_mult']:.1f} "
            f"· swing {b['swing_lookback']} "
            f"· vol {b['volume_sma_period']}@{b['volume_mult']:.1f}x "
            f"· RR {b['risk_reward_ratio']:.1f}")
    else:
        add(ui.warn("      No admissible candidate survived the Phase-A filters."))

    if result.shortlist:
        add(f"      Shortlist: top {len(result.shortlist)} screened on VALIDATION")
        top = result.shortlist[0]
        if top.get("valid_net_return_pct") is not None:
            add(f"        #1 on VALID: return {top['valid_net_return_pct']:.2f}% "
                f"| PF {top['valid_profit_factor']:.3f} "
                f"| Sharpe {top['valid_sharpe']:.2f} "
                f"| DD {top['valid_max_dd_pct']:.2f}% "
                f"| {top['valid_trades']} trades")
    add(f"      Artifacts: {result.run_path}/")
    add("      UNSEEN:    " + ui.warn("[LOCKED]") + ui.dim("  never accessed in Phase A"))

    for stage in stages[2:]:
        add(stage.render("NOT IMPLEMENTED"))

    add("")
    add(ui.warn("No output config written — no optimization winner exists yet."))
    return "\n".join(lines)


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
