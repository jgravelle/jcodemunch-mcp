"""Detection power of the warm-p95 gate, old rule against new (PR 1580).

Method: a share f of calls is slow (above the Floor), each call independently.
A series of n calls FAILS its Floor when its p95, the k-th slowest call
(index round(0.95 * (n - 1)) of the sorted series, as `measure._p95`), is
slow, i.e. when at least k calls are slow. A rule fails a tool only when
every series it takes fails; series are modelled as independent.

  old (F-19): 20 calls, one immediate re-sample: fails if 2 series fail
  new:        40 calls, up to two deferred re-samples: fails if 3 series fail

The model is of a SHARE of slow calls. It does not model a fixed COUNT of
preempted calls (two preemptions are 10% of 20 calls and 5% of 40), and it
does not model the pause before a re-sample; both are what the new rule is
for, and neither is in these numbers.

    python benchmarks/self_latency/detection_power.py > benchmarks/self_latency/detection_power.txt
"""
from __future__ import annotations

import sys
from math import comb

SHARES = (0.02, 0.05, 0.07, 0.075, 0.08, 0.10, 0.15, 0.20, 0.30)


def k_for(n: int) -> int:
    return n - round(0.95 * (n - 1))


def p_series(n: int, f: float) -> float:
    k = k_for(n)
    return 1 - sum(comb(n, i) * f**i * (1 - f) ** (n - i) for i in range(k))


def old(f: float) -> float:
    return p_series(20, f) ** 2


def new(f: float) -> float:
    return p_series(40, f) ** 3


def crossover(step: float = 0.0005) -> float:
    """The smallest share, on a grid of *step*, at which the new rule fails a
    tool at least as often as the old one."""
    f = step
    while new(f) < old(f):
        f += step
    return round(f, 4)


def main() -> None:
    print(f"n=20: a series fails when >= {k_for(20)} of 20 calls are slow; n=40: when >= {k_for(40)} of 40")
    print(f"{'slow share':>10} {'old (20x2)':>11} {'new (40x3)':>11}")
    for f in SHARES:
        print(f"{f:>10.3f} {old(f):>11.4f} {new(f):>11.4f}")
    print(f"crossover: the new rule fails a tool at least as often as the old from a slow share of {crossover()}")
    print(f"two preempted calls, no other slow call: old series fails={2 >= k_for(20)}, new series fails={2 >= k_for(40)}")


if __name__ == "__main__":
    # Windows: a redirected stdout is cp1252 (L-145, L-281).
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
