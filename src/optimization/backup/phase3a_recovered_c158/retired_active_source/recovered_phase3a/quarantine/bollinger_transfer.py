"""Phase 5.5 — fixed Bollinger transfer test. 20 fixed-parameter backtests, no Optuna.

Every frozen candidate is run OFF and ON, on TRAIN and VALID, through the recovered
`campaign_2y_15m.run()` so results are directly comparable to the Scenario-4 ledger.
The ON block is the exact historically optimised C158 filter, identical for all five —
this is a filter-transfer test, not Bollinger tuning.

Guards: quarantine dataset only, hard exclusive bound at 2026-07-16 00:00 UTC,
MarketDataLoader disarmed (no fetch possible), DEV_HI pinned so the recovered module's own
leakage assert stays armed, `unlock_unseen()` never called.
"""
import contextlib
import csv
import io
import json
import os
import sys

ROOT = "/home/rahul/Documents/Claude/Experiment-03_Simple_AlgoTrade"
REC = os.path.join(ROOT, "src", "optimization", "recovered_phase3a")
QUAR = os.path.join(REC, "quarantine")
FROZEN = os.path.join(QUAR, "frozen_challengers")
CACHE = os.path.join(QUAR, "data", "candles_futures_binance_futures_ETHUSDT_15m.csv")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, REC)

import pandas as pd

LOCK = pd.Timestamp("2026-07-16", tz="UTC")
DEV_START = pd.Timestamp("2024-07-16", tz="UTC")
SPLIT = pd.Timestamp("2025-12-09", tz="UTC")          # historical 70/30 boundary

raw = pd.read_csv(CACHE)
df = raw[pd.to_datetime(raw["datetime"], utc=True) < LOCK].reset_index(drop=True)
dt = pd.to_datetime(df["datetime"], utc=True)
assert int((dt >= LOCK).sum()) == 0, "GUARD: locked row present"

import campaign_2y_15m as campaign
import common.market_data as _md
_md.MarketDataLoader.__init__ = lambda *a, **k: (_ for _ in ()).throw(
    RuntimeError("QUARANTINE: fetch blocked"))

dev_lo = int((dt >= DEV_START).to_numpy().argmax())
tr_hi = int((dt >= SPLIT).to_numpy().argmax())
dev_hi = len(df)
campaign.DEV_HI = dev_hi

assert (dev_hi - dev_lo) == 70080, f"DEV rows {dev_hi-dev_lo} != 70080"
assert tr_hi - dev_lo == 49056 and dev_hi - tr_hi == 21024

BOLL_ON = campaign.BollingerFilterConfig(
    enabled=True, length=10, std=2.3, min_bandwidth_pct=0.2, expansion_lookback=10,
    expansion_min_ratio=0.9500000000000001, min_mid_distance=0.15)

CANDS = [("285", "trial285_candidate158_benchmark.json"),
         ("189", "trial189_primary_challenger.json"),
         ("156", "trial156_low_dd_alternate.json"),
         ("125", "trial125_risk_boundary_hypothesis.json"),
         ("52",  "trial52_defensive_high_sample.json")]
PARTS = [("TRAIN", dev_lo, tr_hi), ("VALID", tr_hi, dev_hi)]

print(f"DEV rows {dev_hi-dev_lo:,}  {dt.iloc[dev_lo]} -> {dt.iloc[dev_hi-1]}")
print(f"TRAIN [{dev_lo},{tr_hi}) {dt.iloc[dev_lo]} -> {dt.iloc[tr_hi-1]}")
print(f"VALID [{tr_hi},{dev_hi}) {dt.iloc[tr_hi]} -> {dt.iloc[dev_hi-1]}")
print(f"locked rows loaded: {int((dt >= LOCK).sum())}\n")

