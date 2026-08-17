# RECOVERED Phase-3A source — quarantined, do not import from production

These files are the **historical Candidate #158 workflow**, recovered byte-for-byte by
replaying the original `Write`/`Edit` tool calls found in Claude session transcripts.
They are **not** current project code. Nothing here is imported by `src/main.py`,
`pipeline.sh`, `src/auto_optimise/` or any test.

Original location (deleted; absent from every commit, every dangling git object, and
from both copies of the project tree):

    /home/rahul/Documents/CodeBackup/algo-research/Experiment-03_Simple_AlgoTrade/src/optimization/

Recovery source: `~/.claude/projects/-home-rahul-Documents-Claude/*.jsonl`
(session `08facafa-c7e8-41a2-98d3-9ecebbd981fb`, also present in its resumed copies
`b77eb12e-…` and `df581ee5-…`; events de-duplicated by `tool_use` id).

## Files

| file | bytes | lines | sha256 | first written (UTC) |
|---|---|---|---|---|
| `core_15m_long_optimizer.py` | 19339 | 447 | `69a0c852d966fe75f85809f04a024604d7beb84b930a0020b50b1774aa21b5e0` | 2026-08-16T03:00:56.310Z |
| `stage3_neighbourhood.py` | 5605 | 153 | `aa253ac221c06cd77424ac1d86ab3a41bedbce56d3eb0c0f98c76cad7fae57e8` | 2026-08-16T06:53:07.102Z |
| `stage3_stable_region.py` | 8844 | 218 | `6c5bd3668831e5ba2f0308b00db4932c60e7e457a500dacb5b528d2f723a8f65` | 2026-08-16T07:02:14.097Z |
| `risk_policy_search_t53.py` | 5060 | 120 | `800ff184ca82b86a4ab9d08d90c9a20a45c06b4ab32b6c8da870d7e1c26595dd` | 2026-08-16T07:30:00.033Z |
| `robustness_showdown.py` | 9316 | 209 | `dc541971e62de909742fb288d82817acf8f419b209733dd7b65859cb65ebff6c` | 2026-08-16T07:45:08.171Z |
| `campaign_2y_15m.py` | 17993 | 363 | `a89aee78d1971f574b1ef9029445f61939e730539a36b4864c3433881984f80f` | 2026-08-16T10:07:28.466Z |

`recovery_ledger.json` holds the full chronological edit ledger (tool, timestamp,
`tool_use` id, byte deltas, running sha256 after each step).

Only `core_15m_long_optimizer.py` received edits after its `Write`:
`03:20:54.868Z` (+1456 B, adds `STAGE2_SPACE`) and `03:21:00.235Z` (+52 B).
Both applied with a unique `old_string` match. Every other file is a single `Write`.
No `Bash`/`sed`/`tee` mutation of any of these paths appears anywhere in the transcripts.

## Environment differences vs the historical run

1. `RISK_POLICY_PATH = "configs/riskmanager.json"` — that file was **relocated** to
   `src/risk_management/riskmanager.json` by commit `e03b220`. Content is byte-identical:
   sha256 `a28fe77d87fa4a04…`, matching the sha the historical run printed. Running these
   files unmodified from the repo root raises `FileNotFoundError` until the path is
   supplied externally.
2. `END_DATE = "2026-08-15"` reaches into the locked window. The current market-data
   cache stops at 2026-07-15 00:00, so an unmodified run would trigger a **re-fetch**
   that pulls locked candles into `data/`. Any verification must cap the load itself.
3. Python 3.12.3 · optuna 4.9.0 · pandas 3.0.5 · numpy 2.5.2 — the same `.venv`
   (created 2026-08-16 01:29, before the historical run), so the interpreter and
   library versions are unchanged.

## Verification already performed

Fixed-parameter (no Optuna) reproduction of strategy Trial #53 at the frozen risk
policy, TRAIN and VALIDATION only, locked window guarded and never loaded:

    TRAIN  323 trades (historical 323) · return 49.3291% (49.33) · PF 1.2892 (1.289) · DD 11.0576% (11.06)
    VALID  101 trades (historical 101) · return 11.3840% (11.38) · PF 1.2341 (1.234) · DD 12.4464% (12.45)
    score  0.03516 (historical 0.03516)

Artifacts: `results/reproduction/candidate158/fingerprint/`.

## Before any rerun

`core_15m_long_optimizer.load_dataset()` needs 161,953 candles spanning
2022-01-01 → 2026-08-15 to reproduce the historical 60/20/20 boundaries
(train 0..97171, validation 97171..129562, holdout 129562..161953). The current cache is
short by exactly 2,976 rows (the 31-day locked month, which lives inside HOLDOUT).
Restoring those rows is required for index-identical stages 1–3, and it is also the only
reason the locked window would be fetched — decide that deliberately, not by accident.
