"""Pine <-> JSON parity check.

The Pine port is a mirror, not a second implementation, so every parameter the
generator exposes as an `input.*` default must equal the winner JSON exactly.
This reads the generated `.pine` back and compares defaults field by field
rather than trusting that the generator was called correctly.

Fields the generator cannot represent are reported as UNSUPPORTED, never quietly
passed — a Pine file that silently omits an active feature is worse than no Pine
file at all.
"""

import json
import re
from typing import Any, Dict, List, Tuple

# json path -> the label the generator gives that input in the Pine source
FIELD_MAP: List[Tuple[str, str, str]] = [
    ("strategy.ema_period", "EMA Period", "int"),
    ("strategy.rsi_period", "RSI Period", "int"),
    ("strategy.rsi_overbought", "RSI Overbought Threshold", "float"),
    ("strategy.rsi_oversold", "RSI Oversold Threshold", "float"),
    ("strategy.atr_period", "ATR Period", "int"),
    ("strategy.consolidation_candles", "Consolidation Lookback (Candles)", "int"),
    ("strategy.consolidation_atr_mult", "Consolidation ATR Multiplier", "float"),
    ("strategy.swing_lookback", "Swing High/Low Lookback", "int"),
    ("strategy.volume_sma_period", "Volume SMA Period", "int"),
    ("strategy.volume_mult", "Volume Multiplier vs SMA", "float"),
    ("strategy.risk_reward_ratio", "Risk-to-Reward Target (1 : X)", "float"),
    ("strategy.long_enabled", "Enable Long Trades", "bool"),
    ("strategy.short_enabled", "Enable Short Trades", "bool"),
    ("risk.leverage", "Leverage (x)", "float"),
    ("risk.risk_per_trade_pct",
     "Account Risk % Per Trade (price risk, gross of fees)", "float"),
    ("risk.max_position_allocation_pct", "Max Equity Committed As MARGIN %", "float"),
    ("risk.quantity_step", "Instrument Quantity Step", "float"),
    ("execution.tick_size", "Instrument Tick Size", "float"),
    ("filters.bollinger.enabled", "Enable Bollinger Filter", "bool"),
    ("filters.bollinger.length", "Bollinger Length", "int"),
    ("filters.bollinger.std", "Bollinger StdDev", "float"),
    ("filters.bollinger.min_bandwidth_pct", "Min Bandwidth % (0 = off)", "float"),
    ("filters.bollinger.expansion_lookback", "Expansion Lookback", "int"),
    ("filters.bollinger.expansion_min_ratio",
     "Expansion Min Ratio (0 = off)", "float"),
    ("filters.bollinger.min_mid_distance",
     "Min Middle-Band Distance (0 = off)", "float"),
]

# Checked separately: they live in the `strategy(...)` header, not an input.
HEADER_FIELDS = [("risk.initial_capital", "initial_capital", "int"),
                 ("execution.commission_pct", "commission_value", "float"),
                 ("execution.slippage_ticks", "slippage", "int")]


def _dig(payload: Dict[str, Any], path: str):
    node = payload
    for part in path.split("."):
        node = node[part]
    return node


def _pine_input(src: str, label: str):
    """Default value of the `input.*` whose title is `label`."""
    pattern = re.compile(
        r"input\.(int|float|bool)\(\s*([^,]+?)\s*,\s*\"" + re.escape(label) + r"\"")
    match = pattern.search(src)
    return None if match is None else match.group(2).strip()


def _pine_header(src: str, key: str):
    match = re.search(re.escape(key) + r"=([0-9.]+)", src)
    return None if match is None else match.group(1)


def _equal(kind: str, json_value, pine_text) -> bool:
    if pine_text is None:
        return False
    if kind == "bool":
        return str(bool(json_value)).lower() == pine_text.lower()
    try:
        return abs(float(json_value) - float(pine_text)) < 1e-6
    except ValueError:
        return False


def check(config_path: str, pine_path: str) -> Dict[str, Any]:
    with open(config_path) as fh:
        payload = json.load(fh)
    with open(pine_path) as fh:
        src = fh.read()

    rows, matched, unsupported = [], 0, []
    for json_path, label, kind in FIELD_MAP:
        try:
            json_value = _dig(payload, json_path)
        except KeyError:
            unsupported.append(f"{json_path} (absent from JSON)")
            continue
        pine_text = _pine_input(src, label)
        if pine_text is None:
            unsupported.append(f"{json_path} (no Pine input titled {label!r})")
            rows.append({"field": json_path, "json": json_value,
                         "pine": None, "match": False})
            continue
        ok = _equal(kind, json_value, pine_text)
        matched += int(ok)
        rows.append({"field": json_path, "json": json_value,
                     "pine": pine_text, "match": ok})

    for json_path, key, kind in HEADER_FIELDS:
        json_value = _dig(payload, json_path)
        pine_text = _pine_header(src, key)
        ok = _equal(kind, json_value, pine_text)
        matched += int(ok)
        rows.append({"field": json_path, "json": json_value,
                     "pine": pine_text, "match": ok})

    total = len(FIELD_MAP) + len(HEADER_FIELDS)
    return {"rows": rows, "matched": matched, "total": total,
            "unsupported": unsupported,
            "parity": matched == total and not unsupported}
