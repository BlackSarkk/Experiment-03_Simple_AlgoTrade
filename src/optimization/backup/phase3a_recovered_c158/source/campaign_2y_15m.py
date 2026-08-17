"""
2-Year 15m campaign — 4 scenarios x (strategy+risk optimization -> Bollinger) -> unseen month.

Windows (printed and asserted at startup):
    DEV    2024-07-16 -> 2026-07-15   (TRAIN 70% / VALIDATION 30%, chronological)
    UNSEEN 2026-07-16 -> 2026-08-15   (locked; opened once at the very end)

Leakage guard: `DEV_HI` is the exclusive row bound of the development window. Every
optimization call goes through `run()` which asserts `eval_hi <= DEV_HI` unless explicitly
unlocked. The unseen month is unreachable from any optimization code path.

Optimizer architectures (the ONLY difference between arms):
    LEGACY  scenarios 1,2 — legacy suggest_params structure incl. the inert `side_choice`
                            categorical, which consumes a TPE draw (Candidate #5-era shape)
    NEW     scenarios 3,4 — current suggest structure, no side_choice

Everything else is shared: profit-first objective, ranges, min-sample rules, seed, engine,
RiskManager, fees, slippage, tick size, LONG-only.
"""

import os
import sys
import json
import math
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pandas as pd
import optuna
from optuna.samplers import TPESampler

from common.config import (PipelineConfig, StrategyConfig, RiskConfig,
                           ExecutionConfig, PlatformConfig)
from common.market_data import MarketDataLoader
from strategy.indicators import compute_all_indicators
from backtest.engine import BacktestEngine
from filters.stage_1_bollinger.filter import BollingerFilterConfig, BollingerFilteredStrategy

optuna.logging.set_verbosity(optuna.logging.WARNING)

SYMBOL, PLATFORM, RESOLUTION = "ETHUSDT", "BINANCE_FUTURES", "15m"
DEV_START, DEV_END = "2024-07-16", "2026-07-15"
UNSEEN_START, UNSEEN_END = "2026-07-16", "2026-08-15"
TRAIN_FRAC = 0.70
SEED, INITIAL = 42, 10000.0
STRAT_TRIALS, BOLL_TRIALS = 300, 150
MIN_TRAIN_TRADES, MIN_VAL_TRADES = 100, 40
OUT = os.path.join("results", "campaign_2y_15m")

SCENARIOS = [
    ("scenario1", "config1_candidate5", "LEGACY", "config1_candidate5_2y_optimized"),
    ("scenario2", "config2_legacy_maxprofit", "LEGACY", "config2_legacy_maxprofit_2y_optimized"),
    ("scenario3", "config3_new_maxprofit", "NEW", "config3_new_maxprofit_2y_optimized"),
    ("scenario4", "config4_candidate158_balanced", "NEW", "config4_candidate158_2y_optimized"),
]

DEV_HI = None        # set in load_data(); hard bound for every optimization run
_UNLOCKED = False    # flipped only by unlock_unseen()


def unlock_unseen():
    global _UNLOCKED
    _UNLOCKED = True


def load_data():
    global DEV_HI
    loader = MarketDataLoader(data_dir="data")
    pc = PlatformConfig(platform=PLATFORM, symbol=SYMBOL, resolution=RESOLUTION,
                        start_date="2022-01-01", end_date=UNSEEN_END)
    df = loader.load_ohlcv(pc, quiet=True)
    dt = pd.to_datetime(df["datetime"], utc=True)
    hi = pd.Timestamp(UNSEEN_END, tz="UTC") + pd.Timedelta(hours=23, minutes=59, seconds=59)
    df = df[dt <= hi].reset_index(drop=True)
    dt = pd.to_datetime(df["datetime"], utc=True)
    dev_lo = int(np.argmax((dt >= pd.Timestamp(DEV_START, tz="UTC")).to_numpy()))
    uns_lo = int(np.argmax((dt >= pd.Timestamp(UNSEEN_START, tz="UTC")).to_numpy()))
    DEV_HI = uns_lo
    return df, dev_lo, uns_lo, len(df)


