"""
High-Speed Vectorized Optimizer.
Evaluates strategy hyperparameter configurations in milliseconds directly from trade lists.
"""

import itertools
import sys
import pandas as pd
from config import AppConfig
from indicators import compute_all_indicators
from backtester import DeltaBacktester


def run_grid_optimization(candles_csv_path: str = "data/candles_ETHUSDT_1h.csv"):
    print(f"[*] Loading candle dataset: {candles_csv_path}", flush=True)
    df_raw = pd.read_csv(candles_csv_path)
    df_raw["datetime"] = pd.to_datetime(df_raw["datetime"])
    print(f"[*] Total Candles: {len(df_raw)} ({len(df_raw)/24:.1f} days)", flush=True)

    # Precompute indicators once
    cfg_base = AppConfig()
    df_base = compute_all_indicators(
        df=df_raw,
        ema_period=51,
        rsi_period=14,
        atr_period=14,
        consolidation_candles=8,
        consolidation_atr_mult=2.4,
        swing_lookback=8,
        trend_ema_period=200,
    )

    rr_ratios = [1.2, 1.5, 1.8, 2.0]
    vol_mults = [0.8, 1.0, 1.15]
    be_options = [True, False]
    trend_options = [True, False]
    slope_options = [True, False]

    results = []
    combos = list(itertools.product(rr_ratios, vol_mults, be_options, trend_options, slope_options))
    print(f"[*] Testing {len(combos)} configurations...", flush=True)

    for rr, vol_m, use_be, use_trend, use_slope in combos:
        cfg = AppConfig()
        cfg.strategy.risk_reward_ratio = rr
        cfg.strategy.volume_mult = vol_m
        cfg.strategy.use_breakeven_at_1r = use_be
        cfg.strategy.use_trend_filter = use_trend
        cfg.strategy.use_ema_slope_filter = use_slope

        backtester = DeltaBacktester(cfg)
        bt_res = backtester.run(df_base)
        trades = bt_res["trades"]

        if len(trades) >= 8:
            pnls = [t.net_pnl for t in trades]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]
            win_rate = (len(wins) / len(trades)) * 100.0 if trades else 0.0
            gross_win = sum(wins)
            gross_loss = abs(sum(losses))
            pf = (gross_win / gross_loss) if gross_loss > 0 else (99.0 if gross_win > 0 else 0.0)
            net_profit = sum(pnls)
            net_return = (net_profit / cfg.risk.initial_capital) * 100.0
            
            # Simple max drawdown on trade equity
            equities = [cfg.risk.initial_capital] + [t.equity_after for t in trades]
            peak = equities[0]
            max_dd = 0.0
            for eq in equities:
                if eq > peak:
                    peak = eq
                dd = (peak - eq) / peak * 100.0
                if dd > max_dd:
                    max_dd = dd

            results.append({
                "RR": rr,
                "VolMult": vol_m,
                "Breakeven": use_be,
                "TrendFilter": use_trend,
                "SlopeFilter": use_slope,
                "Trades": len(trades),
                "WinRate": round(win_rate, 1),
                "ProfitFactor": round(pf, 2),
                "NetProfit$": round(net_profit, 2),
                "NetReturn%": round(net_return, 2),
                "MaxDD%": round(max_dd, 2),
            })

    res_df = pd.DataFrame(results)
    if not res_df.empty:
        sorted_df = res_df.sort_values("NetProfit$", ascending=False).reset_index(drop=True)
        print("\n" + "=" * 90, flush=True)
        print("                   TOP 10 STRATEGY CONFIGURATIONS (1-YEAR DATA)", flush=True)
        print("=" * 90, flush=True)
        print(sorted_df.head(10).to_string(index=False), flush=True)
        print("=" * 90 + "\n", flush=True)
        sorted_df.to_csv("output/optimization_results.csv", index=False)
        return sorted_df
    return res_df


if __name__ == "__main__":
    run_grid_optimization()
