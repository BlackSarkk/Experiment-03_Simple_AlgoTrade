"""Diagnostic for the preflight failure — no Optuna, no optimization, 3 backtests.

Question: does the 95-candle DEV shortfall actually change anything, and is TRAIN already
exact? Evaluates the FINAL Candidate #158 vector (fixed parameters, Bollinger OFF) through
the recovered `campaign_2y_15m.run()`:

  A  TRAIN pinned to the historical timestamps  2024-07-16 00:00 -> 2025-12-08 23:45
  B  VALID pinned to the historical start       2025-12-09 00:00 -> (our tail, 2026-07-15 00:00)
  C  VALID as the fraction-split harness would cut it (shows the compounding second error)

Historical scenario-4 reference: TRAIN ret 193.01% n 152 | VALID ret 17.30% n 59.
No row at or after 2026-07-16 is loaded. MarketDataLoader is disarmed.
"""
import contextlib
import io
import json
import os
import sys

ROOT = "/home/rahul/Documents/Claude/Experiment-03_Simple_AlgoTrade"
REC = os.path.join(ROOT, "src", "optimization", "recovered_phase3a")
QUAR = os.path.join(REC, "quarantine")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, REC)

import pandas as pd

LOCK = pd.Timestamp("2026-07-16", tz="UTC")
raw = pd.read_csv(os.path.join(ROOT, "data",
                               "candles_futures_binance_futures_ETHUSDT_15m.csv"))
df = raw[pd.to_datetime(raw["datetime"], utc=True) < LOCK].reset_index(drop=True)
dt = pd.to_datetime(df["datetime"], utc=True)
assert int((dt >= LOCK).sum()) == 0

import campaign_2y_15m as campaign
import common.market_data as _md
_md.MarketDataLoader.__init__ = lambda *a, **k: (_ for _ in ()).throw(
    RuntimeError("QUARANTINE: fetch blocked"))

FINAL158 = {"ema_period": 104, "rsi_period": 20, "rsi_overbought": 64.0, "rsi_oversold": 23.0,
            "atr_period": 7, "consolidation_candles": 7, "consolidation_atr_mult": 2.8,
            "swing_lookback": 17, "volume_sma_period": 12, "volume_mult": 1.8,
            "risk_reward_ratio": 3.6, "leverage": 4.0, "risk_per_trade_pct": 0.026,
            "max_position_allocation_pct": 0.70}
PRESET = json.load(open(os.path.join(REC, "recovered_presets",
                        "config4_candidate158_balanced.AT-0954-stage1-bollinger.json")))


def idx_of(ts):
    return int((dt >= pd.Timestamp(ts, tz="UTC")).to_numpy().argmax())


dev_lo = idx_of("2024-07-16")
tr_hi_hist = idx_of("2025-12-09")                      # historical split point, by timestamp
dev_hi = len(df)
tr_hi_frac = dev_lo + int((dev_hi - dev_lo) * 0.70)    # what the fraction split yields now

campaign.DEV_HI = dev_hi
cfg = campaign.build_cfg(PRESET, FINAL158)
cases = [
    ("A TRAIN  (historical timestamps)", dev_lo, tr_hi_hist, 193.01, 152),
    ("B VALID  (historical start, our tail)", tr_hi_hist, dev_hi, 17.30, 59),
    ("C VALID  (fraction split, our data)", tr_hi_frac, dev_hi, 17.30, 59),
]
rows = []
print(f"loaded {len(df):,} rows  {dt.iloc[0]} -> {dt.iloc[-1]}")
print(f"historical split point 2025-12-09 00:00 -> row {tr_hi_hist} | "
      f"fraction split now -> row {tr_hi_frac} ({dt.iloc[tr_hi_frac]})\n")
print(f"  {'case':<38} {'rows':>7} {'ret%':>9} {'exp':>8} {'n':>5} {'exp':>5} {'PF':>7} {'DD%':>7}")
for name, lo, hi, exp_ret, exp_n in cases:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        m = campaign.run(df, cfg, campaign.OFF, lo, hi)
    rows.append({"case": name, "lo": lo, "hi": hi, "rows": hi - lo,
                 "start": str(dt.iloc[lo]), "end": str(dt.iloc[hi - 1]),
                 "expected_return_pct": exp_ret, "expected_trades": exp_n, **m})
    print(f"  {name:<38} {hi-lo:>7,} {m['return_pct']:>9.2f} {exp_ret:>8.2f} "
          f"{m['trades']:>5} {exp_n:>5} {m['pf']:>7.3f} {m['max_dd']:>7.2f}")

json.dump({"question": "does the 95-candle DEV shortfall change the scenario-4 inputs?",
           "loaded": {"rows": len(df), "start": str(dt.iloc[0]), "end": str(dt.iloc[-1]),
                      "locked_rows_loaded": 0},
           "historical_reference": {"train": {"return_pct": 193.01, "trades": 152},
                                    "valid": {"return_pct": 17.30, "trades": 59}},
           "split_points": {"historical_by_timestamp": tr_hi_hist,
                            "fraction_split_now": tr_hi_frac,
                            "fraction_split_timestamp": str(dt.iloc[tr_hi_frac])},
           "cases": rows, "unlock_unseen_called": campaign._UNLOCKED},
          open(os.path.join(QUAR, "dev_gap_probe.json"), "w"), indent=2, default=str)
assert campaign._UNLOCKED is False
print(f"\n-> {QUAR}/dev_gap_probe.json")
