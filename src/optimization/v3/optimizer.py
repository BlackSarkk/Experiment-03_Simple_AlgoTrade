"""New Optimizer V3 — Stage 1 discovers a 14-dim SEED, Stage 2 enqueues it and finds the
final CONFIG + Bollinger. Heavy module: imports pandas/optuna/production code. The plan-only
CLI never imports this file.

    Stage 1a  broad   400 trials, 11 strategy dims, neutral risk 1.0x / 1.5% / 50%
    Stage 1b  narrow  800 trials, 11 strategy dims, ranges derived from 1a survivors
    Stage 1c  risk    200 trials, strategy frozen, leverage / risk / allocation only
              -> exactly one complete 14-dimension seed
    Stage 2a  final   300 trials, 14 dims jointly, SEED enqueued as trial 0
    Stage 2b  boll    150 trials, strategy + risk frozen

Corrections versus the recovered recipe are listed in SCORING_AND_SELECTION.md.
"""
import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import optuna
import pandas as pd
from optuna.samplers import TPESampler

from common.config import (ExecutionConfig, PipelineConfig, PlatformConfig, RiskConfig,
                           StrategyConfig)
from strategy.indicators import compute_all_indicators
from strategy.baseline_strategy import Signal
from backtest.engine import BacktestEngine
from filters.stage_1_bollinger.filter import BollingerFilterConfig, BollingerFilteredStrategy

from . import scoring, spec

optuna.logging.set_verbosity(optuna.logging.WARNING)


class SkipHeadStrategy(BollingerFilteredStrategy):
    """Bollinger gate plus a FIXED leading-bar cut.

    The frozen BaselineStrategy already ignores its first max(ema_period+10, 60) bars, which
    would make the evaluable window EMA-dependent. Dropping every signal below a fixed index
    above that maximum makes the evaluated window identical for every candidate. This only
    ever REMOVES signals; it cannot create one.
    """

    def __init__(self, strategy_config=None, filter_config=None, skip_bars: int = spec.EVAL_SKIP_BARS):
        super().__init__(strategy_config, filter_config)
        self.skip_bars = int(skip_bars)
        self.head_dropped = 0

    def generate_signals(self, df: pd.DataFrame) -> List[Signal]:
        sigs = super().generate_signals(df)
        kept = [s for s in sigs if s.candle_idx >= self.skip_bars]
        self.head_dropped = len(sigs) - len(kept)
        return kept


def tick_size(symbol: str) -> float:
    if symbol not in spec.TICK_SIZE:
        raise KeyError(f"no tick size declared for {symbol!r}; add it to spec.TICK_SIZE")
    return spec.TICK_SIZE[symbol]


def suggest(trial, ranges: Dict[str, tuple]) -> Dict[str, Any]:
    out = {}
    for name, (kind, lo, hi, step) in ranges.items():
        if kind == "int":
            out[name] = trial.suggest_int(name, int(lo), int(hi), step=int(step))
        else:
            out[name] = trial.suggest_float(name, float(lo), float(hi), step=float(step))
    return out


def build_cfg(symbol: str, timeframe: str, params: Dict[str, Any]) -> PipelineConfig:
    cfg = PipelineConfig(execution_mode="REFERENCE")
    cfg.platform = PlatformConfig(platform="BINANCE_FUTURES", symbol=symbol, resolution=timeframe)
    cfg.strategy = StrategyConfig(
        symbol=symbol, resolution=timeframe,
        ema_period=int(params["ema_period"]), rsi_period=int(params["rsi_period"]),
        rsi_overbought=float(params["rsi_overbought"]), rsi_oversold=float(params["rsi_oversold"]),
        atr_period=int(params["atr_period"]),
        consolidation_candles=int(params["consolidation_candles"]),
        consolidation_atr_mult=float(params["consolidation_atr_mult"]),
        swing_lookback=int(params["swing_lookback"]),
        volume_sma_period=int(params["volume_sma_period"]), use_volume_filter=True,
        volume_mult=float(params["volume_mult"]),
        long_enabled=spec.LONG_ENABLED, short_enabled=spec.SHORT_ENABLED,
        risk_reward_ratio=float(params["risk_reward_ratio"]))
    cfg.risk = RiskConfig(initial_capital=spec.INITIAL_CAPITAL,
                          leverage=float(params["leverage"]),
                          risk_per_trade_pct=float(params["risk_per_trade_pct"]),
                          max_position_allocation_pct=float(params["max_position_allocation_pct"]),
                          quantity_step=spec.QUANTITY_STEP)
    ec = ExecutionConfig(mode="REFERENCE")
    ec.taker_fee_pct = spec.COMMISSION_PCT / 100.0
    ec.maker_fee_pct = spec.COMMISSION_PCT / 100.0
    ec.slippage_ticks = spec.SLIPPAGE_TICKS
    ec.tick_size = tick_size(symbol)
    cfg.execution = ec
    return cfg


