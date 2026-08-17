"""Build the runnable strategy config for a frozen V3 winner.

Publishing (atomic temp-file write, schema re-validation, output-guard re-check)
is reused verbatim from `config_writer.write` — that path is proven and is not
duplicated here. Only the payload shape is V3-specific.
"""

from typing import Any, Dict

from optimization.v3 import spec as V3_SPEC

from . import config_writer

# Commission and slippage are project constants: every V3 trial was scored under
# exactly these. Tick size and quantity step are RESOLVED FROM THE EXCHANGE and
# are therefore per-run values, not constants.
COMMISSION_PCT = 0.05
SLIPPAGE_TICKS = 1

BOLLINGER_FIELDS = ("length", "std", "min_bandwidth_pct", "expansion_lookback",
                    "expansion_min_ratio", "min_mid_distance")
BOLLINGER_INT_FIELDS = ("length", "expansion_lookback")


def build(preset, prepared, rules, result, unseen_report, provenance: Dict[str, Any],
          facts: Dict[str, Any] = None) -> Dict[str, Any]:
    winner = result.winner

    strategy = {}
    for name in V3_SPEC.STRATEGY_KEYS:
        kind = V3_SPEC.STRATEGY_RANGES[name][0]
        strategy[name] = int(winner[name]) if kind == "int" else float(winner[name])
    # Direction comes from the preset and is never searched.
    strategy["long_enabled"] = bool(preset.direction.long_enabled)
    strategy["short_enabled"] = bool(preset.direction.short_enabled)

    risk = {
        "sizing_mode": "RISK_BASED",
        "initial_capital": float(preset.initial_balance),
        "leverage": float(winner["leverage"]),
        # V3 carries these as fractions; the runnable config uses percent.
        "risk_per_trade_pct": float(winner["risk_per_trade_pct"]) * 100.0,
        "max_position_allocation_pct": float(winner["max_position_allocation_pct"]) * 100.0,
        "quantity_step": float(rules.quantity_step),
    }

    bcfg = result.bollinger.to_dict() if hasattr(result.bollinger, "to_dict") else {}
    bollinger = {"enabled": bool(result.bollinger_enabled)}
    for name in BOLLINGER_FIELDS:
        value = bcfg.get(name, 0)
        bollinger[name] = (int(float(value)) if name in BOLLINGER_INT_FIELDS
                           else float(value))

    stage_status = dict(provenance["stages"])
    return {
        "_name": provenance.get("output_name", "V3 optimizer winner"),
        "_description": (
            "Produced by src/auto_optimise driving canonical optimization.v3. "
            "Strategy, risk and filter were selected on TRAIN and VALIDATION only. "
            "UNSEEN was opened once, after the winner was frozen, purely to "
            "confirm it, and did not influence any selection."
        ),
        "_generated_by": "auto_optimise/v3",
        "_optimizer_architecture": "optimization.v3",
        "_v3_version": V3_SPEC.VERSION,
        "_symbol": preset.symbol,
        "_timeframe": preset.timeframe,
        "_direction": ("LONG_ONLY" if preset.direction.long_enabled
                       and not preset.direction.short_enabled
                       else "SHORT_ONLY" if preset.direction.short_enabled
                       and not preset.direction.long_enabled else "LONG_SHORT"),
        "_source_preset": preset.path,
        "_run_id": provenance.get("run_id"),
        "_run_started_utc": provenance.get("started_utc"),
        "_environment": provenance.get("environment"),

        "_trial_budget": {
            "total": provenance["trial_total"],
            "per_stage": provenance["allocation"],
            "resolved_from": provenance["trial_source"],
        },
        "_stages": stage_status,
        "_stages_skipped": provenance["skipped"],
        "_risk_optimized": stage_status.get("stage_1c_risk") == "SELECTED"
                           or preset.stages.risk_management,

        "_warmup_start": str(prepared.warmup_start),
        "_warmup_end": str(prepared.warmup_end),
        "_warmup_candles": int(prepared.warmup_candles),
        "_train_start": str(prepared.train.start),
        "_train_end": str(prepared.train.end),
        "_train_candles": int(prepared.train.n_candles),
        "_validation_start": str(prepared.validation.start),
        "_validation_end": str(prepared.validation.end),
        "_validation_candles": int(prepared.validation.n_candles),
        "_unseen_start": str(prepared.unseen_start),
        "_unseen_end": str(prepared.unseen_end),
        "_unseen_candles": int(prepared.unseen_candles),
        "_data_checksum": prepared.checksum,
        "_partition_policy": {
            "policy": ("reserve the final share of the requested history as sealed "
                       "UNSEEN, then apply V3's canonical DEV split"),
            "unseen_reserved_first": True,
            "unseen_boundary_source": (facts or {}).get("unseen_boundary_source"),
            "dev_local_split": {"train_pct": (facts or {}).get("dev_train_pct"),
                                "valid_pct": (facts or {}).get("dev_valid_pct")},
            "effective_full_history_split": {
                "train_pct": (facts or {}).get("train_pct"),
                "valid_pct": (facts or {}).get("valid_pct"),
                "unseen_pct": (facts or {}).get("unseen_pct")},
            "dev_rows": (facts or {}).get("dev_rows"),
            "evaluated_rows": (facts or {}).get("evaluated_rows"),
        },
        "_direction_policy": result.direction,

        "_selected_before_unseen": {
            "seed": result.seed,
            "winner": result.winner,
            "stage_winners": result.stage_meta,
            "train_valid_metrics": result.dev_metrics,
        },
        "_unseen_confirmation": unseen_report,

        "platform": preset.platform,
        "symbol": preset.symbol,
        "timeframe": preset.timeframe,
        "strategy": strategy,
        "risk": risk,
        "execution": {
            "commission_pct": COMMISSION_PCT,
            "slippage_ticks": SLIPPAGE_TICKS,
            "tick_size": float(rules.tick_size),
            "tick_size_source": rules.tick_source,
            "quantity_step": float(rules.quantity_step),
        },
        "filters": {"bollinger": bollinger},
    }


def write(payload, output):
    return config_writer.write(payload, output)
