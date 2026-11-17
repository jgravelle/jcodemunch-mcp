"""Power for the paired SWE-bench arm comparison, by simulation.

Practice 4: never hand-type a benchmark number. The table in PROTOCOL.md is
printed by this script; if the design changes, re-run it rather than editing
the table.

⚠ The `contested` parameter is an ASSUMPTION, not a measurement. It is the
fraction of instances where the two arms could plausibly disagree at all; the
rest are solved-by-both or solved-by-neither regardless of tooling. Replacing
it with a measured value is one of the four things the 50-instance pilot exists
to find out.
"""

from __future__ import annotations

import sys
import random
from math import comb

ALPHA = 0.05
CONTESTED = 0.30
TRIALS = 4000
SEED = 7


def mcnemar_p(b: int, c: int) -> float:
    """Exact two-sided binomial test on the discordant pairs.

    Only pairs where the arms DISAGREE carry information; concordant pairs are
    not evidence either way, which is the whole reason to pair the design.
    """
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2 * tail)


def power(n: int, lift: float, contested: float = CONTESTED,
          trials: int = TRIALS, alpha: float = ALPHA) -> float:
    """P(detect) for a true `lift` in solve rate, over `n` paired instances."""
    hits = 0
    for _ in range(trials):
        b = c = 0
        for _ in range(n):
            if random.random() < contested:
                p_baseline_wins = 0.5 - lift / (2 * contested)
                if random.random() < p_baseline_wins:
                    b += 1
                else:
                    c += 1
        if mcnemar_p(b, c) < alpha:
            hits += 1
    return hits / trials


def main() -> None:
    random.seed(SEED)
    lifts = (0.06, 0.10, 0.12)
    print(f"Paired design, McNemar exact, alpha={ALPHA}, "
          f"contested={CONTESTED:.0%} (ASSUMED), trials={TRIALS}, seed={SEED}")
    print()
    header = f"{'n':>5} " + " ".join(f"{'+' + str(int(x * 100)) + 'pt':>8}" for x in lifts)
    print(header)
    for n in (50, 100, 150, 200, 300, 500):
        print(f"{n:>5} " + " ".join(f"{power(n, x):>7.0%}" for x in lifts))


if __name__ == "__main__":
    # Windows: a redirected stdout is cp1252; write UTF-8 (L-145, L-320).
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    main()
