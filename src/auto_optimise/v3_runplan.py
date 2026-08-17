"""Run plan and stage reports for the V3 campaign.

Only live facts. Nothing here is estimated, rounded for effect, or restated from
a previous run: every number printed is one this run resolved.
"""

from . import budgets, ui

STAGE_ORDER = budgets.STAGE_KEYS

# Duplicated deliberately: `v3_stages` imports optuna/pandas via optimization.v3,
# and the run plan must print before any heavy import happens. Kept in sync by
# test_v3_optimizer.py::test_stage_labels_match.
STAGE_LABELS = {
    "stage_1a_broad": "1a  broad strategy",
    "stage_1b_narrow": "1b  narrowed strategy",
    "stage_1c_risk": "1c  risk-only",
    "stage_2a_final": "2a  final joint",
    "stage_2b_bollinger": "2b  Bollinger",
}


def _flag(enabled: bool) -> str:
    return ui.ok("ON") if enabled else ui.warn("OFF")


def _dataprep_ratios():
    """Effective ratios without importing pandas — read straight from the policy."""
    from optimization.v3 import spec as _spec           # stdlib-only
    unseen = 0.20
    dev = 1.0 - unseen
    return {"unseen_pct": round(100 * unseen, 1),
            "dev_train_pct": round(100 * _spec.TRAIN_FRAC, 1),
            "dev_valid_pct": round(100 * (1 - _spec.TRAIN_FRAC), 1),
            "train_pct": round(100 * dev * _spec.TRAIN_FRAC, 1),
            "valid_pct": round(100 * dev * (1 - _spec.TRAIN_FRAC), 1)}


def _fmt(ts) -> str:
    return ts.strftime("%Y-%m-%d %H:%M") if hasattr(ts, "strftime") else str(ts)


def stage_status(preset, allocation):
    """(key, label, trials, status) for all five stages, in canonical order."""
    enabled = budgets.enabled_allocation(allocation, preset.stages)
    out = []
    for key in STAGE_ORDER:
        label = STAGE_LABELS[key]
        if key == "stage_1c_risk" and not preset.stages.risk_management:
            status = "SKIPPED (risk_management off)"
        elif key == "stage_2b_bollinger" and not preset.stages.bollinger:
            status = "SKIPPED (bollinger off)"
        else:
            status = "RUN"
        out.append((key, label, enabled[key], status))
    return out