def metrics(result, blocked: int, head_dropped: int) -> Optional[Dict[str, Any]]:
    trades = result["trades"]
    if not trades:
        return None
    pnl = np.array([t.net_pnl for t in trades], dtype=float)
    W, L = pnl[pnl > 0], pnl[pnl < 0]
    eq = np.array([e["equity"] for e in result["equity_curve"]], dtype=float)
    rr = np.diff(eq) / (eq[:-1] + 1e-9)
    sh = float(rr.mean() / (rr.std() + 1e-9) * math.sqrt(35040)) if rr.std() > 1e-12 else 0.0
    gp, gl = float(W.sum()), float(abs(L.sum()))
    return {"return_pct": result["net_return_pct"],
            "pf": gp / gl if gl > 1e-9 else (99.0 if gp > 0 else 0.0), "sharpe": sh,
            "max_dd": result["max_drawdown_pct"], "trades": len(trades),
            "wins": int(len(W)), "losses": int(len(L)),
            "win_rate": 100.0 * len(W) / len(trades),
            "gross_profit": gp, "gross_loss": gl, "net_pnl": gp - gl,
            "fees": float(sum(t.total_fees for t in trades)),
            "blocked": blocked, "head_dropped": head_dropped}


OFF = BollingerFilterConfig(enabled=False)


