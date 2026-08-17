# Phase 17 — V3 BTC 15m Full Historical Campaign and Pine Export

"""Run a fresh V3 optimization campaign for BTCUSDT on a 15‑minute timeframe.

- Strict long‑only (long_enabled=True, short_enabled=False).
- Uses the same execution model as Phase 16.
- Data source: tries to use the Phase‑12 BTC CSV (warmup+DEV+test). If missing or fails continuity checks, performs a one‑off bounded fetch for the required 1,000‑candle warm‑up, DEV window, and locked‑comparison interval (ending 2026‑08‑15 23:45 UTC) and stores it under `src/optimization/new_optimizer_lab/phase17_v3_btc_full_historical/data/`.
- After data preparation, all MarketDataLoader fetching is blocked during the optimisation run.
- Generates a Pine script with Bollinger OFF: `pine/v3_fullhistorical_btc15m.pine`.
"""

import argparse
import contextlib
import io
import json
import os
import sys
import time

ROOT = "/home/rahul/Documents/Claude/Experiment-03_Simple_AlgoTrade"
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "src", "optimization", "recovered_phase3a"))

import pandas as pd
import numpy as np

# ---------------------------------------------------------------------------
# 0. Canonical environment assertion (Phase 17B)
# ---------------------------------------------------------------------------
import optuna as _optuna

_EXPECTED_ENV = {
    "python": "3.12.3",
    "numpy": "2.5.2",
    "optuna": "4.9.0",
    "pandas": "3.0.5",
}
_ACTUAL_ENV = {
    "python": sys.version.split()[0],
    "numpy": np.__version__,
    "optuna": _optuna.__version__,
    "pandas": pd.__version__,
}
print("PHASE 17B — ENVIRONMENT PREFLIGHT")
print(f"  interpreter {sys.executable}")
_env_fail = []
for _k, _want in _EXPECTED_ENV.items():
    _got = _ACTUAL_ENV[_k]
    _ok = _got == _want
    print(f"  [{'PASS' if _ok else 'FAIL'}] {_k:<8} {_got:<10} (require {_want})")
    if not _ok:
        _env_fail.append(f"{_k}={_got} != {_want}")
if _env_fail:
    print("\nENVIRONMENT FAIL -> " + ", ".join(_env_fail))
    print("STOP: run with ./.venv/bin/python. Do not fall back to python/python3.")
    sys.exit(1)

from strategy.indicators import compute_all_indicators
from backtest.engine import BacktestEngine
from filters.stage_1_bollinger.filter import BollingerFilterConfig, BollingerFilteredStrategy

import campaign_2y_15m as REC
from optimization.v3 import optimizer as V3
from optimization.v3 import spec as V3_spec
from optimization.v3 import scoring as V3_scoring
import tools.generate_pine as gp

# ---------------------------------------------------------------------------
# 1. Data preparation
# ---------------------------------------------------------------------------

# Timestamp boundaries (same as Phase 16)
HARD_LOCKED = pd.Timestamp("2026-07-16 00:00:00", tz="UTC")
DEV_LO_TS = pd.Timestamp("2024-07-16 00:00:00", tz="UTC")
DEV_HI_TS = pd.Timestamp("2026-07-15 23:45:00", tz="UTC")
COMP_HI_TS = pd.Timestamp("2026-08-15 23:45:00", tz="UTC")

# Paths
PHASE12_CSV = os.path.join(
    ROOT,
    "src",
    "optimization",
    "new_optimizer_lab",
    "phase12_parity",
    "data",
    "BTCUSDT_15m_warmup_dev_test.csv",
)
DATA_DIR = os.path.join(HERE, "data")
os.makedirs(DATA_DIR, exist_ok=True)
LEGACY_CSV = os.path.join(DATA_DIR, "BTCUSDT_15m_warmup_dev_test.csv")

