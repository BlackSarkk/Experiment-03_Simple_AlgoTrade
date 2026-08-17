"""New Optimizer V2 — clean replacement for the dead new-optimizer attempt.

Selection rules are fixed in SELECTION_RULE.md and recorded before any run. Corrections over
multi_tf_optimizer.py / deep_15m_optimizer.py:

  * one symbol + one timeframe per campaign (no 10-timeframe sweep)
  * indicators computed ONCE on the full warmup+DEV frame per trial and sliced BY INDEX;
    never recomputed on an already-sliced partition
  * TPE seed 42, n_jobs=1 -> reproducible
  * no holdout/unseen partition exists in the frame; nothing to leak
  * no incumbent enqueued or seeded
  * long-only; direction is not a search dimension
  * production BacktestEngine / BaselineRiskManager / compute_all_indicators, unchanged
  * validation-aware gate + capped-TRAIN score, so TRAIN return cannot dominate VALID PF/DD
"""
import math
from typing import Any, Dict, Optional

import numpy as np
import optuna
import pandas as pd
from optuna.samplers import TPESampler

from common.config import (ExecutionConfig, PipelineConfig, PlatformConfig, RiskConfig,
                           StrategyConfig)
from strategy.indicators import compute_all_indicators
from backtest.engine import BacktestEngine
from filters.stage_1_bollinger.filter import BollingerFilterConfig, BollingerFilteredStrategy

optuna.logging.set_verbosity(optuna.logging.WARNING)

SEED = 42
N_JOBS = 1
STRAT_TRIALS = 300
BOLL_TRIALS = 150
INITIAL = 10000.0
MIN_TRAIN_TRADES = 100
MIN_VALID_TRADES = 40
EXEC = {"commission_pct": 0.05, "slippage_ticks": 1, "tick_size": 0.01}
QTY_STEP = 0.001

# 14 dimensions — identical to Scenario 4 for the fair test
RANGES = dict(ema=(20, 150), rsi=(7, 21), ob=(55.0, 80.0), os=(20.0, 45.0), atr=(7, 21),
              cons=(4, 20), cmult=(1.0, 4.0), swing=(4, 20), vsma=(10, 50),
              vmult=(0.5, 2.0), rr=(1.0, 4.0), risk=(0.005, 0.030),
              alloc=(0.25, 0.75), lev=(1.0, 5.0))
BOLL_RANGES = dict(length=(10, 50), std=(1.5, 3.0), min_bw=(0.0, 6.0),
                   exp_lb=(2, 20), exp_ratio=(0.0, 1.6), mid_dist=(0.0, 0.45))
OFF = BollingerFilterConfig(enabled=False)


def clip(v, a, b):
    return max(a, min(b, v))


def suggest(trial) -> Dict[str, Any]:
    R = RANGES
    return {
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
        "max_position_allocation_pct": trial.suggest_float("max_position_allocation_pct",
                                                          *R["alloc"], step=0.05),
        "leverage": trial.suggest_float("leverage", *R["lev"], step=0.5),
    }


def build_cfg(symbol: str, timeframe: str, p: Dict[str, Any]) -> PipelineConfig:
    cfg = PipelineConfig(execution_mode="REFERENCE")
    cfg.platform = PlatformConfig(platform="BINANCE_FUTURES", symbol=symbol, resolution=timeframe)
    cfg.strategy = StrategyConfig(
        symbol=symbol, resolution=timeframe,
        ema_period=int(p["ema_period"]), rsi_period=int(p["rsi_period"]),
        rsi_overbought=float(p["rsi_overbought"]), rsi_oversold=float(p["rsi_oversold"]),
        atr_period=int(p["atr_period"]),
        consolidation_candles=int(p["consolidation_candles"]),
        consolidation_atr_mult=float(p["consolidation_atr_mult"]),
        swing_lookback=int(p["swing_lookback"]),
        volume_sma_period=int(p["volume_sma_period"]), use_volume_filter=True,
        volume_mult=float(p["volume_mult"]),
        long_enabled=True, short_enabled=False,           # long-only, never searched
        risk_reward_ratio=float(p["risk_reward_ratio"]))
    cfg.risk = RiskConfig(initial_capital=INITIAL, leverage=float(p["leverage"]),
                          risk_per_trade_pct=float(p["risk_per_trade_pct"]),
                          max_position_allocation_pct=float(p["max_position_allocation_pct"]),
                          quantity_step=QTY_STEP)
    cfg.execution = ExecutionConfig(mode="REFERENCE")
    cfg.execution.taker_fee_pct = EXEC["commission_pct"] / 100.0
    cfg.execution.maker_fee_pct = EXEC["commission_pct"] / 100.0
    cfg.execution.slippage_ticks = EXEC["slippage_ticks"]
    cfg.execution.tick_size = EXEC["tick_size"]
    return cfg


