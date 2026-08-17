"""EXPERIMENT B — Scenario-4 recipe reproduction, quarantined, no locked-period access.

External harness. Imports the recovered `campaign_2y_15m` and calls its own
`optimize_scenario` / `optimize_bollinger` unmodified. Nothing in
`src/optimization/recovered_phase3a/*.py` is edited, and nothing outside this
quarantine directory is written: the process chdir()s into `quarantine/stage/`, so the
recovered code's relative `configs/...` and `results/campaign_2y_15m/...` paths resolve
inside the quarantine.

Safety design
  * Data comes from a direct pandas read of the existing cache CSV. `MarketDataLoader` is
    never constructed, so no fetch path exists in this process.
  * A hard exclusive bound at 2026-07-16 00:00 UTC is applied at load time and asserted.
  * `campaign.DEV_HI` is pinned to the row count of the truncated frame, so the module's
    own `assert eval_hi <= DEV_HI` leakage guard remains armed.
  * `unlock_unseen()` is never called; `campaign._UNLOCKED` is asserted False at exit.
  * The unseen OFF/ON evaluation and scenarios 1-3 are not reachable from this file.

Preflight gates execution. Any FAIL stops before a single trial runs.
"""
import argparse
import contextlib
import io
import json
import os
import sys
import time
import types

ROOT = "/home/rahul/Documents/Claude/Experiment-03_Simple_AlgoTrade"
REC = os.path.join(ROOT, "src", "optimization", "recovered_phase3a")
QUAR = os.path.join(REC, "quarantine")
STAGE = os.path.join(QUAR, "stage")
CACHE = os.path.join(ROOT, "data", "candles_futures_binance_futures_ETHUSDT_15m.csv")
SEED_PRESET = os.path.join(REC, "recovered_presets",
                           "config4_candidate158_balanced.AT-0954-stage1-bollinger.json")

sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, REC)

import pandas as pd

LOCK_TS = pd.Timestamp("2026-07-16", tz="UTC")     # first locked candle — exclusive bound
DEV_START_TS = pd.Timestamp("2024-07-16", tz="UTC")
HIST = {  # verbatim from the historical campaign's own boundary printout
    "n": 161953, "dev_lo": 88992, "tr_hi": 138048, "dev_hi": 159072,
    "train_span": ("2024-07-16 00:00:00+00:00", "2025-12-08 23:45:00+00:00"),
    "valid_span": ("2025-12-09 00:00:00+00:00", "2026-07-15 23:45:00+00:00"),
}
SEED_STRATEGY = {"ema_period": 105, "rsi_period": 18, "rsi_overbought": 80.0,
                 "rsi_oversold": 33.0, "atr_period": 11, "consolidation_candles": 14,
                 "consolidation_atr_mult": 3.3, "swing_lookback": 8,
                 "volume_sma_period": 32, "volume_mult": 1.5, "risk_reward_ratio": 2.7}
SEED_RISK = {"leverage": 5.0, "risk_per_trade_pct": 1.7, "max_position_allocation_pct": 28.0}

ap = argparse.ArgumentParser()
ap.add_argument("--preflight-only", action="store_true")
ap.add_argument("--cache", default=CACHE,
                help="Dataset CSV to load. Defaults to the project cache; point at the "
                     "quarantine copy to use the bounded DEV repair.")
ap.add_argument("--accept-truncated-dev", action="store_true",
                help="Proceed even if DEV is short of the historical tail. NOT authorized "
                     "by default: a short DEV changes VALID metrics, hence every TPE "
                     "proposal from trial 10 on, so a fingerprint mismatch would be "
                     "uninterpretable.")
args = ap.parse_args()
CACHE = args.cache

# --------------------------------------------------------------- load (no fetch)
raw = pd.read_csv(CACHE)
dt_all = pd.to_datetime(raw["datetime"], utc=True)
df = raw[dt_all < LOCK_TS].reset_index(drop=True)
dt = pd.to_datetime(df["datetime"], utc=True)
dev_lo = int((dt >= DEV_START_TS).to_numpy().argmax())
dev_hi = len(df)                                  # pinned: DEV ends at the lock boundary
tr_hi = dev_lo + int((dev_hi - dev_lo) * 0.70)

import campaign_2y_15m as campaign

# The recovered module imports MarketDataLoader at module scope. Disarm it: any attempt to
# construct one — the only route to a network fetch — now raises. This makes "no fetch can
# execute" provable by construction rather than merely asserted.
import common.market_data as _md


def _no_fetch(*a, **k):
    raise RuntimeError("QUARANTINE: MarketDataLoader construction blocked — no fetch allowed")


_md.MarketDataLoader.__init__ = _no_fetch
_fetch_blocked = False
try:
    _md.MarketDataLoader(data_dir="data")