def build_cfg(preset: dict, p: dict = None) -> PipelineConfig:
    s = dict(preset["strategy"]); r = dict(preset["risk"]); e = preset["execution"]
    if p:
        for k in ("ema_period", "rsi_period", "rsi_overbought", "rsi_oversold", "atr_period",
                  "consolidation_candles", "consolidation_atr_mult", "swing_lookback",
                  "volume_sma_period", "volume_mult", "risk_reward_ratio"):
            s[k] = p[k]
        r["leverage"] = p["leverage"]
        r["risk_per_trade_pct"] = p["risk_per_trade_pct"] * 100.0
        r["max_position_allocation_pct"] = p["max_position_allocation_pct"] * 100.0
    cfg = PipelineConfig(execution_mode="REFERENCE")
    cfg.platform = PlatformConfig(platform=PLATFORM, symbol=SYMBOL, resolution=RESOLUTION,
                                  start_date=DEV_START, end_date=UNSEEN_END)
    cfg.strategy = StrategyConfig(
        symbol=SYMBOL, resolution=RESOLUTION,
        ema_period=int(s["ema_period"]), rsi_period=int(s["rsi_period"]),
        rsi_overbought=float(s["rsi_overbought"]), rsi_oversold=float(s["rsi_oversold"]),
        atr_period=int(s["atr_period"]), consolidation_candles=int(s["consolidation_candles"]),
        consolidation_atr_mult=float(s["consolidation_atr_mult"]),
        swing_lookback=int(s["swing_lookback"]), volume_sma_period=int(s["volume_sma_period"]),
        use_volume_filter=True, volume_mult=float(s["volume_mult"]),
        long_enabled=True, short_enabled=False,
        risk_reward_ratio=float(s["risk_reward_ratio"]))
    rc = RiskConfig()
    rc.initial_capital = r["initial_capital"]; rc.leverage = float(r["leverage"])
    rc.risk_per_trade_pct = float(r["risk_per_trade_pct"]) / 100.0
    rc.max_position_allocation_pct = float(r["max_position_allocation_pct"]) / 100.0
    rc.quantity_step = r["quantity_step"]; rc.sizing_mode = r["sizing_mode"]
    cfg.risk = rc
    ec = ExecutionConfig(mode="REFERENCE")
    ec.taker_fee_pct = e["commission_pct"] / 100.0
    ec.slippage_ticks = e["slippage_ticks"]; ec.tick_size = e["tick_size"]
    cfg.execution = ec
    return cfg


def run(df, cfg, fcfg, eval_lo, eval_hi):
    if not _UNLOCKED:
        assert eval_hi <= DEV_HI, f"LEAKAGE: eval_hi={eval_hi} crosses locked boundary {DEV_HI}"
    frame = df.iloc[:eval_hi].reset_index(drop=True)
    ind = compute_all_indicators(frame.copy(), cfg.strategy).iloc[eval_lo:].reset_index(drop=True)
    engine = BacktestEngine(cfg)
    strat = BollingerFilteredStrategy(cfg.strategy, fcfg)
    engine.strategy = strat
    r = engine.run(ind)
    tr = r["trades"]
    if not tr:
        return None
    pnl = np.array([t.net_pnl for t in tr], float)
    W, L = pnl[pnl > 0], pnl[pnl < 0]
    eq = np.array([e["equity"] for e in r["equity_curve"]], float)
    rr = np.diff(eq) / (eq[:-1] + 1e-9)
    sh = float(rr.mean() / (rr.std() + 1e-9) * math.sqrt(35040)) if rr.std() > 1e-12 else 0.0
    gp, gl = float(W.sum()), float(abs(L.sum()))
    return {"return_pct": r["net_return_pct"], "net_pnl": r["final_balance"] - INITIAL,
            "gross_profit": gp, "gross_loss": gl,
            "pf": gp / gl if gl > 1e-9 else (99.0 if gp > 0 else 0.0), "sharpe": sh,
            "max_dd": r["max_drawdown_pct"], "trades": len(tr), "wins": int(len(W)),
            "losses": int(len(L)), "win_rate": 100.0 * len(W) / len(tr),
            "fees": float(sum(t.total_fees for t in tr)),
            "blocked": strat.blocked_count}


OFF = BollingerFilterConfig(enabled=False)


def clip(v, a, b):
    return max(a, min(b, v))