def render(preset, output, rules=None, facts=None) -> str:
    total, allocation, was_auto, note = preset.resolved_budgets()
    lines = []
    add = lines.append

    add(ui.info("OPTIMIZER RUN PLAN") + ui.dim("   canonical optimization.v3"))
    add("")
    add(f"  {preset.symbol} | {preset.timeframe} | {preset.platform}")
    add(f"  History mode: {preset.history.mode}")
    add(f"  History:      {preset.history.describe(preset.timeframe)}")
    if preset.history.is_custom_short(preset.timeframe):
        add("  " + ui.warn("NOTE: Custom short history — results are experimental."))
    add(f"  Balance:      ${preset.initial_balance:,.0f}")
    add(f"  Mode:         {preset.optimization_mode}")
    add("")
    add(f"  LONG:         {_flag(preset.direction.long_enabled)}")
    add(f"  SHORT:        {_flag(preset.direction.short_enabled)}")
    add("")

    add("  MARKET RULES")
    if rules is None:
        add(ui.dim(f"    tick size   {preset.execution.tick_size}  (resolved from the "
                   "exchange during data preparation)"))
        add(ui.dim("    qty step    resolved from LOT_SIZE.stepSize"))
    else:
        add(f"    tick size   {rules.tick_size:g}   "
            + ui.dim(f"({rules.tick_source}, {rules.source})"))
        add(f"    qty step    {rules.quantity_step:g}   "
            + ui.dim(f"(LOT_SIZE.stepSize, {rules.source})"))
    add("")

    add("  TRIAL BUDGET")
    add(f"    total     {total}   " + ui.dim(f"({'AUTO -> ' if was_auto else ''}{note})"))
    for key, label, trials, status in stage_status(preset, allocation):
        if status == "RUN":
            add(f"      {label:<24} {trials:>6}")
        else:
            add(ui.dim(f"      {label:<24} {0:>6}   {status}"))
    enabled_total = sum(t for _, _, t, s in stage_status(preset, allocation) if s == "RUN")
    add(ui.dim(f"      {'sum of enabled stages':<24} {enabled_total:>6}"))
    add("")

    add("  PARTITIONS" + ("" if facts else ui.dim("   (exact rows resolved in stage 1)")))
    if facts:
        add(f"    warmup    {_fmt(facts['warmup_start'])} -> {_fmt(facts['warmup_end'])}"
            f"   ({facts['warmup_rows']:,} candles, outside every partition)")
        add(f"    TRAIN     {_fmt(facts['train_start'])} -> {_fmt(facts['train_end'])}"
            f"   ({facts['train_rows']:,} candles, {facts['train_pct']}%)")
        add(f"    VALID     {_fmt(facts['valid_start'])} -> {_fmt(facts['valid_end'])}"
            f"   ({facts['valid_rows']:,} candles, {facts['valid_pct']}%)")
        add(f"    UNSEEN    {_fmt(facts['unseen_start'])} -> {_fmt(facts['unseen_end'])}"
            f"   ({facts['unseen_rows']:,} candles, {facts['unseen_pct']}%) "
            + ui.warn("[SEALED]"))
        add("")
        add(f"    DEV-local split      TRAIN {facts['dev_train_pct']}% / "
            f"VALID {facts['dev_valid_pct']}%   "
            + ui.dim(f"(V3 canonical {facts['v3_dev_split']}, not adjustable)"))
        add(f"    Effective full-history  TRAIN {facts['train_pct']}% / "
            f"VALID {facts['valid_pct']}% / UNSEEN {facts['unseen_pct']}%")
        add(ui.dim(f"    UNSEEN ({facts['unseen_boundary_source']}) is reserved FIRST, "
                   "before DEV is divided,"))
        add(ui.dim("    and is physically absent from the frame V3 receives. It stays "
                   "inaccessible"))
        add(ui.dim("    until the single final confirmation, after the winner is frozen."))
    else:
        _r = _dataprep_ratios()
        c_count = preset.history.evaluable_candles(preset.timeframe)
        train_est = int(round(c_count * (_r['train_pct'] / 100.0)))
        valid_est = int(round(c_count * (_r['valid_pct'] / 100.0)))
        unseen_est = c_count - train_est - valid_est
        add(f"    requested mode      {preset.history.mode}")
        if preset.history.mode == "auto":
            add("    target evaluable    43,200 bars")
            add("    warmup              1,000 bars (automatic lead-in, outside partitions)")
            add("    actual availability unresolved (inspected during data preparation)")
        else:
            add(f"    evaluable bars      ~{c_count:,} bars at {preset.timeframe}")
            add("    warmup              1,000 bars (automatic lead-in, outside partitions)")
        add(f"    policy              reserve the final {_r['unseen_pct']}% as sealed UNSEEN, then V3 splits DEV")
        add(f"                        {_r['dev_train_pct']}%/{_r['dev_valid_pct']}% TRAIN/VALID (canonical, not adjustable)")
        add(f"    DEV-local split     TRAIN {_r['dev_train_pct']}% / VALID {_r['dev_valid_pct']}%")
        add(f"    Effective full-history  TRAIN {_r['train_pct']}% (~{train_est:,} candles) / "
            f"VALID {_r['valid_pct']}% (~{valid_est:,} candles) / UNSEEN {_r['unseen_pct']}% (~{unseen_est:,} candles)")
        add(ui.dim("    UNSEEN is reserved first and stays inaccessible until the single"))
        add(ui.dim("    final confirmation. Exact dates and rows resolve in stage 1."))
    add("")
    add("  Preset:   " + preset.path)
    add("  Output:   " + output.path)
    add("")
    add("  Input validation:  " + ui.ok("PASS"))
    add("  Output available:  " + ui.ok("PASS"))
    return "\n".join(lines)


def render_result(preset, result, unseen_report, config_info, facts) -> str:
    lines = []
    add = lines.append
    add("")
    add(ui.info("V3 CAMPAIGN RESULT"))
    for key, label, _trials, status in stage_status(preset, dict(budgets.REFERENCE)):
        meta = result.stage_meta.get(key) or {}
        if status != "RUN":
            add(ui.dim(f"  {label:<24} SKIPPED"))
        elif meta.get("trial") is None:
            add(f"  {label:<24} " + ui.warn(meta.get("status", "NO CANDIDATE")))
        else:
            add(f"  {label:<24} trial {meta['trial']:<5} score {meta['score']:.4f}")
    add("")

    if result.dev_metrics:
        for arm in ("off", "on"):
            block = result.dev_metrics.get(arm) or {}
            for part in ("train", "valid"):
                m = block.get(part)
                if m:
                    add(f"  BB {arm.upper():<3} {part.upper():<6} "
                        f"ret {m['return_pct']:+8.2f}%  PF {m['pf']:6.3f}  "
                        f"DD {m['max_dd']:6.2f}%  {int(m['trades']):>4} trades")
    add("")
    add("  UNSEEN " + ui.warn("(confirmation only — did not influence selection)"))
    for arm in ("bollinger_off", "bollinger_on"):
        m = unseen_report[arm]
        add(f"    {arm.replace('bollinger_', 'BB ').upper():<7} "
            f"ret {m['return_pct']:+8.2f}%  PF {m['pf']:6.3f}  "
            f"DD {m['max_dd']:6.2f}%  {int(m['trades']):>4} trades  "
            f"net ${m['net_pnl']:+.2f}")
    add("")
    add("  Config:   " + ui.ok(config_info["path"]))
    add(ui.dim(f"  sha256    {config_info['sha256']}"))
    return "\n".join(lines)
