# Unclassified legacy utilities

Items that could not be attributed to any optimizer lineage from their contents.
Archived rather than assigned, because a wrong lineage label is worse than none.

## Original paths and role

| archived path | original path | role |
|---|---|---|
| `source/fetch_data.py` | `src/optimization/fetch_data.py` | multi-timeframe pre-download helper; writes into the production `data/` cache |
| `source/fetch_delta.py` | `src/optimization/fetch_delta.py` | one-off ETHUSDT 1m delta fetch (`2026-08-13 → 2026-08-15`); appends to the production `data/` cache |
| `pine/parity_recovered_eth15m_bb_on.pine` | `pine/` | output of the Phase-12 evaluator-parity run (recovered arm) |
| `pine/parity_v2_eth15m_bb_on.pine` | `pine/` | output of the Phase-12 evaluator-parity run (V2 arm) |
| `pine/recovered-unseeded-ETHUSDT-15m-bollinger-on.pine` | `pine/` | output of the 15m bakeoff, unseeded recovered arm |

## Why unclassified

The two fetch scripts are not attributable from their contents to V1, V2, Phase-3A or
V3 — each merely populates the shared market-data cache — and their write target is a
path this archive is forbidden to touch. The three Pine files are outputs of two-arm
comparison runs (recovered recipe **vs** V2) and therefore belong to no single lineage;
splitting them would break the comparison they document.

## ⚠️ Do not run

Both scripts call `MarketDataLoader` against the **production** `data/` cache and will
fetch into it, including dates inside a locked window. Read as evidence only.