def metrics(result, blocked) -> Optional[Dict[str, Any]]:
    tr = result["trades"]
    if not tr:
        return None
    pnl = np.array([t.net_pnl for t in tr], float)
    W, L = pnl[pnl > 0], pnl[pnl < 0]
    eq = np.array([e["equity"] for e in result["equity_curve"]], float)
    rr = np.diff(eq) / (eq[:-1] + 1e-9)
    sh = float(rr.mean() / (rr.std() + 1e-9) * math.sqrt(35040)) if rr.std() > 1e-12 else 0.0
    gp, gl = float(W.sum()), float(abs(L.sum()))
    return {"return_pct": result["net_return_pct"], "pf": gp / gl if gl > 1e-9 else (99.0 if gp > 0 else 0.0),
            "sharpe": sh, "max_dd": result["max_drawdown_pct"], "trades": len(tr),
            "wins": int(len(W)), "losses": int(len(L)),
            "win_rate": 100.0 * len(W) / len(tr),
            "gross_profit": gp, "gross_loss": gl, "net_pnl": gp - gl,
            "fees": float(sum(t.total_fees for t in tr)), "blocked": blocked}


class Campaign:
    """One symbol, one timeframe, one DEV frame that contains no holdout."""

    def __init__(self, symbol, timeframe, df, warmup_rows, train_frac=0.70):
        self.symbol, self.timeframe, self.df = symbol, timeframe, df
        self.warm = int(warmup_rows)
        self.n = len(df)
        dev = self.n - self.warm
        self.dev_lo = self.warm
        self.tr_hi = self.warm + int(dev * train_frac)
        self.dev_hi = self.n
        self._ind_cache = None

    def _indicators(self, cfg):
        """Compute ONCE on the FULL warmup+DEV frame. Never on a sliced partition."""
        return compute_all_indicators(self.df.copy(), cfg.strategy)

    def evaluate(self, cfg, fcfg, ind=None):
        ind = self._indicators(cfg) if ind is None else ind
        out = {}
        for name, lo, hi in (("train", self.dev_lo, self.tr_hi), ("valid", self.tr_hi, self.dev_hi)):
            frame = ind.iloc[lo:hi].reset_index(drop=True)
            engine = BacktestEngine(cfg)
            strat = BollingerFilteredStrategy(cfg.strategy, fcfg)
            engine.strategy = strat
            out[name] = metrics(engine.run(frame), strat.blocked_count)
        return out

    # ---- Stage A score, fixed in SELECTION_RULE.md -------------------------
    @staticmethod
    def gate_a(t, v):
        if t is None or v is None:
            return False
        return (t["trades"] >= MIN_TRAIN_TRADES and v["trades"] >= MIN_VALID_TRADES
                and t["return_pct"] > 0 and v["return_pct"] > 0
                and v["pf"] >= 1.10 and v["max_dd"] <= 35.0)

    @staticmethod
    def score_a(t, v):
        gap = clip(abs(t["return_pct"] - v["return_pct"]) / max(abs(t["return_pct"]), 1e-9), 0, 2)
        return (0.55 * clip(v["return_pct"] / 100.0, -1.0, 1.5)
                + 0.20 * clip(v["pf"] - 1.0, 0.0, 1.0)
                + 0.15 * clip(t["return_pct"] / 100.0, -1.0, 1.0)
                + 0.10 * clip(t["pf"] - 1.0, 0.0, 1.0)
                - 0.50 * max(0.0, v["max_dd"] / 100.0 - 0.20)
                - 0.30 * max(0.0, t["max_dd"] / 100.0 - 0.25)
                - 0.40 * gap)

    def run_stage_a(self, progress=None):
        rows = []

        def objective(trial):
            p = suggest(trial)
            cfg = build_cfg(self.symbol, self.timeframe, p)
            m = self.evaluate(cfg, OFF)
            t, v = m["train"], m["valid"]
            ok = self.gate_a(t, v)
            s = self.score_a(t, v) if ok else -10.0
            rows.append({"trial": trial.number, "score": s, "gated": bool(ok), **p,
                         **{f"tr_{k}": val for k, val in (t or {}).items()},
                         **{f"va_{k}": val for k, val in (v or {}).items()}})
            if progress:
                progress(trial.number, s)
            return s

        study = optuna.create_study(direction="maximize", sampler=TPESampler(seed=SEED))
        study.optimize(objective, n_trials=STRAT_TRIALS, n_jobs=N_JOBS)   # no enqueue_trial
        d = pd.DataFrame(rows)
        g = d[d.gated].sort_values(["score", "trial"], ascending=[False, True])
        if g.empty:
            return d, None
        b = g.iloc[0]
        best = {k: (int(b[k]) if k in ("ema_period", "rsi_period", "atr_period",
                                       "consolidation_candles", "swing_lookback",
                                       "volume_sma_period") else float(b[k]))
                for k in RANGES_KEYS}
        return d, {"trial": int(b.trial), "score": float(b.score), "params": best,
                   "gated_count": int(len(g)), "total": int(len(d))}

    # ---- Stage B score, fixed in SELECTION_RULE.md -------------------------
    @staticmethod
    def score_b(off, on):
        t_off, v_off = off["train"], off["valid"]
        t_on, v_on = on["train"], on["valid"]
        if v_on is None or t_on is None:
            return -10.0, None
        ratio = v_on["trades"] / max(v_off["trades"], 1)
        if v_on["trades"] < 25 or ratio < 0.40 or t_on["trades"] < 50:
            return -10.0, ratio
        s = (0.45 * clip(v_on["pf"] - v_off["pf"], -0.5, 0.8) / 0.8
             + 0.25 * clip(1 - v_on["gross_loss"] / max(v_off["gross_loss"], 1e-9), -0.5, 0.8) / 0.8
             + 0.15 * clip((v_on["net_pnl"] - v_off["net_pnl"]) / max(abs(v_off["net_pnl"]), 1.0), -1, 1)
             + 0.10 * clip(t_on["pf"] - t_off["pf"], -0.5, 0.8) / 0.8
             + 0.05 * clip((v_off["max_dd"] - v_on["max_dd"]) / max(v_off["max_dd"], 1e-9), -1, 1)
             - 0.30 * max(0.0, 0.60 - ratio) / 0.60)
        return s, ratio

    def run_stage_b(self, params, progress=None):
        cfg = build_cfg(self.symbol, self.timeframe, params)
        ind = self._indicators(cfg)                       # strategy frozen -> compute once
        off = self.evaluate(cfg, OFF, ind=ind)
        rows = []

        def objective(trial):
            B = BOLL_RANGES
            f = BollingerFilterConfig(
                enabled=True,
                length=trial.suggest_int("length", *B["length"]),
                std=trial.suggest_float("std", *B["std"], step=0.1),
                min_bandwidth_pct=trial.suggest_float("min_bandwidth_pct", *B["min_bw"], step=0.1),
                expansion_lookback=trial.suggest_int("expansion_lookback", *B["exp_lb"]),
                expansion_min_ratio=trial.suggest_float("expansion_min_ratio", *B["exp_ratio"], step=0.05),
                min_mid_distance=trial.suggest_float("min_mid_distance", *B["mid_dist"], step=0.01))
            on = self.evaluate(cfg, f, ind=ind)
            s, ratio = self.score_b(off, on)
            rows.append({"trial": trial.number, "score": s, **f.to_dict(),
                         "va_trades_ratio": ratio,
                         **{f"tr_on_{k}": v for k, v in (on["train"] or {}).items()},
                         **{f"va_on_{k}": v for k, v in (on["valid"] or {}).items()}})
            if progress:
                progress(trial.number, s)
            return s

        study = optuna.create_study(direction="maximize", sampler=TPESampler(seed=SEED))
        study.optimize(objective, n_trials=BOLL_TRIALS, n_jobs=N_JOBS)
        d = pd.DataFrame(rows).sort_values(["score", "trial"], ascending=[False, True])
        top = d.iloc[0]
        if float(top.score) <= -10.0 + 1e-9:
            return d, None, off                            # no filter passed the gate
        bf = BollingerFilterConfig(
            enabled=True, length=int(top["length"]), std=float(top["std"]),
            min_bandwidth_pct=float(top["min_bandwidth_pct"]),
            expansion_lookback=int(top["expansion_lookback"]),
            expansion_min_ratio=float(top["expansion_min_ratio"]),
            min_mid_distance=float(top["min_mid_distance"]))
        return d, {"trial": int(top.trial), "score": float(top.score), "cfg": bf}, off


RANGES_KEYS = ("ema_period", "rsi_period", "rsi_overbought", "rsi_oversold", "atr_period",
               "consolidation_candles", "consolidation_atr_mult", "swing_lookback",
               "volume_sma_period", "volume_mult", "risk_reward_ratio",
               "risk_per_trade_pct", "max_position_allocation_pct", "leverage")
