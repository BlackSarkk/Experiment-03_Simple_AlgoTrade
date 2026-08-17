"""Optimizer preset (`configs/optimize/*.json`) loading and validation.

Only human-facing inputs live in the preset. Search ranges, objective weights,
Optuna internals, warmup, partition ratios, seeds and storage paths stay
automatic and are deliberately not accepted here.
"""

import json
import os
from dataclasses import dataclass
from typing import Union

from . import budgets as budgets_mod
from . import history as history_mod
from . import trials as trials_mod

PRESET_DIR = os.path.join("configs", "optimize")

REQUIRED_KEYS = (
    "platform", "symbol", "timeframe", "history", "initial_balance",
    "direction", "trials", "optimization_mode", "stages",
)

# Optional blocks. Absent means the documented default, never a silent guess.
OPTIONAL_KEYS = ("execution", "partition")

SUPPORTED_PLATFORMS = ("BINANCE_FUTURES",)
SUPPORTED_MODES = ("balanced", "conservative", "aggressive")
STAGE_KEYS = ("strategy_optimization", "risk_management", "bollinger")

MIN_BALANCE = 100.0


class PresetError(ValueError):
    """Raised when an optimizer preset is missing, malformed or invalid."""


@dataclass(frozen=True)
class Direction:
    long_enabled: bool
    short_enabled: bool

    def label(self) -> str:
        if self.long_enabled and self.short_enabled:
            return "LONG + SHORT (single mixed campaign)"
        return "LONG only" if self.long_enabled else "SHORT only"


@dataclass(frozen=True)
class Stages:
    strategy_optimization: bool
    risk_management: bool
    bollinger: bool


@dataclass(frozen=True)
class Execution:
    # "auto" -> resolve PRICE_FILTER.tickSize from the exchange; or a positive
    # number, validated against that same metadata. Never derived from timeframe.
    tick_size: Union[str, float] = "auto"


@dataclass(frozen=True)
class Partition:
    """Chronological split policy. Not a search input.

    UNSEEN is carved off the END of the requested window first and locked. The
    remaining DEV span is handed to canonical V3, which applies its own fixed
    70/30 TRAIN/VALID split internally — V3 is not modifiable, so the effective
    whole-window split at the default 20% UNSEEN is 56 / 24 / 20.

    `unseen_start` pins the UNSEEN boundary to an exact date instead, which is
    what reproducing a historical campaign requires.
    """
    unseen_pct: float = 20.0
    unseen_start: object = None          # datetime.date | None


@dataclass(frozen=True)
class OptimizerPreset:
    path: str
    platform: str
    symbol: str
    timeframe: str
    history: history_mod.History
    initial_balance: float
    direction: Direction
    trials: Union[str, int]
    optimization_mode: str
    stages: Stages
    execution: Execution = None
    partition: Partition = None
    # Verbatim file contents, captured at load time. The manifest records what the
    # human actually asked for, and re-reading the path later is not safe: the file
    # may have been edited or removed while the campaign was running.
    snapshot: dict = None

    def resolved_trials(self) -> "tuple[int, bool]":
        """Back-compat shim: (total, was_auto)."""
        total, _alloc, was_auto, _note = self.resolved_budgets()
        return total, was_auto

    def resolved_budgets(self):
        """(total, {stage: trials}, was_auto, explanation) — the five V3 budgets."""
        return budgets_mod.resolve(self.trials, self.timeframe,
                                   self.history.span_days(self.timeframe))


def resolve_path(arg: str) -> str:
    """Resolve a preset argument to a file under configs/optimize/.

    Accepted: 'odefault.json', 'odefault', 'configs/optimize/odefault.json'.
    """
    if not arg or not arg.strip():
        raise PresetError("optimizer preset name is empty")
    arg = arg.strip()
    for cand in (arg, f"{arg}.json",
                 os.path.join(PRESET_DIR, arg),
                 os.path.join(PRESET_DIR, f"{arg}.json")):
        if os.path.isfile(cand):
            return cand
    available = _available()
    hint = ("Available presets:\n         " + "\n         ".join(available)
            if available else f"No presets found in {PRESET_DIR}/")
    raise PresetError(f"optimizer preset not found: '{arg}'\n       {hint}")


