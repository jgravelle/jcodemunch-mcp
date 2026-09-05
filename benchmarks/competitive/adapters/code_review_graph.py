"""code-review-graph 2.3.8 through the competitive interface
(docs/competitive/fairness/code_review_graph.md).

purpose:  the loudest token claim in the lane (FIELD.md: "~65x median token
          reduction"), recomputed on our corpora: a tree-sitter graph in
          SQLite with FTS5 behind a FastMCP server, driven over stdio the
          way its `mcpServers` entry drives it
invokes:  the image built from sandbox/code_review_graph.Dockerfile (the
          PyPI wheel and every dependency pinned by hash in
          sandbox/code_review_graph.requirements.txt), one container per
          (corpus, run) in which sandbox/mcp_driver.py speaks MCP to
          `code-review-graph serve`: initialize, tools/list (the schema
          weight), build_or_update_graph_tool, get_minimal_context_tool
          (uncharged, "call this first"), then every task's charged call,
          each a timed stdio round trip
produces: IndexReport from the build (files_parsed); one Answer per task
          whose payload is the tool's DEFAULT output and whose citations are
          read from that same JSON (file_path, line_start); tools_list_tokens
          from the live tools/list
refuses:  the `none` sandbox (DESIGN D2); a build that fails (not_runnable)
pinned:   pypi code-review-graph 2.3.8,
          code_review_graph-2.3.8-py3-none-any.whl
          sha256 013ae3c119cc7de337f9e88fe36daef82e2d4def942a014edcf97f126e208547
fairness: docs/competitive/fairness/code_review_graph.md. P1/T:
          semantic_search_nodes_tool (limit 3; the tool returns no source
          body, disadvantage 1 in the note); P2: query_graph_tool
          callers_of on the bare name, then, when the tool answers
          `ambiguous`, once more per candidate named exactly the query
          (a second session over the persisted graph; every call charged);
          P4: query_graph_tool importers_of.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from adapter import Answer, Corpus, IndexReport, Pin, Task, count_tokens
import sandbox

HERE = Path(__file__).resolve().parent
TAG = "jcm-compete/code-review-graph:2.3.8"
TOP = 3
TIMEOUT_S = 20 * 60
REPO = "/corpus"


class CodeReviewGraph:
    name = "code_review_graph"
    interface = "mcp-stdio"
    categories = frozenset({"P1", "P2", "P4", "T"})
    pin = Pin(registry="pypi", package="code-review-graph", version="2.3.8",
              digest="013ae3c119cc7de337f9e88fe36daef82e2d4def942a014edcf97f126e208547")

    def __init__(self, sandbox_mode: str = "docker") -> None:
        if sandbox_mode != "docker":
            raise RuntimeError("code-review-graph runs only in the container (DESIGN D2)")
        self._image = None
        self._cache: dict[tuple[str, str], dict] = {}

    def image(self) -> sandbox.BuildResult:
        if self._image is None:
            df = HERE.parent / "sandbox" / "code_review_graph.Dockerfile"
            self._image = sandbox.build(TAG, df, df.parent, timeout=1800)
            self.pin = Pin(**{**self.pin.__dict__, "dockerfile_sha256": self._image.dockerfile_sha256})
        return self._image

    # ---- the call plan ---------------------------------------------------
    @staticmethod
    def _plan(tasks: list[Task]) -> list[dict]:
        calls = []
        for t in tasks:
            q = t.query
            if t.category in ("P1", "T"):
                calls.append({"id": f"{t.id}|ss", "tool": "semantic_search_nodes_tool",
                              "args": {"query": q, "limit": TOP, "repo_root": REPO}, "charged": True})
            elif t.category == "P2":
                calls.append({"id": f"{t.id}|callers", "tool": "query_graph_tool",
                              "args": {"pattern": "callers_of", "target": q, "repo_root": REPO}, "charged": True})
            elif t.category == "P4":
                calls.append({"id": f"{t.id}|importers", "tool": "query_graph_tool",
                              "args": {"pattern": "importers_of", "target": q, "repo_root": REPO}, "charged": True})
        return calls

    def prepare(self, corpus: Corpus, scratch: Path, tasks: list[Task]) -> None:
        key = (corpus.id, str(scratch))
        if key in self._cache:
            return
        self.image()
        out = scratch / "crg-out"
        out.mkdir(parents=True, exist_ok=True)
        plan = [{"id": "build", "tool": "build_or_update_graph_tool", "args": {"full_rebuild": True, "repo_root": REPO}, "timeout_s": TIMEOUT_S, "charged": False},
                {"id": "minimal", "tool": "get_minimal_context_tool", "args": {"task": "answer questions about this repository", "repo_root": REPO}, "charged": False}]
        plan.extend(self._plan(tasks))
        (out / "calls.json").write_text(json.dumps(plan), encoding="utf-8")
        res = sandbox.run(TAG, ["-c", "python3 /opt/mcp_driver.py /out/mcp.json /out/calls.json -- code-review-graph serve"],
                          corpus.path, out, timeout=TIMEOUT_S)
        d = _load(out / "mcp.json")
        tl = (d or {}).get("tools_list_json")
        if res.timed_out or d is None or d.get("error"):
            self._cache[key] = {"index": {"secs": None, "ok": False, "err": (d or {}).get("error") or res.stderr[-500:]}, "answers": {}, "timed_out": res.timed_out, "tools_list_json": tl}
            return
        results = {c["id"]: c for c in d["calls"]}
        b = results.get("build")
        if b is None or b["is_error"] or b.get("timed_out"):
            self._cache[key] = {"index": {"secs": None, "ok": False, "err": ((b or {}).get("result_text") or "build not run")[:500]}, "answers": {}, "timed_out": bool((b or {}).get("timed_out")), "tools_list_json": tl}
            return
        files = _build_files(b["result_text"])
        minimal = results.get("minimal")
        # Pass 2: a P2 query on a bare name answers `status: ambiguous` with candidates and
        # "Re-run with a qualified_name"; an agent re-runs it for the candidate named exactly
        # what it asked about. Both calls are charged. The graph persists in /out, so the
        # second session opens it without a build.
        follow = []
        for t in tasks:
            if t.category != "P2":
                continue
            c = results.get(f"{t.id}|callers")
            for i, qn in enumerate(_exact_candidates(c["result_text"], t.query) if c else []):
                follow.append({"id": f"{t.id}|callers{i}", "tool": "query_graph_tool",
                               "args": {"pattern": "callers_of", "target": qn, "repo_root": REPO}, "charged": True})
        if follow:
            (out / "calls2.json").write_text(json.dumps(follow), encoding="utf-8")
            res2 = sandbox.run(TAG, ["-c", "python3 /opt/mcp_driver.py /out/mcp2.json /out/calls2.json -- code-review-graph serve"],
                               corpus.path, out, timeout=TIMEOUT_S)
            d2 = _load(out / "mcp2.json")
            if not res2.timed_out and d2 and not d2.get("error"):
                results.update({c["id"]: c for c in d2["calls"]})
        answers = {}
        for t in tasks:
            cid = {"P1": "ss", "T": "ss", "P2": "callers", "P4": "importers"}.get(t.category)
            c = results.get(f"{t.id}|{cid}") if cid else None
            if c is None:
                answers[t.id] = {"payload": "", "calls": 0, "latency_ms": [], "cited": [], "error": "category not answered by this adapter" if cid is None else "call not run"}
                continue
            chain = [c]
            i = 0
            while f"{t.id}|callers{i}" in results:
                chain.append(results[f"{t.id}|callers{i}"])
                i += 1
            cited: list[list] = []
            for x in chain:
                for row in _cites(x["result_text"]):
                    if row not in cited:
                        cited.append(row)
            answers[t.id] = {"payload": "".join(x["result_text"] for x in chain), "calls": len(chain), "latency_ms": [x["ms"] for x in chain],
                             "cited": cited, "error": (c["result_text"][:200] if c["is_error"] else None)}
        self._cache[key] = {
            "index": {"secs": b["ms"] / 1000.0, "ok": True, "files": files,
                      "err": (f"get_minimal_context {len(minimal['result_text']) if minimal else 0} chars (uncharged); build: {_build_summary(b['result_text'])}")[:500]},
            "answers": answers, "timed_out": False, "tools_list_json": tl,
        }

    def timed_out(self, corpus: Corpus, scratch: Path) -> bool:
        return bool(self._cache.get((corpus.id, str(scratch)), {}).get("timed_out"))

    def index(self, corpus: Corpus, scratch: Path) -> IndexReport:
        idx = self._cache[(corpus.id, str(scratch))]["index"]
        return IndexReport(seconds=idx.get("secs"), ok=bool(idx.get("ok")), files_indexed=idx.get("files"), stderr_tail=str(idx.get("err") or "")[:500])

    def answer(self, corpus: Corpus, task: Task, scratch: Path) -> Answer:
        a = self._cache[(corpus.id, str(scratch))]["answers"].get(task.id)
        if a is None:
            return Answer(payload="", tokens=0, calls=0, latency_ms=[], cited=frozenset(), error="no answer: build failed or task not run")
        return Answer(payload=a["payload"], tokens=count_tokens(a["payload"]), calls=a["calls"], latency_ms=a["latency_ms"],
                      cited=frozenset((f, int(ln)) for f, ln in a["cited"]), error=a["error"])

    def tools_list_tokens(self):
        for v in self._cache.values():
            tl = v.get("tools_list_json")
            if tl:
                return count_tokens(tl)
        return None

    def version(self) -> str:
        return self.pin.version


# ---- parsers over the tool's real shapes (fixtures: tests/fixtures/competitive/code_review_graph_mcp.json)

def _load(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _rel(fp: str) -> str:
    """A graph file_path may be absolute (/corpus/...) or repo-relative."""
    fp = fp.replace("\\", "/")
    if fp.startswith(REPO + "/"):
        return fp[len(REPO) + 1:]
    return fp


def _cites(text: str) -> list[list]:
    """semantic_search_nodes_tool and query_graph_tool both return
    {"results": [{"file_path": ..., "line_start": ...}, ...]}; a File node
    has line_start 0 or absent."""
    try:
        d = json.loads(text)
    except json.JSONDecodeError:
        return []
    out = []
    seen = set()
    for r in (d.get("results") or []) if isinstance(d, dict) else []:
        if not isinstance(r, dict) or not r.get("file_path"):
            continue
        row = (_rel(str(r["file_path"])), int(r.get("line_start") or 0))
        if row not in seen:
            seen.add(row)
            out.append(list(row))
    return out


def _exact_candidates(text: str, query: str) -> list[str]:
    """An ambiguous query_graph reply: {"status":"ambiguous","candidates":[{name,
    qualified_name,...}]}. The candidates whose bare name is exactly the query."""
    try:
        d = json.loads(text)
    except json.JSONDecodeError:
        return []
    if not isinstance(d, dict) or d.get("status") != "ambiguous":
        return []
    return [str(c["qualified_name"]) for c in d.get("candidates") or []
            if isinstance(c, dict) and c.get("name") == query and c.get("qualified_name")]


def _build_files(text: str):
    try:
        d = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"parsed (\d+) files", text)
        return int(m.group(1)) if m else None
    v = d.get("files_parsed") if isinstance(d, dict) else None
    return int(v) if isinstance(v, int) else None


def _build_summary(text: str) -> str:
    try:
        d = json.loads(text)
        return str(d.get("summary") or "")[:200]
    except json.JSONDecodeError:
        return text[:200]


def make(sandbox_mode: str = "docker") -> CodeReviewGraph:
    return CodeReviewGraph(sandbox_mode)