def profit_first(tm, vm):
    """Shared objective — identical for all four scenarios."""
    if tm is None or vm is None:
        return -10.0
    if tm["trades"] < MIN_TRAIN_TRADES or vm["trades"] < MIN_VAL_TRADES:
        return -10.0
    tr, vr = tm["return_pct"] / 100.0, vm["return_pct"] / 100.0
    s = 0.70 * clip(tr, -2, 5) + 0.30 * clip(vr, -2, 5)
    s += 0.10 * clip(tm["pf"] - 1.0, -1, 1.5) + 0.05 * clip(tm["sharpe"], -2, 2)
    w = max(tm["max_dd"], vm["max_dd"])
    if w > 40.0:
        s -= 0.40 + 0.02 * (w - 40.0)
    if vr < 0:
        s -= 0.50
    if tr > 0 and vr < 0.30 * tr:
        s -= 0.30 * clip((0.30 * tr - vr) / max(0.30 * tr, 1e-9), 0, 2)
    return s


RANGES = dict(ema=(20, 150), rsi=(7, 21), ob=(55.0, 80.0), os=(20.0, 45.0), atr=(7, 21),
              cons=(4, 20), cmult=(1.0, 4.0), swing=(4, 20), vsma=(10, 50),
              vmult=(0.5, 2.0), rr=(1.0, 4.0), risk=(0.005, 0.030),
              alloc=(0.25, 0.75), lev=(1.0, 5.0))


def suggest(trial, arch):
    R = RANGES
    p = {
        "ema_period": trial.suggest_int("ema_period", *R["ema"]),
        "rsi_period": trial.suggest_int("rsi_period", *R["rsi"]),
        "rsi_overbought": trial.suggest_float("rsi_overbought", *R["ob"], step=1.0),
        "rsi_oversold": trial.suggest_float("rsi_oversold", *R["os"], step=1.0),
        "atr_period": trial.suggest_int("atr_period", *R["atr"]),
        "consolidation_candles": trial.suggest_int("consolidation_candles", *R["cons"]),
        "consolidation_atr_mult": trial.suggest_float("consolidation_atr_mult", *R["cmult"], step=0.1),
        "swing_lookback": trial.suggest_int("swing_lookback", *R["swing"]),
        "volume_sma_period": trial.suggest_int("volume_sma_period", *R["vsma"]),
        "volume_mult": trial.suggest_float("volume_mult", *R["vmult"], step=0.1),
        "risk_reward_ratio": trial.suggest_float("risk_reward_ratio", *R["rr"], step=0.1),
        "risk_per_trade_pct": trial.suggest_float("risk_per_trade_pct", *R["risk"], step=0.001),
        "max_position_allocation_pct": trial.suggest_float("max_position_allocation_pct", *R["alloc"], step=0.05),
        "leverage": trial.suggest_float("leverage", *R["lev"], step=0.5),
    }
    if arch == "LEGACY":
        trial.suggest_categorical("side_choice", ["both", "long_only", "short_only"])
    return p


def seed_params(preset):
    s, r = preset["strategy"], preset["risk"]
    return {k: s[k] for k in ("ema_period", "rsi_period", "rsi_overbought", "rsi_oversold",
                              "atr_period", "consolidation_candles", "consolidation_atr_mult",
                              "swing_lookback", "volume_sma_period", "volume_mult",
                              "risk_reward_ratio")} | {
        "risk_per_trade_pct": r["risk_per_trade_pct"] / 100.0,
        "max_position_allocation_pct": r["max_position_allocation_pct"] / 100.0,
        "leverage": r["leverage"]}


def optimize_scenario(tag, cfgname, arch, df, dev_lo, tr_hi, dev_hi):
    preset = json.load(open(f"configs/{cfgname}.json"))
    od = os.path.join(OUT, tag); os.makedirs(od, exist_ok=True)
    rows = []

    def objective(trial):
        p = suggest(trial, arch)
        c = build_cfg(preset, p)
        tm = run(df, c, OFF, dev_lo, tr_hi)
        vm = run(df, c, OFF, tr_hi, dev_hi)
        s = profit_first(tm, vm)
        rows.append({"trial": trial.number, "score": s, **p,
                     **{f"tr_{k}": v for k, v in (tm or {}).items()},
                     **{f"va_{k}": v for k, v in (vm or {}).items()}})
        return s

    st = optuna.create_study(direction="maximize", sampler=TPESampler(seed=SEED))
    sp = seed_params(preset)
    if arch == "LEGACY":
        sp = dict(sp, side_choice="long_only")
    st.enqueue_trial(sp)                       # start from the scenario's own config
    st.optimize(objective, n_trials=STRAT_TRIALS, n_jobs=1)

    d = pd.DataFrame(rows).sort_values("score", ascending=False)
    d.to_csv(os.path.join(od, "strategy_trials.csv"), index=False)
    cred = d[(d["tr_trades"] >= MIN_TRAIN_TRADES) & (d["va_trades"] >= MIN_VAL_TRADES)]
    b = cred.iloc[0]
    best = {k: (int(b[k]) if k in ("ema_period", "rsi_period", "atr_period",
                                   "consolidation_candles", "swing_lookback", "volume_sma_period")
                else float(b[k])) for k in sp if k != "side_choice"}
    print(f"  [{tag}/{arch}] strat score {b['score']:.4f} | tr {b['tr_return_pct']:.1f}% va {b['va_return_pct']:.1f}% "
          f"| n {int(b['tr_trades'])}/{int(b['va_trades'])} | credible {len(cred)}/{len(d)}")
    return preset, best


