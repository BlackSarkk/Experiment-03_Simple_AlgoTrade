"""Diversity selection — deterministic de-duplication of the TRAIN shortlist.

The 180-day campaign produced a Top-25 that collapsed into one narrow parameter
cluster: ranks 12-18 were identical to two decimal places, and the whole shortlist
sat inside EMA 177-197 / consolidation 4@3.2-3.5 / RR 2.0-2.1. Stage 3 would have
spent its robustness budget re-testing seven copies of one candidate.

This layer sits AFTER the TRAIN ranking and BEFORE Stage 3:

    raw shortlist  ->  diversity filter  ->  diverse shortlist  ->  VALIDATION report

It is emphatically NOT a second objective:
  * it never becomes an Optuna objective and never feeds back into TPE,
  * it never reads VALIDATION or UNSEEN,
  * it only ever removes near-duplicates and never re-orders on merit —
    TRAIN rank is the sole preference order.

METHOD (`greedy_max_min_v1`)
---------------------------
Each candidate becomes an 11-dimensional point, one dimension per real Phase-A
parameter, min-max normalised to [0, 1] using that parameter's own search range.
Raw Euclidean distance would be meaningless here: `ema_period` spans 190 units and
`volume_mult` spans 2.0, so an unnormalised metric would be almost purely an EMA
comparison.

Distance is the RMS per-dimension difference — Euclidean divided by sqrt(dims) —
so the number is directly readable as "average fraction of a parameter's range
that separates these two candidates", independent of how many parameters exist.

Selection is greedy max-min, seeded with the best TRAIN candidate:
  1. take TRAIN rank 1,
  2. repeatedly take the candidate whose distance to the nearest already-selected
     candidate is largest,
  3. stop at `MAX_DIVERSE`, or as soon as the best remaining candidate is closer
     than `MIN_DISTANCE` to something already chosen.

Step 3 is why the result is not padded: if the search only found six genuinely
distinct regions, six candidates are returned. Ties break on TRAIN rank, so the
output is fully deterministic for a given input ordering.
"""

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence

from . import search_space

METHOD_VERSION = "greedy_max_min_v1"

# Average per-dimension separation, as a fraction of each parameter's range,
# below which two candidates are treated as the same region.
MIN_DISTANCE = 0.10

TARGET_DIVERSE = 15
MIN_DIVERSE = 1

# How many TRAIN-ranked candidates the filter may choose from. The raw shortlist
# is deliberately small and its top entries are often near-identical, so drawing
# only from it would surface very few regions. A wider ranked pool lets genuinely
# distinct regions appear while TRAIN rank still drives all preference.
POOL_SIZE = 150


def _bounds() -> Dict[str, "tuple[float, float]"]:
    out = {}
    for name, (low, high, _step) in search_space.INT_PARAMS.items():
        out[name] = (float(low), float(high))
    for name, (low, high, _step) in search_space.FLOAT_PARAMS.items():
        out[name] = (float(low), float(high))
    return out


BOUNDS = _bounds()


def normalise(params: Dict[str, Any]) -> List[float]:
    """Map a parameter set to a point in [0,1]^11 using the search ranges."""
    point = []
    for name in search_space.PARAM_NAMES:
        low, high = BOUNDS[name]
        span = high - low
        value = float(params[name])
        point.append(0.0 if span <= 0 else (value - low) / span)
    return point


def distance(a: Sequence[float], b: Sequence[float]) -> float:
    """RMS per-dimension difference: 0 = identical, 1 = opposite corners."""
    total = sum((x - y) ** 2 for x, y in zip(a, b))
    return math.sqrt(total / len(a))


@dataclass
class Decision:
    train_rank: int
    trial: int
    selected: bool
    reason: str
    nearest_selected_rank: int = -1
    nearest_distance: float = float("nan")


def select(ranked: List[Dict[str, Any]],
           min_distance: float = MIN_DISTANCE,
           max_diverse: int = TARGET_DIVERSE) -> "tuple[List[Dict[str, Any]], List[Decision]]":
    """Pick a diverse subset of TRAIN-ranked candidates.

    `ranked` must be sorted best-first; each entry needs the 11 parameter keys and
    a `train_rank`. Returns (selected_rows, decisions_for_every_candidate).
    """
    if not ranked:
        return [], []

    points = [normalise(row) for row in ranked]
    selected_idx = [0]
    decisions: Dict[int, Decision] = {
        0: Decision(train_rank=ranked[0].get("train_rank", 1),
                    trial=ranked[0].get("trial", -1),
                    selected=True, reason="best TRAIN candidate (seed)")
    }

    while len(selected_idx) < max_diverse:
        best_i, best_d = -1, -1.0
        for i in range(len(ranked)):
            if i in selected_idx:
                continue
            d = min(distance(points[i], points[j]) for j in selected_idx)
            if d > best_d:                       # ties keep the earlier (better) rank
                best_i, best_d = i, d

        if best_i < 0 or best_d < min_distance:
            break

        nearest = min(selected_idx,
                      key=lambda j: distance(points[best_i], points[j]))
        selected_idx.append(best_i)
        decisions[best_i] = Decision(
            train_rank=ranked[best_i].get("train_rank", best_i + 1),
            trial=ranked[best_i].get("trial", -1),
            selected=True,
            reason=f"distinct region (nearest selected {distance(points[best_i], points[nearest]):.3f} away)",
            nearest_selected_rank=ranked[nearest].get("train_rank", nearest + 1),
            nearest_distance=distance(points[best_i], points[nearest]),
        )

    # Everything not chosen is a near-duplicate of whichever selected candidate
    # it sits closest to.
    for i, row in enumerate(ranked):
        if i in decisions:
            continue
        nearest = min(selected_idx, key=lambda j: distance(points[i], points[j]))
        d = distance(points[i], points[nearest])
        decisions[i] = Decision(
            train_rank=row.get("train_rank", i + 1),
            trial=row.get("trial", -1),
            selected=False,
            reason=f"near-duplicate of TRAIN rank {ranked[nearest].get('train_rank', nearest + 1)}",
            nearest_selected_rank=ranked[nearest].get("train_rank", nearest + 1),
            nearest_distance=d,
        )

    ordered = sorted(selected_idx)               # preserve TRAIN order in the output
    rows = [ranked[i] for i in ordered]
    decision_list = [decisions[i] for i in range(len(ranked))]
    return rows, decision_list


def describe(min_distance: float, max_diverse: int, pool_size: int) -> Dict[str, Any]:
    """Serializable snapshot for the run manifest."""
    return {
        "method": METHOD_VERSION,
        "metric": "RMS per-dimension difference over min-max normalised parameters",
        "dimensions": list(search_space.PARAM_NAMES),
        "min_distance": min_distance,
        "target_diverse": max_diverse,
        "pool_size": pool_size,
        "uses_validation": False,
        "uses_unseen": False,
        "feeds_back_into_sampler": False,
    }