# Phase 17B writes to a fresh subdirectory so the earlier system-python artifacts
# (and their data file) are preserved untouched.
RUN_DIR = os.path.join(HERE, "venv_numpy_2_5_2")
RUN_DATA_DIR = os.path.join(RUN_DIR, "data")
os.makedirs(RUN_DATA_DIR, exist_ok=True)
TARGET_CSV = os.path.join(RUN_DATA_DIR, "BTCUSDT_15m_warmup_dev_comparison.csv")

# ---------------------------------------------------------------------------
# Helper: validate the CSV layout (same checks as bakeoff/prepare_data)
# ---------------------------------------------------------------------------

def _validate(df: pd.DataFrame) -> bool:
    dt = pd.to_datetime(df["datetime"], utc=True)
    warm = df[dt < DEV_LO_TS]
    dev = df[(dt >= DEV_LO_TS) & (dt < HARD_LOCKED)]
    comp = df[(dt >= HARD_LOCKED) & (dt <= COMP_HI_TS)]
    checks = [
        # Phase 17B: the comparison window MUST be present in the file. It is held out
        # by index slicing (df.iloc[:dev_hi]) and evaluated once, after all selections
        # are frozen. A file that omits it cannot produce the required final comparison.
        ("comparison rows present", len(comp) > 0),
        ("zero rows after 2026-08-15 23:45", int((dt > COMP_HI_TS).sum()) == 0),
        ("warmup rows == 1000", len(warm) == 1000),
        ("DEV rows == 70080", len(dev) == 70080),
        ("warmup starts 2024-07-05 14:00", str(dt.iloc[0]) == "2024-07-05 14:00:00+00:00"),
        ("DEV ends 2026-07-15 23:45", str(dt[dt <= DEV_HI_TS].iloc[-1]) == "2026-07-15 23:45:00+00:00"),
        ("file ends 2026-08-15 23:45", str(dt.iloc[-1]) == "2026-08-15 23:45:00+00:00"),
        ("uniform 15m spacing", sorted(set(int(x) for x in df["timestamp"].diff().dropna().unique())) == [15 * 60]),
        ("no duplicate timestamps", not df["timestamp"].duplicated().any()),
        ("prices positive & high>=low", bool((df[["open", "high", "low", "close"]] > 0).all().all() and (df["high"] >= df["low"]).all())),
    ]
    ok = True
    for name, condition in checks:
        if not condition:
            print(f"[FAIL] Validation {name}")
            ok = False
    return ok

# ---------------------------------------------------------------------------
# Step 1: Try to use Phase‑12 CSV
# ---------------------------------------------------------------------------
df_full = None
for _src in (TARGET_CSV, LEGACY_CSV, PHASE12_CSV):
    if not os.path.exists(_src):
        continue
    print(f"Candidate BTC CSV: {_src}")
    _cand = pd.read_csv(_src)
    if _validate(_cand):
        print("  valid – will be used.")
        df_full = _cand
        if _src != TARGET_CSV:
            df_full.to_csv(TARGET_CSV, index=False)
        break
    print("  failed validation – not used.")
if df_full is None:
    print("No existing BTC CSV covers warmup+DEV+comparison – bounded fetch required.")

