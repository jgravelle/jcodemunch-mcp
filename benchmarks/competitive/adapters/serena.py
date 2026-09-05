"""Serena 1.7.0 through the competitive interface
(docs/competitive/fairness/serena.md).

purpose:  the LSP-backed alternative every third-party source names first
          (FIELD.md, set row 1): symbol lookup and references through a
          language server (pyright for Python), exposed over MCP stdio the
          way its `mcpServers` launch command exposes it
invokes:  the image built from sandbox/serena.Dockerfile (the PyPI wheel,
          pyright 1.1.403 and every dependency pinned by hash in
          sandbox/serena.requirements.txt; pyright's Node fetched at
          build), two container sessions per (corpus, run) in which
          sandbox/mcp_driver.py speaks MCP to `serena start-mcp-server
          --project /corpus`: initialize, tools/list (the schema weight),
          one uncharged get_current_config (its wall time plus the
          initialize round trip is the index time: the language server's
          start and first analysis), then every task's charged calls; a
          second session runs the follow-ups the first session's answers
          name (source reads, references)
produces: IndexReport with the language-server start time and no file
          count (the tool has no index artefact); one Answer per task
          whose payload is the tool's DEFAULT output and whose citations
          are read from that JSON (relative_path, body_location lines,
          LSP 0-based converted to 1-based); tools_list_tokens from the
          live tools/list
refuses:  the `none` sandbox (DESIGN D2); a server that fails initialize
          or the first call (not_runnable); P4 (no documented route: NOT
          COMPARABLE, never a zero)
pinned:   pypi serena-agent 1.7.0, serena_agent-1.7.0-py3-none-any.whl
          sha256 6dbf1459670d96fb0595f84932adef34260a6fe14ba5135b901fdb3c8c76e891;
          pyright 1.1.403 (the version the tool pins)
fairness: docs/competitive/fairness/serena.md. P1: find_symbol then
          find_symbol include_body on the first three matches; T:
          search_for_pattern over the query words; P2: find_symbol then
          find_referencing_symbols per exact-name match. Every call
          charged; get_current_config charged to nothing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from adapter import Answer, Corpus, IndexReport, Pin, Task, count_tokens
import sandbox

HERE = Path(__file__).resolve().parent
TAG = "jcm-compete/serena:1.7.0"
TOP = 3
TIMEOUT_S = 20 * 60
SERVER = "mkdir -p /out/serena-home && cp /opt/serena_config.yml /out/serena-home/serena_config.yml && python3 /opt/mcp_driver.py /out/{out} /out/{calls} -- serena start-mcp-server --project /corpus"


class Serena:
    name = "serena"
    interface = "mcp-stdio"
    categories = frozenset({"P1", "P2", "T"})
    pin = Pin(registry="pypi", package="serena-agent", version="1.7.0",
              digest="6dbf1459670d96fb0595f84932adef34260a6fe14ba5135b901fdb3c8c76e891")

    def __init__(self, sandbox_mode: str = "docker") -> None:
        if sandbox_mode != "docker":
            raise RuntimeError("Serena runs only in the container (DESIGN D2)")
        self._image = None
        self._cache: dict[tuple[str, str], dict] = {}

    def image(self) -> sandbox.BuildResult:
        if self._image is None:
            df = HERE.parent / "sandbox" / "serena.Dockerfile"
            self._image = sandbox.build(TAG, df, df.parent, timeout=1800)
            self.pin = Pin(**{**self.pin.__dict__, "dockerfile_sha256": self._image.dockerfile_sha256})
        return self._image

    # ---- the call plan ---------------------------------------------------
    @staticmethod
    def _plan(tasks: list[Task]) -> list[dict]:
        calls = []
        for t in tasks:
            q = t.query
            if t.category in ("P1", "P2"):
                calls.append({"id": f"{t.id}|fs", "tool": "find_symbol", "args": {"name_path_pattern": q}, "charged": True})
            elif t.category == "T":
                pattern = "|".join(re.escape(w) for w in q.split())
                calls.append({"id": f"{t.id}|sp", "tool": "search_for_pattern",
                              "args": {"substring_pattern": pattern, "restrict_search_to_code_files": True}, "charged": True})
        return calls

    def _session(self, corpus: Corpus, out: Path, n: int, calls: list[dict]):
        (out / f"calls{n}.json").write_text(json.dumps(calls), encoding="utf-8")
        # the driver waits longer than the tool's own `tool_timeout: 240`, so a slow answer is the tool's, not the harness's
        res = sandbox.run(TAG, ["-c", SERVER.format(out=f"mcp{n}.json", calls=f"calls{n}.json")], corpus.path, out, timeout=TIMEOUT_S,
                          extra_env={"MCP_DRIVER_TIMEOUT_S": "300"})
        return res, _load(out / f"mcp{n}.json")

    def prepare(self, corpus: Corpus, scratch: Path, tasks: list[Task]) -> None:
        key = (corpus.id, str(scratch))
        if key in self._cache:
            return
        self.image()
        out = scratch / "serena-out"
        out.mkdir(parents=True, exist_ok=True)
        plan = [{"id": "warm", "tool": "get_current_config", "args": {}, "timeout_s": TIMEOUT_S, "charged": False}]
        plan.extend(self._plan(tasks))
        res, d = self._session(corpus, out, 1, plan)
        tl = (d or {}).get("tools_list_json")
        if res.timed_out or d is None or d.get("error"):
            self._cache[key] = {"index": {"secs": None, "ok": False, "err": (d or {}).get("error") or res.stderr[-500:]}, "answers": {}, "timed_out": res.timed_out, "tools_list_json": tl}
            return
        results = {c["id"]: c for c in d["calls"]}
        warm = results.get("warm")
        if warm is None or warm["is_error"] or warm.get("timed_out"):
            self._cache[key] = {"index": {"secs": None, "ok": False, "err": ((warm or {}).get("result_text") or "first call not run")[:500]}, "answers": {}, "timed_out": bool((warm or {}).get("timed_out")), "tools_list_json": tl}
            return
        index_s = (float(d.get("initialize_ms") or 0) + warm["ms"]) / 1000.0
        # Pass 2: the follow-ups the first answers name.
        follow = []
        for t in tasks:
            c = results.get(f"{t.id}|fs")
            if c is None:
                continue
            syms = _symbols(c["result_text"])
            if t.category == "P1":
                for i, s in enumerate(syms[:TOP]):
                    follow.append({"id": f"{t.id}|body{i}", "tool": "find_symbol", "charged": True,
                                   "args": {"name_path_pattern": s["name_path"], "relative_path": s["relative_path"], "include_body": True}})
            elif t.category == "P2":
                exact = [s for s in syms if s["name_path"].split("/")[-1] == t.query]
                for i, s in enumerate(exact):
                    follow.append({"id": f"{t.id}|refs{i}", "tool": "find_referencing_symbols", "charged": True,
                                   "args": {"name_path": s["name_path"], "relative_path": s["relative_path"]}})
        if follow:
            res2, d2 = self._session(corpus, out, 2, follow)
            if not res2.timed_out and d2 and not d2.get("error"):
                results.update({c["id"]: c for c in d2["calls"]})
        answers = {}
        for t in tasks:
            first = {"P1": "fs", "P2": "fs", "T": "sp"}.get(t.category)
            c = results.get(f"{t.id}|{first}") if first else None
            if c is None:
                answers[t.id] = {"payload": "", "calls": 0, "latency_ms": [], "cited": [], "error": "category not answered by this adapter" if first is None else "call not run"}
                continue
            chain = [c]
            for tag in ("body", "refs"):
                i = 0
                while f"{t.id}|{tag}{i}" in results:
                    chain.append(results[f"{t.id}|{tag}{i}"])
                    i += 1
            cited: list[list] = []
            # a P2 answer cites the references it found, not the lookup that located the symbol
            for x in (chain[1:] if t.category == "P2" else chain):
                for row in _cites(x["result_text"]):
                    if row not in cited:
                        cited.append(row)
            answers[t.id] = {"payload": "".join(x["result_text"] for x in chain), "calls": len(chain), "latency_ms": [x["ms"] for x in chain],
                             "cited": cited, "error": (c["result_text"][:200] if c["is_error"] else None)}
        self._cache[key] = {
            "index": {"secs": index_s, "ok": True, "files": None,
                      "err": f"initialize {d.get('initialize_ms')} ms + get_current_config {warm['ms']} ms (uncharged); server {d.get('server_info')}"[:500]},
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


# ---- parsers over the tool's real shapes (fixtures: tests/fixtures/competitive/serena_mcp.json)

def _load(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _parse_json(text: str):
    """The tool returns JSON, sometimes behind a one-line prefix ("Depth 0 overview:")."""
    text = text.strip()
    for start in (0, text.find("{"), text.find("[")):
        if start < 0:
            continue
        try:
            return json.loads(text[start:])
        except json.JSONDecodeError:
            continue
    return None


def _symbols(text: str) -> list[dict]:
    """Every symbol dict in a find_symbol / find_referencing_symbols result:
    {name_path, relative_path, start_line (1-based) or None}. The tool groups
    results by relative_path and kind, so a dict may inherit its path from an
    enclosing key."""
    out: list[dict] = []

    def walk(obj, path: str | None):
        if isinstance(obj, dict):
            if "name_path" in obj:
                rel = obj.get("relative_path") or path
                loc = obj.get("body_location") or {}
                start = loc.get("start_line")
                out.append({"name_path": str(obj["name_path"]), "relative_path": str(rel or ""),
                            "start_line": (int(start) + 1) if isinstance(start, int) else None})
                return
            for k, v in obj.items():
                walk(v, k if (isinstance(k, str) and ("/" in k or "." in k) and not isinstance(v, (str, int))) else path)
        elif isinstance(obj, list):
            for v in obj:
                walk(v, path)

    walk(_parse_json(text), None)
    return [s for s in out if s["relative_path"]]


def _cites(text: str) -> list[list]:
    return [[s["relative_path"], s["start_line"] or 0] for s in _symbols(text)]


def make(sandbox_mode: str = "docker") -> Serena:
    return Serena(sandbox_mode)
