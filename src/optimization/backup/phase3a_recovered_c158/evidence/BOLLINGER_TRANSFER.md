# FIXED BOLLINGER TRANSFER TEST — historical DEV

Every frozen candidate run OFF and ON with the **identical** historically optimised C158 filter (`10 / 2.3 / 0.2 / 10 / 0.9500000000000001 / 0.15`). Filter-transfer test only: no Bollinger tuning, no parameter search, no winner selection.

DEV 2024-07-16 00:00 .. 2026-07-15 23:45 UTC · TRAIN 49,056 rows / VALID 21,024 rows (historical 70/30 split) · 0 rows at or after 2026-07-16 loaded · all 10 OFF runs reconcile to the Scenario-4 ledger to <1e-4 %% return and exact trade counts.

`gross profit` = sum of winning trades' PnL · `gross loss` = |sum of losing trades' PnL| · `net P&L` = their difference — all already after 0.05%% commission and 1-tick adverse slippage (naming per Phase 5.4). `blocked` = signals removed by the filter.

| trial | role | part | BB | return % | PF | max DD % | trades | gross profit | gross loss | net P&L | fees | blocked |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 285 | C158 benchmark | TRAIN | OFF | +193.01 | 1.336 | 32.72 | 152 | $76,726.02 | −$57,424.77 | $19,301.25 | $5,775.83 | 0 |
| 285 |  | TRAIN | ON | +194.33 | 1.534 | 35.12 | 109 | $55,844.33 | −$36,411.29 | $19,433.04 | $3,639.18 | 196 |
| 285 |  | VALID | OFF | +17.30 | 1.157 | 29.68 | 59 | $12,783.22 | −$11,052.86 | $1,730.36 | $1,139.59 | 0 |
| 285 |  | VALID | ON | +41.06 | 1.465 | 16.33 | 45 | $12,940.35 | −$8,834.42 | $4,105.93 | $1,026.57 | 76 |
| 189 | primary challenger | TRAIN | OFF | +99.16 | 1.383 | 24.64 | 110 | $35,795.17 | −$25,879.51 | $9,915.66 | $2,568.55 | 0 |
| 189 |  | TRAIN | ON | +49.30 | 1.389 | 26.09 | 69 | $17,584.35 | −$12,654.79 | $4,929.56 | $1,195.81 | 163 |
| 189 |  | VALID | OFF | +51.25 | 1.615 | 18.68 | 44 | $13,453.86 | −$8,328.39 | $5,125.47 | $908.34 | 0 |
| 189 |  | VALID | ON | +76.92 | 2.218 | 13.05 | 33 | $14,007.99 | −$6,315.99 | $7,692.00 | $797.23 | 58 |
| 156 | low-DD alternate | TRAIN | OFF | +29.56 | 1.220 | 27.63 | 109 | $16,393.81 | −$13,438.31 | $2,955.50 | $1,381.76 | 0 |
| 156 |  | TRAIN | ON | +0.05 | 1.001 | 25.59 | 76 | $8,493.12 | −$8,487.63 | $5.49 | $798.17 | 134 |
| 156 |  | VALID | OFF | +19.91 | 1.385 | 13.99 | 42 | $7,160.54 | −$5,169.44 | $1,991.10 | $632.52 | 0 |
| 156 |  | VALID | ON | +19.65 | 1.513 | 13.08 | 33 | $5,797.79 | −$3,832.49 | $1,965.30 | $444.58 | 50 |
| 125 | risk-boundary | TRAIN | OFF | +13.35 | 1.069 | 28.89 | 148 | $20,653.75 | −$19,319.24 | $1,334.51 | $1,758.69 | 0 |
| 125 |  | TRAIN | ON | +2.71 | 1.021 | 20.28 | 102 | $13,379.54 | −$13,108.08 | $271.46 | $1,267.93 | 444 |
| 125 |  | VALID | OFF | +20.72 | 1.302 | 16.00 | 56 | $8,943.43 | −$6,871.25 | $2,072.18 | $829.62 | 0 |
| 125 |  | VALID | ON | +19.35 | 1.412 | 13.82 | 38 | $6,632.57 | −$4,698.05 | $1,934.52 | $536.96 | 131 |
| 52 | defensive / high-sample | TRAIN | OFF | +1.19 | 1.013 | 8.38 | 183 | $9,301.26 | −$9,182.06 | $119.20 | $1,119.06 | 0 |
| 52 |  | TRAIN | ON | -5.04 | 0.927 | 12.04 | 137 | $6,435.16 | −$6,938.91 | $-503.75 | $783.60 | 168 |
| 52 |  | VALID | OFF | +12.67 | 1.286 | 7.12 | 89 | $5,702.94 | −$4,435.73 | $1,267.21 | $606.44 | 0 |
| 52 |  | VALID | ON | +14.54 | 1.421 | 7.96 | 71 | $4,908.51 | −$3,454.67 | $1,453.84 | $480.78 | 54 |

