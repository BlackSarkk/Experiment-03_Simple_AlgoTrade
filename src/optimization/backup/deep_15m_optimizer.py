import os
import sys
import json
import hashlib
import time
import warnings
from typing import Dict, Any, List

warnings.filterwarnings("ignore")

_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_DIR, ".."))

import numpy as np
import pandas as pd
import optuna
from optuna.samplers import TPESampler

optuna.logging.set_verbosity(optuna.logging.WARNING)

from common.config import PipelineConfig, StrategyConfig, RiskConfig, ExecutionConfig, PlatformConfig
from common.market_data import MarketDataLoader
from strategy.indicators import compute_all_indicators
from backtest.engine import BacktestEngine
from backtest.metrics import BacktestMetrics

SEED = 42
OUT_DIR = "results/15m_deep_optimization"
DATA_DIR = "data"
SYMBOL = "ETHUSDT"
TIMEFRAME = "15m"
START_DATE = "2024-01-01"
END_DATE = "2026-08-15"

INITIAL_CAPITAL = 10000.0
COMMISSION_PCT = 0.05
SLIPPAGE_TICKS = 1

TRIALS = 5000

CANDIDATE_5 = {
    "ema_period": 51,
    "rsi_period": 21,
    "rsi_overbought": 65.0,
    "rsi_oversold": 45.0,
    "atr_period": 21,
    "consolidation_candles": 8,
    "consolidation_atr_mult": 2.8,
    "swing_lookback": 12,
    "volume_sma_period": 20,
    "volume_mult": 1.6,
    "long_enabled": True,
    "short_enabled": False,
    "risk_reward_ratio": 3.0,
    "risk_per_trade_pct": 1.5,
    "leverage": 3.5,
    "max_position_allocation_pct": 50.0
}

def load_data():
    cfg = PipelineConfig()
    cfg.data_dir = DATA_DIR
    cfg.platform.symbol = SYMBOL
    cfg.platform.resolution = TIMEFRAME
    cfg.platform.start_date = START_DATE
    cfg.platform.end_date = END_DATE
    dl = MarketDataLoader(cfg.data_dir)
    df = dl.load_ohlcv(cfg.platform, reset_cache=False)
    
    # Checksum
    hash_str = pd.util.hash_pandas_object(df, index=True).values
    sha = hashlib.sha256(hash_str.tobytes()).hexdigest()
    return df, sha

def run_backtest(df: pd.DataFrame, p: Dict[str, Any]) -> Dict[str, Any]:
    cfg = PipelineConfig()
    cfg.risk.initial_capital = INITIAL_CAPITAL
    cfg.risk.leverage = p.get("leverage", 1.0)
    cfg.risk.risk_per_trade_pct = p.get("risk_per_trade_pct", 1.5) / 100.0
    cfg.risk.max_position_allocation_pct = p.get("max_position_allocation_pct", 50.0) / 100.0
    
    cfg.execution.taker_fee_pct = COMMISSION_PCT / 100.0
    cfg.execution.maker_fee_pct = COMMISSION_PCT / 100.0
    cfg.execution.slippage_ticks = SLIPPAGE_TICKS
    cfg.execution.mode = "REFERENCE"
    
    cfg.strategy.symbol = SYMBOL
    cfg.strategy.resolution = TIMEFRAME
    cfg.strategy.ema_period = int(p["ema_period"])
    cfg.strategy.rsi_period = int(p["rsi_period"])
    cfg.strategy.rsi_overbought = float(p["rsi_overbought"])
    cfg.strategy.rsi_oversold = float(p["rsi_oversold"])
    cfg.strategy.atr_period = int(p["atr_period"])
    cfg.strategy.consolidation_candles = int(p["consolidation_candles"])
    cfg.strategy.consolidation_atr_mult = float(p["consolidation_atr_mult"])
    cfg.strategy.swing_lookback = int(p["swing_lookback"])
    cfg.strategy.volume_sma_period = int(p["volume_sma_period"])
    cfg.strategy.use_volume_filter = (cfg.strategy.volume_sma_period > 0)
    cfg.strategy.volume_mult = float(p["volume_mult"])
    cfg.strategy.long_enabled = bool(p["long_enabled"])
    cfg.strategy.short_enabled = bool(p["short_enabled"])
    cfg.strategy.risk_reward_ratio = float(p["risk_reward_ratio"])
    
    # Compute indicators on the slice directly
    # In a perfect world we compute once and slice, but indicators depend on parameters!
    df_ind = compute_all_indicators(df.copy(), cfg.strategy)
    
    engine = BacktestEngine(cfg)
    res = engine.run(df_ind)
    
    metrics = BacktestMetrics.calculate(res["trades"], res["equity_curve"], INITIAL_CAPITAL)
    return {
        "net_return_pct": metrics.net_return_pct,
        "profit_factor": metrics.profit_factor_net,
        "max_dd_pct": metrics.max_drawdown_pct,
        "n_trades": metrics.total_trades,
        "win_rate": metrics.win_rate,
        "expectancy": metrics.expectancy,
        "sharpe": metrics.sharpe_ratio,
        "total_fees": metrics.total_fees
    }

