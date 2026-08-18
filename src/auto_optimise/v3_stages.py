"""The five canonical V3 stages, driven from the human preset.

This module OWNS NO OPTIMIZER MATHEMATICS. Every range, gate, score weight,
sampler, seed and selection rule lives in `optimization.v3` and is imported, not
copied. If a number here disagrees with V3, V3 wins.

What this module adds on top of V3 is only orchestration a human run needs:
stage toggles, budget overrides, per-stage ledgers, live progress, and the
structural guarantee that UNSEEN never reaches a search.

UNSEEN BARRIER
--------------
V3's `Campaign` is constructed with a frame that PHYSICALLY EXCLUDES the UNSEEN
rows — they are sliced off before construction, so no V3 stage can address them
even by index. V3's own data contract asserts the same thing. The `UnseenVault`
holding those rows stays locked for the whole of this module; it is opened once,
by `evaluation.confirm_unseen`, after the winner is frozen.
"""

import contextlib
import io
import os
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from optimization.v3 import optimizer as V3
from optimization.v3 import spec as V3_SPEC

from . import budgets, v3_dashboard


@contextlib.contextmanager
def _quiet():
    """Silence the engine's per-bar tqdm and console output for the enclosed block.

    `BacktestEngine` prints one `Backtesting <SYMBOL> ...` progress bar per
    evaluation — two per trial, so ~3,700 for a full campaign. Left alone it floods
    the terminal and destroys any fixed dashboard region.

    Same pattern already used by `evaluation.py`: stderr to devnull, stdout to a
    buffer. The engine is NOT modified. The dashboard keeps rendering because its
    console is bound to the real stdout captured before this wrapper is entered.

    On error the captured stdout is replayed and the traceback is re-raised, so a
    genuine failure is never swallowed by the redirection.
    """
    buffer = io.StringIO()
    try:
        with open(os.devnull, "w") as devnull, \
                contextlib.redirect_stderr(devnull), \
                contextlib.redirect_stdout(buffer):
            yield buffer
    except BaseException:
        # Redirection is already unwound here, so this reaches the real terminal.
        tail = buffer.getvalue().strip().splitlines()[-20:]
        if tail:
            print("--- suppressed engine output (last 20 lines) ---")
            print("\n".join(tail))
            print("--- end suppressed output ---")
        traceback.print_exc()
        raise

# The V3 budget constants this module temporarily overrides, keyed by our own
# stage names. V3's spec is READ, never edited on disk.
_SPEC_ATTR = {
    "stage_1a_broad": "BROAD_TRIALS",
    "stage_1b_narrow": "NARROW_TRIALS",
    "stage_1c_risk": "RISK_SEED_TRIALS",
    "stage_2a_final": "FINAL_TRIALS",
    "stage_2b_bollinger": "BOLL_TRIALS",
}

STAGE_LABELS = {
    "stage_1a_broad": "1a  broad strategy",
    "stage_1b_narrow": "1b  narrowed strategy",
    "stage_1c_risk": "1c  risk-only",
    "stage_2a_final": "2a  final joint",
    "stage_2b_bollinger": "2b  Bollinger",
}


class StageFailure(RuntimeError):
    """A required, enabled stage produced no usable result. No config may be written."""


@dataclass
class V3Result:
    seed: Dict[str, Any] = None
    winner: Dict[str, Any] = None
    bollinger: Any = None
    bollinger_enabled: bool = False
    ledgers: Dict[str, Any] = field(default_factory=dict)
    stage_meta: Dict[str, Any] = field(default_factory=dict)
    skipped: List[str] = field(default_factory=list)
    dev_metrics: Dict[str, Any] = None
    direction: Dict[str, bool] = None
    seconds: float = 0.0


class _DirectionOverride:
    """Apply the preset's direction to V3's spec for the duration of one campaign.

    V3 hardcodes `LONG_ENABLED`/`SHORT_ENABLED` and `build_cfg` reads them, so this
    is the only way to honour the preset without editing V3's source. The values are
    restored on exit even if a stage raises.

    LONG + SHORT is ONE campaign, not two: the same 14-dimension vector is searched,
    each trial runs a SINGLE `BacktestEngine` simulation with both directions
    enabled, and the combined result produces one score and one winner. There are no
    side-specific parameters, no parallel studies and no separate winners — the
    frozen `StrategyConfig` has no side-specific fields to search even if we wanted
    them. When only one side is enabled, only that side is ever evaluated.
    """

    def __init__(self, direction):
        self.direction = direction
        self._saved = {}

    def __enter__(self):
        self._saved = {"LONG_ENABLED": V3_SPEC.LONG_ENABLED,
                       "SHORT_ENABLED": V3_SPEC.SHORT_ENABLED}
        V3_SPEC.LONG_ENABLED = bool(self.direction.long_enabled)
        V3_SPEC.SHORT_ENABLED = bool(self.direction.short_enabled)
        return self

    def __exit__(self, *exc):
        for attr, value in self._saved.items():
            setattr(V3_SPEC, attr, value)
        return False