## ON minus OFF

| trial | part | Δ return % | Δ PF | Δ max DD % | Δ trades | Δ net P&L |
|---|---|---|---|---|---|---|
| 285 | TRAIN | +1.32 | +0.198 | +2.39 | -43 | $+131.79 |
| 285 | VALID | +23.76 | +0.308 | -13.35 | -14 | $+2,375.57 |
| 189 | TRAIN | -49.86 | +0.006 | +1.45 | -41 | $-4,986.10 |
| 189 | VALID | +25.67 | +0.603 | -5.63 | -11 | $+2,566.53 |
| 156 | TRAIN | -29.50 | -0.219 | -2.03 | -33 | $-2,950.01 |
| 156 | VALID | -0.26 | +0.128 | -0.92 | -9 | $-25.80 |
| 125 | TRAIN | -10.63 | -0.048 | -8.61 | -46 | $-1,063.05 |
| 125 | VALID | -1.38 | +0.110 | -2.18 | -18 | $-137.66 |
| 52 | TRAIN | -6.23 | -0.086 | +3.66 | -46 | $-622.95 |
| 52 | VALID | +1.87 | +0.135 | +0.84 | -18 | $+186.63 |

## Findings — no winner named

**Does the fixed C158 filter transfer positively?**

| trial | TRAIN | VALID | verdict |
|---|---|---|---|
| 285 | positive (net P&L +$132, PF +0.198) | strongly positive (+$2,376, PF +0.308, DD −13.35 pts) | **transfers positively on both** |
| 189 | negative (−$4,986, return −49.86 pts) | strongly positive (+$2,567, PF +0.603, DD −5.63 pts) | **mixed** |
| 156 | strongly negative (−$2,950, cut to +0.05% / PF 1.001) | flat (−$26, PF +0.128) | **does not transfer** |
| 125 | negative (−$1,063, PF −0.048) | slightly negative (−$138, PF +0.110) | **does not transfer** |
| 52  | negative — turns loss-making (−$624, return −5.04%, PF 0.927) | slightly positive (+$187, PF +0.135) | **does not transfer** |

**Strongest VALID Bollinger-ON result: trial 189** — +76.92% return, PF 2.218, max DD 13.05%,
33 trades, net P&L $7,692. Trial 285 ON is second on VALID (+41.06%, PF 1.465, DD 16.33%, 45 trades).

**Results weaker than their OFF version:** on TRAIN, four of five are weaker (189, 156, 125, 52);
trial 52 ON is the only case that becomes outright unprofitable (−5.04%, PF 0.927, net P&L −$503.75)
and it is also the only candidate whose VALID drawdown worsens (+0.84 pts). On VALID, 156 and 125 are
marginally weaker in net P&L (−$26, −$138) despite improved PF and drawdown. Profit factor improves
on VALID for all five; trade counts fall 25-35% everywhere.

**Interpretation limit.** This filter was optimised on trial 285 over this same DEV span, so 285's
two-sided gain is in-sample for the filter while the other four are genuine out-of-candidate
transfers — the comparison is not symmetric. More importantly, this is **reused historical DEV data
that has already been searched twice**. It cannot select a final champion; it only characterises how a
fixed filter behaves when moved between candidates. Discrimination still requires the forward window.
