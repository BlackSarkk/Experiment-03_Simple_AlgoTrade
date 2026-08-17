# MANIFEST — frozen challenger pack (Phase 5)

Five fixed benchmark configs recovered from the regenerated Scenario-4 ledger. Quarantine only: nothing here is referenced by `pipeline.sh`, `src/main.py`, `src/auto_optimise/` or any test, and no Pine file was generated.

## File hashes

| file | bytes | sha256 |
|---|---|---|
| `trial285_candidate158_benchmark.json` | 2745 | `2b5527435f23fc6e354896b816ecb6fb783b862ad6de7c44745301ddcd41f0cd` |
| `trial189_primary_challenger.json` | 2790 | `72e49651583db52dece8c4e75cbe47c1633ba1bea024d254f97b72785956cff9` |
| `trial156_low_dd_alternate.json` | 2760 | `816426cc3f8fe52ab7318c6e51fafb0b786e38eda75f461a28b24e9fe2441d43` |
| `trial125_risk_boundary_hypothesis.json` | 2786 | `c82b950f34e68681a4211d9a5465eff2adaf14ba0e98b6cb79b0c6ff30546908` |
| `trial52_defensive_high_sample.json` | 2730 | `71152a564683bac90b13460589febed9dab56b69fbd6e4d664ce8188275ea3a1` |
| `trial285_candidate158_bollinger_on_shadow.json` (shadow, **not** in the ranking pack) | 5428 | `b5055d438d5a42c82fa841c648ea4ef8144a7486fc782f4c917ef49a7f8150ec` |

Source ledger `stage/results/campaign_2y_15m/scenario4/strategy_trials.csv` sha256 `863893f71e0d55f9d24f3d9409cb6576f19ccef8cf7373b23a595360876d3b58` (300 rows).
Bollinger ledger sha256 `f9c49c06dc239c687e72e46184e5020dbd7fde654750f577e9db7a711077b895` (150 rows) — **not applied to any file here**.

## Sixth file — C158 + Bollinger shadow benchmark (Phase 5.1)

`trial285_candidate158_bollinger_on_shadow.json` is **not a sixth candidate**. It is the deployed
historical system — Candidate #158 with its campaign-optimised Bollinger filter switched ON — kept
alongside the pack so the raw-strategy comparison can be read next to what was actually shipped.

* **The original five remain the only fair Bollinger-OFF ranking pack.** All five run the filter
  disabled, so they differ only in the 11 strategy + 3 risk values.
* **This sixth config is a shadow / deployed benchmark only.**
* **It is not eligible to win the five-way raw-strategy ranking**, and must never be entered into it.
  A filtered system trades a different, smaller signal set; ranking it against unfiltered systems
  would compare a strategy against a strategy-plus-filter.
* **It is observed alongside the five from 2026-08-18 00:00 UTC**, under identical feed, fees,
  slippage, sizing and execution assumptions, from its own independent $10,000 paper account.
* **It may be compared descriptively with the five** — "filter ON retained X% of profit and cut
  gross loss by Y%" — **but not used to rank the raw strategies.**

**Executable difference from `trial285_candidate158_benchmark.json`: the six Bollinger values plus
`enabled`, and nothing else.** 70 leaf fields compared, 13 differ — 7 executable (all inside
`filters.bollinger`, 0 outside it) and 6 descriptive-metadata fields. `strategy`, `risk` and
`execution` are field-for-field identical to trial 285 OFF, and all source-ledger provenance
(`_scenario4_trial_id`, `_source_ledger`, `_source_ledger_sha256`, `_campaign`, `_dev_window`,
`_scenario4_score`, `_region`, `_risk_policy`) is unchanged.

Bollinger values `10 / 2.3 / 0.2 / 10 / 0.9500000000000001 / 0.15` — **bit-identical** to the
deployed `configs/config/config1-ETHUSDTP15m-long.json`, including the `expansion_min_ratio` float
artifact of Optuna's `0.0 + 19 x 0.05` step grid. They come from the campaign's own Bollinger stage
(150 trials, TPE seed 42, winning trial 66, score 0.5727).

**Descriptive metadata intentionally differs** so the file states its own role accurately rather
than inheriting trial 285's wording. Corrected fields: `_name`, `_role`, `_why_frozen`, `_status`,
`_notes`, plus a new `_bollinger` field. They now record that this is Candidate #158 / Scenario-4
trial 285, that Bollinger is ENABLED with the six historically optimised parameters, that the file
is a shadow / deployed benchmark only, and that it is excluded from the five-way unfiltered ranking.
`_notes` also records that the retained `_dev_train` / `_dev_valid` blocks are the FILTER-OFF ledger
metrics for trial 285, kept as provenance, and are not this configuration's own results.

