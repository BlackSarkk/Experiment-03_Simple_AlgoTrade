"""Optimizer preset (`configs/optimize/*.json`) loading and validation.

Only human-facing inputs live in the preset. Search ranges, objective weights,
Optuna internals, warmup, partition ratios, seeds and storage paths stay
automatic and are deliberately not accepted here.
"""

import json
import os
from dataclasses import dataclass
from typing import Union

from . import history as history_mod
from . import trials as trials_mod

PRESET_DIR = os.path.join("configs", "optimize")

REQUIRED_KEYS = (
    "platform", "symbol", "timeframe", "history", "initial_balance",
    "direction", "trials", "optimization_mode", "stages",
)

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

    def resolved_trials(self) -> "tuple[int, bool]":
        return trials_mod.resolve(self.trials, self.timeframe)


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

    trials = raw["trials"]
    if isinstance(trials, str):
        if trials != "auto":
            raise PresetError(
                f"trials must be \"auto\" or a whole number, got {trials!r}"
            )
    elif isinstance(trials, bool) or not isinstance(trials, int):
        raise PresetError(f"trials must be \"auto\" or a whole number, got {trials!r}")
    elif not (trials_mod.MIN_TRIALS <= trials <= trials_mod.MAX_TRIALS):
        raise PresetError(
            f"trials must be between {trials_mod.MIN_TRIALS} and "
            f"{trials_mod.MAX_TRIALS}, got {trials}"
        )

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
    )