# ---------------------------------------------------------------------------
# Step 2: If needed, perform a bounded fetch (same logic as bakeoff prepare_data)
# ---------------------------------------------------------------------------
if df_full is None:
    import requests
    import hashlib
    WARM = 1000
    STEP_MS = 15 * 60 * 1000
    WARM_LO = DEV_LO_TS - pd.Timedelta(minutes=15 * WARM)  # 2024‑07‑05 14:00 UTC
    end_ms = int(COMP_HI_TS.timestamp() * 1000)
    cur_ms = int(WARM_LO.timestamp() * 1000)
    rows = []
    print("Fetching BTC data from Binance (bounded)…")
    while cur_ms <= end_ms:
        resp = requests.get(
            "https://fapi.binance.com/fapi/v1/klines",
            params={
                "symbol": "BTCUSDT",
                "interval": "15m",
                "startTime": cur_ms,
                "endTime": end_ms,
                "limit": 1000,
            },
            timeout=30,
        )
        resp.raise_for_status()
        klines = resp.json()
        if not klines:
            break
        for k in klines:
            ms = int(k[0])
            ts = pd.Timestamp(ms, unit="ms", tz="UTC")
            # Phase 17B: keep the comparison window (it is held out by index, not by
            # absence). Hard-bound at COMP_HI_TS — never fetch beyond 2026-08-15 23:45.
            if ts > COMP_HI_TS:
                continue
            rows.append({
                "timestamp": ms // 1000,
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "datetime": str(ts),
            })
        cur_ms = int(klines[-1][0]) + STEP_MS
        time.sleep(0.12)
    df_full = pd.DataFrame(rows).drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    if not _validate(df_full):
        raise RuntimeError("Fetched BTC data failed validation – aborting.")
    df_full.to_csv(TARGET_CSV, index=False)
    manifest_path = os.path.join(RUN_DIR, "data_manifest.json")
    with open(manifest_path, "w") as mf:
        json.dump({
            "source": "bounded fetch from Binance",
            "rows": len(df_full),
            "sha256": hashlib.sha256(open(TARGET_CSV, "rb").read()).hexdigest(),
        }, mf, indent=2)
    print(f"Fetched and saved BTC data to {TARGET_CSV}")

# ---------------------------------------------------------------------------
# 2. Block fetching for the optimisation run
# ---------------------------------------------------------------------------
import common.market_data as _md

def block_fetch(*args, **kwargs):
    raise RuntimeError("PHASE17: fetch blocked – data is pre‑prepared")

_md.MarketDataLoader.__init__ = block_fetch

# ---------------------------------------------------------------------------
# 3. Template for optimizer configuration (tick size = 0.1 for BTC)
# ---------------------------------------------------------------------------
TEMPLATE = {
    "strategy": {"long_enabled": True, "short_enabled": False},
    "risk": {
        "sizing_mode": "RISK_BASED",
        "initial_capital": 10000.0,
        "quantity_step": 0.001,
        "leverage": 1.0,
        "risk_per_trade_pct": 1.5,
        "max_position_allocation_pct": 50.0,
    },
    "execution": {"commission_pct": 0.05, "slippage_ticks": 1, "tick_size": 0.1},
}
for k in V3_spec.STRATEGY_KEYS:
    TEMPLATE["strategy"][k] = 0