class _MarketRulesOverride:
    """Apply the exchange-resolved tick size and quantity step for one campaign.

    V3 carries a small per-symbol `TICK_SIZE` map and a constant `QUANTITY_STEP`;
    `build_cfg` reads both and raises KeyError for any symbol that is not in the
    map. The campaign already resolves these from the exchange, so they are pushed
    into the spec here — same idiom as the direction and budget overrides, and V3's
    source is still never edited. Restored on exit even if a stage raises. Without
    this, every trial would be scored on a different tick/step than the one written
    into the emitted config.
    """

    def __init__(self, symbol, rules):
        self.symbol = symbol
        self.rules = rules
        self._saved = {}

    def __enter__(self):
        if self.rules is None:
            return self
        self._saved = {"TICK_SIZE": dict(V3_SPEC.TICK_SIZE),
                       "QUANTITY_STEP": V3_SPEC.QUANTITY_STEP}
        V3_SPEC.TICK_SIZE = dict(V3_SPEC.TICK_SIZE)
        V3_SPEC.TICK_SIZE[self.symbol] = float(self.rules.tick_size)
        V3_SPEC.QUANTITY_STEP = float(self.rules.quantity_step)
        return self

    def __exit__(self, *exc):
        for attr, value in self._saved.items():
            setattr(V3_SPEC, attr, value)
        return False


class _BudgetOverride:
    """Apply per-stage budgets to V3's spec for the duration of one campaign.

    V3's source file is never modified. The attributes are restored on exit even
    if a stage raises, so a failed run cannot leave the canonical spec altered.
    """

    def __init__(self, allocation: Dict[str, int]):
        self.allocation = allocation
        self._saved = {}

    def __enter__(self):
        for key, attr in _SPEC_ATTR.items():
            self._saved[attr] = getattr(V3_SPEC, attr)
            value = self.allocation.get(key)
            if value:
                setattr(V3_SPEC, attr, int(value))
        return self

    def __exit__(self, *exc):
        for attr, value in self._saved.items():
            setattr(V3_SPEC, attr, value)
        return False


class _ObservedCampaign(V3.Campaign):
    """`V3.Campaign` plus a read-only tap on each partition evaluation.

    `_one` is overridden purely to record the metrics it already returns, so the
    dashboard can show the current best candidate's return / PF / DD / trades. It
    calls `super()._one(...)` and returns that value untouched — no parameter, no
    ordering, no score and no result can differ because of it.
    """

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self._observed: List[Any] = []

    def _one(self, cfg, fcfg, ind, lo, hi):
        result = super()._one(cfg, fcfg, ind, lo, hi)
        try:
            self._observed.append(result)
        except Exception:
            pass                                     # observation must never fail a trial
        return result

    def take_observed(self):
        """Metrics of the trial just scored, then reset. Best-effort only.

        `evaluate()` runs TRAIN then VALID, so the last entry is the VALID result —
        the one worth showing as "current best".
        """
        seen, self._observed = self._observed, []
        return seen[-1] if seen else None


def _progress_bridge(dashboard, tag_to_key, campaign):
    """Adapt V3's `progress(tag, trial_number, score)` hook to the dashboard."""
    def on_trial(tag, trial_number, score):
        try:
            key = tag_to_key.get(tag)
            if key is not None and dashboard.stage_key != key:
                dashboard.start_stage(key)
            dashboard.trial(score, campaign.take_observed())
        except Exception:
            pass                                     # presentation must never fail a trial
    return on_trial


def dev_frame_and_warmup(prepared):
    """The frame handed to V3: warmup + TRAIN + VALID, with UNSEEN rows absent.

    Slicing here is what makes the barrier structural rather than procedural.
    """
    unseen_lo = prepared._bounds["unseen"][0]
    dev = prepared.raw_full.iloc[:unseen_lo].reset_index(drop=True)
    return dev, int(prepared.warmup_candles)


