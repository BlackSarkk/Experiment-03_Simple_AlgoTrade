# Historical optimizer reference only.

This implementation produced the legacy Candidate #5 workflow.
Not imported or used by current production optimization.

| file | origin | byte-exact from Git |
|---|---|---|
| `multi_tf_optimizer_old.py` | `git show HEAD:src/optimization/multi_tf_optimizer.py` | YES |
| `run_candidate5_robustness.py` | `git show 0d70ae7^:run_candidate5_robustness.py` | YES |
| `run_multi_tf_optimization.py` | `git show 0d70ae7^:run_multi_tf_optimization.py` | YES |
| `fetch_data_old.py` | `git show HEAD:src/optimization/fetch_data.py` | YES |
| `deep_15m_optimizer.py` | working tree copy (gitignored, never committed) | NO |
| `fetch_delta.py` | working tree copy (gitignored, never committed) | NO |

The two root-level scripts were deleted in commit `0d70ae7`
("freeze neutral validated baseline"); they are recovered from its parent.

The active optimizer is `src/optimization/core_15m_long_optimizer.py` and its
Stage-3 helpers. `src/optimization/multi_tf_optimizer.py` remains in the repo
unmodified — the Phase-3 work was a fork, not a rewrite.
