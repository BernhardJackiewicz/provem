"""Dependency-free statistics for benchmark significance claims.

These helpers exist so every reliability claim in this repo ships with a
reproducible confidence interval or significance test, computed in pure Python
without numpy/scipy. The goal is auditability: a reviewer can read the formula,
not trust a black box.

Implemented:

- ``wilson_interval``: score confidence interval for a binomial proportion.
  Preferred over the normal (Wald) interval for small samples and extreme
  rates (it never leaves [0, 1] and behaves near 0/1).
- ``mcnemar_exact``: exact McNemar test for two paired binary classifiers on
  the same items (the correct test when comparing two memory systems on the
  same tasks, because per-task success is paired, not independent).
- ``bootstrap_diff_ci``: paired bootstrap confidence interval for the
  difference of a statistic between two systems measured on the same items.
"""

from __future__ import annotations

from math import comb, sqrt
import random
from typing import Callable, List, Sequence, Tuple


def wilson_interval(successes: int, n: int, z: float = 1.959963984540054) -> Tuple[float, float]:
    """Return the Wilson score interval ``(low, high)`` for a proportion.

    ``z`` defaults to the 97.5th percentile of the standard normal, i.e. a
    two-sided 95% interval. ``successes`` must satisfy ``0 <= successes <= n``.
    An empty sample returns ``(0.0, 1.0)`` (maximum uncertainty).
    """
    if n <= 0:
        return (0.0, 1.0)
    if successes < 0 or successes > n:
        raise ValueError("successes must be within [0, n]")
    phat = successes / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (phat + z2 / (2 * n)) / denom
    margin = (z * sqrt((phat * (1 - phat) + z2 / (4 * n)) / n)) / denom
    low = center - margin
    high = center + margin
    return (max(0.0, low), min(1.0, high))


def wilson_point_and_interval(successes: int, n: int, z: float = 1.959963984540054) -> Tuple[float, float, float]:
    """Return ``(rate, low, high)`` for convenience in reporting tables."""
    rate = successes / n if n > 0 else 0.0
    low, high = wilson_interval(successes, n, z=z)
    return (rate, low, high)


def _binom_pmf(k: int, n: int, p: float) -> float:
    return comb(n, k) * (p ** k) * ((1 - p) ** (n - k))


def mcnemar_exact(b: int, c: int) -> Tuple[int, float]:
    """Exact two-sided McNemar test on discordant pair counts.

    ``b`` and ``c`` are the two discordant cells: e.g. ``b`` = number of items
    where system A succeeded and system B failed, ``c`` = the reverse. Under
    the null hypothesis (no difference), each discordant item is a fair coin
    flip, so the smaller cell follows ``Binomial(b + c, 0.5)``.

    Returns ``(n_discordant, p_value)``. When there are no discordant pairs the
    p-value is ``1.0`` (no evidence of a difference). Concordant pairs (both
    right or both wrong) carry no information and are intentionally ignored.
    """
    if b < 0 or c < 0:
        raise ValueError("discordant counts must be non-negative")
    n = b + c
    if n == 0:
        return (0, 1.0)
    k = min(b, c)
    tail = sum(_binom_pmf(i, n, 0.5) for i in range(0, k + 1))
    p_value = min(1.0, 2.0 * tail)
    return (n, p_value)


def mcnemar_from_pairs(
    success_a: Sequence[bool],
    success_b: Sequence[bool],
) -> Tuple[int, int, int, float]:
    """Compute McNemar inputs from two paired boolean sequences.

    Returns ``(b, c, n_discordant, p_value)`` where ``b`` counts items only A
    got right and ``c`` counts items only B got right.
    """
    if len(success_a) != len(success_b):
        raise ValueError("paired sequences must have equal length")
    b = sum(1 for a, bb in zip(success_a, success_b) if a and not bb)
    c = sum(1 for a, bb in zip(success_a, success_b) if bb and not a)
    n, p_value = mcnemar_exact(b, c)
    return (b, c, n, p_value)


def bootstrap_diff_ci(
    values_a: Sequence[float],
    values_b: Sequence[float],
    *,
    iterations: int = 10000,
    z_confidence: float = 0.95,
    seed: int = 12345,
    statistic: Callable[[Sequence[float]], float] = None,
) -> Tuple[float, float, float]:
    """Paired bootstrap CI for ``stat(A) - stat(B)`` over the same items.

    ``values_a`` and ``values_b`` are per-item measurements (e.g. per-task
    binary success as 0.0/1.0, or a per-item continuous score) for the two
    systems, aligned by index. Resampling is paired: the same resampled item
    indices are used for both systems each iteration, which preserves the
    correlation between the two systems and is the correct bootstrap for a
    difference measured on shared items.

    Returns ``(observed_diff, ci_low, ci_high)`` using the percentile method.
    ``statistic`` defaults to the mean.
    """
    if len(values_a) != len(values_b):
        raise ValueError("paired sequences must have equal length")
    n = len(values_a)
    if statistic is None:
        statistic = lambda xs: (sum(xs) / len(xs)) if xs else 0.0
    if n == 0:
        return (0.0, 0.0, 0.0)

    observed = statistic(values_a) - statistic(values_b)
    rng = random.Random(seed)
    diffs: List[float] = []
    a = list(values_a)
    b = list(values_b)
    for _ in range(iterations):
        idx = [rng.randrange(n) for _ in range(n)]
        sample_a = [a[i] for i in idx]
        sample_b = [b[i] for i in idx]
        diffs.append(statistic(sample_a) - statistic(sample_b))
    diffs.sort()
    alpha = (1.0 - z_confidence) / 2.0
    lo_index = max(0, int(alpha * iterations) - 1)
    hi_index = min(iterations - 1, int((1.0 - alpha) * iterations) - 1)
    return (observed, diffs[lo_index], diffs[hi_index])


def independence_baseline(per_step_success_rate: float, n_steps: int) -> float:
    """Expected end-to-end success if per-step errors were independent.

    This is the ``p^n`` (equivalently ``(1 - epsilon)^T``) baseline. Comparing
    observed multi-step success against this number quantifies error
    compounding: observed *below* this baseline indicates positively correlated
    (super-linear) failure, which is the empirically reported regime for long
    agent trajectories.
    """
    p = max(0.0, min(1.0, per_step_success_rate))
    return p ** max(0, n_steps)


def mean(values: Sequence[float]) -> float:
    return (sum(values) / len(values)) if values else 0.0


def stdev(values: Sequence[float]) -> float:
    """Sample standard deviation (n-1); 0.0 for fewer than two values."""
    n = len(values)
    if n < 2:
        return 0.0
    mu = mean(values)
    return sqrt(sum((v - mu) ** 2 for v in values) / (n - 1))