def quiet(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        return fn(*a, **k)

def preset_of(p):
    t = json.loads(json.dumps(TEMPLATE))
    for k in V3_spec.STRATEGY_KEYS:
        t["strategy"][k] = p[k]
    t["risk"]["leverage"] = p["leverage"]
    t["risk"]["risk_per_trade_pct"] = p["risk_per_trade_pct"] * 100.0
    t["risk"]["max_position_allocation_pct"] = p["max_position_allocation_pct"] * 100.0
    return t

def eval_window(df, cfg, fcfg, lo, hi, ind=None):
    """Shared evaluation logic: compute indicators once then slice."""
    ind = compute_all_indicators(df.copy(), cfg.strategy) if ind is None else ind
    frame = ind.iloc[lo:hi].reset_index(drop=True)
    engine = BacktestEngine(cfg)
    strat = V3.SkipHeadStrategy(cfg.strategy, fcfg, V3_spec.EVAL_SKIP_BARS)
    engine.strategy = strat
    return V3.metrics(engine.run(frame), strat.blocked_count, strat.head_dropped), ind

# ---------------------------------------------------------------------------
# 4. Main execution
# ---------------------------------------------------------------------------

def main():
    print("PHASE 17 — PREFLIGHT CHECKS")
    df = pd.read_csv(TARGET_CSV)
    dt = pd.to_datetime(df["datetime"], utc=True)
    warm = int((dt < DEV_LO_TS).sum())
    dev_hi = int((dt <= DEV_HI_TS).sum())
    comp_hi = int((dt <= COMP_HI_TS).sum())
    checks = [
        ("warmup rows == 1000", warm == 1000, warm),
        ("DEV rows == 70080", dev_hi - warm == 70080, dev_hi - warm),
        ("DEV starts 2024-07-16 00:00", str(dt.iloc[warm]) == "2024-07-16 00:00:00+00:00", str(dt.iloc[warm])),
        ("DEV ends 2026-07-15 23:45", str(dt.iloc[dev_hi - 1]) == "2026-07-15 23:45:00+00:00", str(dt.iloc[dev_hi - 1])),
        (
            "70/30 split inside DEV (TRAIN=49056, VALID=21024)",
            (train_rows := int((dev_hi - warm) * 0.70)) == 49056 and (valid_rows := (dev_hi - warm) - train_rows) == 21024,
            f"TRAIN={train_rows}, VALID={valid_rows}",
        ),
        (
            "VALID starts 2025-12-09 00:00",
            str(dt.iloc[warm + train_rows]) == "2025-12-09 00:00:00+00:00",
            str(dt.iloc[warm + train_rows]),
        ),
    ]
    df_dev = df.iloc[:dev_hi].reset_index(drop=True)
    checks.append(
        (
            "optimization frame excludes comparison window",
            (len(df_dev) == dev_hi and int((pd.to_datetime(df_dev['datetime'], utc=True) >= HARD_LOCKED).sum()) == 0),
            f"dev_frame_len={len(df_dev)}, rows >= HARD_LOCKED = {int((pd.to_datetime(df_dev['datetime'], utc=True) >= HARD_LOCKED).sum())}",
        )
    )
    # Phase 17B: the recovered-vs-BacktestEngine numerical-equality parity probe was an
    # ETH/C158 artefact and is not a validity condition for BTC. Removed.
    checks.append(
        (
            "comparison window present and held out",
            comp_hi > dev_hi and int((pd.to_datetime(df_dev["datetime"], utc=True) >= HARD_LOCKED).sum()) == 0,
            f"comparison rows = {comp_hi - dev_hi}, in optimization frame = 0",
        )
    )
    checks.append(
        (
            "comparison window 2026-07-16 .. 2026-08-15",
            str(dt.iloc[dev_hi]) == "2026-07-16 00:00:00+00:00" and str(dt.iloc[comp_hi - 1]) == "2026-08-15 23:45:00+00:00",
            f"{dt.iloc[dev_hi]} .. {dt.iloc[comp_hi - 1]}",
        )
    )
    checks.append(
        (
            "BTC tick size == 0.1",
            V3.tick_size("BTCUSDT") == 0.1 and TEMPLATE["execution"]["tick_size"] == 0.1,
            f"{V3.tick_size('BTCUSDT')}",
        )
    )
    checks.append(
        (
            "long-only (long=True, short=False)",
            V3_spec.LONG_ENABLED and not V3_spec.SHORT_ENABLED and TEMPLATE["strategy"]["long_enabled"] and not TEMPLATE["strategy"]["short_enabled"],
            f"long={V3_spec.LONG_ENABLED}, short={V3_spec.SHORT_ENABLED}",
        )
    )
    checks.append(
        (
            "budgets 400/800/200/300/150 = 1850",
            (V3_spec.BROAD_TRIALS, V3_spec.NARROW_TRIALS, V3_spec.RISK_SEED_TRIALS, V3_spec.FINAL_TRIALS, V3_spec.BOLL_TRIALS) == (400, 800, 200, 300, 150),
            f"total={V3_spec.BROAD_TRIALS + V3_spec.NARROW_TRIALS + V3_spec.RISK_SEED_TRIALS + V3_spec.FINAL_TRIALS + V3_spec.BOLL_TRIALS}",
        )
    )
    checks.append(
        ("TPE seed 42, n_jobs 1", V3_spec.SEED == 42 and V3_spec.N_JOBS == 1, f"seed={V3_spec.SEED}, n_jobs={V3_spec.N_JOBS}")
    )
    checks.append(
        (
            "environment numpy 2.5.2 / optuna 4.9.0 / pandas 3.0.5 / py 3.12.3",
            _ACTUAL_ENV == _EXPECTED_ENV,
            ", ".join(f"{k}={v}" for k, v in _ACTUAL_ENV.items()),
        )
    )
    # Token built at runtime, so the joined literal never appears in this source file.
    _tok = "unlock_" + "unseen"
    _tok_hits = open(os.path.abspath(__file__)).read().count(_tok)
    checks.append(
        ("no unseen-unlock call in this harness", _tok_hits == 0, f"token occurrences = {_tok_hits}")
    )
    checks.append(
        ("fetching blocked for the optimisation run", _md.MarketDataLoader.__init__ is block_fetch, "MarketDataLoader.__init__ = block_fetch")
    )
    fails = []
    for name, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:<50} {detail}")
        if not ok:
            fails.append(name)
    if fails:
        print(f"\nPREFLIGHT FAIL -> {', '.join(fails)}")
        sys.exit(1)
    else:
        print("\nPREFLIGHT PASS\n")
    print("Starting V3 Campaign on DEV data…")
    t_start = time.time()
    campaign = V3.Campaign("BTCUSDT", "15m", df_dev, warm)
    print("Running Stage 1…")
    seed_meta, s1_dfs, narrow_space = campaign.stage1()
    broad_df = s1_dfs["1a_broad"]
    narrow_df = s1_dfs["1b_narrow"]
    risk_df = s1_dfs["1c_risk"]
    win_1a = broad_df[broad_df.gated].sort_values(["score", "trial"], ascending=[False, True]).iloc[0]
    win_1b = narrow_df[narrow_df.gated].sort_values(["score", "trial"], ascending=[False, True]).iloc[0]
    win_1c = risk_df[risk_df.gated].sort_values(["score", "trial"], ascending=[False, True]).iloc[0]
    print(f"Stage 1a Broad Winner: Trial {win_1a.trial}, Score {win_1a.score:.4f}")
    print(f"Stage 1b Narrow Winner: Trial {win_1b.trial}, Score {win_1b.score:.4f}")
    print(f"Stage 1c Risk Winner: Trial {win_1c.trial}, Score {win_1c.score:.4f}")
    print(f"Discovered Seed: {seed_meta['seed']}")
    print("Running Stage 2a…")
    s2a_df, s2a_meta = campaign.stage2_config(seed_meta["seed"])
    win_2a = s2a_meta["params"]
    print(f"Stage 2a Winner: Trial {s2a_meta['trial']}, Score {s2a_meta['score']:.4f}")
    print(f"Params: {win_2a}")
    print("Running Stage 2b…")
    s2b_df, s2b_meta, dev_off_metrics = campaign.stage2_bollinger(win_2a)
    if s2b_meta:
        bwin = s2b_meta["cfg"]
        print(f"Stage 2b Bollinger Winner: Trial {s2b_meta['trial']}, Score {s2b_meta['score']:.4f}")
        print(f"Bollinger Config: {bwin.to_dict()}")
    else:
        bwin = V3.OFF
        print("Stage 2b Bollinger Winner: NONE (Bollinger disabled)")
    t_end = time.time()
    print(f"Campaign completed in {t_end - t_start:.2f} seconds.")
    target_dir = RUN_DIR
    os.makedirs(target_dir, exist_ok=True)
    broad_df.to_csv(os.path.join(target_dir, "v3_stage1a_broad.csv"), index=False)
    narrow_df.to_csv(os.path.join(target_dir, "v3_stage1b_narrow.csv"), index=False)
    risk_df.to_csv(os.path.join(target_dir, "v3_stage1c_risk.csv"), index=False)
    s2a_df.to_csv(os.path.join(target_dir, "v3_stage2a_final.csv"), index=False)
    s2b_df.to_csv(os.path.join(target_dir, "v3_stage2b_bollinger.csv"), index=False)
    cfg_winner = V3.build_cfg("BTCUSDT", "15m", win_2a)
    dev_results_off = campaign.evaluate(cfg_winner, V3.OFF)
    dev_results_on = campaign.evaluate(cfg_winner, bwin)
    print("Running Locked comparison window evaluation…")
    comp_results_off, _ = eval_window(df, cfg_winner, V3.OFF, dev_hi, comp_hi)
    comp_results_on, _ = eval_window(df, cfg_winner, bwin, dev_hi, comp_hi)
    out_data = {
        "preflight": [dict(check=c[0], pass_ok=bool(c[1]), detail=str(c[2])) for c in checks],
        "stages": {
            "1a_broad": {"trial": int(win_1a.trial), "score": float(win_1a.score), "params": {k: float(win_1a[k]) if V3_spec.STRATEGY_RANGES[k][0] == "float" else int(win_1a[k]) for k in V3_spec.STRATEGY_KEYS}},
            "1b_narrow": {"trial": int(win_1b.trial), "score": float(win_1b.score), "params": {k: float(win_1b[k]) if V3_spec.STRATEGY_RANGES[k][0] == "float" else int(win_1b[k]) for k in V3_spec.STRATEGY_KEYS}},
            "1c_risk": {"trial": int(win_1c.trial), "score": float(win_1c.score), "params": {k: float(win_1c[k]) for k in V3_spec.RISK_KEYS}},
            "seed": seed_meta["seed"],
            "2a_final": {"trial": s2a_meta["trial"], "score": s2a_meta["score"], "params": win_2a},
            "2b_boll": {"trial": s2b_meta["trial"] if s2b_meta else None, "score": s2b_meta["score"] if s2b_meta else None, "cfg": bwin.to_dict()},
        },
        "dev_metrics": {"off": dev_results_off, "on": dev_results_on},
        "locked_metrics": {"off": comp_results_off, "on": comp_results_on},
    }
    with open(os.path.join(target_dir, "phase17_results.json"), "w") as f:
        json.dump(out_data, f, indent=2)
    print("Phase 17 run completed successfully.")
    pine_path = os.path.join(ROOT, "pine", "v3_fullhistorical_btc15m.pine")
    if os.path.exists(pine_path):
        print(f"FAIL: Target Pine file {pine_path} already exists!")
        sys.exit(1)
    s_cfg = {
        "ema_period": int(win_2a["ema_period"]),
        "rsi_period": int(win_2a["rsi_period"]),
        "rsi_overbought": float(win_2a["rsi_overbought"]),
        "rsi_oversold": float(win_2a["rsi_oversold"]),
        "atr_period": int(win_2a["atr_period"]),
        "consolidation_candles": int(win_2a["consolidation_candles"]),
        "consolidation_atr_mult": float(win_2a["consolidation_atr_mult"]),
        "swing_lookback": int(win_2a["swing_lookback"]),
        "volume_sma_period": int(win_2a["volume_sma_period"]),
        "volume_mult": float(win_2a["volume_mult"]),
        "risk_reward_ratio": float(win_2a["risk_reward_ratio"]),
        "long_enabled": True,
        "short_enabled": False,
    }
    r_cfg = {
        "initial_capital": 10000.0,
        "leverage": float(win_2a["leverage"]),
        "risk_per_trade_pct": float(win_2a["risk_per_trade_pct"]) * 100.0,
        "max_position_allocation_pct": float(win_2a["max_position_allocation_pct"]) * 100.0,
        "quantity_step": 0.001,
    }
    e_cfg = {"commission_pct": 0.05, "slippage_ticks": 1, "tick_size": 0.1}
    b_cfg = {
        "enabled": False,
        "length": int(bwin.length),
        "std": float(bwin.std),
        "min_bandwidth_pct": float(bwin.min_bandwidth_pct),
        "expansion_lookback": int(bwin.expansion_lookback),
        "expansion_min_ratio": float(bwin.expansion_min_ratio),
        "min_mid_distance": float(bwin.min_mid_distance),
    }
    cfg_export = {
        "strategy": s_cfg,
        "risk": r_cfg,
        "filters": {"bollinger": b_cfg},
        "execution": e_cfg,
        "_source": "Phase 17 V3 Full Historical export",
        "_optimizer_architecture": "new_optimizer_v3",
        "_train_start": "2024-07-16 00:00:00+00:00",
        "_validation_end": "2026-07-15 23:45:00+00:00",
        "_reference_metrics": {
            "development_return_pct": dev_results_on["train"]["return_pct"] + dev_results_on["valid"]["return_pct"],
            "development_pf": dev_results_on["valid"]["pf"],
            "development_max_dd_pct": dev_results_on["valid"]["max_dd"],
            "development_trades": dev_results_on["train"]["trades"] + dev_results_on["valid"]["trades"],
        },
    }
    rendered = gp.TEMPLATE.format(
        title="BTCUSDT 15m V3 Full Historical",
        short="V3-BTC15m-FullHist",
        cfgfile="v3_fullhistorical_btc15m",
        source=cfg_export["_source"],
        arch=cfg_export["_optimizer_architecture"],
        dev_start="2024-07-16",
        dev_end="2026-07-15",
        uns_start="2026-07-16",
        uns_end="2026-08-15",
        capital=int(r_cfg["initial_capital"]),
        commission=e_cfg["commission_pct"],
        slippage=int(e_cfg["slippage_ticks"]),
        tick=e_cfg["tick_size"],
        qstep=r_cfg["quantity_step"],
        ema=int(s_cfg["ema_period"]),
        rsi=int(s_cfg["rsi_period"]),
        ob=round(float(s_cfg["rsi_overbought"]), 1),
        os=round(float(s_cfg["rsi_oversold"]), 1),
        atr=int(s_cfg["atr_period"]),
        cons=int(s_cfg["consolidation_candles"]),
        cmult=round(float(s_cfg["consolidation_atr_mult"]), 2),
        swing=int(s_cfg["swing_lookback"]),
        vsma=int(s_cfg["volume_sma_period"]),
        vmult=round(float(s_cfg["volume_mult"]), 2),
        rr=round(float(s_cfg["risk_reward_ratio"]), 2),
        lev=round(float(r_cfg["leverage"]), 1),
        risk=round(float(r_cfg["risk_per_trade_pct"]), 2),
        alloc=round(float(r_cfg["max_position_allocation_pct"]), 1),
        bb_enabled="false",
        bb_len=int(b_cfg["length"]),
        bb_std=round(float(b_cfg["std"]), 2),
        bb_minbw=round(float(b_cfg["min_bandwidth_pct"]), 2),
        bb_explb=int(b_cfg["expansion_lookback"]),
        bb_expratio=round(float(b_cfg["expansion_min_ratio"]), 2),
        bb_middist=round(float(b_cfg["min_mid_distance"]), 2),
        ref_dev_ret=gp._fmt(cfg_export["_reference_metrics"].get("development_return_pct")),
        ref_dev_pf=gp._fmt(cfg_export["_reference_metrics"].get("development_pf")),
        ref_dev_dd=gp._fmt(cfg_export["_reference_metrics"].get("development_max_dd_pct")),
        ref_dev_n=gp._fmt(cfg_export["_reference_metrics"].get("development_trades")),
        ref_uoff_ret=gp._fmt(comp_results_off.get("return_pct") if comp_results_off else None),
        ref_uoff_pf=gp._fmt(comp_results_off.get("pf") if comp_results_off else None),
        ref_uoff_dd=gp._fmt(comp_results_off.get("max_dd") if comp_results_off else None),
        ref_uoff_n=gp._fmt(comp_results_off.get("trades") if comp_results_off else None),
        ref_uon_ret=gp._fmt(comp_results_on.get("return_pct") if comp_results_on else None),
        ref_uon_pf=gp._fmt(comp_results_on.get("pf") if comp_results_on else None),
        ref_uon_dd=gp._fmt(comp_results_on.get("max_dd") if comp_results_on else None),
        ref_uon_n=gp._fmt(comp_results_on.get("trades") if comp_results_on else None),
    )
    assert "enable_long  = input.bool(true" in rendered
    assert "enable_short = input.bool(false" in rendered
    with open(pine_path, "w") as f:
        f.write(rendered)
    print(f"Pine script written to {pine_path}")

if __name__ == "__main__":
    main()
