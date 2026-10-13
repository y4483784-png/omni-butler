"""Small statistics helpers for eval reports (no numpy dependency)."""

from __future__ import annotations

import random
from typing import Sequence


def bootstrap_ci(
    values: Sequence[float | int | bool],
    *,
    n_boot: int = 1000,
    alpha: float = 0.05,
    seed: int = 0,
) -> dict[str, float | int]:
    """Percentile bootstrap CI of the mean. Deterministic for a given seed."""
    vals = [float(v) for v in values]
    n = len(vals)
    if n == 0:
        return {"mean": 0.0, "lo": 0.0, "hi": 0.0, "n": 0}
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(n_boot):
        s = 0.0
        for _ in range(n):
            s += vals[rng.randrange(n)]
        means.append(s / n)
    means.sort()
    lo = means[int((alpha / 2) * n_boot)]
    hi = means[min(n_boot - 1, int((1 - alpha / 2) * n_boot))]
    return {"mean": round(sum(vals) / n, 4), "lo": round(lo, 4), "hi": round(hi, 4), "n": n}


def ci_overlap(a: dict[str, float], b: dict[str, float]) -> bool:
    """True when two runs' CIs overlap, i.e. their difference is not distinguishable from noise."""
    return not (a["hi"] < b["lo"] or b["hi"] < a["lo"])
