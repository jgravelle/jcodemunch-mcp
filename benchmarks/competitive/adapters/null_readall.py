"""Null alternative A: read every file (DESIGN s1.2; ARCHAEOLOGY R23).

purpose:  the ceiling nobody pays, on the same file set as every other row,
          so a reader sees what "no tool, read everything" costs
invokes:  the corpus files through adapter.read_file
produces: an Answer whose payload is the whole corpus, cites_all=True
refuses:  nothing; it answers every category by construction
pinned:   registry "none", the tree itself
fairness: DESIGN s1.2. Its F1 is scored like every row: recall 1 by
          construction, precision = expected lines over corpus lines, so it
          is the floor every tool must clear and it sits on every table.
"""

from __future__ import annotations

import time
from pathlib import Path

from adapter import Answer, Corpus, IndexReport, Pin, Task, count_tokens, read_file


class NullReadAll:
    name = "null_readall"
    pin = Pin(registry="none", package="read-all", version="baseline-A")
    categories = frozenset({"P1", "P2", "P4", "P5", "T"})
    interface = "null"

    def index(self, corpus: Corpus, scratch: Path) -> IndexReport:
        return IndexReport(seconds=None, ok=True, files_indexed=len(corpus.files))

    def answer(self, corpus: Corpus, task: Task, scratch: Path) -> Answer:
        t0 = time.perf_counter()
        payload = "".join(read_file(corpus, rel) for rel in corpus.files)  # bare, as run_benchmark.py counts it
        ms = (time.perf_counter() - t0) * 1000
        return Answer(
            payload=payload,
            tokens=count_tokens(payload),
            calls=len(corpus.files),
            latency_ms=[ms],
            cited=frozenset(),
            cites_all=True,
        )

    def tools_list_tokens(self):
        return None

    def version(self) -> str:
        return "baseline-A"


def make() -> NullReadAll:
    return NullReadAll()
