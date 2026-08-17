"""Exchange market rules — tick size and quantity step, resolved from the exchange.

There is no per-symbol map in this repo and there must never be one: a hardcoded
tick size silently goes stale when an exchange relists or re-scales a contract,
and the error only shows up as mispriced slippage deep inside a campaign.

Tick size is a property of the SYMBOL, never of the timeframe.

`tick_size: "auto"` resolves `PRICE_FILTER.tickSize` once, during data
preparation. A numeric override is validated against the same metadata and
rejected if it is not a positive multiple of the exchange tick. The quantity step
(`LOT_SIZE.stepSize`) is always resolved automatically and is not a preset field;
the resolved value is written into the manifest and the emitted config.
"""

import json
import os
import time
import urllib.request
from dataclasses import dataclass
from typing import Optional

EXCHANGE_INFO_URL = {
    "BINANCE_FUTURES": "https://fapi.binance.com/fapi/v1/exchangeInfo",
}

CACHE_DIR = os.path.join("results", "auto_optimise", "_market_rules_cache")
CACHE_TTL_SECONDS = 24 * 3600
TIMEOUT_SECONDS = 20


class MarketRuleError(RuntimeError):
    """Raised when exchange metadata is unavailable or a preset value contradicts it."""


@dataclass(frozen=True)
class MarketRules:
    platform: str
    symbol: str
    tick_size: float
    quantity_step: float
    min_qty: float
    source: str                  # "exchange" | "cache"
    fetched_at: str
    tick_source: str             # "auto" | "preset-override"

    def to_dict(self) -> dict:
        return {
            "platform": self.platform, "symbol": self.symbol,
            "tick_size": self.tick_size, "quantity_step": self.quantity_step,
            "min_qty": self.min_qty, "source": self.source,
            "fetched_at": self.fetched_at, "tick_size_source": self.tick_source,
        }


def _cache_path(platform: str, symbol: str) -> str:
    return os.path.join(CACHE_DIR, f"{platform}_{symbol}.json")


def _read_cache(platform: str, symbol: str) -> Optional[dict]:
    path = _cache_path(platform, symbol)
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as fh:
            blob = json.load(fh)
    except (OSError, ValueError):
        return None
    if time.time() - blob.get("_cached_at", 0) > CACHE_TTL_SECONDS:
        return None
    return blob


def _write_cache(platform: str, symbol: str, blob: dict):
    os.makedirs(CACHE_DIR, exist_ok=True)
    blob = dict(blob)
    blob["_cached_at"] = time.time()
    with open(_cache_path(platform, symbol), "w") as fh:
        json.dump(blob, fh, indent=2)


def _fetch(platform: str, symbol: str) -> dict:
    url = EXCHANGE_INFO_URL.get(platform)
    if url is None:
        raise MarketRuleError(f"no exchange metadata endpoint for platform {platform!r}")
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as resp:
            payload = json.load(resp)
    except Exception as exc:                       # network, DNS, JSON, HTTP
        raise MarketRuleError(
            f"could not reach {platform} exchange metadata: {exc}\n"
            f"       tick size cannot be guessed. Retry, or set "
            f"execution.tick_size to the exact value for {symbol}."
        )
    for entry in payload.get("symbols", []):
        if entry.get("symbol") == symbol:
            return entry
    raise MarketRuleError(
        f"{symbol!r} is not listed on {platform}. Check the symbol in the preset."
    )


def _filter_value(entry: dict, filter_type: str, key: str) -> float:
    for filt in entry.get("filters", []):
        if filt.get("filterType") == filter_type:
            try:
                return float(filt[key])
            except (KeyError, TypeError, ValueError):
                break
    raise MarketRuleError(
        f"{filter_type}.{key} missing from exchange metadata for "
        f"{entry.get('symbol')!r}"
    )


def _is_multiple(value: float, unit: float) -> bool:
    """True when `value` lands on the exchange grid, within float tolerance."""
    if unit <= 0:
        return False
    ratio = value / unit
    return abs(ratio - round(ratio)) < 1e-6


def resolve(platform: str, symbol: str, tick_size_pref, allow_network: bool = True
            ) -> MarketRules:
    """Resolve market rules. `tick_size_pref` is "auto" or a positive number."""
    entry = _read_cache(platform, symbol)
    source = "cache"
    if entry is None:
        if not allow_network:
            raise MarketRuleError(
                f"no cached exchange metadata for {symbol} and network access is "
                "disabled for this run"
            )
        entry = _fetch(platform, symbol)
        _write_cache(platform, symbol, entry)
        source = "exchange"

    exchange_tick = _filter_value(entry, "PRICE_FILTER", "tickSize")
    step = _filter_value(entry, "LOT_SIZE", "stepSize")
    min_qty = _filter_value(entry, "LOT_SIZE", "minQty")

    if exchange_tick <= 0:
        raise MarketRuleError(f"exchange reported a non-positive tick size for {symbol}")

    if isinstance(tick_size_pref, str):
        tick = exchange_tick
        tick_source = "auto"
    else:
        tick = float(tick_size_pref)
        if tick <= 0:
            raise MarketRuleError(
                f"execution.tick_size must be positive, got {tick_size_pref!r}"
            )
        if tick < exchange_tick or not _is_multiple(tick, exchange_tick):
            raise MarketRuleError(
                f"execution.tick_size {tick:g} is not valid for {symbol} on "
                f"{platform}: the exchange tick is {exchange_tick:g}, and an "
                f"override must be a positive whole multiple of it.\n"
                f"       Use \"auto\" unless you have a specific reason not to."
            )
        tick_source = "preset-override"

    return MarketRules(
        platform=platform, symbol=symbol,
        tick_size=tick, quantity_step=step, min_qty=min_qty,
        source=source,
        fetched_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        tick_source=tick_source,
    )