def optimize_bollinger(tag, preset, best, df, dev_lo, dev_hi):
    cfg = build_cfg(preset, best)
    od = os.path.join(OUT, tag)
    off = run(df, cfg, OFF, dev_lo, dev_hi)
    rows = []

    def objective(trial):
        f = BollingerFilterConfig(enabled=True,
            length=trial.suggest_int("length", 10, 50),
            std=trial.suggest_float("std", 1.5, 3.0, step=0.1),
            min_bandwidth_pct=trial.suggest_float("min_bandwidth_pct", 0.0, 6.0, step=0.1),
            expansion_lookback=trial.suggest_int("expansion_lookback", 2, 20),
            expansion_min_ratio=trial.suggest_float("expansion_min_ratio", 0.0, 1.6, step=0.05),
            min_mid_distance=trial.suggest_float("min_mid_distance", 0.0, 0.45, step=0.01))
        on = run(df, cfg, f, dev_lo, dev_hi)
        if on is None or on["trades"] < 40 or on["trades"] / off["trades"] < 0.40:
            rows.append({"trial": trial.number, "score": -10.0, **f.to_dict()})
            return -10.0
        pr = on["gross_profit"] / max(off["gross_profit"], 1e-9)
        lr = 1.0 - on["gross_loss"] / max(off["gross_loss"], 1e-9)
        ratio = on["trades"] / off["trades"]
        s = (0.45 * pr + 0.35 * lr + 0.10 * clip(on["pf"] - off["pf"], -0.5, 0.5) * 2
             + 0.10 * clip(on["sharpe"] - off["sharpe"], -1, 1)
             - 0.60 * max(0.0, 0.60 - ratio) / 0.60)
        rows.append({"trial": trial.number, "score": s, **f.to_dict(),
                     "profit_retained": pr, "loss_reduction": lr, "trades_retained": ratio})
        return s

    st = optuna.create_study(direction="maximize", sampler=TPESampler(seed=SEED))
    st.optimize(objective, n_trials=BOLL_TRIALS, n_jobs=1)
    d = pd.DataFrame(rows).sort_values("score", ascending=False)
    d.to_csv(os.path.join(od, "bollinger_trials.csv"), index=False)
    b = d.iloc[0]
    bf = BollingerFilterConfig(enabled=True, length=int(b["length"]), std=float(b["std"]),
        min_bandwidth_pct=float(b["min_bandwidth_pct"]),
        expansion_lookback=int(b["expansion_lookback"]),
        expansion_min_ratio=float(b["expansion_min_ratio"]),
        min_mid_distance=float(b["min_mid_distance"]))
    on = run(df, cfg, bf, dev_lo, dev_hi)
    print(f"  [{tag}] boll score {b['score']:.4f} | dev OFF {off['return_pct']:.1f}% -> ON {on['return_pct']:.1f}% "
          f"| loss {off['gross_loss']:,.0f} -> {on['gross_loss']:,.0f}")
    return bf, off, on


