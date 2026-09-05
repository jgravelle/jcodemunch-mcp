"""CodeGraph 1.6.0 through the competitive interface
(docs/competitive/fairness/codegraph.md).

purpose:  the largest adoption in the lane (FIELD.md, set row 4): a
          tree-sitter knowledge graph in SQLite behind an MCP server whose
          default surface is one tool, driven over stdio the way its
          installer's `serve --mcp` launch line drives it
invokes:  the image built from sandbox/codegraph.Dockerfile (the linux-x64
          release bundle verified against the release's SHA256SUMS); per
          (corpus, run) one container for the P1/P2/P4 tasks and one per T
          task, each copying the read-only corpus to the uid-owned tmpfs
          (the tool keeps its index inside the project root), running
          `codegraph init --yes` (the index time, from the first container),
          then sandbox/mcp_driver.py speaking MCP to `codegraph serve --mcp
          --path <copy> --no-watch`: initialize, tools/list (the schema
          weight, under the README's example CODEGRAPH_MCP_TOOLS allowlist),
          then every task's charged calls
produces: IndexReport from `init`'s wall time and `status`'s file count;
          one Answer per task whose payload is the tool's DEFAULT text
          output and whose citations are parsed from the locations that
          text prints; tools_list_tokens from the live tools/list
refuses:  the `none` sandbox (DESIGN D2); a server that fails initialize or
          an init that exits non-zero (not_runnable)
pinned:   github-release colbymchenry/codegraph v1.6.0,
          codegraph-linux-x64.tar.gz
          sha256 de3391f79ed42622d937e6cd5b7642a7ea8bb7d1473607e80b879ba73ef216b0
fairness: docs/competitive/fairness/codegraph.md. P1: codegraph_search
          (limit 3) and codegraph_node(symbol, includeCode) for the name;
          T: codegraph_explore, one session per task (explore elides lines
          it sent earlier in a session, so a shared session would make a
          task's tokens depend on the order); P2: codegraph_callers; P4:
          codegraph_node(file, symbolsOnly). Every call charged.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from adapter import Answer, Corpus, IndexReport, Pin, Task, count_tokens
import sandbox

HERE = Path(__file__).resolve().parent
TAG = "jcm-compete/codegraph:1.6.0"
TOP = 3
TIMEOUT_S = 20 * 60
MCP_TOOLS = "explore,node,search,callers"  # the README's own example, verbatim (fairness note)
# The corpus is read-only and the tool's index lives inside the project root, so each container
# indexes a copy on the uid-owned tmpfs; the copy is a harness cost, `init` is the index time.
SERVER = ("cp -r /corpus /private/project && cd /private/project && s=$(date +%s%N); "
          "codegraph init --yes /private/project > /out/{n}-init.log 2>&1; rc=$?; e=$(date +%s%N); echo \"$rc $s $e\" > /out/{n}-init.txt; "
          "codegraph status /private/project > /out/{n}-status.log 2>&1; "
          "python3 /opt/mcp_driver.py /out/{n}-mcp.json /out/{n}-calls.json -- codegraph serve --mcp --path /private/project --no-watch")


class CodeGraph:
    name = "codegraph"
    interface = "mcp-stdio"
    categories = frozenset({"P1", "P2", "P4", "T"})
    pin = Pin(registry="github-release", package="colbymchenry/codegraph", version="1.6.0",
              digest="de3391f79ed42622d937e6cd5b7642a7ea8bb7d1473607e80b879ba73ef216b0")

    def __init__(self, sandbox_mode: str = "docker") -> None:
        if sandbox_mode != "docker":
            raise RuntimeError("CodeGraph runs only in the container (DESIGN D2)")
        self._image = None
        self._cache: dict[tuple[str, str], dict] = {}

    def image(self) -> sandbox.BuildResult:
        if self._image is None:
            df = HERE.parent / "sandbox" / "codegraph.Dockerfile"
            self._image = sandbox.build(TAG, df, df.parent, timeout=1800)
            self.pin = Pin(**{**self.pin.__dict__, "dockerfile_sha256": self._image.dockerfile_sha256})
        return self._image

    # ---- the call plan ---------------------------------------------------
    @staticmethod
    def _calls(t: Task) -> list[dict]:
        q = t.query
        if t.category == "P1":
            return [{"id": f"{t.id}|search", "tool": "codegraph_search", "args": {"query": q, "limit": TOP}, "charged": True},
                    {"id": f"{t.id}|node", "tool": "codegraph_node", "args": {"symbol": q, "includeCode": True}, "charged": True}]
        if t.category == "T":
            return [{"id": f"{t.id}|explore", "tool": "codegraph_explore", "args": {"query": q}, "charged": True}]
        if t.category == "P2":
            return [{"id": f"{t.id}|callers", "tool": "codegraph_callers", "args": {"symbol": q}, "charged": True}]
        if t.category == "P4":
            return [{"id": f"{t.id}|deps", "tool": "codegraph_node", "args": {"file": q, "symbolsOnly": True}, "charged": True}]
        return []

    @staticmethod
    def _sessions(tasks: list[Task]) -> list[list[Task]]:
        """One session for every non-T task, then one per T task (fairness note)."""
        base = [t for t in tasks if t.category != "T"]
        return ([base] if base else []) + [[t] for t in tasks if t.category == "T"]

    def _session(self, corpus: Corpus, out: Path, n: int, calls: list[dict]):
        (out / f"{n}-calls.json").write_text(json.dumps(calls), encoding="utf-8")
        res = sandbox.run(TAG, ["-c", SERVER.format(n=n)], corpus.path, out, timeout=TIMEOUT_S, private_home=True,
                          extra_env={"CODEGRAPH_MCP_TOOLS": MCP_TOOLS, "MCP_DRIVER_TIMEOUT_S": "300"})
        return res, _load(out / f"{n}-mcp.json")

    def prepare(self, corpus: Corpus, scratch: Path, tasks: list[Task]) -> None:
        key = (corpus.id, str(scratch))
        if key in self._cache:
            return
        self.image()
        out = scratch / "codegraph-out"
        out.mkdir(parents=True, exist_ok=True)
        results: dict[str, dict] = {}
        index = None
        tl = None
        timed_out = False
        for n, group in enumerate(self._sessions(tasks)):
            calls = [c for t in group for c in self._calls(t)]
            res, d = self._session(corpus, out, n, calls)
            if n == 0:
                index = _index_report(out, n)
                tl = (d or {}).get("tools_list_json")
            if res.timed_out or d is None or d.get("error"):
                timed_out = timed_out or res.timed_out
                if n == 0:
                    index = {"secs": None, "ok": False, "files": None, "err": ((d or {}).get("error") or res.stderr[-500:])}
                continue
            results.update({c["id"]: c for c in d["calls"]})
        answers = {}
        for t in tasks:
            chain = [results[c["id"]] for c in self._calls(t) if c["id"] in results]
            if not chain:
                answers[t.id] = {"payload": "", "calls": 0, "latency_ms": [], "cited": [], "error": "call not run"}
                continue
            cited: list[list] = []
            for c in chain:
                for row in _cites(c["tool"], c["args"], c["result_text"]):
                    if row not in cited:
                        cited.append(row)
            answers[t.id] = {"payload": "".join(c["result_text"] for c in chain), "calls": len(chain),
                             "latency_ms": [c["ms"] for c in chain], "cited": cited,
                             "error": (chain[0]["result_text"][:200] if chain[0]["is_error"] else None)}
        self._cache[key] = {"index": index or {"secs": None, "ok": False, "files": None, "err": "no session ran"},
                            "answers": answers, "timed_out": timed_out, "tools_list_json": tl}

    def timed_out(self, corpus: Corpus, scratch: Path) -> bool:
        return bool(self._cache.get((corpus.id, str(scratch)), {}).get("timed_out"))

    def index(self, corpus: Corpus, scratch: Path) -> IndexReport:
        idx = self._cache[(corpus.id, str(scratch))]["index"]
        return IndexReport(seconds=idx.get("secs"), ok=bool(idx.get("ok")), files_indexed=idx.get("files"), stderr_tail=str(idx.get("err") or "")[:500])

    def answer(self, corpus: Corpus, task: Task, scratch: Path) -> Answer:
        a = self._cache[(corpus.id, str(scratch))]["answers"].get(task.id)
        if a is None:
            return Answer(payload="", tokens=0, calls=0, latency_ms=[], cited=frozenset(), error="no answer: server failed or task not run")
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


# ---- the index report from the container's files ----------------------------

def _load(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _index_report(out: Path, n: int) -> dict:
    try:
        rc, s, e = (out / f"{n}-init.txt").read_text(encoding="utf-8").split()
    except (OSError, ValueError):
        return {"secs": None, "ok": False, "files": None, "err": "init did not run"}
    log = _read(out / f"{n}-init.log")
    if rc != "0":
        return {"secs": None, "ok": False, "files": None, "err": f"init exit {rc}: {log[-400:]}"}
    return {"secs": (int(e) - int(s)) / 1e9, "ok": True, "files": _files_indexed(_read(out / f"{n}-status.log")),
            "err": f"codegraph init --yes (wall); status: {_read(out / f'{n}-status.log')[:200]!r}"[:500]}


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _files_indexed(status_text: str):
    m = re.search(r"Files:\s+([\d,]+)", status_text)
    return int(m.group(1).replace(",", "")) if m else None


# ---- parsers over the tool's real shapes (fixtures: tests/fixtures/competitive/codegraph_mcp.json)

_SEARCH_LOC = re.compile(r"(?m)^(\S+?):(\d+)\s*$")                                # search: a `path:line` line under each hit
_NODE_LOC = re.compile(r"\*\*Location:\*\*\s+(\S+?):(\d+)")                         # node symbol mode
_CALLER_LOC = re.compile(r"(?m)^-\s+\S+\s+\([^)]*\)\s+-\s+(\S+?):(\d+)\s*$")         # callers: `- name (kind) - path:line`
_DEPENDENTS = re.compile(r"used by\s+[\d,]+\s+files?:\s*(.*)")                       # node file mode: `used by N files: a, b, +K more`


def _cites(tool: str, args: dict, text: str) -> list[list]:
    if tool == "codegraph_search":
        return [[f, int(ln)] for f, ln in _SEARCH_LOC.findall(text)]
    if tool == "codegraph_callers":
        return [[f, int(ln)] for f, ln in _CALLER_LOC.findall(text)]
    if tool == "codegraph_node" and args.get("file") and not args.get("symbol"):
        m = _DEPENDENTS.search(text)
        if not m:
            return []
        return [[p.strip(), 0] for p in m.group(1).split(",") if p.strip() and not p.strip().startswith("+")]
    if tool == "codegraph_node":
        return [[f, int(ln)] for f, ln in _NODE_LOC.findall(text)]
    return []  # explore: the T payload is charged, nothing is cited


def make(sandbox_mode: str = "docker") -> CodeGraph:
    return CodeGraph(sandbox_mode)
