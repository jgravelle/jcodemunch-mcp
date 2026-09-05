"""Graft 0.16.0 through the competitive interface, deterministic path only
(docs/competitive/fairness/graft.md).

purpose:  the structurally different approach in the lane (FIELD.md, set
          row 5): a tree-sitter wiring graph plus per-file markdown cards
          written to disk by `graft build`, served over MCP by `graft mcp`;
          no model, no key (DESIGN s1.3 pins this row to that path)
invokes:  the image built from sandbox/graft.Dockerfile (npm ci from a
          lockfile that pins the package and its dependencies by integrity
          hash); per (corpus, run) one container that runs `graft --dir
          /private/graft build /corpus --no-gitignore --no-ignore` (the
          index time, cold: a new container has no extraction cache) and
          then sandbox/mcp_driver.py speaking MCP to `graft --dir
          /private/graft mcp /corpus`: initialize, tools/list (the schema
          weight), every task's charged calls; a second container (build
          again, uncharged) for the P4 follow-up the first answer names
produces: IndexReport from the build's wall time and its own "parsed: N of
          M files" line; one Answer per task whose payload is the tool's
          DEFAULT text output (its "[graft] tokens saved" preamble
          included: an agent receives it) and whose citations are parsed
          from the `path:Lstart-Lend` spans that text prints;
          tools_list_tokens from the live tools/list
refuses:  the `none` sandbox (DESIGN D2); a build that exits non-zero or a
          server that fails initialize (not_runnable)
pinned:   npm @nanonets/graft 0.16.0, integrity
          sha512-L3E5F1aDYJDCARgfR7O2VaMt8xwO1XNYyHiW2n1WhKnj87gPqoxoZJGNbGXfw6XeA9JSJX3naA36RZ+jDf4AcQ==
          (tarball sha256 84771e6417e41a46e76f2cb3886ddeb3814d9efa7ebc15bd7aba41f342b2b9c4)
fairness: docs/competitive/fairness/graft.md. P1 and T: graft_find_code at
          its defaults; P2: graft_trace_calls(symbol); P4:
          graft_trace_calls(path), then, when its answer says "no indexed
          callers ... find its uses with graft grep", graft_find_all over
          the module name as a fixed string (the tool's own instruction,
          second session). Every call charged.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from adapter import Answer, Corpus, IndexReport, Pin, Task, count_tokens
import sandbox

HERE = Path(__file__).resolve().parent
TAG = "jcm-compete/graft:0.16.0"
TIMEOUT_S = 20 * 60
NO_CALLERS = "no indexed callers"
BUILD = "graft --dir /private/graft build /corpus --no-gitignore --no-ignore"
SERVER = ("s=$(date +%s%N); " + BUILD + " > /out/{n}-build.log 2>&1; rc=$?; e=$(date +%s%N); echo \"$rc $s $e\" > /out/{n}-build.txt; "
          "python3 /opt/mcp_driver.py /out/{n}-mcp.json /out/{n}-calls.json -- graft --dir /private/graft mcp /corpus")


INTEGRITY = "sha512-L3E5F1aDYJDCARgfR7O2VaMt8xwO1XNYyHiW2n1WhKnj87gPqoxoZJGNbGXfw6XeA9JSJX3naA36RZ+jDf4AcQ=="


class Graft:
    name = "graft"
    interface = "mcp-stdio"
    categories = frozenset({"P1", "P2", "P4", "T"})
    pin = Pin(registry="npm", package="@nanonets/graft", version="0.16.0",
              digest=INTEGRITY)  # the sha512 `npm ci` enforces from the lockfile, not a hash nothing checks

    def __init__(self, sandbox_mode: str = "docker") -> None:
        if sandbox_mode != "docker":
            raise RuntimeError("Graft runs only in the container (DESIGN D2)")
        self._image = None
        self._cache: dict[tuple[str, str], dict] = {}

    def image(self) -> sandbox.BuildResult:
        if self._image is None:
            df = HERE.parent / "sandbox" / "graft.Dockerfile"
            self._image = sandbox.build(TAG, df, df.parent, timeout=1800)
            self.pin = Pin(**{**self.pin.__dict__, "dockerfile_sha256": self._image.dockerfile_sha256})
        return self._image

    # ---- the call plan ---------------------------------------------------
    @staticmethod
    def _calls(t: Task) -> list[dict]:
        q = t.query
        if t.category in ("P1", "T"):
            return [{"id": f"{t.id}|find", "tool": "graft_find_code", "args": {"query": q}, "charged": True}]
        if t.category == "P2":
            return [{"id": f"{t.id}|trace", "tool": "graft_trace_calls", "args": {"symbol": q}, "charged": True}]
        if t.category == "P4":
            return [{"id": f"{t.id}|trace", "tool": "graft_trace_calls", "args": {"symbol": q}, "charged": True}]
        return []

    @staticmethod
    def _follow_up(t: Task, first: dict) -> list[dict]:
        """The P4 follow-up the tool's own answer names: on "no indexed callers ...
        find its uses with graft grep", a fixed-string search for the module name."""
        if t.category == "P4" and NO_CALLERS in first.get("result_text", ""):
            module = Path(t.query).stem
            return [{"id": f"{t.id}|grep", "tool": "graft_find_all", "args": {"pattern": module, "fixed": True}, "charged": True}]
        return []

    def _session(self, corpus: Corpus, out: Path, n: int, calls: list[dict]):
        (out / f"{n}-calls.json").write_text(json.dumps(calls), encoding="utf-8")
        res = sandbox.run(TAG, ["-c", SERVER.format(n=n)], corpus.path, out, timeout=TIMEOUT_S, private_home=True,
                          extra_env={"MCP_DRIVER_TIMEOUT_S": "300"})
        return res, _load(out / f"{n}-mcp.json")

    def prepare(self, corpus: Corpus, scratch: Path, tasks: list[Task]) -> None:
        key = (corpus.id, str(scratch))
        if key in self._cache:
            return
        self.image()
        out = scratch / "graft-out"
        out.mkdir(parents=True, exist_ok=True)
        calls = [c for t in tasks for c in self._calls(t)]
        res, d = self._session(corpus, out, 0, calls)
        index = _index_report(out, 0)
        tl = (d or {}).get("tools_list_json")
        if res.timed_out or d is None or d.get("error"):
            if index.get("ok"):
                index = {**index, "ok": False, "err": ((d or {}).get("error") or res.stderr[-500:])}
            self._cache[key] = {"index": index, "answers": {}, "timed_out": res.timed_out, "tools_list_json": tl}
            return
        results = {c["id"]: c for c in d["calls"]}
        follow = [c for t in tasks for c in self._follow_up(t, results.get(f"{t.id}|trace", {}))]
        follow_err = None
        wanted = {c["id"] for c in follow}
        if follow:
            res2, d2 = self._session(corpus, out, 1, follow)
            if not res2.timed_out and d2 and not d2.get("error"):
                results.update({c["id"]: c for c in d2["calls"]})
            else:  # the follow-up the tool asked for did not run: UNKNOWN, never a quieter answer
                follow_err = "follow-up session " + ("timed out" if res2.timed_out else f"failed: {((d2 or {}).get('error') or res2.stderr[-200:])!s}")
        answers = {}
        for t in tasks:
            chain = [results[cid] for cid in (f"{t.id}|find", f"{t.id}|trace", f"{t.id}|grep") if cid in results]
            if not chain:
                answers[t.id] = {"payload": "", "calls": 0, "latency_ms": [], "cited": [], "error": "call not run"}
                continue
            cited: list[list] = []
            for c in chain:
                for row in _cites(c["tool"], t.category, c["result_text"]):
                    if row not in cited:
                        cited.append(row)
            answers[t.id] = {"payload": "".join(c["result_text"] for c in chain), "calls": len(chain),
                             "latency_ms": [c["ms"] for c in chain], "cited": cited,
                             "error": (chain[0]["result_text"][:200] if chain[0]["is_error"]
                                       else follow_err if (f"{t.id}|grep" in wanted and f"{t.id}|grep" not in results) else None)}
        self._cache[key] = {"index": index, "answers": answers, "timed_out": False, "tools_list_json": tl}

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


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


