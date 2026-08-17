"""Phase-12 data prep — isolated cache, hard-bounded below 2026-08-17.

    warmup  2024-07-05 14:00Z .. 2024-07-15 23:45Z    1,000 rows
    DEV     2024-07-16 00:00Z .. 2026-05-31 23:45Z    optimization only
    TEST    2026-06-01 00:00Z .. 2026-08-16 23:45Z    sealed, evaluated once at the end

Base rows come from the Phase-8 ETH dataset (already validated, ends 2026-07-15 23:45).
Only the extension 2026-07-16 00:00 .. 2026-08-16 23:45 is fetched. Every row at or after
2026-08-17 00:00 UTC is rejected, so the still-forming Aug-17 day can never enter.
"""
import hashlib, json, os, time
import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data"); os.makedirs(DATA, exist_ok=True)
BASE = os.path.abspath(os.path.join(HERE, "..", "bakeoff_15m", "data",
                                    "ETHUSDT_15m_warmup1000_dev.csv"))
DST = os.path.join(DATA, "ETHUSDT_15m_warmup_dev_test.csv")

HARD = pd.Timestamp("2026-08-17 00:00:00", tz="UTC")      # exclusive — Aug 17 forming day
LAST = pd.Timestamp("2026-08-16 23:45:00", tz="UTC")      # last allowed candle
WARM_LO = pd.Timestamp("2024-07-05 14:00:00", tz="UTC")
DEV_LO = pd.Timestamp("2024-07-16 00:00:00", tz="UTC")
DEV_HI = pd.Timestamp("2026-05-31 23:45:00", tz="UTC")    # last DEV candle
TEST_LO = pd.Timestamp("2026-06-01 00:00:00", tz="UTC")
STEP = 900
COLS = ["timestamp", "open", "high", "low", "close", "volume", "datetime"]

base = pd.read_csv(BASE)
b_dt = pd.to_datetime(base["datetime"], utc=True)
ext_from = b_dt.max() + pd.Timedelta(seconds=STEP)

rows, cur, end = [], int(ext_from.timestamp() * 1000), int(LAST.timestamp() * 1000)
while cur <= end:
    r = requests.get("https://fapi.binance.com/fapi/v1/klines",
                     params={"symbol": "ETHUSDT", "interval": "15m", "startTime": cur,
                             "endTime": end, "limit": 1000}, timeout=25)
    r.raise_for_status()
    kl = r.json()
    if not kl:
        break
    for k in kl:
        ms = int(k[0]); ts = pd.Timestamp(ms, unit="ms", tz="UTC")
        if ts >= HARD or ts > LAST:                        # hard reject
            continue
        rows.append({"timestamp": ms // 1000, "open": float(k[1]), "high": float(k[2]),
                     "low": float(k[3]), "close": float(k[4]), "volume": float(k[5]),
                     "datetime": str(ts)})
    cur = int(kl[-1][0]) + STEP * 1000
    time.sleep(0.12)
ext = pd.DataFrame(rows)

df = pd.concat([base[COLS], ext[COLS]], ignore_index=True) if len(ext) else base[COLS]
df = df.drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
dt = pd.to_datetime(df["datetime"], utc=True)

warm = int((dt < DEV_LO).sum())
dev = int(((dt >= DEV_LO) & (dt <= DEV_HI)).sum())
test = int((dt >= TEST_LO).sum())
diffs = sorted(set(int(x) for x in df["timestamp"].diff().dropna().unique()))
checks = [
    ("zero rows at/after 2026-08-17", int((dt >= HARD).sum()) == 0, int((dt >= HARD).sum())),
    ("last candle 2026-08-16 23:45", str(dt.iloc[-1]) == "2026-08-16 23:45:00+00:00", str(dt.iloc[-1])),
    ("warmup rows == 1000", warm == 1000, warm),
    ("warmup starts 2024-07-05 14:00", str(dt.iloc[0]) == "2024-07-05 14:00:00+00:00", str(dt.iloc[0])),
    ("DEV starts 2024-07-16 00:00", str(dt.iloc[warm]) == "2024-07-16 00:00:00+00:00", str(dt.iloc[warm])),
    ("DEV ends 2026-05-31 23:45", str(dt.iloc[warm + dev - 1]) == "2026-05-31 23:45:00+00:00",
     str(dt.iloc[warm + dev - 1])),
    ("TEST starts 2026-06-01 00:00", str(dt.iloc[warm + dev]) == "2026-06-01 00:00:00+00:00",
     str(dt.iloc[warm + dev])),
    ("TEST rows == 7392 (77 days)", test == 7392, test),
    ("warmup+DEV+TEST == total", warm + dev + test == len(df), (warm, dev, test, len(df))),
    ("uniform 15m spacing", diffs == [STEP], diffs[:4]),
    ("no duplicate timestamps", not df["timestamp"].duplicated().any(),
     int(df["timestamp"].duplicated().sum())),
    ("prices sane", bool((df[["open", "high", "low", "close"]] > 0).all().all()
                         and (df["high"] >= df["low"]).all()), ""),
]
for n, ok, d in checks:
    print(f"  [{'PASS' if ok else 'FAIL'}] {n:<34} {d}")
allok = all(ok for _, ok, _ in checks)
if allok:
    df.to_csv(DST, index=False)
json.dump({"validated": allok, "fetched_extension_rows": len(ext),
           "hard_bound_exclusive": str(HARD),
           "warmup_rows": warm, "dev_rows": dev, "test_rows": test, "total": len(df),
           "dev": [str(DEV_LO), str(DEV_HI)], "test": [str(TEST_LO), str(dt.iloc[-1])],
           "path": DST, "sha256": hashlib.sha256(open(DST, "rb").read()).hexdigest() if allok else None},
          open(os.path.join(HERE, "data_manifest.json"), "w"), indent=2)
print(f"\nfetched {len(ext)} extension rows | total {len(df):,} | validated {allok}")