def partition_facts(prepared) -> Dict[str, Any]:
    """The boundaries V3 will ACTUALLY use, reported before anything runs.

    Policy: UNSEEN is reserved off the end FIRST, then V3 splits the remaining
    DEV rows by its own fixed `TRAIN_FRAC` (70/30). Both views are reported — the
    DEV-local ratio and the effective full-history ratio — because quoting only
    one of them is what makes partition documentation misleading.

    Recomputed here exactly the way `V3.Campaign.__init__` does, so the plan can
    never advertise boundaries the search did not use.
    """
    dev, warm = dev_frame_and_warmup(prepared)
    dev_rows = len(dev) - warm
    tr_rows = int(dev_rows * V3_SPEC.TRAIN_FRAC)
    va_rows = dev_rows - tr_rows
    dt = dev["datetime"]
    total = dev_rows + int(prepared.unseen_candles)
    return {
        "warmup_rows": warm,
        "warmup_start": prepared.warmup_start, "warmup_end": prepared.warmup_end,
        "train_rows": tr_rows,
        "train_start": dt.iloc[warm], "train_end": dt.iloc[warm + tr_rows - 1],
        "valid_rows": va_rows,
        "valid_start": dt.iloc[warm + tr_rows], "valid_end": dt.iloc[-1],
        "unseen_rows": int(prepared.unseen_candles),
        "unseen_start": prepared.unseen_start, "unseen_end": prepared.unseen_end,
        "dev_rows": dev_rows,
        "evaluated_rows": total,
        "train_pct": round(100.0 * tr_rows / total, 1),
        "valid_pct": round(100.0 * va_rows / total, 1),
        "unseen_pct": round(100.0 * prepared.unseen_candles / total, 1),
        "dev_train_pct": round(100.0 * tr_rows / dev_rows, 1),
        "dev_valid_pct": round(100.0 * va_rows / dev_rows, 1),
        "dev_pct": round(100.0 * dev_rows / total, 1),
        "v3_dev_split": f"{V3_SPEC.TRAIN_FRAC:.0%}/{1 - V3_SPEC.TRAIN_FRAC:.0%}",
        "policy": ("UNSEEN reserved first, then V3 splits DEV "
                   f"{V3_SPEC.TRAIN_FRAC:.0%}/{1 - V3_SPEC.TRAIN_FRAC:.0%}"),
        "unseen_boundary_source": ("preset.partition.unseen_start (reproduction "
                                   "override)" if _pinned(prepared) else
                                   "default reservation of the final share"),
    }


def _pinned(prepared) -> bool:
    return bool(getattr(prepared, "_unseen_pinned", False))


TAG_TO_KEY = {"1a_broad": "stage_1a_broad", "1b_narrow": "stage_1b_narrow",
              "1c_risk": "stage_1c_risk", "2a_final": "stage_2a_final",
              "2b_boll": "stage_2b_bollinger"}


