# Warmup Ablation Research Report

## Executive Summary
- **1000-bar warmup justified**: YES
- **Smallest safe universal warmup**: 2000 bars

## Detailed Ablation Results

| Timeframe | Vector | Warmup | EMA-200 Diff (Bar 0) | EMA-200 Diff (Bar 170) | Trades Match | Trade Count | Return % | Max DD % | Divergence |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| 1m | Vector A (Standard V3 200-EMA) | 0 | 1.007893 | 0.184123 | YES | 5 | 0.00% | 0.31% | EMA-200 bar 0 diff = 1.007893 |
| 1m | Vector A (Standard V3 200-EMA) | 170 | 0.318816 | 0.058242 | YES | 5 | 0.00% | 0.31% | EMA-200 bar 0 diff = 0.318816 |
| 1m | Vector A (Standard V3 200-EMA) | 250 | 0.088482 | 0.016164 | YES | 5 | 0.00% | 0.31% | EMA-200 bar 0 diff = 0.088482 |
| 1m | Vector A (Standard V3 200-EMA) | 500 | 0.004218 | 0.000771 | YES | 5 | 0.00% | 0.31% | EMA-200 bar 0 diff = 0.004218 |
| 1m | Vector A (Standard V3 200-EMA) | 750 | 0.001235 | 0.000226 | YES | 5 | 0.00% | 0.31% | EMA-200 bar 0 diff = 0.001235 |
| 1m | Vector A (Standard V3 200-EMA) | 1000 | 0.000126 | 0.000023 | YES | 5 | 0.00% | 0.31% | EMA-200 bar 0 diff = 0.000126 |
| 1m | Vector A (Standard V3 200-EMA) | 2000 | 0.000000 | 0.000000 | YES | 5 | 0.00% | 0.31% | NONE |
| 1m | Vector B (Max Lookback 220-EMA) | 0 | 0.984980 | 0.210010 | YES | 3 | 0.00% | 0.25% | EMA-200 bar 0 diff = 0.984980 |
| 1m | Vector B (Max Lookback 220-EMA) | 170 | 0.402596 | 0.085839 | YES | 3 | 0.00% | 0.25% | EMA-200 bar 0 diff = 0.402596 |
| 1m | Vector B (Max Lookback 220-EMA) | 250 | 0.120999 | 0.025799 | YES | 3 | 0.00% | 0.25% | EMA-200 bar 0 diff = 0.120999 |
| 1m | Vector B (Max Lookback 220-EMA) | 500 | 0.005614 | 0.001197 | YES | 3 | 0.00% | 0.25% | EMA-200 bar 0 diff = 0.005614 |
| 1m | Vector B (Max Lookback 220-EMA) | 750 | 0.002499 | 0.000533 | YES | 3 | 0.00% | 0.25% | EMA-200 bar 0 diff = 0.002499 |
| 1m | Vector B (Max Lookback 220-EMA) | 1000 | 0.000331 | 0.000071 | YES | 3 | 0.00% | 0.25% | EMA-200 bar 0 diff = 0.000331 |
| 1m | Vector B (Max Lookback 220-EMA) | 2000 | 0.000000 | 0.000000 | YES | 3 | 0.00% | 0.25% | NONE |
| 15m | Vector A (Standard V3 200-EMA) | 0 | 36.601169 | 6.686336 | YES | 20 | 0.00% | 1.15% | EMA-200 bar 0 diff = 36.601169 |
| 15m | Vector A (Standard V3 200-EMA) | 170 | 10.287748 | 1.879376 | YES | 20 | 0.00% | 1.15% | EMA-200 bar 0 diff = 10.287748 |
| 15m | Vector A (Standard V3 200-EMA) | 250 | 4.370794 | 0.798461 | YES | 20 | 0.00% | 1.15% | EMA-200 bar 0 diff = 4.370794 |
| 15m | Vector A (Standard V3 200-EMA) | 500 | 0.018650 | 0.003407 | YES | 20 | 0.00% | 1.15% | EMA-200 bar 0 diff = 0.018650 |
| 15m | Vector A (Standard V3 200-EMA) | 750 | 0.002184 | 0.000399 | YES | 20 | 0.00% | 1.15% | EMA-200 bar 0 diff = 0.002184 |
| 15m | Vector A (Standard V3 200-EMA) | 1000 | 0.000300 | 0.000055 | YES | 20 | 0.00% | 1.15% | EMA-200 bar 0 diff = 0.000300 |
| 15m | Vector A (Standard V3 200-EMA) | 2000 | 0.000000 | 0.000000 | YES | 20 | 0.00% | 1.15% | NONE |
| 15m | Vector B (Max Lookback 220-EMA) | 0 | 36.984664 | 7.885599 | YES | 5 | 0.00% | 0.90% | EMA-200 bar 0 diff = 36.984664 |
| 15m | Vector B (Max Lookback 220-EMA) | 170 | 12.881590 | 2.746518 | YES | 5 | 0.00% | 0.90% | EMA-200 bar 0 diff = 12.881590 |
| 15m | Vector B (Max Lookback 220-EMA) | 250 | 5.728388 | 1.221365 | YES | 5 | 0.00% | 0.90% | EMA-200 bar 0 diff = 5.728388 |
| 15m | Vector B (Max Lookback 220-EMA) | 500 | 0.044422 | 0.009471 | YES | 5 | 0.00% | 0.90% | EMA-200 bar 0 diff = 0.044422 |
| 15m | Vector B (Max Lookback 220-EMA) | 750 | 0.005187 | 0.001106 | YES | 5 | 0.00% | 0.90% | EMA-200 bar 0 diff = 0.005187 |
| 15m | Vector B (Max Lookback 220-EMA) | 1000 | 0.000540 | 0.000115 | YES | 5 | 0.00% | 0.90% | EMA-200 bar 0 diff = 0.000540 |
| 15m | Vector B (Max Lookback 220-EMA) | 2000 | 0.000000 | 0.000000 | YES | 5 | 0.00% | 0.90% | NONE |
| 1h | Vector A (Standard V3 200-EMA) | 0 | 90.611059 | 16.552913 | YES | 22 | 0.00% | 6.16% | EMA-200 bar 0 diff = 90.611059 |
| 1h | Vector A (Standard V3 200-EMA) | 170 | 5.821707 | 1.063515 | YES | 22 | 0.00% | 6.16% | EMA-200 bar 0 diff = 5.821707 |
| 1h | Vector A (Standard V3 200-EMA) | 250 | 1.486359 | 0.271530 | YES | 22 | 0.00% | 6.16% | EMA-200 bar 0 diff = 1.486359 |
| 1h | Vector A (Standard V3 200-EMA) | 500 | 0.106087 | 0.019380 | YES | 22 | 0.00% | 6.16% | EMA-200 bar 0 diff = 0.106087 |
| 1h | Vector A (Standard V3 200-EMA) | 750 | 0.015456 | 0.002824 | YES | 22 | 0.00% | 6.16% | EMA-200 bar 0 diff = 0.015456 |
| 1h | Vector A (Standard V3 200-EMA) | 1000 | 0.000457 | 0.000084 | YES | 22 | 0.00% | 6.16% | EMA-200 bar 0 diff = 0.000457 |
| 1h | Vector A (Standard V3 200-EMA) | 2000 | 0.000000 | 0.000000 | YES | 22 | 0.00% | 6.16% | NONE |
| 1h | Vector B (Max Lookback 220-EMA) | 0 | 97.260857 | 20.737246 | NO | 11 | 0.00% | 3.48% | Trade #1 entry 2026-04-29 05:00:00+00:00 vs baseline 2026-04-26 06:00:00+00:00 |
| 1h | Vector B (Max Lookback 220-EMA) | 170 | 6.889982 | 1.469032 | YES | 12 | 0.00% | 3.48% | EMA-200 bar 0 diff = 6.889982 |
| 1h | Vector B (Max Lookback 220-EMA) | 250 | 2.018728 | 0.430418 | NO | 12 | 0.00% | 3.48% | Trade #1 entry 2026-04-24 16:00:00+00:00 vs baseline 2026-04-26 06:00:00+00:00 |
| 1h | Vector B (Max Lookback 220-EMA) | 500 | 0.173819 | 0.037060 | YES | 12 | 0.00% | 3.48% | EMA-200 bar 0 diff = 0.173819 |
| 1h | Vector B (Max Lookback 220-EMA) | 750 | 0.033977 | 0.007244 | YES | 12 | 0.00% | 3.48% | EMA-200 bar 0 diff = 0.033977 |
| 1h | Vector B (Max Lookback 220-EMA) | 1000 | 0.001110 | 0.000237 | YES | 12 | 0.00% | 3.48% | EMA-200 bar 0 diff = 0.001110 |
| 1h | Vector B (Max Lookback 220-EMA) | 2000 | 0.000000 | 0.000000 | YES | 12 | 0.00% | 3.48% | NONE |

## Case-by-Case Smallest Safe Warmup

- `1m | Vector A (Standard V3 200-EMA)`: **2000 bars**
- `1m | Vector B (Max Lookback 220-EMA)`: **2000 bars**
- `15m | Vector A (Standard V3 200-EMA)`: **2000 bars**
- `15m | Vector B (Max Lookback 220-EMA)`: **2000 bars**
- `1h | Vector A (Standard V3 200-EMA)`: **2000 bars**
- `1h | Vector B (Max Lookback 220-EMA)`: **2000 bars**

## Trade-Divergent Configurations (< Baseline 2,000)
- 1h / Vector B (Max Lookback 220-EMA) / W=0 (Trades: 11 vs Baseline 12)
- 1h / Vector B (Max Lookback 220-EMA) / W=250 (Trades: 12 vs Baseline 12)

## Conclusion & Decision Verification
1000-bar warmup justified: YES
smallest safe universal warmup: 2000
any timeframe/config where lower warmup changes trades: 1h / Vector B (Max Lookback 220-EMA) / W=0 (Trades: 11 vs Baseline 12); 1h / Vector B (Max Lookback 220-EMA) / W=250 (Trades: 12 vs Baseline 12)