def main():
    os.makedirs(OUT, exist_ok=True)
    df, dev_lo, uns_lo, n = load_data()
    dev_hi = uns_lo
    tr_hi = dev_lo + int((dev_hi - dev_lo) * TRAIN_FRAC)

    print("=" * 78)
    print("2-YEAR 15m CAMPAIGN — DATE BOUNDARIES")
    print(f"  warmup      rows [0, {dev_lo})            {df['datetime'].iloc[0]} -> {df['datetime'].iloc[dev_lo-1]}")
    print(f"  DEV TRAIN   rows [{dev_lo}, {tr_hi})   {df['datetime'].iloc[dev_lo]} -> {df['datetime'].iloc[tr_hi-1]}")
    print(f"  DEV VALID   rows [{tr_hi}, {dev_hi})   {df['datetime'].iloc[tr_hi]} -> {df['datetime'].iloc[dev_hi-1]}")
    print(f"  UNSEEN      rows [{uns_lo}, {n})   {df['datetime'].iloc[uns_lo]} -> {df['datetime'].iloc[-1]}   [LOCKED]")
    print(f"  final unseen month excluded from optimization : YES (hard assert eval_hi <= {DEV_HI})")
    print(f"  final unseen month excluded from Bollinger    : YES")
    print(f"  final unseen month excluded from ranking      : YES")
    print("=" * 78)

    results = []
    for tag, cfgname, arch, outname in SCENARIOS:
        t0 = time.time()
        print(f"\n{tag} <- {cfgname} [{arch}]")
        preset, best = optimize_scenario(tag, cfgname, arch, df, dev_lo, tr_hi, dev_hi)
        bf, dev_off, dev_on = optimize_bollinger(tag, preset, best, df, dev_lo, dev_hi)
        cfg = build_cfg(preset, best)
        tm = run(df, cfg, OFF, dev_lo, tr_hi); vm = run(df, cfg, OFF, tr_hi, dev_hi)
        newp = json.loads(json.dumps(preset))
        newp["_name"] = f"{outname} (2y campaign, {arch} optimizer)"
        newp["_source"] = f"campaign_2y_15m {tag}; dev {DEV_START}..{DEV_END}; unseen {UNSEEN_START}..{UNSEEN_END} untouched"
        newp["_trial_id"] = tag
        for k in ("ema_period", "rsi_period", "rsi_overbought", "rsi_oversold", "atr_period",
                  "consolidation_candles", "consolidation_atr_mult", "swing_lookback",
                  "volume_sma_period", "volume_mult", "risk_reward_ratio"):
            newp["strategy"][k] = best[k]
        newp["risk"]["leverage"] = best["leverage"]
        newp["risk"]["risk_per_trade_pct"] = round(best["risk_per_trade_pct"] * 100.0, 4)
        newp["risk"]["max_position_allocation_pct"] = round(best["max_position_allocation_pct"] * 100.0, 4)
        newp["filters"]["bollinger"] = dict(bf.to_dict(), enabled=False)
        json.dump(newp, open(f"configs/{outname}.json", "w"), indent=2)
        results.append({"tag": tag, "arch": arch, "outname": outname, "params": best,
                        "bollinger": bf.to_dict(), "dev_train": tm, "dev_val": vm,
                        "dev_off": dev_off, "dev_on": dev_on})
        print(f"  saved configs/{outname}.json ({time.time()-t0:.0f}s)")

    json.dump({"boundaries": {"dev": [DEV_START, DEV_END], "unseen": [UNSEEN_START, UNSEEN_END],
                              "dev_lo": dev_lo, "train_hi": tr_hi, "dev_hi": dev_hi, "n": n},
               "results": results}, open(os.path.join(OUT, "development.json"), "w"),
              indent=2, default=str)

    # ---- unlock the unseen month, once, after all optimization is complete ----
    print("\n" + "=" * 78)
    print("UNLOCKING FINAL UNSEEN MONTH — all optimization complete, no parameter may change")
    print("=" * 78)
    unlock_unseen()
    final = []
    for r in results:
        preset = json.load(open(f"configs/{r['outname']}.json"))
        cfg = build_cfg(preset)
        bf_on = BollingerFilterConfig.from_dict(dict(preset["filters"]["bollinger"], enabled=True))
        off = run(df, cfg, OFF, uns_lo, n)
        on = run(df, cfg, bf_on, uns_lo, n)
        r["unseen_off"], r["unseen_on"] = off, on
        final.append(r)
        print(f"  {r['tag']}: OFF ret {off['return_pct']:7.2f}% PF {off['pf']:.3f} DD {off['max_dd']:6.2f}% n {off['trades']:3d} | "
              f"ON ret {on['return_pct']:7.2f}% PF {on['pf']:.3f} DD {on['max_dd']:6.2f}% n {on['trades']:3d}"
              if on else f"  {r['tag']}: OFF ret {off['return_pct']:.2f}% | ON no trades")
    json.dump(final, open(os.path.join(OUT, "final_results.json"), "w"), indent=2, default=str)
    print(f"\n-> {OUT}/")


if __name__ == "__main__":
    main()