### Shadow historical benchmark scores — DEV only, filter OFF vs ON

Historical Scenario-4 DEV reference (2024-07-16 .. 2026-07-15). **Not a new run and not forward
performance.** Recorded in the shadow config as the inert `_historical_benchmark_reference` block.
Strategy = Scenario-4 trial 285; Bollinger = Bollinger-stage trial 66, score 0.5727.
Gross profit / gross loss are sums of winning / losing trades' PnL, already after 0.05% commission
and 1-tick adverse slippage; net P&L is their difference. Fees are shown separately and are already
inside gross profit and gross loss.

| measure | filter OFF | filter ON | change |
|---|---|---|---|
| return % | 274.3 (274.35 exact) | 352.2 (352.17 exact) | +77.8 pts |
| profit factor | 1.296 | 1.544 | +0.248 |
| max drawdown % | 32.72 | 35.12 | +2.40 pts |
| gross profit | $120,133 | $99,948 | −16.8% |
| gross loss | −$92,698 | −$64,731 | −30.2% |
| net P&L | $27,435 | $35,217 | +$7,782 |
| fees paid | $9,496 | $7,014 | −26.1% |
| trades | 212 | **155** | −57 |
| wins / losses | 64 / 148 | 52 / 103 | — |

The filtered trade count **155** was **recovered directly**, not inferred: the historical campaign
log printed `DEV ON ret 352.17% ... n 155 W 52/L 103` (session `08facafa`, 2026-08-16T10:38:20.462Z).
The filter's effect on DEV was to cut gross loss 30.2% while giving up 16.8% of gross profit —
raising PF and net P&L at the cost of a slightly deeper drawdown and 27% fewer trades.

These figures rank nothing. They are the deployed system's historical DEV record, retained so the
forward comparison can be read against it.

## Exact parameter table

| trial | role | region | ema | rsi | ob | os | atr | cons | cmult | swing | vsma | vmult | rr | leverage | risk/trade % | max alloc % | long | short | Bollinger |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **285** | HISTORICAL BENCHMARK — Candidate #158 | A0 | 104 | 20 | 64 | 23 | 7 | 7 | 2.8 | 17 | 12 | 1.8 | 3.6 | 4.0x | 2.6 | 70 | true | false | DISABLED |
| **189** | primary generalisation challenger | A1 | 122 | 20 | 63 | 24 | 9 | 8 | 2.9 | 16 | 10 | 1.9 | 3.9 | 3.0x | 2.5 | 70 | true | false | DISABLED |
| **156** | low-drawdown alternate | B | 145 | 17 | 73 | 27 | 13 | 9 | 2.5 | 19 | 31 | 1.5 | 4.0 | 3.0x | 1.5 | 75 | true | false | DISABLED |
| **125** | risk-boundary hypothesis | B-risk-bound | 150 | 13 | 79 | 31 | 13 | 8 | 2.3 | 19 | 25 | 0.8 | 4.0 | 5.0x | 1.6 | 40 | true | false | DISABLED |
| **52** | defensive / high-sample hypothesis | C | 124 | 19 | 70 | 42 | 19 | 6 | 3.6 | 13 | 48 | 1.7 | 1.9 | 3.0x | 0.8 | 35 | true | false | DISABLED |

Execution, identical in all five: `commission_pct 0.05`, `slippage_ticks 1`, `tick_size 0.01`, `sizing_mode RISK_BASED`, `initial_capital 10000.0`, `quantity_step 0.001`. `filters.bollinger.enabled = false` with neutral values in every file.

## DEV results — net profit, net loss and net P&L separately, after all fees and slippage

Every figure is a sum of per-trade **net** PnL (entry/exit commission at 0.05% and 1-tick adverse slippage already deducted inside each trade). `net profit` = sum of winning trades, `net loss` = sum of losing trades, `net P&L` = their difference. `fees paid` is reported separately for visibility and is already inside the three columns. Starting equity $10,000 per partition.

