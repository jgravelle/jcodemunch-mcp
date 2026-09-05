"""jCodeMunch through the same interface as everyone else (DESIGN s1.4).

purpose:  our own row, driven the way our docs say (ARCHAEOLOGY R27, R28):
          search_symbols(max_results=5) then get_symbol_source on the top 3
          for P1 and T; find_references for P2; find_importers for P4;
          shipped defaults (context providers ON, as index_folder ships them;
          the self-latency harness turns them off and this adapter does not),
          AI summaries off (R28), no config file
invokes:  a FRESH SUBPROCESS per run that indexes the corpus into a scratch
          CODE_INDEX_PATH and answers every task (a cold index in-process
          is not cold: the IndexStore LRU keeps the previous .db open,
          benchmarks/self_latency/measure.py); `python -m jcodemunch_mcp`'s
          tool functions from the working tree under PYTHONPATH=src
produces: IndexReport (cold index wall seconds) and one Answer per task
          whose payload is the serialised JSON of every tool response, the
          shape an agent receives (R15, R17: _meta kept)
refuses:  a corpus the index step did not index completely; a task category
          outside its set
pinned:   registry "tree", the working tree's commit (the tier measures
          the checkout, like every bench-tier harness)
fairness: DESIGN s1.4. ⚠ Runs in-process on the runner in this PR, not in
          the D2 container: docs/competitive/FINDINGS.md CF-3.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from adapter import Answer, Corpus, IndexReport, Pin, Task, count_tokens

REPO = Path(__file__).resolve().parents[3]
SEARCH_MAX_RESULTS = 5
SYMBOLS_FETCHED = 3

_WORKER = r'''
import json, sys, time
from jcodemunch_mcp.tools.index_folder import index_folder
corpus, store, tasks_json = sys.argv[1], sys.argv[2], sys.argv[3]
tasks = json.loads(open(tasks_json, encoding="utf-8").read())
t = time.perf_counter()
r = index_folder(path=corpus, use_ai_summaries=False, storage_path=store)
idx = {"secs": time.perf_counter() - t, "repo": r.get("repo"), "success": r.get("success"),
       "file_count": r.get("file_count"), "symbol_count": r.get("symbol_count"), "error": r.get("error")}
out = {"index": idx, "answers": {}}
if idx["success"] and idx["repo"]:
    from jcodemunch_mcp.tools.search_symbols import search_symbols
    from jcodemunch_mcp.tools.get_symbol import get_symbol_source
    from jcodemunch_mcp.tools.find_references import find_references
    from jcodemunch_mcp.tools.find_importers import find_importers
    repo = idx["repo"]
    def ser(o): return json.dumps(o, separators=(",", ":"), default=str)
    for task in tasks:
        cat, q = task["category"], task["query"]
        payload, lat, cited, err = [], [], [], None
        try:
            if cat in ("P1", "T"):
                t0 = time.perf_counter()
                s = search_symbols(repo=repo, query=q, max_results=%(k)d, detail_level="standard")
                lat.append((time.perf_counter() - t0) * 1000); payload.append(ser(s))
                rows = s.get("results") or s.get("symbols") or []
                for row in rows:
                    f, ln = row.get("file") or row.get("file_path"), row.get("line") or row.get("start_line")
                    if f and ln: cited.append([f, int(ln)])
                ids = [x.get("id") or x.get("symbol_id") for x in rows if x.get("id") or x.get("symbol_id")][:%(n)d]
                for sid in ids:
                    t0 = time.perf_counter()
                    g = get_symbol_source(repo=repo, symbol_id=sid)
                    lat.append((time.perf_counter() - t0) * 1000); payload.append(ser(g))
                    f, ln = g.get("file") or g.get("file_path"), g.get("start_line") or g.get("line")
                    if f and ln: cited.append([f, int(ln)])
            elif cat == "P2":
                t0 = time.perf_counter()
                s = find_references(repo=repo, identifier=q)
                lat.append((time.perf_counter() - t0) * 1000); payload.append(ser(s))
                for key in ("references", "results", "importers"):
                    for row in (s.get(key) or []):
                        f, ln = row.get("file") or row.get("file_path") or row.get("path"), row.get("line") or row.get("start_line") or 0
                        if f: cited.append([f, int(ln or 0)])
            elif cat == "P4":
                t0 = time.perf_counter()
                s = find_importers(repo=repo, file_path=q)
                lat.append((time.perf_counter() - t0) * 1000); payload.append(ser(s))
                for row in (s.get("importers") or s.get("results") or []):
                    f = row.get("file") or row.get("file_path") or row.get("path") or (row if isinstance(row, str) else None)
                    if f: cited.append([f, 0])
            else:
                err = "category not answered by this adapter"
        except Exception as e:  # the row fails, not the run
            err = f"{type(e).__name__}: {e}"
        out["answers"][task["id"]] = {"payload": "".join(payload), "calls": len(lat), "latency_ms": lat, "cited": cited, "error": err}
print("JCMCOMPETE " + json.dumps(out))
'''


class JCodeMunch:
    name = "jcodemunch"
    interface = "python"
    categories = frozenset({"P1", "P2", "P4", "T"})

    def __init__(self) -> None:
        commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, text=True, encoding="utf-8").strip()
        self.pin = Pin(registry="tree", package="jcodemunch-mcp", version=commit)
        self._cache: dict[tuple[str, str], dict] = {}

    # The subprocess does index + every answer in one go, so `index` runs it
    # and `answer` reads the cache; both are keyed by (corpus, scratch).
    def _run(self, corpus: Corpus, scratch: Path, tasks: list[Task]) -> dict:
        key = (corpus.id, str(scratch))
        if key in self._cache:
            return self._cache[key]
        store = scratch / "jcm-store"
        store.mkdir(parents=True, exist_ok=True)
        tasks_json = scratch / "jcm-tasks.json"
        tasks_json.write_text(json.dumps([{"id": t.id, "category": t.category, "query": t.query} for t in tasks]), encoding="utf-8")
        env = dict(
            os.environ,
            CODE_INDEX_PATH=str(store),
            PYTHONPATH=str(REPO / "src"),
            JCODEMUNCH_TRUSTED_FOLDERS=str(corpus.path),
            JCODEMUNCH_LIVE_JOURNAL="0",
        )
        code = _WORKER % {"k": SEARCH_MAX_RESULTS, "n": SYMBOLS_FETCHED}
        proc = subprocess.run(
            [sys.executable, "-c", code, str(corpus.path), str(store), str(tasks_json)],
            env=env, text=True, capture_output=True, encoding="utf-8", errors="replace", timeout=1200,
        )
        line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("JCMCOMPETE ")), None)
        if proc.returncode != 0 or line is None:
            out = {"index": {"secs": None, "success": False, "error": (proc.stderr or proc.stdout)[-2000:]}, "answers": {}}
        else:
            out = json.loads(line[len("JCMCOMPETE "):])
        self._cache[key] = out
        return out

    def prepare(self, corpus: Corpus, scratch: Path, tasks: list[Task]) -> None:
        self._run(corpus, scratch, tasks)

    def index(self, corpus: Corpus, scratch: Path) -> IndexReport:
        out = self._cache.get((corpus.id, str(scratch)))
        if out is None:
            raise RuntimeError("jcodemunch.index called before prepare(); the runner calls prepare with the task list")
        idx = out["index"]
        return IndexReport(seconds=idx.get("secs"), ok=bool(idx.get("success")), files_indexed=idx.get("file_count"), stderr_tail=str(idx.get("error") or ""))

    def answer(self, corpus: Corpus, task: Task, scratch: Path) -> Answer:
        out = self._cache.get((corpus.id, str(scratch))) or {}
        a = (out.get("answers") or {}).get(task.id)
        if a is None:
            return Answer(payload="", tokens=0, calls=0, latency_ms=[], cited=frozenset(), error="no answer: index failed or task not run")
        return Answer(
            payload=a["payload"],
            tokens=count_tokens(a["payload"]),
            calls=a["calls"],
            latency_ms=a["latency_ms"],
            cited=frozenset((f, int(ln)) for f, ln in a["cited"]),
            error=a.get("error"),
        )

    def tools_list_tokens(self):
        """The shipped default surface's `tools/list` weight, from the
        committed baseline (never a hand-typed figure, R46/R50)."""
        try:
            base = json.loads((REPO / "benchmarks" / "schema_baseline.json").read_text(encoding="utf-8"))
        except OSError:
            return None
        node = base.get("profiles") or base
        for key in ("full_full", "full"):
            v = node.get(key)
            if isinstance(v, dict) and "tokens" in v:
                return int(v["tokens"])
            if isinstance(v, (int, float)):
                return int(v)
        return None

    def version(self) -> str:
        return self.pin.version


def make() -> JCodeMunch:
    return JCodeMunch()
