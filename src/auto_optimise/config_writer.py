"""Final config generation — the optimizer's only write into `configs/config/`.

Written atomically: a temporary file, validated by loading it back through the
same schema the pipeline uses, then `os.replace` onto the target. A partially
written or schema-invalid config never appears at the destination.

The output-name guard from stage [1/6] still applies and is re-checked here
immediately before the rename, so a file appearing during a long campaign cannot
be overwritten.
"""

import hashlib
import json
import os
from typing import Any, Dict

from . import bollinger_policy, output_guard, risk_policy, search_space

# The live preset schema `pipeline.sh` and `src/main.py` validate against.
REQUIRED_BLOCKS = ("platform", "symbol", "timeframe", "strategy", "risk",
                   "execution")

# Execution values are project constants, not optimizer outputs — every stage
# was evaluated under exactly these.
EXECUTION = {"commission_pct": 0.05, "slippage_ticks": 1, "tick_size": 0.01}


def build(winner: Dict[str, Any], preset, prepared, provenance: Dict[str, Any]
          ) -> Dict[str, Any]:
    """Assemble the runnable strategy config for a frozen winner."""
    strategy = {name: (int(winner[name]) if name in search_space.INT_PARAMS
                       else float(winner[name]))
                for name in search_space.PARAM_NAMES}
    strategy["long_enabled"] = bool(preset.direction.long_enabled)
    strategy["short_enabled"] = bool(preset.direction.short_enabled)

    risk = {
        "sizing_mode": "RISK_BASED",
        "initial_capital": float(preset.initial_balance),
        "leverage": float(winner["leverage"]),
        "risk_per_trade_pct": float(winner["risk_per_trade_pct"]),
        "max_position_allocation_pct": float(winner["max_position_allocation_pct"]),
        "quantity_step": 0.001,
    }

    enabled = bool(winner.get("bollinger_enabled"))
    bollinger = {"enabled": enabled}
    for name in bollinger_policy.PARAM_NAMES:
        value = winner.get(name, 0)
        bollinger[name] = (int(float(value)) if name in bollinger_policy.INT_PARAMS
                           else float(value))
    if not enabled:
        # Keep the live defaults rather than zeros when the filter is off, so the
        # block is a usable starting point if someone flips `enabled` by hand.
        bollinger.update({"length": 20, "std": 2.0, "min_bandwidth_pct": 0.0,
                          "expansion_lookback": 5, "expansion_min_ratio": 0.0,
                          "min_mid_distance": 0.0})

    return {
        "_name": provenance.get("name", "auto-optimizer winner"),
        "_description": ("Produced automatically by src/auto_optimise stages 1-6. "
                         "Strategy, risk and filter were selected on TRAIN and "
                         "VALIDATION; UNSEEN was opened once, after the champion "
                         "was frozen, and only to confirm it."),
        "_generated_by": "auto_optimise",
        "_risk_policy": "preset",
        "_symbol": preset.symbol,
        "_timeframe": preset.timeframe,
        "_direction": ("LONG_ONLY" if preset.direction.long_enabled
                       and not preset.direction.short_enabled
                       else "SHORT_ONLY" if preset.direction.short_enabled
                       and not preset.direction.long_enabled else "LONG_SHORT"),
        "_train_start": str(prepared.train.start),
        "_train_end": str(prepared.train.end),
        "_validation_start": str(prepared.validation.start),
        "_validation_end": str(prepared.validation.end),
        "_unseen_start": str(prepared.unseen_start),
        "_unseen_end": str(prepared.unseen_end),
        "_provenance": provenance,
        "platform": preset.platform,
        "symbol": preset.symbol,
        "timeframe": preset.timeframe,
        "strategy": strategy,
        "risk": risk,
        "execution": dict(EXECUTION),
        "filters": {"bollinger": bollinger},
    }


def _validate(payload: Dict[str, Any]):
    """Load the written file back through the real schema before publishing it."""
    missing = [k for k in REQUIRED_BLOCKS if k not in payload]
    if missing:
        raise ValueError(f"generated config is missing: {', '.join(missing)}")

    from common.config import ExecutionConfig, RiskConfig, StrategyConfig
    from filters.stage_1_bollinger.filter import BollingerFilterConfig

    strat = StrategyConfig()
    for key, value in payload["strategy"].items():
        if not hasattr(strat, key):
            raise ValueError(f"strategy.{key} is not a StrategyConfig field")
        setattr(strat, key, value)

    risk = RiskConfig()
    for key in ("initial_capital", "leverage", "quantity_step"):
        if key not in payload["risk"]:
            raise ValueError(f"risk.{key} missing")
    # main.py converts these two from percent to fraction; check the raw range.
    for key in ("risk_per_trade_pct", "max_position_allocation_pct"):
        value = float(payload["risk"][key])
        if not (0.0 < value <= 100.0):
            raise ValueError(f"risk.{key}={value} is not a percentage")
    risk.leverage = float(payload["risk"]["leverage"])

    execution = ExecutionConfig()
    for key in EXECUTION:
        if key not in payload["execution"]:
            raise ValueError(f"execution.{key} missing")

    BollingerFilterConfig.from_dict(payload["filters"]["bollinger"])
    return strat, risk, execution


def write(payload: Dict[str, Any], output) -> Dict[str, str]:
    """Atomically publish the config. Returns {path, sha256}."""
    _validate(payload)

    # Re-check the guard: the campaign may have run for a long time.
    target = output_guard.validate(output.name)
    tmp = target.path + ".tmp"

    blob = json.dumps(payload, indent=2) + "\n"
    with open(tmp, "w") as fh:
        fh.write(blob)
        fh.flush()
        os.fsync(fh.fileno())

    # Prove the bytes on disk parse and satisfy the schema before publishing.
    with open(tmp) as fh:
        _validate(json.load(fh))

    os.replace(tmp, target.path)
    digest = hashlib.sha256(blob.encode()).hexdigest()
    return {"path": target.path, "sha256": digest}
