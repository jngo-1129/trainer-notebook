"""Pull probability math: exact binomial odds plus spark (pity exchange).

Safety: pure math on numbers the user types in. No I/O, no network, no game access.

Community-documented Global rates (editable in the UI, verify on each banner's rate page):
  SSR total 3%, featured (rate-up) SSR 0.75% when one card is featured,
  150 carats per pull, 1 spark point per pull, 200 points = exchange for one copy of a featured item.
Assumes every spark exchange goes to the target. MLB = 5 copies of a support card.
"""
from math import comb

SSR_RATE = 0.03
RATE_UP = 0.0075
SPARK_AT = 200
CARATS_PER_PULL = 150


def binom_at_least(k: int, n: int, p: float) -> float:
    """Exact P(X >= k) for X ~ Binomial(n, p)."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    return 1.0 - sum(comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k))


def p_copies(k: int, n: int, p: float = RATE_UP, points: int = 0, spark_at: int = SPARK_AT) -> float:
    """P(at least k copies of the target after n pulls), counting spark exchanges.
    `points` = spark points already banked on this banner."""
    sparks = (n + points) // spark_at if spark_at else 0
    return binom_at_least(k - sparks, n, p)


def pulls_needed(target: float, k: int, p: float = RATE_UP, points: int = 0, spark_at: int = SPARK_AT,
                 cap: int = 2000) -> int | None:
    """Fewest pulls reaching P(>= k copies) >= target, or None if not within cap."""
    return next((n for n in range(cap + 1) if p_copies(k, n, p, points, spark_at) >= target), None)


def summary(n: int, p: float = RATE_UP, owned: int = 0, points: int = 0, spark_at: int = SPARK_AT,
            ssr_rate: float = SSR_RATE, step: int = 10) -> dict:
    """Everything the pull calculator screen shows. `owned` = copies already owned (MLB needs 5)."""
    need_mlb = max(0, 5 - owned)
    curve_max = max(n, spark_at * 2)
    return {
        "pulls": n,
        "carats": n * CARATS_PER_PULL,
        "sparks": (n + points) // spark_at if spark_at else 0,
        "expected_copies": n * p + ((n + points) // spark_at if spark_at else 0),
        "p_any_ssr": binom_at_least(1, n, ssr_rate),
        "p_at_least": {k: p_copies(k, n, p, points, spark_at) for k in range(1, 6)},
        "p_mlb": p_copies(need_mlb, n, p, points, spark_at),
        "pulls_for_50_mlb": pulls_needed(0.5, need_mlb, p, points, spark_at),
        "pulls_for_90_mlb": pulls_needed(0.9, need_mlb, p, points, spark_at),
        "curve": [
            {"n": i, "p1": p_copies(1, i, p, points, spark_at), "pmlb": p_copies(need_mlb, i, p, points, spark_at)}
            for i in range(0, curve_max + 1, step)
        ],
    }


def _selftest():
    import random
    assert binom_at_least(0, 10, 0.5) == 1.0
    assert abs(binom_at_least(1, 100, RATE_UP) - (1 - (1 - RATE_UP) ** 100)) < 1e-12
    assert abs(binom_at_least(1, 100, RATE_UP) - 0.52894) < 1e-4
    assert p_copies(1, 200) == 1.0  # spark guarantees a copy
    assert p_copies(1, 199) < 1.0
    assert p_copies(1, 1, points=199) == 1.0  # banked points count toward spark
    assert p_copies(11, 10) == 0.0 and p_copies(6, 10) < 1e-9
    assert pulls_needed(1.0, 1) == 200
    # exact math vs Monte Carlo: P(>=2 copies in 300 pulls) with one spark
    random.seed(1)
    trials = 20000
    hits = sum(sum(random.random() < RATE_UP for _ in range(300)) + 1 >= 2 for _ in range(trials))
    assert abs(hits / trials - p_copies(2, 300)) < 0.015, (hits / trials, p_copies(2, 300))
    s = summary(200, owned=4)
    assert s["p_mlb"] == 1.0 and s["sparks"] == 1
    print("pulls selftest: PASS")


if __name__ == "__main__":
    _selftest()
