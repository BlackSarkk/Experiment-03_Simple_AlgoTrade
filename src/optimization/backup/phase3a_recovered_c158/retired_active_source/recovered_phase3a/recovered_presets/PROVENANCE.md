# Recovered campaign presets — generator-exact, NOT recovered file blobs

`config4_candidate158_balanced.json` was never created by a `Write` tool call, so no file
body exists in the transcripts. It was produced by two `Bash` generator scripts, and
**both scripts are recovered verbatim**. The JSON here is the output of re-executing them.

Distinguish carefully:

* **PROVEN byte-for-byte** — the *generator source* (both heredocs, recovered verbatim).
* **PROVEN parameter values** — all 11 strategy + 3 risk values, independently confirmed
  at the time against `stage2_15m_long_trials.csv#53` and `risk_search_t53.csv#158` by a
  verification script whose output was `DISCREPANCIES: NONE`.
* **GENERATOR-EXACT, not blob-verified** — the byte layout below. `json.dump(..., indent=2)`
  with the key order the generator built is deterministic, so these bytes should equal the
  historical file, but no historical checksum of the file survives to confirm it.

## Chronology

| time (UTC) | event |
|---|---|
| 2026-08-16T09:18:29.665Z | generator 1 writes `configs/candidates/balanced_new_optimizer.json` (no `filters` block) |
| 2026-08-16T09:29:18.501Z | generator 2 copies it to `configs/config4.json`, injects a **neutral** `filters.bollinger` block, deletes `configs/candidates/` |
| 2026-08-16T09:30:52.062Z | `src/filters/stage_1_bollinger/optimize.py` written (pre-campaign Bollinger optimizer, 10424 B — recoverable, not yet extracted) |
| 2026-08-16T09:54:25.954Z | verification prints `DISCREPANCIES: NONE` and `bollinger preserved: {length 22, std 1.8, min_bandwidth_pct 0.7, expansion_lookback 13, expansion_min_ratio 0.55, min_mid_distance 0.01}` — i.e. the Stage-1 Bollinger optimizer had by then overwritten the neutral block |
| 2026-08-16T09:54:34.762Z | renamed `configs/config4.json` → `configs/config4_candidate158_balanced.json` |
| 2026-08-16T10:07:32.665Z | `campaign_2y_15m.py` reads it as Scenario 4's seed |

## Files

| file | bytes | sha256 | state represented |
|---|---|---|---|
| `balanced_new_optimizer.json` | 1648 | `751dca94676e9fd2299ae86e5e7e34fb55f1b03d9fe1b301f3ad71337c5e14c4` | as first generated, 09:18 |
| `config4_candidate158_balanced.AT-0929-neutral-bollinger.json` | 1882 | `5a7af87accaadbc3f8bf91e2409193a04b28f134715620daafa5b3d4d98ea3df` | after generator 2, 09:29 |
| `config4_candidate158_balanced.AT-0954-stage1-bollinger.json` | 1900 | `ac1d213b21b7b63a090b2b7b6de083f5b08e20fe09eef9641162669c82b3601b` | **the state Scenario 4 actually read** |

The three differ **only** in `filters.bollinger`. That block was inert during Scenario 4:
`campaign_2y_15m.build_cfg()` reads `preset["strategy"]`, `["risk"]`, `["execution"]` and
never `preset["filters"]`; the filter comes from a separate `fcfg` argument. So the
Bollinger ambiguity has no effect on the reproduction — use the 0954 file.

## Executable content (identical in all three)

```
strategy  ema 105 · rsi 18 · OB 80.0 · OS 33.0 · atr 11 · cons 14 · cmult 3.3
          swing 8 · vsma 32 · vmult 1.5 · RR 2.7 · long True · short False
risk      RISK_BASED · capital 10000.0 · leverage 5.0 · risk_per_trade_pct 1.7
          max_position_allocation_pct 28.0 · quantity_step 0.001
execution commission_pct 0.05 · slippage_ticks 1 · tick_size 0.01
```

Note the metadata the generator itself recorded: this candidate **FAILED Stage-3
neighbourhood robustness (8.6% acceptance, 51.0% within-30%)** and was carried into the
campaign as a benchmark, not as a validated winner.