def _available():
    if not os.path.isdir(PRESET_DIR):
        return []
    return sorted(os.path.join(PRESET_DIR, f)
                  for f in os.listdir(PRESET_DIR) if f.endswith(".json"))


def _require_bool(block, key, where):
    value = block.get(key)
    if not isinstance(value, bool):
        raise PresetError(f"{where}.{key} must be true or false, got {value!r}")
    return value


def load(arg: str) -> OptimizerPreset:
    path = resolve_path(arg)

    try:
        with open(path, "r") as fh:
            raw = json.load(fh)
    except json.JSONDecodeError as exc:
        raise PresetError(f"optimizer preset is not valid JSON: {path}\n       {exc}")
    except OSError as exc:
        raise PresetError(f"cannot read optimizer preset: {path}\n       {exc}")

    if not isinstance(raw, dict):
        raise PresetError(f"optimizer preset must be a JSON object: {path}")

    missing = [k for k in REQUIRED_KEYS if k not in raw]
    if missing:
        raise PresetError(
            f"optimizer preset is missing required field(s): {', '.join(missing)}\n"
            f"       file: {path}"
        )

    schema_version = raw.get("_schema_version")
    if schema_version != 3:
        raise PresetError(
            f"unsupported preset schema version: {schema_version!r} (expected 3)\n"
            f"       file: {path}"
        )

    platform = raw["platform"]
    if platform not in SUPPORTED_PLATFORMS:
        raise PresetError(
            f"unsupported platform: {platform!r} "
            f"(supported: {', '.join(SUPPORTED_PLATFORMS)})"
        )

    symbol = raw["symbol"]
    if not isinstance(symbol, str) or not symbol.strip():
        raise PresetError(f"symbol must be a non-empty string, got {symbol!r}")

    timeframe = raw["timeframe"]
    if timeframe not in trials_mod.SUPPORTED_TIMEFRAMES:
        raise PresetError(
            f"unsupported timeframe: {timeframe!r} "
            f"(supported: {', '.join(trials_mod.SUPPORTED_TIMEFRAMES)})"
        )

    try:
        hist = history_mod.resolve(raw["history"])
    except history_mod.HistoryError as exc:
        raise PresetError(str(exc))

    balance = raw["initial_balance"]
    if isinstance(balance, bool) or not isinstance(balance, (int, float)):
        raise PresetError(f"initial_balance must be a number, got {balance!r}")
    if balance < MIN_BALANCE:
        raise PresetError(f"initial_balance must be at least {MIN_BALANCE:g}, got {balance}")

    dir_block = raw["direction"]
    if not isinstance(dir_block, dict):
        raise PresetError("direction must be an object with long_enabled/short_enabled")
    direction = Direction(
        long_enabled=_require_bool(dir_block, "long_enabled", "direction"),
        short_enabled=_require_bool(dir_block, "short_enabled", "direction"),
    )
    if not (direction.long_enabled or direction.short_enabled):
        raise PresetError(
            "direction has both long_enabled and short_enabled false; "
            "there is nothing to optimize"
        )

    # `trials` is the TOTAL across all five V3 stages. Per-stage budgets are
    # derived in budgets.py and are deliberately not preset fields.
    trials = raw["trials"]
    if isinstance(trials, str):
        if trials != "auto":
            raise PresetError(
                f"trials must be \"auto\" or a whole number, got {trials!r}"
            )
    elif isinstance(trials, bool) or not isinstance(trials, int):
        raise PresetError(f"trials must be \"auto\" or a whole number, got {trials!r}")
    else:
        try:
            budgets_mod.allocate(trials)
        except budgets_mod.BudgetError as exc:
            raise PresetError(str(exc))

    mode = raw["optimization_mode"]
    if mode not in SUPPORTED_MODES:
        raise PresetError(
            f"unsupported optimization_mode: {mode!r} "
            f"(supported: {', '.join(SUPPORTED_MODES)})"
        )

    stage_block = raw["stages"]
    if not isinstance(stage_block, dict):
        raise PresetError(f"stages must be an object with {', '.join(STAGE_KEYS)}")
    unknown = set(stage_block) - set(STAGE_KEYS)
    if unknown:
        raise PresetError(
            f"unknown key(s) in stages: {', '.join(sorted(unknown))} "
            f"(allowed: {', '.join(STAGE_KEYS)})"
        )
    missing_stages = [k for k in STAGE_KEYS if k not in stage_block]
    if missing_stages:
        raise PresetError(f"stages is missing: {', '.join(missing_stages)}")
    stages = Stages(**{k: _require_bool(stage_block, k, "stages") for k in STAGE_KEYS})

    if not any((stages.strategy_optimization, stages.risk_management, stages.bollinger)):
        raise PresetError("every stage is disabled; there is nothing to optimize")

    if not stages.strategy_optimization:
        raise PresetError(
            "stages.strategy_optimization is false, but a new optimizer run cannot "
            "start without it: stages 1a/1b discover the strategy seed that the "
            "risk and Bollinger stages are searched around.\n"
            "       Enable strategy_optimization, or run a backtest instead of an "
            "optimization."
        )

    # ---- optional execution block -----------------------------------------
    exec_block = raw.get("execution", {})
    if not isinstance(exec_block, dict):
        raise PresetError("execution must be an object with tick_size")
    unknown = set(exec_block) - {"tick_size"}
    if unknown:
        raise PresetError(
            f"unknown key(s) in execution: {', '.join(sorted(unknown))} "
            "(allowed: tick_size). Commission, slippage and quantity step are "
            "project constants or resolved from the exchange."
        )
    tick = exec_block.get("tick_size", "auto")
    if isinstance(tick, str):
        if tick != "auto":
            raise PresetError(
                f"execution.tick_size must be \"auto\" or a positive number, got {tick!r}"
            )
    elif isinstance(tick, bool) or not isinstance(tick, (int, float)):
        raise PresetError(
            f"execution.tick_size must be \"auto\" or a positive number, got {tick!r}"
        )
    elif tick <= 0:
        raise PresetError(f"execution.tick_size must be positive, got {tick!r}")
    else:
        tick = float(tick)
    execution = Execution(tick_size=tick)

    # ---- optional partition block -----------------------------------------
    part_block = raw.get("partition", {})
    if not isinstance(part_block, dict):
        raise PresetError("partition must be an object with unseen_pct/unseen_start")
    unknown = set(part_block) - {"unseen_pct", "unseen_start"}
    if unknown:
        raise PresetError(
            f"unknown key(s) in partition: {', '.join(sorted(unknown))} "
            "(allowed: unseen_pct, unseen_start)"
        )
    unseen_pct = part_block.get("unseen_pct", 20.0)
    if isinstance(unseen_pct, bool) or not isinstance(unseen_pct, (int, float)):
        raise PresetError(f"partition.unseen_pct must be a number, got {unseen_pct!r}")
    if not (5.0 <= float(unseen_pct) <= 40.0):
        raise PresetError(
            f"partition.unseen_pct must be between 5 and 40, got {unseen_pct}"
        )
    unseen_start = part_block.get("unseen_start")
    if unseen_start is not None:
        if "unseen_pct" in part_block:
            raise PresetError(
                "partition.unseen_pct and partition.unseen_start are mutually "
                "exclusive; set the percentage OR the exact boundary date."
            )
        unseen_start = history_mod._parse_date(unseen_start, "partition.unseen_start")
    partition = Partition(unseen_pct=float(unseen_pct), unseen_start=unseen_start)

    return OptimizerPreset(
        path=path,
        platform=platform,
        symbol=symbol.strip(),
        timeframe=timeframe,
        history=hist,
        initial_balance=float(balance),
        direction=direction,
        trials=trials,
        optimization_mode=mode,
        stages=stages,
        execution=execution,
        partition=partition,
        snapshot=raw,
    )
