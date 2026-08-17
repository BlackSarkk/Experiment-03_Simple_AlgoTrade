# BENCHMARK COMPARISON — historical DEV record

Scenario-4 DEV window 2024-07-16 .. 2026-07-15 (TRAIN 70% / VALID 30%). Historical figures read from the regenerated ledger — not a new run, not forward performance, and not a ranking.

`gross profit` = sum of winning trades' PnL · `gross loss` = |sum of losing trades' PnL| · `net P&L` = gross profit − gross loss. All three are **already after** 0.05% commission and 1-tick adverse slippage; "gross" means only that wins and losses are not offset. `fees` are shown separately and are already inside gross profit and gross loss. $10,000 start per partition.

## 1. Five Bollinger-OFF candidates — TRAIN and VALID

| trial | role | part | return % | PF | max DD % | trades | gross profit | gross loss | net P&L | fees |
|---|---|---|---|---|---|---|---|---|---|---|
| **285** | C158 benchmark | TRAIN | +193.01 | 1.336 | 32.72 | 152 | $76,726.02 | −$57,424.77 | $19,301.25 | $5,775.83 |
| **285** |  | VALID | +17.30 | 1.157 | 29.68 | 59 | $12,783.22 | −$11,052.86 | $1,730.36 | $1,139.59 |
| 189 | primary challenger | TRAIN | +99.16 | 1.383 | 24.64 | 110 | $35,795.17 | −$25,879.51 | $9,915.66 | $2,568.55 |
| 189 |  | VALID | +51.25 | 1.615 | 18.68 | 44 | $13,453.86 | −$8,328.39 | $5,125.47 | $908.34 |
| 156 | low-DD alternate | TRAIN | +29.56 | 1.220 | 27.63 | 109 | $16,393.81 | −$13,438.31 | $2,955.50 | $1,381.76 |
| 156 |  | VALID | +19.91 | 1.385 | 13.99 | 42 | $7,160.54 | −$5,169.44 | $1,991.10 | $632.52 |
| 125 | risk-boundary | TRAIN | +13.35 | 1.069 | 28.89 | 148 | $20,653.75 | −$19,319.24 | $1,334.51 | $1,758.69 |
| 125 |  | VALID | +20.72 | 1.302 | 16.00 | 56 | $8,943.43 | −$6,871.25 | $2,072.18 | $829.62 |
| 52 | defensive / high-sample | TRAIN | +1.19 | 1.013 | 8.38 | 183 | $9,301.26 | −$9,182.06 | $119.20 | $1,119.06 |
| 52 |  | VALID | +12.67 | 1.286 | 7.12 | 89 | $5,702.94 | −$4,435.73 | $1,267.21 | $606.44 |

## 2. Candidate #158 — Bollinger OFF vs ON, full DEV

| measure | filter OFF | filter ON | change |
|---|---|---|---|
| return % | +274.35 | +352.17 | +77.82 pts |
| profit factor | 1.296 | 1.544 | +0.248 |
| max drawdown % | 32.72 | 35.12 | +2.40 pts |
| trades | 212 | 155 | −57 (−26.9%) |
| wins / losses | 64 / 148 | 52 / 103 | — |
| gross profit | $120,133 | $99,948 | −16.8% |
| gross loss | −$92,698 | −$64,731 | −30.2% |
| net P&L | $27,435 | $35,217 | +$7,782 |
| fees | $9,496 | $7,014 | −26.1% |

## 3. Conclusion

1. **Highest historical DEV return — trial 285 (Candidate #158):** TRAIN +193.01% and full-DEV +274.35% unfiltered, roughly double the next candidate, but bought with the deepest drawdowns (TRAIN 32.72%, VALID 29.68%) and the weakest VALID profit factor of the five (1.157).
2. **Strongest VALID challenger — trial 189:** VALID +51.25% at PF 1.615 and 18.68% drawdown versus #158's +17.30% / 1.157 / 29.68%, on comparable TRAIN evidence (+99.16%, PF 1.383) — the same signal shape at leverage 3.0 instead of 4.0.
3. **Lowest drawdown and largest sample — trial 52:** VALID drawdown 7.12% and TRAIN 8.38%, roughly a quarter of #158's, with 183 TRAIN / 89 VALID trades — but it barely profits on TRAIN (+1.19%, PF 1.013) and pays $1,119 of fees to net $119 there. Trial 156 is the middle option: VALID drawdown 13.99% at PF 1.385.
