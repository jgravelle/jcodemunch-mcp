"""Self-corpus latency and incremental-cost harness (STANDARD criteria 3 and 5).

Measures, on THIS repository's `src/` tree copied into a temp dir:

  index.cold_self_seconds          cold `index_folder` wall clock (median of N)
  index.one_file_reindex_ms        `index_file` after appending one line (median of N)
  latency.<tool>_warm_p95_ms       warm p95 over N calls for search_symbols,
                                   search_text, get_symbol_source, get_file_outline
  latency.<tool>_cold_ms           first call in this process

Determinism (docs/harness/DESIGN.md s5): the corpus is a copy of `src/` at
HEAD (sha256 over `git ls-files src/` recorded), no AI summaries, no network,
a scratch CODE_INDEX_PATH, fixed query list. Timings vary by machine, so the
Floors in harness/thresholds.json are 2x the first committed measurement and
the artifact records the box. Nothing here is a wall-clock ASSERTION; the
runner compares the written values to the threshold file.

A warm p95 that FAILS its Floor is measured once more in the same process and
the second series is reported (`_warm_p95`, #906/#911, harness F-19): on a
shared runner two preempted calls are the p95 of twenty, and the thirteen FAILs
of F-19's twelve occurrences read 7x to 73x the clean median with the median
unmoved. A tail that
reproduces still fails; the first p95 is kept beside the reported one.

Usage: python benchmarks/self_latency/measure.py [--n 20] [--out harness/results/self_latency.json]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO))

QUERIES = ["cache_put", "index_folder", "validate_path", "search_symbols", "ProgressReporter"]


def _corpus_digest() -> str:
    files = subprocess.check_output(["git", "ls-files", "src/"], cwd=REPO, text=True, encoding="utf-8", errors="replace").split()
    h = hashlib.sha256()
    for f in sorted(files):
        p = REPO / f
        if p.is_file():
            h.update(f.encode()); h.update(b"\0"); h.update(p.read_bytes()); h.update(b"\0")
    return h.hexdigest()


def _p95(xs: list[float]) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(0.95 * (len(xs) - 1))))]


def _warm_p95(label: str, sample, passes) -> dict:
    """The warm-series keys for *label*. *sample* returns one series of call
    times in ms; *passes(key, value)* is the Floor, or None for an id with no
    Floor. A series that fails is sampled once more and the second is
    reported, with the first p95 and `resampled` beside it; a tail that
    reproduces fails on the second series exactly as on the first."""
    key = f"latency.{label}_warm_p95_ms"
    warm = sample()
    rec = {"resampled": False}
    if passes is not None and not passes(key, round(_p95(warm), 1)):
        rec = {"resampled": True, "warm_p95_first_ms": round(_p95(warm), 1)}
        warm = sample()
    out = {
        key: round(_p95(warm), 1),
        f"latency.{label}_warm_median_ms": round(statistics.median(warm), 1),
        f"latency.{label}_resampled": rec["resampled"],
    }
    if rec["resampled"]:
        out[f"latency.{label}_warm_p95_first_ms"] = rec["warm_p95_first_ms"]
    return out


def _floor_check():
    """`passes(key, value)` against harness/thresholds.json; an id the file
    does not carry passes, so it is measured once."""
    from harness import thresholds as T
    entries = T.load(announce=False)

    def passes(key, value):
        return T.passes(key, value) if key in entries else True

    return passes


def run(n: int) -> dict:
    scratch = Path(tempfile.mkdtemp(prefix="jcm-self-latency-"))
    corpus = scratch / "corpus"
    shutil.copytree(REPO / "src", corpus / "src")
    os.environ["JCODEMUNCH_TRUSTED_FOLDERS"] = str(corpus)
    os.environ.setdefault("JCODEMUNCH_LIVE_JOURNAL", "0")

    out: dict = {"schema": "jcm-self-latency/v1", "n": n, "date": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "commit": subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, text=True, encoding="utf-8", errors="replace").strip(),
                 "corpus_sha256": _corpus_digest(),
                 "env": {"os": platform.platform(), "python": platform.python_version(), "cpus": os.cpu_count()}}

    # Cold index, repeated in a FRESH SUBPROCESS with a FRESH store each time.
    # In-process repeats are not cold (the IndexStore LRU keeps the previous
    # .db open, and wiping the directory under it raised `no such table`
    # on the first draft). The subprocess timer wraps only the tool call, not
    # Python start-up.
    colds = []
    repo_id = None
    files = symbols = None
    store = None
    for i in range(max(1, min(n, 3))):
        store = scratch / f"store{i}"
        store.mkdir()
        code = (
            "import json,os,sys,time\n"
            "from jcodemunch_mcp.tools.index_folder import index_folder\n"
            "t=time.perf_counter()\n"
            f"r=index_folder(path={str(corpus)!r}, use_ai_summaries=False, context_providers=False, storage_path={str(store)!r})\n"
            "print('SELFLAT', json.dumps({'secs': time.perf_counter()-t, 'repo': r.get('repo'), 'success': r.get('success'), "
            "'file_count': r.get('file_count'), 'symbol_count': r.get('symbol_count'), 'error': r.get('error')}))\n"
        )
        env = dict(os.environ, CODE_INDEX_PATH=str(store), PYTHONPATH=str(REPO / "src"))
        proc = subprocess.run([sys.executable, "-c", code], env=env, text=True, capture_output=True, encoding="utf-8", errors="replace")
        line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("SELFLAT ")), None)
        if proc.returncode != 0 or line is None:
            raise SystemExit(f"cold index subprocess failed:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
        r = json.loads(line[len("SELFLAT "):])
        if not r.get("success") or not r.get("repo"):
            raise SystemExit(f"index_folder did not index the corpus: {r}")
        colds.append(r["secs"])
        repo_id, files, symbols = r["repo"], r["file_count"], r["symbol_count"]
    out["index.cold_self_seconds"] = round(statistics.median(colds), 3)
    out["index.cold_runs"] = [round(c, 3) for c in colds]
    out["files"], out["symbols"], out["repo"] = files, symbols, repo_id

    # The rest runs in THIS process against the last store.
    os.environ["CODE_INDEX_PATH"] = str(store)
    from jcodemunch_mcp.tools.index_file import index_file
    from jcodemunch_mcp.tools.search_symbols import search_symbols
    from jcodemunch_mcp.tools.search_text import search_text
    from jcodemunch_mcp.tools.get_symbol import get_symbol_source
    from jcodemunch_mcp.tools.get_file_outline import get_file_outline

    # one-file reindex
    target = corpus / "src" / "jcodemunch_mcp" / "storage" / "token_tracker.py"
    reidx = []
    for i in range(n):
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(f"\n# self-latency probe {i}\n")
        t = time.perf_counter()
        index_file(path=str(target), use_ai_summaries=False)
        reidx.append((time.perf_counter() - t) * 1000)
    out["index.one_file_reindex_ms"] = round(statistics.median(reidx), 1)
    out["index.one_file_reindex_p95_ms"] = round(_p95(reidx), 1)

    passes = _floor_check()

    def timed(label: str, fn):
        t = time.perf_counter(); fn(); cold = (time.perf_counter() - t) * 1000

        def sample():
            warm = []
            for _ in range(n):
                t = time.perf_counter(); fn(); warm.append((time.perf_counter() - t) * 1000)
            return warm

        out[f"latency.{label}_cold_ms"] = round(cold, 1)
        out.update(_warm_p95(label, sample, passes))

    qi = {"i": 0}
    def next_q():
        q = QUERIES[qi["i"] % len(QUERIES)]; qi["i"] += 1; return q
    timed("search_symbols", lambda: search_symbols(repo=repo_id, query=next_q()))
    timed("search_text", lambda: search_text(repo=repo_id, query=next_q()))
    sid = search_symbols(repo=repo_id, query="cache_put")["results"][0]["id"]
    timed("get_symbol_source", lambda: get_symbol_source(repo=repo_id, symbol_id=sid))
    timed("get_file_outline", lambda: get_file_outline(repo=repo_id, file_path="src/jcodemunch_mcp/storage/token_tracker.py"))

    shutil.rmtree(scratch, ignore_errors=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--out", default=str(REPO / "harness" / "results" / "self_latency.json"))
    a = ap.parse_args()
    res = run(a.n)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, indent=2) + "\n", encoding="utf-8")
    for k, v in res.items():
        if k.startswith(("index.", "latency.")):
            print(f"{k:<40} {v}")
    # verdicts against the threshold file, when entries exist
    from harness import thresholds as T
    entries = T.load(announce=False)
    rc = 0
    for k in res:
        if k in entries:
            line = T.verdict_line(k, res[k]); print(line)
            if line.endswith("FAIL"):
                rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