rows = []
for tag, fname in CANDS:
    cfgj = json.load(open(os.path.join(FROZEN, fname)))
    p = {k: cfgj["strategy"][k] for k in
         ("ema_period", "rsi_period", "rsi_overbought", "rsi_oversold", "atr_period",
          "consolidation_candles", "consolidation_atr_mult", "swing_lookback",
          "volume_sma_period", "volume_mult", "risk_reward_ratio")}
    p["leverage"] = cfgj["risk"]["leverage"]
    p["risk_per_trade_pct"] = cfgj["risk"]["risk_per_trade_pct"] / 100.0
    p["max_position_allocation_pct"] = cfgj["risk"]["max_position_allocation_pct"] / 100.0
    cfg = campaign.build_cfg(cfgj, p)
    for part, lo, hi in PARTS:
        assert dt.iloc[hi - 1] < LOCK
        for state, f in (("OFF", campaign.OFF), ("ON", BOLL_ON)):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                m = campaign.run(df, cfg, f, lo, hi)
            rows.append({"trial": tag, "partition": part, "bollinger": state,
                         "return_pct": round(m["return_pct"], 4),
                         "pf": round(m["pf"], 4), "max_dd_pct": round(m["max_dd"], 4),
                         "trades": m["trades"], "wins": m["wins"], "losses": m["losses"],
                         "gross_profit": round(m["gross_profit"], 2),
                         "gross_loss": round(m["gross_loss"], 2),
                         "net_pnl": round(m["gross_profit"] - m["gross_loss"], 2),
                         "fees": round(m["fees"], 2), "signals_blocked": m["blocked"]})

assert campaign._UNLOCKED is False, "unlock_unseen() was called"

out = os.path.join(QUAR, "bollinger_transfer_results.csv")
with open(out, "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)

idx = {(r["trial"], r["partition"], r["bollinger"]): r for r in rows}
print(f"{'trial':<6}{'part':<7}{'st':<5}{'ret%':>9}{'PF':>7}{'DD%':>7}{'n':>5}"
      f"{'grossP':>12}{'grossL':>12}{'netP&L':>12}{'fees':>10}{'blocked':>9}")
for tag, _ in CANDS:
    for part, _, _ in PARTS:
        for st in ("OFF", "ON"):
            r = idx[(tag, part, st)]
            print(f"{tag:<6}{part:<7}{st:<5}{r['return_pct']:>9.2f}{r['pf']:>7.3f}"
                  f"{r['max_dd_pct']:>7.2f}{r['trades']:>5}{r['gross_profit']:>12,.2f}"
                  f"{-r['gross_loss']:>12,.2f}{r['net_pnl']:>12,.2f}{r['fees']:>10,.2f}"
                  f"{r['signals_blocked']:>9}")

print("\nON minus OFF")
print(f"{'trial':<6}{'part':<7}{'dRet':>9}{'dPF':>8}{'dDD':>8}{'dTrades':>9}{'dNetP&L':>12}")
for tag, _ in CANDS:
    for part, _, _ in PARTS:
        o, n = idx[(tag, part, "OFF")], idx[(tag, part, "ON")]
        print(f"{tag:<6}{part:<7}{n['return_pct']-o['return_pct']:>+9.2f}"
              f"{n['pf']-o['pf']:>+8.3f}{n['max_dd_pct']-o['max_dd_pct']:>+8.2f}"
              f"{n['trades']-o['trades']:>+9d}{n['net_pnl']-o['net_pnl']:>+12,.2f}")

# OFF must reproduce the Scenario-4 ledger exactly
led = pd.read_csv(os.path.join(QUAR, "stage/results/campaign_2y_15m/scenario4/strategy_trials.csv"))
print("\nOFF vs Scenario-4 ledger (must match)")
bad = 0
for tag, _ in CANDS:
    L = led[led.trial == int(tag)].iloc[0]
    for part, pre in (("TRAIN", "tr"), ("VALID", "va")):
        r = idx[(tag, part, "OFF")]
        d1 = abs(r["return_pct"] - float(L[f"{pre}_return_pct"]))
        d2 = r["trades"] - int(L[f"{pre}_trades"])
        ok = d1 < 1e-3 and d2 == 0
        bad += (not ok)
        print(f"  {tag:<5}{part:<7}ret d={d1:.6f} trades d={d2:+d}  {'MATCH' if ok else 'MISMATCH'}")
print(f"\nledger reconciliation: {'ALL MATCH' if bad == 0 else f'{bad} MISMATCHES'}")
json.dump({"scope": "fixed C158 Bollinger transfer test on historical DEV; no tuning, no selection",
           "dev": [str(dt.iloc[dev_lo]), str(dt.iloc[dev_hi - 1])],
           "train_rows": tr_hi - dev_lo, "valid_rows": dev_hi - tr_hi,
           "locked_rows_loaded": 0, "unlock_unseen_called": campaign._UNLOCKED,
           "bollinger_on_block": BOLL_ON.to_dict(), "ledger_reconciliation_mismatches": bad,
           "rows": rows},
          open(os.path.join(QUAR, "bollinger_transfer_results.json"), "w"), indent=2, default=str)
