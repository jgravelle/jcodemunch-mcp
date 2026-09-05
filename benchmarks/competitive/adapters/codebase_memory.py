"""codebase-memory-mcp 0.10.8 through the competitive interface
(docs/competitive/fairness/codebase_memory.md).

purpose:  the closest peer in our own words (#290's reply) and the field's
          (FIELD.md): a tree-sitter knowledge graph behind an MCP server,
          driven over stdio the way its `mcpServers` entry drives it
invokes:  the image built from sandbox/codebase_memory.Dockerfile (the
          portable release archive verified against the published
          checksums.txt), one container per (corpus, run) in which
          sandbox/mcp_driver.py speaks MCP to the server: initialize,
          tools/list (the schema weight), index_repository, then every
          task's charged calls and their uncharged JSON twins, each a timed
          stdio round trip
produces: IndexReport from index_repository (falling back to each top-level
          directory of the corpus when the tool refuses the root as "too
          broad"); one Answer per task whose payload is the tool's DEFAULT
          output format and whose citations come from the uncharged twins;
          tools_list_tokens from the live tools/list
refuses:  the `none` sandbox (DESIGN D2); a corpus with no indexable
          project (not_runnable)
pinned:   github-release DeusData/codebase-memory-mcp v0.10.8,
          codebase-memory-mcp-linux-amd64-portable.tar.gz
          sha256 6eef49652bc0c7820f43114125044d40bf7f4d97c11b2592f6b0f6a307702325
fairness: docs/competitive/fairness/codebase_memory.md. P1/T: search_graph
          (limit 3) then get_code_snippet on each hit; P2: trace_path
          inbound depth 1; P4: query_graph over IMPORTS edges.
          get_graph_schema ("Run this first") is called once per run and
          charged to nothing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from adapter import Answer, Corpus, IndexReport, Pin, Task, count_tokens
import sandbox

HERE = Path(__file__).resolve().parent
TAG = "jcm-compete/codebase-memory:0.10.8"
TOP = 3
TIMEOUT_S = 20 * 60


class CodebaseMemory:
    name = "codebase_memory"
    interface = "mcp-stdio"
    categories = frozenset({"P1", "P2", "P4", "T"})
    pin = Pin(registry="github-release", package="DeusData/codebase-memory-mcp", version="0.10.8",
              digest="6eef49652bc0c7820f43114125044d40bf7f4d97c11b2592f6b0f6a307702325")

    def __init__(self, sandbox_mode: str = "docker") -> None:
        if sandbox_mode != "docker":
            raise RuntimeError("codebase-memory-mcp runs only in the container (DESIGN D2)")
        self._image = None
        self._cache: dict[tuple[str, str], dict] = {}

    def image(self) -> sandbox.BuildResult:
        if self._image is None:
            df = HERE.parent / "sandbox" / "codebase_memory.Dockerfile"
            self._image = sandbox.build(TAG, df, df.parent, timeout=900)
            self.pin = Pin(**{**self.pin.__dict__, "dockerfile_sha256": self._image.dockerfile_sha256})
        return self._image

    # ---- the call plan ---------------------------------------------------
    @staticmethod
    def _projects(corpus: Corpus) -> list[tuple[str, str]]:
        """(repo_path inside the container, prefix for citations). The root
        first; the top-level directories as the fallback the tool's own
        refusal message asks for."""
        cands = [("/corpus", "")]
        tops = sorted({f.split("/", 1)[0] for f in corpus.files if "/" in f})
        for d in tops:
            cands.append((f"/corpus/{d}", d + "/"))
        return cands

    @staticmethod
    def _plan(project: str, prefix: str, tasks: list[Task]) -> list[dict]:
        calls = []
        for t in tasks:
            q = t.query
            if t.category in ("P1", "T"):
                calls.append({"id": f"{t.id}|sg", "tool": "search_graph", "args": {"project": project, "query": q, "limit": TOP}, "charged": True})
                calls.append({"id": f"{t.id}|sg.json", "tool": "search_graph", "args": {"project": project, "query": q, "limit": TOP, "format": "json"}, "charged": False})
                # the snippet calls are appended by the driver plan after the JSON twin resolves:
                # the adapter runs a SECOND driver pass for them (see prepare)
            elif t.category == "P2":
                calls.append({"id": f"{t.id}|tr", "tool": "trace_path", "args": {"project": project, "function_name": q, "direction": "inbound", "depth": 1}, "charged": True})
                calls.append({"id": f"{t.id}|tr.cy", "tool": "query_graph", "charged": False,
                              "args": {"project": project, "query": f"MATCH (a)-[:CALLS]->(f) WHERE f.name = '{_q(q)}' RETURN a.file_path, a.start_line, a.name"}})
            elif t.category == "P4":
                rel = q[len(prefix):] if prefix and q.startswith(prefix) else q
                calls.append({"id": f"{t.id}|cy", "tool": "query_graph", "charged": True,
                              "args": {"project": project, "query": f"MATCH (a)-[:IMPORTS]->(b) WHERE b.path CONTAINS '{_q(rel)}' RETURN DISTINCT a.path"}})
        return calls

    def prepare(self, corpus: Corpus, scratch: Path, tasks: list[Task]) -> None:
        key = (corpus.id, str(scratch))
        if key in self._cache:
            return
        self.image()
        out = scratch / "cbm-out"
        out.mkdir(parents=True, exist_ok=True)
        # Pass 1: initialize, tools/list, index (root, then fallbacks), schema, every planned call.
        # The driver runs the calls in order; a failed index_repository on the root is followed
        # by the fallbacks, and the plan for every candidate project is included so one
        # container session answers everything (a project that failed to index just errors).
        pass1 = []
        cands = self._projects(corpus)
        for repo_path, prefix in cands:
            pass1.append({"id": f"index|{prefix or 'root'}", "tool": "index_repository", "args": {"repo_path": repo_path}, "timeout_s": TIMEOUT_S})
        pass1.append({"id": "list_projects", "tool": "list_projects", "args": {}})
        (out / "calls1.json").write_text(json.dumps(pass1), encoding="utf-8")
        res1 = sandbox.run(TAG, ["-c", "python3 /opt/mcp_driver.py /out/mcp1.json /out/calls1.json -- codebase-memory-mcp"],
                           corpus.path, out, timeout=TIMEOUT_S, private_home=True)
        d1 = _load(out / "mcp1.json")
        if res1.timed_out or d1 is None or d1.get("error"):
            self._cache[key] = {"index": {"secs": None, "ok": False, "err": (d1 or {}).get("error") or res1.stderr[-500:]}, "answers": {}, "timed_out": res1.timed_out, "tools_list_json": (d1 or {}).get("tools_list_json")}
            return
        # which projects indexed, and their names
        projects: list[tuple[str, str]] = []  # (project name, citation prefix)
        index_ms = 0.0
        index_err = []
        by_id = {c["id"]: c for c in d1["calls"]}
        root_ok = False
        for repo_path, prefix in cands:
            c = by_id.get(f"index|{prefix or 'root'}")
            if c is None:
                continue
            if prefix == "" and not c["is_error"]:
                root_ok = True
            if prefix != "" and root_ok:
                continue  # the root indexed: the fallbacks are not needed and are not charged
            if c["is_error"]:
                index_err.append(f"{repo_path}: {c['result_text'][:160]}")
                continue
            index_ms += c["ms"]
            m = re.search(r'"project"\s*:\s*"([^"]+)"', c["result_text"])
            if m:
                projects.append((m.group(1), prefix))
        if not projects:
            self._cache[key] = {"index": {"secs": None, "ok": False, "err": "; ".join(index_err)[-500:]}, "answers": {}, "timed_out": False, "tools_list_json": d1.get("tools_list_json")}
            return
        # Pass 2: the index persists in /private? No: /private is a tmpfs that died with pass 1.
        # So pass 2 re-indexes (uncharged, its time discarded) and runs the plan. One session.
        plan = [{"id": f"reindex|{prefix or 'root'}", "tool": "index_repository", "args": {"repo_path": ("/corpus" if prefix == "" else f"/corpus/{prefix.rstrip('/')}")}, "timeout_s": TIMEOUT_S, "charged": False} for _, prefix in projects]
        plan.append({"id": "schema", "tool": "get_graph_schema", "args": {"project": projects[0][0]}, "charged": False})
        for project, prefix in projects:
            plan.extend(self._plan(project, prefix, tasks))
        (out / "calls2.json").write_text(json.dumps(plan), encoding="utf-8")
        res2 = sandbox.run(TAG, ["-c", "python3 /opt/mcp_driver.py /out/mcp2.json /out/calls2.json -- codebase-memory-mcp"],
                           corpus.path, out, timeout=TIMEOUT_S, private_home=True)
        d2 = _load(out / "mcp2.json")
        if res2.timed_out or d2 is None or d2.get("error"):
            self._cache[key] = {"index": {"secs": index_ms / 1000.0, "ok": False, "err": "pass 2: " + ((d2 or {}).get("error") or res2.stderr[-400:])}, "answers": {}, "timed_out": res2.timed_out, "tools_list_json": d1.get("tools_list_json")}
            return
        # Pass 3: snippets for the top hits of every P1/T search (charged), in one more session.
        results2 = {c["id"]: c for c in d2["calls"]}
        snip_plan = [{"id": f"reindex|{prefix or 'root'}", "tool": "index_repository", "args": {"repo_path": ("/corpus" if prefix == "" else f"/corpus/{prefix.rstrip('/')}")}, "timeout_s": TIMEOUT_S, "charged": False} for _, prefix in projects]
        hits: dict[str, list[tuple[str, str, str, int]]] = {}  # task -> [(project, prefix, qn, line)]
        for project, prefix in projects:
            for t in tasks:
                if t.category in ("P1", "T"):
                    twin = results2.get(f"{t.id}|sg.json")
                    for qn, file, line in _search_rows(twin["result_text"] if twin else ""):
                        hits.setdefault(t.id, []).append((project, prefix, qn, line))
        for tid, rows in hits.items():
            for i, (project, prefix, qn, _) in enumerate(rows[:TOP * len(projects)]):
                snip_plan.append({"id": f"{tid}|snip{i}|{prefix}", "tool": "get_code_snippet", "args": {"project": project, "qualified_name": qn}, "charged": True})
        (out / "calls3.json").write_text(json.dumps(snip_plan), encoding="utf-8")
        d3 = None
        if any(c["tool"] == "get_code_snippet" for c in snip_plan):
            res3 = sandbox.run(TAG, ["-c", "python3 /opt/mcp_driver.py /out/mcp3.json /out/calls3.json -- codebase-memory-mcp"],
                               corpus.path, out, timeout=TIMEOUT_S, private_home=True)
            d3 = _load(out / "mcp3.json")
        results3 = {c["id"]: c for c in (d3 or {}).get("calls", [])}
        # Assemble answers
        answers = {}
        for t in tasks:
            payload, lat, cited, err = [], [], [], None
            for project, prefix in projects:
                if t.category in ("P1", "T"):
                    c = results2.get(f"{t.id}|sg")
                    if c is None:
                        err = "search_graph not run"
                        continue
                    payload.append(c["result_text"])
                    lat.append(c["ms"])
                    twin = results2.get(f"{t.id}|sg.json")
                    for qn, file, line in _search_rows(twin["result_text"] if twin else ""):
                        cited.append([prefix + file, line])
                    i = 0
                    while f"{t.id}|snip{i}|{prefix}" in results3:
                        s = results3[f"{t.id}|snip{i}|{prefix}"]
                        payload.append(s["result_text"])
                        lat.append(s["ms"])
                        cited.extend(_snippet_cite(s["result_text"], prefix))
                        i += 1
                elif t.category == "P2":
                    c = results2.get(f"{t.id}|tr")
                    if c is None:
                        err = "trace_path not run"
                        continue
                    payload.append(c["result_text"])
                    lat.append(c["ms"])
                    twin = results2.get(f"{t.id}|tr.cy")
                    cited.extend(_cypher_rows(twin["result_text"] if twin else "", prefix))
                elif t.category == "P4":
                    c = results2.get(f"{t.id}|cy")
                    if c is None:
                        err = "query_graph not run"
                        continue
                    payload.append(c["result_text"])
                    lat.append(c["ms"])
                    cited.extend(_cypher_rows(c["result_text"], prefix))
                else:
                    err = "category not answered by this adapter"
            answers[t.id] = {"payload": "".join(payload), "calls": len(lat), "latency_ms": lat, "cited": cited, "error": err}
        schema = results2.get("schema")
        self._cache[key] = {
            "index": {"secs": index_ms / 1000.0, "ok": True, "files": None,
                      "err": (f"projects {[p for p, _ in projects]}; get_graph_schema {len(schema['result_text']) if schema else 0} chars (uncharged); " + "; ".join(index_err))[:500]},
            "answers": answers, "timed_out": False, "tools_list_json": d1.get("tools_list_json"),
        }

    def timed_out(self, corpus: Corpus, scratch: Path) -> bool:
        return bool(self._cache.get((corpus.id, str(scratch)), {}).get("timed_out"))

    def index(self, corpus: Corpus, scratch: Path) -> IndexReport:
        idx = self._cache[(corpus.id, str(scratch))]["index"]
        return IndexReport(seconds=idx.get("secs"), ok=bool(idx.get("ok")), files_indexed=idx.get("files"), stderr_tail=str(idx.get("err") or "")[:500])

    def answer(self, corpus: Corpus, task: Task, scratch: Path) -> Answer:
        a = self._cache[(corpus.id, str(scratch))]["answers"].get(task.id)
        if a is None:
            return Answer(payload="", tokens=0, calls=0, latency_ms=[], cited=frozenset(), error="no answer: index failed or task not run")
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


# ---- parsers over the tool's real shapes (fixtures: tests/fixtures/competitive/codebase_memory_mcp.json)

def _q(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "\\'")


def _load(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _search_rows(text: str) -> list[tuple[str, str, int]]:
    """search_graph format=json: {"cols":["qn","label","file","lines","rank"],"rows":[[...]]}."""
    try:
        d = json.loads(text)
    except json.JSONDecodeError:
        return []
    cols = d.get("cols") or []
    out = []
    for row in d.get("rows") or []:
        r = dict(zip(cols, row))
        lines = str(r.get("lines") or "")
        m = re.match(r"(\d+)", lines)
        out.append((str(r.get("qn") or ""), str(r.get("file") or ""), int(m.group(1)) if m else 0))
    return out


def _snippet_cite(text: str, prefix: str) -> list[list]:
    """get_code_snippet: JSON with file_path (absolute /corpus/...) and start_line."""
    try:
        d = json.loads(text)
    except json.JSONDecodeError:
        return []
    fp = str(d.get("file_path") or "")
    if fp.startswith("/corpus/"):
        fp = fp[len("/corpus/"):]
    elif prefix and not fp.startswith(prefix):
        fp = prefix + fp
    return [[fp, int(d.get("start_line") or 0)]] if fp else []


def _cypher_rows(text: str, prefix: str) -> list[list]:
    """query_graph tree text: a `rows: N  (cols: a b c)` header then indented rows;
    the first column is a path, an optional second numeric column a line."""
    cited = []
    for ln in text.splitlines():
        if not ln.startswith("  ") or ln.strip().startswith("(cols") or ln.strip().startswith("hint"):
            continue
        parts = ln.strip().split()
        if not parts:
            continue
        path = parts[0]
        line = 0
        if len(parts) > 1:
            m = re.match(r'^"?(\d+)"?$', parts[1])
            if m:
                line = int(m.group(1))
        cited.append([prefix + path, line])
    return cited


def make(sandbox_mode: str = "docker") -> CodebaseMemory:
    return CodebaseMemory(sandbox_mode)
