# 15m BAKEOFF — recovered Scenario-4 recipe (unseeded) vs New Optimizer V2

Reused-historical-DEV **architecture** bakeoff. **Not a final winner claim.** DEV 2024-07-16 00:00 → 2026-07-15 23:45 UTC, 1,000-candle warmup, chronological 70/30 split (boundary 2025-12-09 00:00), TRAIN 49,056 / VALID 21,024 rows. Zero rows at or after 2026-07-16 exist in either dataset. Both arms: 300 strategy+risk trials then 150 Bollinger trials, TPE seed 42, n_jobs=1, unseeded, long-only, identical 14-dim space, identical production engine / RiskManager / fees 0.05% / slippage 1 tick / tick 0.01 / qty step 0.001.

Evaluator parity verified in preflight: V2's full-frame-then-slice evaluator reproduces the recovered `run()` exactly (TRAIN 152 trades / +193.012381%, VALID 59 / +17.303842%), so the arms differ only in search and selection discipline.

`gross profit` = sum of winning trades' PnL · `gross loss` = |sum of losing trades' PnL| · `net P&L` = difference. All after fees and slippage.

## Selected winners

| optimizer | symbol | trial | score | eligible | strategy (ema/rsi/ob/os/atr/cons@mult/swing/vsma@mult/rr) | risk (lev/risk/alloc) | Bollinger (len/std/minbw/lb/ratio/mid) | boll trial | runtime |
|---|---|---|---|---|---|---|---|---|---|---|
| Recovered recipe (unseeded) | ETHUSDT | **279** | 1.5884 | 260/300 | 49/12/66/24/8/12@3.2/18/29@1.4/2.7 | 4.0x / 2.6% / 65% | 45/3.0/0.3/8/0.50/0.02 | 147 | 245s |
| New Optimizer V2 | ETHUSDT | **267** | 0.5097 | 129/300 | 50/15/62/34/19/11@3.5/10/12@1.7/2.6 | 3.5x / 3.0% / 45% | 50/2.4/0.5/12/0.70/0.04 | 95 | 240s |
| Recovered recipe (unseeded) | BTCUSDT | **297** | 0.4931 | 272/300 | 120/20/58/26/7/6@3.2/13/49@1.2/2.5 | 3.0x / 2.7% / 50% | 19/1.5/0.7/13/0.45/0.11 | 139 | 243s |
| New Optimizer V2 | BTCUSDT | **290** | 0.1231 | 15/300 | 62/13/73/32/16/12@4.0/10/30@1.2/3.5 | 1.0x / 1.6% / 50% | 11/2.3/0.1/10/1.25/0.29 | 134 | 239s |

## TRAIN and VALID (Bollinger OFF, as searched)

| optimizer | symbol | part | return % | PF | DD % | trades | gross profit | gross loss | net P&L | fees |
|---|---|---|---|---|---|---|---|---|---|---|
| Recovered recipe (unseeded) | ETHUSDT | TRAIN | +175.07 | 1.269 | 28.59 | 212 | $82,476 | −$64,969 | $17,507 | $7,910 |
| Recovered recipe (unseeded) | ETHUSDT | VALID | +81.91 | 1.409 | 20.30 | 93 | $28,201 | −$20,010 | $8,191 | $2,687 |
| New Optimizer V2 | ETHUSDT | TRAIN | +59.32 | 1.122 | 28.64 | 234 | $54,572 | −$48,640 | $5,932 | $5,520 |
| New Optimizer V2 | ETHUSDT | VALID | +66.59 | 1.511 | 15.22 | 107 | $19,685 | −$13,026 | $6,659 | $2,050 |
| Recovered recipe (unseeded) | BTCUSDT | TRAIN | +67.72 | 1.269 | 21.30 | 211 | $31,945 | −$25,174 | $6,772 | $4,492 |
| Recovered recipe (unseeded) | BTCUSDT | VALID | +12.37 | 1.143 | 22.14 | 76 | $9,902 | −$8,664 | $1,237 | $1,219 |
| New Optimizer V2 | BTCUSDT | TRAIN | +10.86 | 1.122 | 16.36 | 219 | $9,953 | −$8,867 | $1,086 | $1,250 |
| New Optimizer V2 | BTCUSDT | VALID | +11.38 | 1.256 | 13.14 | 110 | $5,580 | −$4,442 | $1,138 | $561 |

## Full DEV — Bollinger OFF vs ON for the selected winner

| optimizer | symbol | BB | return % | PF | DD % | trades | gross profit | gross loss | net P&L | fees |
|---|---|---|---|---|---|---|---|---|---|---|
| Recovered recipe (unseeded) | ETHUSDT | OFF | +400.38 | 1.334 | 28.59 | 305 | $160,050 | −$120,012 | $40,038 | $15,303 |
| Recovered recipe (unseeded) | ETHUSDT | ON | +538.51 | 1.503 | 30.82 | 267 | $160,829 | −$106,977 | $53,851 | $14,295 |
| New Optimizer V2 | ETHUSDT | OFF | +165.40 | 1.238 | 31.34 | 341 | $85,934 | −$69,394 | $16,540 | $8,786 |
| New Optimizer V2 | ETHUSDT | ON | +100.66 | 1.373 | 22.72 | 192 | $37,048 | −$26,982 | $10,066 | $3,751 |
| Recovered recipe (unseeded) | BTCUSDT | OFF | +98.02 | 1.242 | 22.15 | 287 | $50,256 | −$40,454 | $9,802 | $6,644 |
| Recovered recipe (unseeded) | BTCUSDT | ON | +190.98 | 1.706 | 14.46 | 150 | $46,157 | −$27,059 | $19,098 | $4,032 |
| New Optimizer V2 | BTCUSDT | OFF | +25.01 | 1.181 | 19.25 | 330 | $16,352 | −$13,851 | $2,501 | $1,885 |
| New Optimizer V2 | BTCUSDT | ON | +34.80 | 1.491 | 10.62 | 150 | $10,572 | −$7,091 | $3,480 | $858 |

## ON minus OFF, full DEV

| optimizer | symbol | Δ return % | Δ PF | Δ DD % | Δ trades | Δ net P&L |
|---|---|---|---|---|---|---|
| Recovered recipe (unseeded) | ETHUSDT | +138.13 | +0.170 | +2.23 | -38 | $+13,813 |
| New Optimizer V2 | ETHUSDT | -64.75 | +0.135 | -8.61 | -149 | $-6,475 |
| Recovered recipe (unseeded) | BTCUSDT | +92.96 | +0.463 | -7.69 | -137 | $+9,296 |
| New Optimizer V2 | BTCUSDT | +9.80 | +0.310 | -8.63 | -180 | $+980 |