| trial | partition | gross profit | gross loss | net P&L | fees paid | return % | PF | max DD % | trades | W/L |
|---|---|---|---|---|---|---|---|---|---|---|
| 285 | TRAIN | $76,726.02 | −$57,424.77 | $19,301.25 | $5,775.83 | +193.01 | 1.336 | 32.72 | 152 | 47/105 |
| 285 | VALID | $12,783.22 | −$11,052.86 | $1,730.36 | $1,139.59 | +17.30 | 1.157 | 29.68 | 59 | 16/43 |
| 189 | TRAIN | $35,795.17 | −$25,879.51 | $9,915.66 | $2,568.55 | +99.16 | 1.383 | 24.64 | 110 | 31/79 |
| 189 | VALID | $13,453.86 | −$8,328.39 | $5,125.47 | $908.34 | +51.25 | 1.615 | 18.68 | 44 | 15/29 |
| 156 | TRAIN | $16,393.81 | −$13,438.31 | $2,955.50 | $1,381.76 | +29.56 | 1.220 | 27.63 | 109 | 29/80 |
| 156 | VALID | $7,160.54 | −$5,169.44 | $1,991.10 | $632.52 | +19.91 | 1.385 | 13.99 | 42 | 12/30 |
| 125 | TRAIN | $20,653.75 | −$19,319.24 | $1,334.51 | $1,758.69 | +13.35 | 1.069 | 28.89 | 148 | 35/113 |
| 125 | VALID | $8,943.43 | −$6,871.25 | $2,072.18 | $829.62 | +20.72 | 1.302 | 16.00 | 56 | 16/40 |
| 52 | TRAIN | $9,301.26 | −$9,182.06 | $119.20 | $1,119.06 | +1.19 | 1.013 | 8.38 | 183 | 68/115 |
| 52 | VALID | $5,702.94 | −$4,435.73 | $1,267.21 | $606.44 | +12.67 | 1.286 | 7.12 | 89 | 39/50 |

Each `net P&L` reconciles with the engine's balance-derived net PnL to within $0.05 (rounding).

## Status statements

**None of these five is a new winner, and the shadow is not a candidate at all.** All five were selected from DEV-window evidence
(2024-07-16 .. 2026-07-15) that has already been optimised against. Trials 189, 156, 125 and 52
are *hypotheses awaiting an out-of-sample test*, nothing more. No claim is made that any of them
beats trial 285.

**Trial 285 / Candidate #158 is the historical benchmark only.** It is frozen here to be measured
against, not to be defended or promoted. Its DEV rank-1 came from TRAIN return 193.0% under an
objective weighted 0.70 TRAIN / 0.30 VALID; it has the weakest VALID profit factor (1.157) and the
deepest VALID drawdown (29.7%) of the DEV top ten, and a #158-adjacent vector failed the historical
Stage-3 neighbourhood test at 8.6% acceptance. Its production copy
`configs/config/config1-ETHUSDTP15m-long.json` is untouched by this pack.

## Data-use history through 2026-08-17

| window | used for | when |
|---|---|---|
| 2022-01-01 .. 2024-10-09 (Phase-3A TRAIN) | Stage-1 400 trials, Stage-2 800 trials, Stage-3 neighbourhood, 200-trial risk search | 2026-08-16 |
| 2024-10-09 .. 2025-09-11 (Phase-3A VALID) | Stage-2 scoring, Stage-3, risk search | 2026-08-16 |
| 2025-09-11 .. 2026-08-15 (Phase-3A holdout) | never read by Phase-3A | — |
| 2024-07-16 .. 2025-12-08 (campaign TRAIN) | scenario 1-4 strategy+risk search; **re-searched** in the Phase-B reproduction | 2026-08-16, 2026-08-17 |
| 2025-12-09 .. 2026-07-15 (campaign VALID) | scenario 1-4 scoring; Bollinger stage; **re-searched** in the Phase-B reproduction; Phase-4 challenger mining | 2026-08-16, 2026-08-17 |
| 2026-07-16 .. 2026-08-15 | opened once by the historical campaign for confirmation, and by later auto-optimise stage-6 benchmarks. **Locked for this project since; not fetched, loaded or evaluated in Phases B, 4 or 5.** | 2026-08-16 |
| 2026-08-16 .. 2026-08-17 | not used | — |

**No historical period may be used again to select among these five.** Every window through
2026-08-15 has now informed selection at least once, and 2024-07-16 .. 2026-07-15 has been searched
twice. Any further backtest over those dates can only confirm what the ledger already reports; it
cannot break the tie between these candidates. Discrimination must come from data that did not exist
when they were chosen — see `FORWARD_PROTOCOL.md`.