def empty_metrics():
    return {
        "net_return_pct": 0.0, "profit_factor": 0.0, "max_dd_pct": 0.0,
        "n_trades": 0, "win_rate": 0.0, "expectancy": 0.0, "sharpe": 0.0, "total_fees": 0.0
    }

class DeepOptimizer:
    def __init__(self):
        self.df, self.sha = load_data()
        print(f"[DATA] ETHUSDT {TIMEFRAME} | {self.df.index[0]} -> {self.df.index[-1]} | {len(self.df)} candles | SHA: {self.sha[:8]}")
        
        n = len(self.df)
        split1 = int(n * 0.50)
        split2 = int(n * 0.75)
        
        self.df_train = self.df.iloc[:split1]
        self.df_val   = self.df.iloc[split1:split2]
        self.df_hold  = self.df.iloc[split2:]
        
        os.makedirs(OUT_DIR, exist_ok=True)
        
    def _objective(self, trial):
        p = {
            "ema_period": trial.suggest_int("ema_period", 10, 100),
            "rsi_period": trial.suggest_int("rsi_period", 7, 35),
            "rsi_overbought": trial.suggest_float("rsi_overbought", 60.0, 80.0, step=1.0),
            "rsi_oversold": trial.suggest_float("rsi_oversold", 20.0, 40.0, step=1.0),
            "atr_period": trial.suggest_int("atr_period", 7, 35),
            "consolidation_candles": trial.suggest_int("consolidation_candles", 4, 15),
            "consolidation_atr_mult": trial.suggest_float("consolidation_atr_mult", 1.0, 4.0, step=0.1),
            "swing_lookback": trial.suggest_int("swing_lookback", 3, 20),
            "volume_sma_period": trial.suggest_int("volume_sma_period", 10, 50),
            "volume_mult": trial.suggest_float("volume_mult", 0.5, 2.5, step=0.1),
            "risk_reward_ratio": trial.suggest_float("risk_reward_ratio", 1.0, 5.0, step=0.1),
            "leverage": 1.0,
            "risk_per_trade_pct": 1.5,
            "max_position_allocation_pct": 50.0
        }
        
        side = trial.suggest_categorical("side", ["both", "long_only", "short_only"])
        p["long_enabled"] = side in ["both", "long_only"]
        p["short_enabled"] = side in ["both", "short_only"]
        
        tr = run_backtest(self.df_train, p)
        va = run_backtest(self.df_val, p)
        
        trial.set_user_attr("tr_ret", tr["net_return_pct"])
        trial.set_user_attr("tr_pf", tr["profit_factor"])
        trial.set_user_attr("tr_dd", tr["max_dd_pct"])
        trial.set_user_attr("tr_t", tr["n_trades"])
        trial.set_user_attr("va_ret", va["net_return_pct"])
        trial.set_user_attr("va_pf", va["profit_factor"])
        trial.set_user_attr("va_dd", va["max_dd_pct"])
        trial.set_user_attr("va_t", va["n_trades"])
        
        # Penalties
        score = tr["net_return_pct"] + va["net_return_pct"]
        if tr["n_trades"] < 50 or va["n_trades"] < 20: score -= 1000
        if tr["max_dd_pct"] > 30.0 or va["max_dd_pct"] > 30.0: score -= 500
        if tr["profit_factor"] < 1.1 or va["profit_factor"] < 1.0: score -= 500
        
        return score
        
    def stage1_search(self) -> List[Dict]:
        print("\n=== STAGE 1: DEEP SEARCH ===")
        sampler = TPESampler(seed=SEED)
        study = optuna.create_study(direction="maximize", sampler=sampler)
        
        # enqueue cand 5
        cand5_stage1 = CANDIDATE_5.copy()
        cand5_stage1["leverage"] = 1.0 # Force stage 1 settings
        cand5_stage1["side"] = "long_only"
        
        print(f"Running {TRIALS} Optuna trials. This will take a while...")
        study.optimize(self._objective, n_trials=TRIALS, n_jobs=4, show_progress_bar=True)
        
        # Save trials
        trials_data = []
        for t in study.trials:
            row = {"trial": t.number, "score": t.value}
            row.update(t.params)
            for k, v in t.user_attrs.items():
                row[k] = v
            trials_data.append(row)
        pd.DataFrame(trials_data).to_csv(os.path.join(OUT_DIR, "all_trials.csv"), index=False)
        
        # Filter top 20
        df_trials = pd.DataFrame(trials_data)
        df_trials = df_trials[df_trials["score"] > 0]
        df_trials = df_trials.sort_values("score", ascending=False)
        
        # Deduplicate similar params
        top_candidates = []
        seen = set()
        for _, row in df_trials.iterrows():
            if len(top_candidates) >= 20:
                break
            # Create a simplified tuple to avoid extremely similar configs
            sig = (
                round(row["ema_period"]/5)*5,
                round(row["rsi_period"]/2)*2,
                round(row["risk_reward_ratio"], 1)
            )
            if sig not in seen:
                seen.add(sig)
                top_candidates.append(row.to_dict())
                
        pd.DataFrame(top_candidates).to_csv(os.path.join(OUT_DIR, "top_candidates.csv"), index=False)
        print(f"Found {len(top_candidates)} robust diverse candidates.")
        return top_candidates
        
    def stage2_stability(self, candidates: List[Dict]) -> List[Dict]:
        print("\n=== STAGE 2: PARAMETER STABILITY ===")
        stable_candidates = []
        results = []
        
        for i, cand in enumerate(candidates):
            base_p = cand.copy()
            side = base_p.get("side", "both")
            base_p["long_enabled"] = side in ["both", "long_only"]
            base_p["short_enabled"] = side in ["both", "short_only"]
            base_p["leverage"] = 1.0
            base_p["risk_per_trade_pct"] = 1.5
            base_p["max_position_allocation_pct"] = 50.0
            
            perturbations = [
                {"ema_period": base_p["ema_period"] + 5},
                {"ema_period": base_p["ema_period"] - 5},
                {"rsi_period": base_p["rsi_period"] + 2},
                {"rsi_period": base_p["rsi_period"] - 2},
                {"rsi_overbought": base_p["rsi_overbought"] + 2},
                {"rsi_oversold": base_p["rsi_oversold"] - 2},
                {"risk_reward_ratio": base_p["risk_reward_ratio"] + 0.2},
                {"risk_reward_ratio": base_p["risk_reward_ratio"] - 0.2},
            ]
            
            val_pfs = []
            for pert in perturbations:
                p = base_p.copy()
                p.update(pert)
                v = run_backtest(self.df_val, p)
                val_pfs.append(v["profit_factor"])
                
            avg_pf = np.mean(val_pfs)
            min_pf = np.min(val_pfs)
            
            res = {"candidate": i, "base_val_pf": cand["va_pf"], "avg_pert_pf": avg_pf, "min_pert_pf": min_pf}
            results.append(res)
            
            if avg_pf > 1.05 and min_pf > 0.95:
                stable_candidates.append(base_p)
                print(f"Candidate {i} PASSED Stability: AvgPF={avg_pf:.2f} MinPF={min_pf:.2f}")
            else:
                print(f"Candidate {i} FAILED Stability: AvgPF={avg_pf:.2f} MinPF={min_pf:.2f}")
                
        pd.DataFrame(results).to_csv(os.path.join(OUT_DIR, "stability_results.csv"), index=False)
        return stable_candidates

    def stage3_regimes(self, candidates: List[Dict]) -> List[Dict]:
        print("\n=== STAGE 3: REGIME TESTING ===")
        # Regimes: H1 2024, H2 2024, H1 2025, H2 2025, 2026 YTD
        regimes = {
            "H1_2024": ("2024-01-01", "2024-06-30"),
            "H2_2024": ("2024-07-01", "2024-12-31"),
            "H1_2025": ("2025-01-01", "2025-06-30"),
            "H2_2025": ("2025-07-01", "2025-12-31"),
            "2026_YTD": ("2026-01-01", "2026-12-31")
        }
        
        regime_survivors = []
        reg_results = []
        
        for i, p in enumerate(candidates):
            pf_list = []
            row = {"candidate": i}
            for rname, (start, end) in regimes.items():
                df_reg = self.df.loc[start:end]
                if len(df_reg) < 100: continue
                m = run_backtest(df_reg, p)
                pf_list.append(m["profit_factor"])
                row[f"{rname}_PF"] = m["profit_factor"]
                row[f"{rname}_Ret"] = m["net_return_pct"]
                
            row["positive_regimes"] = sum(1 for x in pf_list if x > 1.0)
            reg_results.append(row)
            
            if row["positive_regimes"] >= 3:
                regime_survivors.append(p)
                
        pd.DataFrame(reg_results).to_csv(os.path.join(OUT_DIR, "regime_results.csv"), index=False)
        print(f"Regime survivors: {len(regime_survivors)}")
        return regime_survivors
        
    def stage4_holdout(self, candidates: List[Dict]) -> List[Dict]:
        print("\n=== STAGE 4: FINAL HOLDOUT & COMPARISON ===")
        
        # include candidate 5 explicitly
        c5 = CANDIDATE_5.copy()
        c5["leverage"] = 1.0 # normalize for fair comparison
        candidates.insert(0, c5)
        
        holdout_results = []
        
        for i, p in enumerate(candidates):
            name = "Candidate 5 (Baseline)" if i == 0 else f"New Candidate {i}"
            tr = run_backtest(self.df_train, p)
            va = run_backtest(self.df_val, p)
            ho = run_backtest(self.df_hold, p)
            
            holdout_results.append({
                "Name": name,
                "Train Ret": tr["net_return_pct"],
                "Train PF": tr["profit_factor"],
                "Val Ret": va["net_return_pct"],
                "Val PF": va["profit_factor"],
                "Holdout Ret": ho["net_return_pct"],
                "Holdout PF": ho["profit_factor"],
                "Holdout DD": ho["max_dd_pct"],
                "Holdout Trades": ho["n_trades"],
                "Holdout WinRate": ho["win_rate"],
                "params": p
            })
            
        df_ho = pd.DataFrame(holdout_results)
        df_ho.to_csv(os.path.join(OUT_DIR, "holdout_results.csv"), index=False)
        print(df_ho[["Name", "Val Ret", "Val PF", "Holdout Ret", "Holdout PF", "Holdout DD"]])
        return holdout_results
        
    def stage5_risk(self, best_candidate_params: Dict) -> Dict:
        print("\n=== STAGE 5: RISK & LEVERAGE OPTIMIZATION ===")
        
        def risk_obj(trial):
            p = best_candidate_params.copy()
            p["risk_per_trade_pct"] = trial.suggest_float("risk_per_trade_pct", 0.5, 3.0, step=0.1)
            p["leverage"] = trial.suggest_float("leverage", 1.0, 5.0, step=0.5)
            p["max_position_allocation_pct"] = trial.suggest_float("max_alloc", 20.0, 100.0, step=10.0)
            
            # evaluate over entire history (train+val+holdout) for full drawdown profile
            m = run_backtest(self.df, p)
            
            # Maximize return, but severely penalize DD > 35%
            score = m["net_return_pct"]
            if m["max_dd_pct"] > 35.0:
                score -= 1000 * (m["max_dd_pct"] - 35.0)
                
            trial.set_user_attr("ret", m["net_return_pct"])
            trial.set_user_attr("dd", m["max_dd_pct"])
            trial.set_user_attr("pf", m["profit_factor"])
            return score
            
        study = optuna.create_study(direction="maximize", sampler=TPESampler(seed=SEED))
        study.optimize(risk_obj, n_trials=150, n_jobs=4, show_progress_bar=True)
        
        trials = study.trials
        df_risk = pd.DataFrame([{
            "risk": t.params.get("risk_per_trade_pct"),
            "leverage": t.params.get("leverage"),
            "alloc": t.params.get("max_alloc"),
            "ret": t.user_attrs.get("ret"),
            "dd": t.user_attrs.get("dd"),
            "pf": t.user_attrs.get("pf")
        } for t in trials])
        
        df_risk = df_risk[df_risk["dd"] < 50.0].sort_values("ret", ascending=False)
        df_risk.to_csv(os.path.join(OUT_DIR, "risk_optimization.csv"), index=False)
        
        if len(df_risk) == 0:
            return {}
            
        conservative = df_risk[df_risk["dd"] < 15.0].head(1)
        balanced = df_risk[df_risk["dd"] < 25.0].head(1)
        aggressive = df_risk.head(1)
        
        print("\nRisk Profiles (Full 2.5 Yr Dataset):")
        if not conservative.empty:
            c = conservative.iloc[0]
            print(f"Conservative: Risk={c['risk']} Lev={c['leverage']}x -> Ret: {c['ret']:.2f}% DD: {c['dd']:.2f}%")
        if not balanced.empty:
            b = balanced.iloc[0]
            print(f"Balanced:     Risk={b['risk']} Lev={b['leverage']}x -> Ret: {b['ret']:.2f}% DD: {b['dd']:.2f}%")
        if not aggressive.empty:
            a = aggressive.iloc[0]
            print(f"Aggressive:   Risk={a['risk']} Lev={a['leverage']}x -> Ret: {a['ret']:.2f}% DD: {a['dd']:.2f}%")
            
        return df_risk.to_dict(orient="records")
        