_PARSED = re.compile(r"parsed:\s+(\d+)\s+of\s+(\d+)\s+files")


def _index_report(out: Path, n: int) -> dict:
    try:
        rc, s, e = _read(out / f"{n}-build.txt").split()
    except ValueError:
        return {"secs": None, "ok": False, "files": None, "err": "build did not run"}
    log = _read(out / f"{n}-build.log")
    if rc != "0":
        return {"secs": None, "ok": False, "files": None, "err": f"build exit {rc}: {log[-400:]}"}
    m = _PARSED.search(log)
    return {"secs": (int(e) - int(s)) / 1e9, "ok": True, "files": int(m.group(1)) if m else None,
            "err": f"graft build (wall, cold); {m.group(0) if m else 'no parsed line'}"[:500]}


# ---- parsers over the tool's real shapes (fixtures: tests/fixtures/competitive/graft_mcp.json)

_FIND_SPAN = re.compile(r"(?m)^\s+(\S+?):L(\d+)-L\d+\s*$")                       # find_code: a span line under each ranked node
_TRACE_HIT = re.compile(r"(?m)^\s+\S+\s+[←→]\s+\S+\s+\((\S+?):L(\d+)-L\d+\)")     # trace_calls: `  calls ← name (path:Lx-Ly)`
_GREP_SYM = re.compile(r"(?m)^\S+\s+·\s+\S+\s+·\s+(\S+?):L(\d+)-L\d+\s+·")        # find_all: `name · kind · path:Lx-Ly · N in-edges`
_GREP_MODULE = re.compile(r"(?m)^(\S+?)\s+\(module level\)\s+·")                       # find_all: `path (module level) · N in-edges`


def _cites(tool: str, category: str, text: str) -> list[list]:
    if tool == "graft_find_code":
        return [[f, int(ln)] for f, ln in _FIND_SPAN.findall(text)]
    if tool == "graft_trace_calls":  # the header names the queried symbol; only the hits are references
        return [[f, int(ln)] for f, ln in _TRACE_HIT.findall(text)]
    if tool == "graft_find_all":  # a P4 answer is files; one citation per file
        seen: list[list] = []
        for f in [f for f, _ in _GREP_SYM.findall(text)] + _GREP_MODULE.findall(text):
            if [f, 0] not in seen:
                seen.append([f, 0])
        return seen
    return []


def make(sandbox_mode: str = "docker") -> Graft:
    return Graft(sandbox_mode)
