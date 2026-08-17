"""Bakeoff data preparation — isolated experiment cache, hard-bounded below the lock.

Builds, for each symbol, a frame of exactly 1,000 warmup candles followed by the DEV window:

    warmup  2024-07-05 14:00:00Z .. 2024-07-15 23:45:00Z   1,000 rows
    DEV     2024-07-16 00:00:00Z .. 2026-07-15 23:45:00Z  70,080 rows

Every row at or after 2026-07-16 00:00 UTC is rejected before anything is written. ETH is
sliced from the existing quarantine dataset (no fetch). BTC is fetched, bounded, and validated.
Writes only under this directory.
"""
import hashlib
import json
import os
import time

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
ETH_SRC = os.path.abspath(os.path.join(
    HERE, "..", "..", "recovered_phase3a", "quarantine", "data",
    "candles_futures_binance_futures_ETHUSDT_15m.csv"))

LOCK = pd.Timestamp("2026-07-16 00:00:00", tz="UTC")     # exclusive
DEV_LO = pd.Timestamp("2024-07-16 00:00:00", tz="UTC")
DEV_HI = pd.Timestamp("2026-07-15 23:45:00", tz="UTC")   # last DEV candle
WARM = 1000
WARM_LO = DEV_LO - pd.Timedelta(minutes=15 * WARM)       # 2024-07-05 14:00Z
STEP = 900
COLS = ["timestamp", "open", "high", "low", "close", "volume", "datetime"]

os.makedirs(DATA, exist_ok=True)


def fetch(symbol):
    rows, cur = [], int(WARM_LO.timestamp() * 1000)
    end = int(DEV_HI.timestamp() * 1000)
    while cur <= end:
        r = requests.get("https://fapi.binance.com/fapi/v1/klines",
                         params={"symbol": symbol, "interval": "15m", "startTime": cur,
                                 "endTime": end, "limit": 1000}, timeout=25)
        r.raise_for_status()
        kl = r.json()
        if not kl:
            break
        for k in kl:
            ms = int(k[0])
            ts = pd.Timestamp(ms, unit="ms", tz="UTC")
            if ts >= LOCK:                                    # hard reject
                continue
            rows.append({"timestamp": ms // 1000, "open": float(k[1]), "high": float(k[2]),
                         "low": float(k[3]), "close": float(k[4]), "volume": float(k[5]),
                         "datetime": str(ts)})
        cur = int(kl[-1][0]) + STEP * 1000
        time.sleep(0.12)
    return pd.DataFrame(rows).drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)


def slice_eth():
    df = pd.read_csv(ETH_SRC)
    dt = pd.to_datetime(df["datetime"], utc=True)
    return df[(dt >= WARM_LO) & (dt < LOCK)].reset_index(drop=True)


def validate(sym, df):
    dt = pd.to_datetime(df["datetime"], utc=True)
    dev = df[(dt >= DEV_LO) & (dt < LOCK)]
    warm = df[dt < DEV_LO]
    diffs = sorted(set(int(x) for x in df["timestamp"].diff().dropna().unique()))
    checks = [
        ("zero rows at/after lock", int((dt >= LOCK).sum()) == 0, int((dt >= LOCK).sum())),
        ("warmup rows == 1000", len(warm) == WARM, len(warm)),
        ("DEV rows == 70080", len(dev) == 70080, len(dev)),
        ("warmup starts 2024-07-05 14:00", str(dt.iloc[0]) == "2024-07-05 14:00:00+00:00", str(dt.iloc[0])),
        ("DEV ends 2026-07-15 23:45", str(dt.iloc[-1]) == "2026-07-15 23:45:00+00:00", str(dt.iloc[-1])),
        ("uniform 15m spacing", diffs == [STEP], diffs[:4]),
        ("no duplicate timestamps", not df["timestamp"].duplicated().any(), int(df["timestamp"].duplicated().sum())),
        ("prices positive, high>=low", bool((df[["open", "high", "low", "close"]] > 0).all().all()
                                            and (df["high"] >= df["low"]).all()), ""),
    ]
    for n, ok, d in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {sym} {n:<34} {d}")
    return all(ok for _, ok, _ in checks)


out = {}
allok = True
for sym, getter in (("ETHUSDT", slice_eth), ("BTCUSDT", lambda: fetch("BTCUSDT"))):
    df = getter()[COLS]
    ok = validate(sym, df)
    allok &= ok
    if not ok:
        print(f"  {sym}: VALIDATION FAILED — not written")
        continue
    p = os.path.join(DATA, f"{sym}_15m_warmup1000_dev.csv")
    df.to_csv(p, index=False)
    dt = pd.to_datetime(df["datetime"], utc=True)
    out[sym] = {"path": p, "rows": len(df), "warmup_rows": 1000, "dev_rows": 70080,
                "start": str(dt.iloc[0]), "end": str(dt.iloc[-1]),
                "sha256": hashlib.sha256(open(p, "rb").read()).hexdigest(),
                "source": "sliced from quarantine ETH dataset (no fetch)" if sym == "ETHUSDT"
                          else "fetched from fapi.binance.com, bounded below 2026-07-16"}
    print(f"  {sym}: {len(df):,} rows -> {os.path.basename(p)}")

json.dump({"lock_boundary_exclusive": str(LOCK), "dev": [str(DEV_LO), str(DEV_HI)],
           "warmup_candles": WARM, "warmup_start": str(WARM_LO),
           "all_validations_passed": allok, "datasets": out},
          open(os.path.join(HERE, "data_manifest.json"), "w"), indent=2)
print(f"\nall validations passed: {allok}")