except RuntimeError:
    _fetch_blocked = True

checks = []


def chk(name, ok, detail):
    checks.append((name, bool(ok), detail))


chk("no row at/after 2026-07-16 loaded",
    int((dt >= LOCK_TS).sum()) == 0,
    f"rows >= lock boundary: {int((dt >= LOCK_TS).sum())}   max loaded: {dt.max()}")
chk("no fetch path can execute", _fetch_blocked,
    "MarketDataLoader.__init__ patched to raise; verified by trial construction. "
    "campaign.load_data() is never called by this harness.")
chk("no locked frame in memory",
    int((dt_all >= LOCK_TS).sum()) == 0 and len(raw) == len(df),
    f"cache itself holds {int((dt_all >= LOCK_TS).sum())} locked rows; "
    f"raw={len(raw)} loaded={len(df)}")
chk("unlock_unseen not called", campaign._UNLOCKED is False,
    f"campaign._UNLOCKED = {campaign._UNLOCKED}")
seed_cfg = json.load(open(SEED_PRESET))
chk("seed strategy == Trial #53",
    all(abs(float(seed_cfg["strategy"][k]) - v) < 1e-9 for k, v in SEED_STRATEGY.items()),
    "105/18/80/33/11/14/3.3/8/32/1.5/2.7")
chk("seed risk == Risk Trial #158",
    all(abs(float(seed_cfg["risk"][k]) - v) < 1e-9 for k, v in SEED_RISK.items()),
    "5.0x / 1.7% / 28%")
chk("budgets 300 / 150",
    (campaign.STRAT_TRIALS, campaign.BOLL_TRIALS) == (300, 150),
    f"STRAT_TRIALS={campaign.STRAT_TRIALS} BOLL_TRIALS={campaign.BOLL_TRIALS}")
chk("credible filter 100 / 40",
    (campaign.MIN_TRAIN_TRADES, campaign.MIN_VAL_TRADES) == (100, 40),
    f"{campaign.MIN_TRAIN_TRADES} / {campaign.MIN_VAL_TRADES}")
chk("seed 42, TRAIN_FRAC 0.70",
    campaign.SEED == 42 and campaign.TRAIN_FRAC == 0.70,
    f"SEED={campaign.SEED} TRAIN_FRAC={campaign.TRAIN_FRAC}")
chk("DEV start aligns with history",
    str(dt.iloc[dev_lo]) == HIST["train_span"][0],
    f"{dt.iloc[dev_lo]}  (historical {HIST['train_span'][0]})")
dev_exact = (str(dt.iloc[dev_hi - 1]) == HIST["valid_span"][1]
             and dev_hi - dev_lo == HIST["dev_hi"] - HIST["dev_lo"])
chk("DEV is exactly the historical period",
    dev_exact,
    f"DEV rows {dev_hi-dev_lo:,} vs historical {HIST['dev_hi']-HIST['dev_lo']:,}; "
    f"DEV ends {dt.iloc[dev_hi-1]} vs historical {HIST['valid_span'][1]}")

print("=" * 82)
print("EXPERIMENT B — PREFLIGHT")
print(f"  cache            {CACHE}")
print(f"  loaded rows      {len(df):,}   {dt.iloc[0]} -> {dt.iloc[-1]}")
print(f"  lock boundary    {LOCK_TS}  (exclusive)")
print(f"  DEV   rows [{dev_lo}, {dev_hi})   {dt.iloc[dev_lo]} -> {dt.iloc[dev_hi-1]}")
print(f"  TRAIN rows [{dev_lo}, {tr_hi})   {dt.iloc[dev_lo]} -> {dt.iloc[tr_hi-1]}")
print(f"  VALID rows [{tr_hi}, {dev_hi})   {dt.iloc[tr_hi]} -> {dt.iloc[dev_hi-1]}")
print(f"  historical        TRAIN [{HIST['dev_lo']}, {HIST['tr_hi']}) "
      f"VALID [{HIST['tr_hi']}, {HIST['dev_hi']})")
print()
for name, ok, detail in checks:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name:<42} {detail}")
hard_fail = [n for n, ok, _ in checks if not ok]
print()
if hard_fail:
    print(f"  PREFLIGHT: FAIL ({len(hard_fail)}) -> {', '.join(hard_fail)}")
else:
    print("  PREFLIGHT: PASS")
print("=" * 82)

os.makedirs(QUAR, exist_ok=True)
json.dump({"preflight": [{"check": n, "pass": ok, "detail": d} for n, ok, d in checks],
           "preflight_pass": not hard_fail,
           "loaded": {"rows": len(df), "start": str(dt.iloc[0]), "end": str(dt.iloc[-1]),
                      "locked_rows_loaded": int((dt >= LOCK_TS).sum())},
           "boundaries": {"dev_lo": dev_lo, "train_hi": tr_hi, "dev_hi": dev_hi},
           "historical_boundaries": HIST,
           "fetched_any_data": False, "unlock_unseen_called": campaign._UNLOCKED},
          open(os.path.join(QUAR, "preflight.json"), "w"), indent=2)