def run(preset, prepared, allocation: Dict[str, int], progress=None,
        dashboard=None, show_dashboard: bool = True,
        time_fn=None, stream=None, rules=None) -> V3Result:
    """Execute stages 1a → 2b. Raises StageFailure if a required stage yields nothing."""

    def say(msg):
        if progress is not None:
            progress(msg)

    started = time.time()
    dev, warm = dev_frame_and_warmup(prepared)

    # Hard invariant: the frame V3 sees must not reach into UNSEEN.
    assert len(dev) == prepared._bounds["unseen"][0], "DEV frame overruns UNSEEN"
    assert prepared.unseen.is_locked, "UNSEEN vault was opened before the search"

    enabled = budgets.enabled_allocation(allocation, preset.stages)
    result = V3Result(direction={
        'long_enabled': bool(preset.direction.long_enabled),
        'short_enabled': bool(preset.direction.short_enabled),
        'campaign': 'single combined simulation per trial, shared parameter vector',
    })

    if dashboard is None:
        kw = {"enabled": show_dashboard, "stream": stream}
        if time_fn is not None:
            kw["time_fn"] = time_fn
        dashboard = v3_dashboard.V3Dashboard(
            {k: v for k, v in enabled.items() if v},
            STAGE_LABELS, preset.symbol, preset.timeframe,
            ("LONG+SHORT" if preset.direction.long_enabled and preset.direction.short_enabled
             else "LONG" if preset.direction.long_enabled else "SHORT"), **kw)

    with _BudgetOverride(enabled), _DirectionOverride(preset.direction), \
            _MarketRulesOverride(preset.symbol, rules), dashboard:
        assert V3_SPEC.LONG_ENABLED == preset.direction.long_enabled
        assert V3_SPEC.SHORT_ENABLED == preset.direction.short_enabled
        campaign = _ObservedCampaign(preset.symbol, preset.timeframe, dev, warm)
        on_trial = _progress_bridge(dashboard, TAG_TO_KEY, campaign)

        # ---- Stage 1: 1a broad -> 1b narrowed -> 1c risk -------------------
        dashboard.note(f"seed discovery: {enabled['stage_1a_broad']}"
                       f"+{enabled['stage_1b_narrow']}+{enabled['stage_1c_risk']} trials")
        if not preset.stages.risk_management:
            # V3's 1c searches risk on the frozen strategy. With risk disabled the
            # stage does not run and the seed keeps V3's neutral risk policy.
            result.skipped.append("stage_1c_risk")
            dashboard.skip_stage("stage_1c_risk", "risk_management disabled")

        try:
            with _quiet():
                seed_meta, s1, narrow_space = campaign.stage1(progress=on_trial)
        except RuntimeError as exc:
            raise StageFailure(f"stage 1 produced no usable seed: {exc}")

        result.ledgers.update(s1)
        result.seed = dict(seed_meta["seed"])
        if not preset.stages.risk_management:
            result.seed.update({k: float(v) for k, v in V3_SPEC.NEUTRAL_RISK.items()})
        result.stage_meta["seed"] = seed_meta
        result.stage_meta["narrow_space"] = {k: list(v) for k, v in narrow_space.items()}

        for key, frame in (("stage_1a_broad", s1["1a_broad"]),
                           ("stage_1b_narrow", s1["1b_narrow"]),
                           ("stage_1c_risk", s1["1c_risk"])):
            result.stage_meta[key] = _winner_of(frame)
        for _k in ("stage_1a_broad", "stage_1b_narrow", "stage_1c_risk"):
            if _k not in result.skipped:
                dashboard.finish_stage(_k)
        dashboard.note(f"seed frozen from {seed_meta['strategy_from']['stage']} "
                       f"trial {seed_meta['strategy_from']['trial']}")

        # ---- Stage 2a: joint final search, seed enqueued as trial 0 --------
        dashboard.note(f"final joint search: {enabled['stage_2a_final']} trials, "
                       "seed enqueued as trial 0")
        try:
            with _quiet():
                s2a_df, s2a_meta = campaign.stage2_config(result.seed,
                                                          progress=on_trial)
        except (RuntimeError, IndexError) as exc:
            raise StageFailure(f"stage 2a selected no configuration: {exc}")
        result.ledgers["2a_final"] = s2a_df
        result.winner = dict(s2a_meta["params"])
        result.stage_meta["stage_2a_final"] = {
            "trial": s2a_meta["trial"], "score": s2a_meta["score"],
            "seed_was_trial_0": s2a_meta.get("seed_was_trial_0", True),
        }
        dashboard.finish_stage("stage_2a_final",
                               detail=f"trial {s2a_meta['trial']} "
                                      f"score {s2a_meta['score']:.4f}")

        # ---- Stage 2b: Bollinger ------------------------------------------
        if not preset.stages.bollinger:
            result.skipped.append("stage_2b_bollinger")
            result.bollinger = V3.OFF
            result.bollinger_enabled = False
            result.stage_meta["stage_2b_bollinger"] = {
                "status": "SKIPPED", "reason": "disabled in preset",
                "trial": None, "score": None,
            }
            dashboard.skip_stage("stage_2b_bollinger",
                                 "disabled in preset — filter ships OFF")
        else:
            dashboard.note(f"Bollinger search: {enabled['stage_2b_bollinger']} trials")
            with _quiet():
                s2b_df, s2b_meta, dev_off = campaign.stage2_bollinger(
                    result.winner, progress=on_trial)
            result.ledgers["2b_bollinger"] = s2b_df
            result.dev_metrics = {"off": dev_off}
            if s2b_meta:
                result.bollinger = s2b_meta["cfg"]
                result.bollinger_enabled = True
                result.stage_meta["stage_2b_bollinger"] = {
                    "status": "SELECTED", "trial": s2b_meta["trial"],
                    "score": s2b_meta["score"],
                }
                dashboard.finish_stage("stage_2b_bollinger",
                                       detail=f"filter ON, trial {s2b_meta['trial']} "
                                              f"score {s2b_meta['score']:.4f}")
            else:
                # Not a failure: V3 ships the filter disabled when nothing clears
                # the gate, and that is a real, reportable outcome.
                result.bollinger = V3.OFF
                result.bollinger_enabled = False
                result.stage_meta["stage_2b_bollinger"] = {
                    "status": "NO_CANDIDATE",
                    "reason": "no filter cleared the stage-2b gate",
                    "trial": None, "score": None,
                }
                dashboard.finish_stage("stage_2b_bollinger", status="no candidate",
                                       detail="shipping Bollinger OFF")

        # ---- frozen winner measured on TRAIN and VALID only ----------------
        cfg = V3.build_cfg(preset.symbol, preset.timeframe, result.winner)
        with _quiet():
            result.dev_metrics = {
                "off": campaign.evaluate(cfg, V3.OFF),
                "on": campaign.evaluate(cfg, result.bollinger),
            }

    assert prepared.unseen.is_locked, "UNSEEN vault was opened during the search"
    result.seconds = time.time() - started
    return result


def _winner_of(frame) -> Optional[Dict[str, Any]]:
    gated = frame[frame.gated].sort_values(["score", "trial"], ascending=[False, True])
    if gated.empty:
        return {"trial": None, "score": None, "status": "NO_GATED_TRIAL"}
    row = gated.iloc[0]
    return {"trial": int(row.trial), "score": float(row.score), "status": "SELECTED"}