class Campaign:
    """One symbol, one timeframe, one warmup+DEV frame. No holdout exists in the frame."""

    def __init__(self, symbol: str, timeframe: str, df: pd.DataFrame, warmup_rows: int):
        self.symbol, self.timeframe, self.df = symbol, timeframe, df
        self.warm = int(warmup_rows)
        n = len(df)
        dev = n - self.warm
        if dev <= 0:
            raise ValueError("frame contains no DEV rows")
        self.dev_lo, self.dev_hi = self.warm, n
        self.tr_hi = self.warm + int(dev * spec.TRAIN_FRAC)
        self.train_rows = self.tr_hi - self.dev_lo
        self.valid_rows = self.dev_hi - self.tr_hi
        if min(self.train_rows, self.valid_rows) <= spec.EVAL_SKIP_BARS:
            raise ValueError("partition shorter than the fixed evaluation skip")
        self.min_tr = scoring.min_trades(self.train_rows)
        self.min_va = scoring.min_trades(self.valid_rows)
        tick_size(symbol)                       # fail loud on an undeclared symbol

    # -------------------------------------------------------------- evaluation
    def indicators(self, cfg) -> pd.DataFrame:
        """ONCE per candidate, on the FULL warmup+DEV frame. Never on a sliced partition."""
        return compute_all_indicators(self.df.copy(), cfg.strategy)

    def _one(self, cfg, fcfg, ind, lo, hi):
        frame = ind.iloc[lo:hi].reset_index(drop=True)
        engine = BacktestEngine(cfg)
        strat = SkipHeadStrategy(cfg.strategy, fcfg, spec.EVAL_SKIP_BARS)
        engine.strategy = strat
        return metrics(engine.run(frame), strat.blocked_count, strat.head_dropped)

    def evaluate(self, cfg, fcfg=OFF, ind=None) -> Dict[str, Any]:
        ind = self.indicators(cfg) if ind is None else ind
        return {"train": self._one(cfg, fcfg, ind, self.dev_lo, self.tr_hi),
                "valid": self._one(cfg, fcfg, ind, self.tr_hi, self.dev_hi)}

    def _study(self):
        return optuna.create_study(direction="maximize", sampler=TPESampler(seed=spec.SEED))

    def _strategy_stage(self, ranges, n_trials, fixed_risk, tag, progress=None):
        rows = []

        def objective(trial):
            p = dict(suggest(trial, ranges))
            p.update(fixed_risk)
            m = self.evaluate(build_cfg(self.symbol, self.timeframe, p))
            s, comp = scoring.score(m["train"], m["valid"], self.min_tr, self.min_va)
            rows.append({"stage": tag, "trial": trial.number, "score": s,
                         "gated": scoring.passes(m["train"], m["valid"], self.min_tr, self.min_va),
                         **p, **{f"tr_{k}": v for k, v in (m["train"] or {}).items()},
                         **{f"va_{k}": v for k, v in (m["valid"] or {}).items()},
                         **{f"c_{k}": v for k, v in comp.items()}})
            if progress:
                progress(tag, trial.number, s)
            return s

        st = self._study()
        st.optimize(objective, n_trials=n_trials, n_jobs=spec.N_JOBS)   # no enqueue in stage 1
        return pd.DataFrame(rows)

    # -------------------------------------------------------------- stage 1
    @staticmethod
    def narrow_ranges(broad: pd.DataFrame) -> Dict[str, tuple]:
        """Deterministic: top NARROW_TOP_FRACTION of gated trials by score, take each
        dimension's observed [min, max], widen one step per side, clip to the broad bounds."""
        g = broad[broad.gated].sort_values(["score", "trial"], ascending=[False, True])
        if g.empty:
            raise RuntimeError("stage 1a produced no gated trial; cannot narrow")
        k = max(spec.NARROW_MIN_CANDIDATES, int(round(len(g) * spec.NARROW_TOP_FRACTION)))
        top = g.head(min(k, len(g)))
        out = {}
        for name, (kind, lo, hi, step) in spec.STRATEGY_RANGES.items():
            obs_lo, obs_hi = float(top[name].min()), float(top[name].max())
            n_lo = max(lo, obs_lo - spec.NARROW_WIDEN_STEPS * step)
            n_hi = min(hi, obs_hi + spec.NARROW_WIDEN_STEPS * step)
            if kind == "int":
                n_lo, n_hi = int(round(n_lo)), int(round(n_hi))
                if n_hi <= n_lo:
                    n_lo, n_hi = int(lo), int(hi)
            else:
                n_lo, n_hi = round(n_lo, 6), round(n_hi, 6)
                if n_hi <= n_lo:
                    n_lo, n_hi = float(lo), float(hi)
            out[name] = (kind, n_lo, n_hi, step)
        return out

    def stage1(self, progress=None) -> Tuple[Dict[str, Any], Dict[str, pd.DataFrame], Dict[str, tuple]]:
        neutral = dict(spec.NEUTRAL_RISK)
        broad = self._strategy_stage(spec.STRATEGY_RANGES, spec.BROAD_TRIALS, neutral, "1a_broad", progress)
        narrow_space = self.narrow_ranges(broad)
        narrow = self._strategy_stage(narrow_space, spec.NARROW_TRIALS, neutral, "1b_narrow", progress)

        pool = pd.concat([broad, narrow], ignore_index=True)
        g = pool[pool.gated].sort_values(["score", "trial"], ascending=[False, True])
        if g.empty:
            raise RuntimeError("stage 1 produced no gated strategy; no seed can be formed")
        best = g.iloc[0]
        frozen = {k: (int(best[k]) if spec.STRATEGY_RANGES[k][0] == "int" else float(best[k]))
                  for k in spec.STRATEGY_KEYS}

        rows = []

        def risk_objective(trial):
            p = dict(frozen)
            p.update(suggest(trial, spec.RISK_RANGES))
            m = self.evaluate(build_cfg(self.symbol, self.timeframe, p))
            s, comp = scoring.score(m["train"], m["valid"], self.min_tr, self.min_va)
            rows.append({"stage": "1c_risk", "trial": trial.number, "score": s,
                         "gated": scoring.passes(m["train"], m["valid"], self.min_tr, self.min_va),
                         **p, **{f"tr_{k}": v for k, v in (m["train"] or {}).items()},
                         **{f"va_{k}": v for k, v in (m["valid"] or {}).items()},
                         **{f"c_{k}": v for k, v in comp.items()}})
            if progress:
                progress("1c_risk", trial.number, s)
            return s

        st = self._study()
        st.optimize(risk_objective, n_trials=spec.RISK_SEED_TRIALS, n_jobs=spec.N_JOBS)
        risk_df = pd.DataFrame(rows)
        rg = risk_df[risk_df.gated].sort_values(["score", "trial"], ascending=[False, True])
        if rg.empty:
            raise RuntimeError("stage 1c produced no gated risk policy; no seed can be formed")
        rb = rg.iloc[0]
        seed = dict(frozen)
        seed.update({k: float(rb[k]) for k in spec.RISK_KEYS})
        seed_meta = {"strategy_from": {"stage": str(best["stage"]), "trial": int(best["trial"]),
                                       "score": float(best["score"])},
                     "risk_from": {"trial": int(rb["trial"]), "score": float(rb["score"])},
                     "gated": {"broad": int(broad.gated.sum()), "narrow": int(narrow.gated.sum()),
                               "risk": int(risk_df.gated.sum())},
                     "narrowed_space": {k: list(v) for k, v in narrow_space.items()},
                     "seed": seed}
        return seed_meta, {"1a_broad": broad, "1b_narrow": narrow, "1c_risk": risk_df}, narrow_space

    # -------------------------------------------------------------- stage 2
    def stage2_config(self, seed: Dict[str, Any], progress=None):
        ranges = dict(spec.STRATEGY_RANGES)
        ranges.update(spec.RISK_RANGES)
        rows = []

        def objective(trial):
            p = suggest(trial, ranges)
            m = self.evaluate(build_cfg(self.symbol, self.timeframe, p))
            s, comp = scoring.score(m["train"], m["valid"], self.min_tr, self.min_va)
            rows.append({"trial": trial.number, "score": s,
                         "gated": scoring.passes(m["train"], m["valid"], self.min_tr, self.min_va),
                         **p, **{f"tr_{k}": v for k, v in (m["train"] or {}).items()},
                         **{f"va_{k}": v for k, v in (m["valid"] or {}).items()},
                         **{f"c_{k}": v for k, v in comp.items()}})
            if progress:
                progress("2a_final", trial.number, s)
            return s

        st = self._study()
        st.enqueue_trial({k: seed[k] for k in spec.ALL_KEYS})      # seed becomes trial 0
        st.optimize(objective, n_trials=spec.FINAL_TRIALS, n_jobs=spec.N_JOBS)
        d = pd.DataFrame(rows)
        g = d[d.gated].sort_values(["score", "trial"], ascending=[False, True])
        if g.empty:
            raise RuntimeError("stage 2a produced no gated configuration")
        b = g.iloc[0]
        winner = {k: (int(b[k]) if spec.STRATEGY_RANGES.get(k, ("float",))[0] == "int" else float(b[k]))
                  for k in spec.ALL_KEYS}
        return d, {"trial": int(b.trial), "score": float(b.score),
                   "gated_count": int(len(g)), "total": int(len(d)),
                   "seed_was_trial_0": True, "params": winner}

    def stage2_bollinger(self, params: Dict[str, Any], progress=None):
        cfg = build_cfg(self.symbol, self.timeframe, params)
        ind = self.indicators(cfg)                                # strategy frozen -> once
        off = self.evaluate(cfg, OFF, ind=ind)
        rows = []

        def objective(trial):
            f = BollingerFilterConfig(enabled=True, **suggest(trial, spec.BOLLINGER_RANGES))
            on = self.evaluate(cfg, f, ind=ind)
            s, comp = scoring.boll_score(off, on, self.min_tr, self.min_va)
            rows.append({"trial": trial.number, "score": s, **f.to_dict(),
                         **{f"tr_on_{k}": v for k, v in (on["train"] or {}).items()},
                         **{f"va_on_{k}": v for k, v in (on["valid"] or {}).items()},
                         **{f"c_{k}": v for k, v in comp.items()}})
            if progress:
                progress("2b_boll", trial.number, s)
            return s

        st = self._study()
        st.optimize(objective, n_trials=spec.BOLL_TRIALS, n_jobs=spec.N_JOBS)
        d = pd.DataFrame(rows).sort_values(["score", "trial"], ascending=[False, True])
        top = d.iloc[0]
        if float(top.score) <= spec.FAIL_BASE:                    # nothing cleared the gate
            return d, None, off
        bf = BollingerFilterConfig(
            enabled=True, length=int(top["length"]), std=float(top["std"]),
            min_bandwidth_pct=float(top["min_bandwidth_pct"]),
            expansion_lookback=int(top["expansion_lookback"]),
            expansion_min_ratio=float(top["expansion_min_ratio"]),
            min_mid_distance=float(top["min_mid_distance"]))
        return d, {"trial": int(top.trial), "score": float(top.score), "cfg": bf}, off