blocking = [n for n in hard_fail
            if n != "DEV is exactly the historical period" or not args.accept_truncated_dev]
if args.preflight_only or blocking:
    print("STOPPED before running any trial." if blocking else "Preflight-only run.")
    sys.exit(1 if blocking else 0)

# --------------------------------------------------------------- run scenario 4
os.makedirs(os.path.join(STAGE, "configs"), exist_ok=True)
json.dump(seed_cfg, open(os.path.join(STAGE, "configs",
                                      "config4_candidate158_balanced.json"), "w"), indent=2)
os.chdir(STAGE)
campaign.DEV_HI = dev_hi          # arms the module's own leakage assert at our boundary

t0 = time.time()
buf = io.StringIO()
with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
    preset, best = campaign.optimize_scenario(
        "scenario4", "config4_candidate158_balanced", "NEW", df, dev_lo, tr_hi, dev_hi)
    bf, dev_off, dev_on = campaign.optimize_bollinger(
        "scenario4", preset, best, df, dev_lo, dev_hi)
elapsed = time.time() - t0
print(buf.getvalue().strip())

assert campaign._UNLOCKED is False, "unlock_unseen() was called — must never happen here"

sd = pd.read_csv(os.path.join(STAGE, "results", "campaign_2y_15m", "scenario4",
                              "strategy_trials.csv"))
bd = pd.read_csv(os.path.join(STAGE, "results", "campaign_2y_15m", "scenario4",
                              "bollinger_trials.csv"))
cred = sd[(sd["tr_trades"] >= 100) & (sd["va_trades"] >= 40)]
win = cred.iloc[0]
bwin = bd.iloc[0]

TARGET_S = {"ema_period": 104, "rsi_period": 20, "rsi_overbought": 64.0, "rsi_oversold": 23.0,
            "atr_period": 7, "consolidation_candles": 7, "consolidation_atr_mult": 2.8,
            "swing_lookback": 17, "volume_sma_period": 12, "volume_mult": 1.8,
            "risk_reward_ratio": 3.6}
TARGET_R = {"leverage": 4.0, "risk_per_trade_pct": 0.026, "max_position_allocation_pct": 0.70}
TARGET_B = {"length": 10, "std": 2.3, "min_bandwidth_pct": 0.2, "expansion_lookback": 10,
            "expansion_min_ratio": 0.95, "min_mid_distance": 0.15}

mismatch = []
for k, v in {**TARGET_S, **TARGET_R}.items():
    if abs(float(best[k]) - v) > 1e-6:
        mismatch.append(("strategy/risk", k, float(best[k]), v))
for k, v in TARGET_B.items():
    if abs(float(bf.to_dict()[k]) - v) > 1e-6:
        mismatch.append(("bollinger", k, float(bf.to_dict()[k]), v))

print(f"\nwinner trial            {int(win['trial'])}   score {float(win['score']):.4f} "
      f"(expected 1.3203)")
print(f"TRAIN/VALID trades      {int(win['tr_trades'])}/{int(win['va_trades'])} "
      f"(expected 152/59)")
print(f"credible                {len(cred)}/{len(sd)} (expected 257/300)")
print(f"bollinger winner trial  {int(bwin['trial'])}   score {float(bwin['score']):.4f} "
      f"(expected 0.5727)")
print(f"strategy/risk           {best}")
print(f"bollinger               {bf.to_dict()}")
print(f"regenerated exactly     {'YES' if not mismatch else 'NO'}")
for m in mismatch:
    print(f"   MISMATCH {m[0]}.{m[1]}: got {m[2]} expected {m[3]}")

json.dump({"elapsed_seconds": round(elapsed, 1),
           "winner_trial": int(win["trial"]), "winner_score": float(win["score"]),
           "train_trades": int(win["tr_trades"]), "valid_trades": int(win["va_trades"]),
           "credible": [len(cred), len(sd)],
           "bollinger_trial": int(bwin["trial"]), "bollinger_score": float(bwin["score"]),
           "strategy_risk": {k: float(v) for k, v in best.items()},
           "bollinger": bf.to_dict(),
           "regenerated_exactly": not mismatch, "mismatches": mismatch,
           "unlock_unseen_called": campaign._UNLOCKED,
           "dev_boundary_row": dev_hi, "dev_end": str(dt.iloc[dev_hi - 1])},
          open(os.path.join(QUAR, "scenario4_result.json"), "w"), indent=2, default=str)
print(f"\nartifacts -> {QUAR}/  ({elapsed:.0f}s)")
