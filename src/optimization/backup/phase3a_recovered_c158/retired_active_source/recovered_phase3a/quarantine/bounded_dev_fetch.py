"""Bounded DEV-data repair — 95 candles only, quarantine copy only.

Authorized interval (inclusive):  2026-07-15 00:15:00 UTC .. 2026-07-15 23:45:00 UTC
Hard lock boundary (exclusive):   2026-07-16 00:00:00 UTC

* Requests a window that stops at the last authorized candle; nothing at/after the lock
  boundary is asked for, accepted, or written.
* Every returned row is validated: symbol, interval spacing, inside the authorized
  interval, strictly before the boundary, no duplicates, no gaps, exactly 95 rows.
* Writes ONLY to quarantine/data/. The project's data/ cache is opened read-only.
"""
import hashlib
import json
import os
import sys

ROOT = "/home/rahul/Documents/Claude/Experiment-03_Simple_AlgoTrade"
QUAR = os.path.join(ROOT, "src", "optimization", "recovered_phase3a", "quarantine")
QDATA = os.path.join(QUAR, "data")
SRC_CACHE = os.path.join(ROOT, "data", "candles_futures_binance_futures_ETHUSDT_15m.csv")
DST_CACHE = os.path.join(QDATA, "candles_futures_binance_futures_ETHUSDT_15m.csv")

import pandas as pd
import requests

SYMBOL, INTERVAL, STEP_S = "ETHUSDT", "15m", 900
LO = pd.Timestamp("2026-07-15 00:15:00", tz="UTC")
HI = pd.Timestamp("2026-07-15 23:45:00", tz="UTC")     # last authorized candle
LOCK = pd.Timestamp("2026-07-16 00:00:00", tz="UTC")   # exclusive
N_EXPECT = 95

os.makedirs(QDATA, exist_ok=True)
base = pd.read_csv(SRC_CACHE)
b_dt = pd.to_datetime(base["datetime"], utc=True)
assert int((b_dt >= LOCK).sum()) == 0, "source cache already contains locked rows"
before = {"rows": len(base), "start": str(b_dt.min()), "end": str(b_dt.max()),
          "sha256": hashlib.sha256(open(SRC_CACHE, "rb").read()).hexdigest()}

# ---------------------------------------------------------------- fetch
params = {"symbol": SYMBOL, "interval": INTERVAL,
          "startTime": int(LO.timestamp() * 1000),
          "endTime": int(HI.timestamp() * 1000),      # inclusive of the last authorized open
          "limit": 1000}
r = requests.get("https://fapi.binance.com/fapi/v1/klines", params=params, timeout=20)
r.raise_for_status()
kl = r.json()
print(f"fetch: HTTP {r.status_code}, {len(kl)} klines returned")

rows = []
for k in kl:
    open_ms = int(k[0])
    ts = pd.Timestamp(open_ms, unit="ms", tz="UTC")
    rows.append({"timestamp": open_ms // 1000, "open": float(k[1]), "high": float(k[2]),
                 "low": float(k[3]), "close": float(k[4]), "volume": float(k[5]),
                 "datetime": str(ts)})
new = pd.DataFrame(rows)

# ---------------------------------------------------------------- validate
v = []


def chk(name, ok, detail):
    v.append((name, bool(ok), detail))


ndt = pd.to_datetime(new["datetime"], utc=True) if len(new) else pd.Series([], dtype="datetime64[ns, UTC]")
chk("row count == 95", len(new) == N_EXPECT, f"{len(new)}")
chk("all timestamps >= authorized start", len(new) and ndt.min() >= LO, f"min {ndt.min() if len(new) else '-'}")
chk("all timestamps <= authorized end", len(new) and ndt.max() <= HI, f"max {ndt.max() if len(new) else '-'}")
chk("zero rows at/after lock boundary", int((ndt >= LOCK).sum()) == 0 if len(new) else True,
    f"{int((ndt >= LOCK).sum()) if len(new) else 0}")
chk("no duplicate timestamps", len(new) and not new["timestamp"].duplicated().any(),
    f"dups {int(new['timestamp'].duplicated().sum()) if len(new) else 0}")
diffs = new["timestamp"].diff().dropna().unique() if len(new) > 1 else []
chk("uniform 15m spacing, no gaps", len(diffs) == 1 and int(diffs[0]) == STEP_S,
    f"distinct deltas {sorted(int(d) for d in diffs)}")
chk("contiguous with existing cache",
    len(new) and int(new['timestamp'].iloc[0]) - int(base['timestamp'].iloc[-1]) == STEP_S,
    f"cache_last {base['timestamp'].iloc[-1]} -> new_first {new['timestamp'].iloc[0] if len(new) else '-'}")
chk("no overlap with existing cache",
    len(new) and int(new["timestamp"].min()) > int(base["timestamp"].max()), "")
chk("prices sane", len(new) and bool(((new[["open", "high", "low", "close"]] > 0).all().all())
    and (new["high"] >= new["low"]).all()), "")

print()
for n, ok, d in v:
    print(f"  [{'PASS' if ok else 'FAIL'}] {n:<38} {d}")
fails = [n for n, ok, _ in v if not ok]
if fails:
    print(f"\nFETCH VALIDATION FAIL -> {', '.join(fails)}   nothing merged")
    json.dump({"validated": False, "failures": fails,
               "checks": [{"check": n, "pass": ok, "detail": d} for n, ok, d in v]},
              open(os.path.join(QUAR, "bounded_fetch.json"), "w"), indent=2)
    sys.exit(1)

# ---------------------------------------------------------------- merge (quarantine only)
merged = pd.concat([base, new[base.columns.tolist()]], ignore_index=True)
mdt = pd.to_datetime(merged["datetime"], utc=True)
assert int((mdt >= LOCK).sum()) == 0, "merged frame contains locked rows"
assert merged["timestamp"].is_monotonic_increasing and not merged["timestamp"].duplicated().any()
merged.to_csv(DST_CACHE, index=False)

after = {"rows": len(merged), "start": str(mdt.min()), "end": str(mdt.max()),
         "sha256": hashlib.sha256(open(DST_CACHE, "rb").read()).hexdigest()}
print(f"\nbefore  rows {before['rows']:,}  {before['start']} -> {before['end']}")
print(f"        sha256 {before['sha256']}")
print(f"after   rows {after['rows']:,}  {after['start']} -> {after['end']}   (+{after['rows']-before['rows']})")
print(f"        sha256 {after['sha256']}")
print(f"quarantine dataset -> {DST_CACHE}")
print(f"project data/ cache modified: NO (sha unchanged: "
      f"{hashlib.sha256(open(SRC_CACHE,'rb').read()).hexdigest() == before['sha256']})")

json.dump({"validated": True, "authorized_interval": [str(LO), str(HI)],
           "lock_boundary_exclusive": str(LOCK), "fetched_rows": len(new),
           "fetched_first": str(ndt.min()), "fetched_last": str(ndt.max()),
           "checks": [{"check": n, "pass": ok, "detail": d} for n, ok, d in v],
           "before": before, "after": after,
           "quarantine_dataset": DST_CACHE, "project_cache_modified": False},
          open(os.path.join(QUAR, "bounded_fetch.json"), "w"), indent=2)