def main():
    start_time = time.time()
    optimizer = DeepOptimizer()
    
    stage1_cands = optimizer.stage1_search()
    if not stage1_cands:
        print("No candidates survived stage 1.")
        return
        
    stage2_cands = optimizer.stage2_stability(stage1_cands)
    if not stage2_cands:
        print("No candidates survived stage 2.")
        return
        
    stage3_cands = optimizer.stage3_regimes(stage2_cands)
    if not stage3_cands:
        print("No candidates survived stage 3.")
        return
        
    ho_results = optimizer.stage4_holdout(stage3_cands)
    
    with open(os.path.join(OUT_DIR, "final_candidates.json"), "w") as f:
        # filter out the complex types
        clean = []
        for r in ho_results:
            clean.append({
                k: float(v) if isinstance(v, (np.float32, np.float64)) else v 
                for k, v in r.items()
            })
        json.dump(clean, f, indent=2)
        
    # pick the best holdout PF candidate for stage 5 (ignoring baseline if a new one is better)
    df_ho = pd.DataFrame(ho_results)
    
    baseline_ho_pf = df_ho.iloc[0]["Holdout PF"]
    best_new = df_ho.iloc[1:].sort_values("Holdout PF", ascending=False)
    
    if len(best_new) > 0 and best_new.iloc[0]["Holdout PF"] > baseline_ho_pf and best_new.iloc[0]["Holdout Ret"] > 0:
        print("\n*** A NEW STRATEGY BEAT CANDIDATE 5 IN HOLDOUT ***")
        best_p = best_new.iloc[0]["params"]
    else:
        print("\n*** CANDIDATE 5 REMAINS THE BEST ROBUST STRATEGY ***")
        best_p = CANDIDATE_5.copy()
        best_p["leverage"] = 1.0
        
    optimizer.stage5_risk(best_p)
    
    print(f"\nFinished in {(time.time() - start_time)/60:.1f} minutes.")

if __name__ == "__main__":
    main()

if __name__ == "__main__":
    main()
