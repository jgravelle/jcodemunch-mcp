"""Self-corpus latency and incremental-cost harness (STANDARD criteria 3 and 5).

Measures, on THIS repository's `src/` tree copied into a temp dir:

  index.cold_self_seconds          cold `index_folder` wall clock (median of N)
  index.one_file_reindex_ms        `index_file` after appending one line (median of N)
  index.cold_over_one_file_ratio   the cold time in ms over the one-file reindex
                                   time, both from this run (runner speed
                                   divides out; LEDGER L-314)
  latency.<tool>_warm_p95_ms       warm p95 over WARM_N calls for search_symbols,
                                   search_text, get_symbol_source, get_file_outline
  latency.<tool>_cold_ms           first call in this process

Determinism (docs/harness/DESIGN.md s5): the corpus is a copy of `src/` at
HEAD (sha256 over `git ls-files src/` recorded), no AI summaries, no network,
a scratch CODE_INDEX_PATH, fixed query list. Timings vary by machine, so the
Floors in harness/thresholds.json are 2x the first committed measurement and
the artifact records the box. Nothing here is a wall-clock ASSERTION; the
runner compares the written values to the threshold file.

A warm series is WARM_N (40) calls, so two preempted calls are not its p95.
A series that FAILS its Floor is re-sampled, later and up to RESAMPLES times
(`_warm_series`, PR 1580; first #906/#911, harness F-19): no re-sample runs
until every tool's first series has, each round waits RESAMPLE_PAUSE_S, and
only the series still failing are taken again. The lowest p95 of a tool's
series is reported (`_warm_p95`), with the first p95 and every series' p95
beside it. A tail that reproduces in every series still fails. F-19 re-sampled
once, at once: a runner contended for one series was often contended for the
next, and PR 1580, a LEDGER-only diff, failed two Floors that way.

Usage: python benchmarks/self_latency/measure.py [--n 20] [--warm-n 40] [--out harness/results/self_latency.json]
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

#: Calls in one warm series. At 40 the p95 is the third slowest call, so two
#: preempted calls no longer set it (at 20 it was the second slowest).
WARM_N = 40
#: Re-sample rounds after the first series, for a series still failing.
RESAMPLES = 2
#: Seconds each re-sample round waits first, so a preemption can pass.
RESAMPLE_PAUSE_S = 2.0


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


def cold_over_one_file_ratio(cold_seconds: float, one_file_ms: float) -> float:
    """Cold index time over one-file reindex time, both from THIS run.

    A slow runner slows both by about the same factor, so the ratio divides
    runner speed out (LEDGER L-314): on 22 CI readings the cold time alone ran
    from 2.9 s to 5.3 s while this stayed in a narrow band."""
    return round(cold_seconds * 1000 / one_file_ms, 1)


def _warm_series(samplers: dict, passes, *, resamples: int = RESAMPLES,
                 pause: float = RESAMPLE_PAUSE_S, sleep=time.sleep) -> dict:
    """Every tool's warm series: ``{label: [series, ...]}``.

    *samplers* maps a label to a function returning one series of call times
    in ms; *passes(key, value)* is the Floor, or None for no Floors (each tool
    is then measured once). Every first series runs before any re-sample;
    then each round sleeps *pause* and re-samples only the labels whose
    lowest p95 so far still fails, for at most *resamples* rounds."""
    series = {label: [sample()] for label, sample in samplers.items()}
    if passes is None:
        return series

    def failing():
        return [
            label for label, runs in series.items()
            if not passes(f"latency.{label}_warm_p95_ms", min(round(_p95(r), 1) for r in runs))
        ]

    for _ in range(resamples):
        todo = failing()
        if not todo:
            break
        sleep(pause)
        for label in todo:
            series[label].append(samplers[label]())
    return series


def _warm_p95(label: str, runs: list) -> dict:
    """The warm keys for *label* from its series: the lowest p95 is the one
    gated, its median beside it, and, when there was more than one series,
    the first p95 and every series' p95, so nothing is hidden."""
    p95s = [round(_p95(r), 1) for r in runs]
    best = min(range(len(runs)), key=lambda i: p95s[i])
    out = {
        f"latency.{label}_warm_p95_ms": p95s[best],
        f"latency.{label}_warm_median_ms": round(statistics.median(runs[best]), 1),
        f"latency.{label}_resampled": len(runs) > 1,
    }
    if len(runs) > 1:
        out[f"latency.{label}_warm_p95_first_ms"] = p95s[0]
        out[f"latency.{label}_warm_p95_series_ms"] = p95s
    return out


def _floor_check():
    """`passes(key, value)` against harness/thresholds.json; an id the file
    does not carry passes, so it is measured once."""
    from harness import thresholds as T
    entries = T.load(announce=False)

    def passes(key, value):
        return T.passes(key, value) if key in entries else True

    return passes


def run(n: int, warm_n: int = WARM_N) -> dict:
    scratch = Path(tempfile.mkdtemp(prefix="jcm-self-latency-"))
    corpus = scratch / "corpus"
    shutil.copytree(REPO / "src", corpus / "src")
    os.environ["JCODEMUNCH_TRUSTED_FOLDERS"] = str(corpus)
    os.environ.setdefault("JCODEMUNCH_LIVE_JOURNAL", "0")

    out: dict = {"schema": "jcm-self-latency/v1", "n": n, "warm_n": warm_n, "date": time.strftime("%Y-%m-%dT%H:%M:%S"),
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
    out["index.cold_over_one_file_ratio"] = cold_over_one_file_ratio(
        out["index.cold_self_seconds"], out["index.one_file_reindex_ms"]
    )

    passes = _floor_check()
    samplers: dict = {}

    def timed(label: str, fn):
        t = time.perf_counter(); fn(); cold = (time.perf_counter() - t) * 1000

        def sample():
            warm = []
            for _ in range(warm_n):
                t = time.perf_counter(); fn(); warm.append((time.perf_counter() - t) * 1000)
            return warm

        out[f"latency.{label}_cold_ms"] = round(cold, 1)
        samplers[label] = sample

    qi = {"i": 0}
    def next_q():
        q = QUERIES[qi["i"] % len(QUERIES)]; qi["i"] += 1; return q
    timed("search_symbols", lambda: search_symbols(repo=repo_id, query=next_q()))
    timed("search_text", lambda: search_text(repo=repo_id, query=next_q()))
    sid = search_symbols(repo=repo_id, query="cache_put")["results"][0]["id"]
    timed("get_symbol_source", lambda: get_symbol_source(repo=repo_id, symbol_id=sid))
    timed("get_file_outline", lambda: get_file_outline(repo=repo_id, file_path="src/jcodemunch_mcp/storage/token_tracker.py"))
    for label, runs in _warm_series(samplers, passes).items():
        out.update(_warm_p95(label, runs))

    shutil.rmtree(scratch, ignore_errors=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--warm-n", type=int, default=WARM_N)
    ap.add_argument("--out", default=str(REPO / "harness" / "results" / "self_latency.json"))
    a = ap.parse_args()
    res = run(a.n, a.warm_n)
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
    # Windows: a redirected stdout is cp1252; write UTF-8 (L-145, L-320).
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
