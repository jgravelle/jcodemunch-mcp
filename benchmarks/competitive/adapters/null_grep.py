"""Null alternative B: grep, rank by match count, open the top 3 whole
(DESIGN s1.2; ARCHAEOLOGY R24-R26; the agent's own tools).

purpose:  the row every competitor must beat to be worth installing
invokes:  the corpus files through adapter.read_file; no subprocess, so the
          count is bit-reproducible (the same modelling as
          benchmarks/harness/run_benchmark.py::measure_grep_baseline)
produces: an Answer whose payload is the `rg -l` file list plus the top 3
          files whole; cited = every matching line in those 3 files
refuses:  an empty query
pinned:   registry "none"
fairness: every modelling choice favours this baseline (R25): paths-only
          grep cost, ranking the agent does not get, case-insensitive
          substring on ANY term. Files are read WHOLE (R26) with no
          line-range estimator. The payload is the bare `rg -l` list plus the
          bare files, exactly what run_benchmark.py::measure_grep_baseline
          counts; no header line is charged to the baseline (review, finding 4).
          `match_lines_tokens` (the larger rg-with-lines cost) is not reported here.
"""

from __future__ import annotations

import time
from pathlib import Path

from adapter import Answer, Corpus, IndexReport, Pin, Task, count_tokens, read_file

FILES_READ = 3


class NullGrep:
    name = "null_grep"
    pin = Pin(registry="none", package="grep-top-3", version="baseline-B")
    categories = frozenset({"P1", "P2", "P4", "P5", "T"})
    interface = "null"

    def index(self, corpus: Corpus, scratch: Path) -> IndexReport:
        return IndexReport(seconds=None, ok=True, files_indexed=len(corpus.files))

    def answer(self, corpus: Corpus, task: Task, scratch: Path) -> Answer:
        terms = [t.lower() for t in task.query.split() if t]
        if not terms:
            return Answer(payload="", tokens=0, calls=0, latency_ms=[], cited=frozenset(), error="empty query")
        t0 = time.perf_counter()
        per_file: list[tuple[int, str]] = []
        hits_by_file: dict[str, list[int]] = {}
        for rel in corpus.files:
            content = read_file(corpus, rel)
            if not content:
                continue
            lines = [n for n, line in enumerate(content.splitlines(), 1) if any(t in line.lower() for t in terms)]
            if lines:
                per_file.append((len(lines), rel))
                hits_by_file[rel] = lines
        # match count desc, then path asc: deterministic (R42-shaped)
        per_file.sort(key=lambda x: (-x[0], x[1]))
        top = [rel for _, rel in per_file[:FILES_READ]]
        grep_ms = (time.perf_counter() - t0) * 1000

        payload = "".join(f"{rel}\n" for _, rel in per_file)
        latencies = [grep_ms]
        cited: set[tuple[str, int]] = set()
        for rel in top:
            t1 = time.perf_counter()
            payload += read_file(corpus, rel)  # bare, as run_benchmark.py counts it
            latencies.append((time.perf_counter() - t1) * 1000)
            cited.update((rel, n) for n in hits_by_file[rel])
        return Answer(
            payload=payload,
            tokens=count_tokens(payload),
            calls=1 + len(top),
            latency_ms=latencies,
            cited=frozenset(cited),
        )

    def tools_list_tokens(self):
        return None

    def version(self) -> str:
        return "baseline-B"


def make() -> NullGrep:
    return NullGrep()
