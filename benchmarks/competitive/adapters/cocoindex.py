"""CocoIndex Code 0.2.41 through the competitive interface
(docs/competitive/fairness/cocoindex.md): the embedding representative.

purpose:  the embedding-shaped competitor: a vector search over AST chunks
          with a local sentence-transformers model on the CPU, served over
          MCP stdio by a daemon its own client starts inside the container
invokes:  the image built from sandbox/cocoindex.Dockerfile (pip from a hash
          lockfile, the [full] default model downloaded at build), one
          container per (corpus, run): the corpus copied to the uid-owned
          tmpfs (its databases, settings and .gitignore line live inside
          the project), the documented user settings file written, then
          `ccc init` + `ccc index` (the index time, daemon start and model
          load included), `ccc status` (its own file count), and the driver
          speaking MCP to `ccc mcp`
produces: IndexReport from the init+index wall; one Answer per P1 or T task
          from its one `search` tool at the defaults (limit 5, refresh_index
          true), citations from the `file_path`/`start_line` fields of the
          result; P2 and P4 are not attempted (no documented tool answers
          them: fairness note)
refuses:  the `none` sandbox (a competitor runs only in the container,
          DESIGN D2)
pinned:   pypi cocoindex-code 0.2.41, wheel sha256
          bf71bf24388f6cd9cdd76cca7b4bcd76e8636f3af27fed29604f0e39bd115328,
          the same hash the lockfile makes pip require; model
          Snowflake/snowflake-arctic-embed-xs (the [full] default)
fairness: docs/competitive/fairness/cocoindex.md.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from adapter import Answer, Corpus, IndexReport, Pin, Task, count_tokens
import sandbox

HERE = Path(__file__).resolve().parent
TAG = "jcm-compete/cocoindex:0.2.41"
TIMEOUT_S = 20 * 60  # DESIGN s9.2: the per-tool ceiling
PROJECT = "/private/project"
MODEL = "Snowflake/snowflake-arctic-embed-xs"
SETTINGS_YML = f"embedding:\n  provider: sentence-transformers\n  model: {MODEL}\n"
# The user settings file first (the documented keys), the copy, then init + index
# timed together (the daemon's first start and the model load are the tool's own
# first-use cost), status for its file count, then the MCP server.
SERVER = ("mkdir -p /private/.cocoindex_code && printf " + repr(SETTINGS_YML).replace("'", '"') + " > /private/.cocoindex_code/global_settings.yml; "
          f"cp -r /corpus {PROJECT} && cd {PROJECT} && s=$(date +%s%N); "
          "ccc init > /out/{n}-init.log 2>&1 && ccc index > /out/{n}-index.log 2>&1; rc=$?; e=$(date +%s%N); echo \"$rc $s $e\" > /out/{n}-index.txt; "
          "ccc status > /out/{n}-status.log 2>&1; "
          "python3 /opt/mcp_driver.py /out/{n}-mcp.json /out/{n}-calls.json -- ccc mcp; "
          "cp /private/.cocoindex_code/daemon.log /out/{n}-daemon.log 2>/dev/null; ls -la /private/.cocoindex_code " + PROJECT + "/.cocoindex_code > /out/{n}-ls.log 2>&1")


class CocoIndex:
    name = "cocoindex"
    interface = "mcp-stdio"
    categories = frozenset({"P1", "T"})
    pin = Pin(registry="pypi", package="cocoindex-code", version="0.2.41",
              digest="bf71bf24388f6cd9cdd76cca7b4bcd76e8636f3af27fed29604f0e39bd115328")

    def __init__(self, sandbox_mode: str = "docker") -> None:
        if sandbox_mode != "docker":
            raise RuntimeError("cocoindex runs only in the container (DESIGN D2)")
        self._image = None
        self._cache: dict[tuple[str, str], dict] = {}

    def image(self) -> sandbox.BuildResult:
        if self._image is None:
            df = HERE.parent / "sandbox" / "cocoindex.Dockerfile"
            self._image = sandbox.build(TAG, df, df.parent, timeout=3600)
            self.pin = Pin(**{**self.pin.__dict__, "dockerfile_sha256": self._image.dockerfile_sha256})
        return self._image

    @staticmethod
    def _calls(t: Task) -> list[dict]:
        if t.category in ("P1", "T"):
            return [{"id": f"{t.id}|search", "tool": "search", "args": {"query": t.query}, "charged": True}]
        return []

    def prepare(self, corpus: Corpus, scratch: Path, tasks: list[Task]) -> None:
        key = (corpus.id, str(scratch))
        if key in self._cache:
            return
        self.image()
        out = scratch / "cocoindex-out"
        out.mkdir(parents=True, exist_ok=True)
        calls = [c for t in tasks for c in self._calls(t)]
        (out / "0-calls.json").write_text(json.dumps(calls), encoding="utf-8")
        res = sandbox.run(TAG, ["-c", SERVER.format(n=0)], corpus.path, out, timeout=TIMEOUT_S, private_home=True,
                          extra_env={"MCP_DRIVER_TIMEOUT_S": "300"})
        d = _load(out / "0-mcp.json")
        index = _index_report(out, 0)
        tl = (d or {}).get("tools_list_json")
        if res.timed_out or d is None or d.get("error"):
            if index.get("ok"):
                index = {**index, "ok": False, "err": ((d or {}).get("error") or res.stderr[-500:])}
            self._cache[key] = {"index": index, "answers": {}, "timed_out": res.timed_out, "tools_list_json": tl}
            return
        results = {c["id"]: c for c in d["calls"]}
        answers = {}
        for t in tasks:
            c = results.get(f"{t.id}|search")
            if c is None:
                answers[t.id] = {"payload": "", "calls": 0, "latency_ms": [], "cited": [], "error": "call not run"}
                continue
            answers[t.id] = {"payload": c["result_text"], "calls": 1, "latency_ms": [c["ms"]], "cited": _cites(c["result_text"]),
                             "error": (c["result_text"][:200] if c["is_error"] else None)}
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


_FILES = re.compile(r"^\s*Files:\s+(\d[\d,]*)", re.M)  # the `Index stats:` block's own line; "Chunks:" is not a file count


def _index_report(out: Path, n: int) -> dict:
    try:
        rc, s, e = _read(out / f"{n}-index.txt").split()
    except ValueError:
        return {"secs": None, "ok": False, "files": None, "err": "init/index did not run"}
    log = _read(out / f"{n}-init.log") + _read(out / f"{n}-index.log")
    if rc != "0":
        return {"secs": None, "ok": False, "files": None, "err": f"init/index exit {rc}: {log[-400:]}"}
    status = _read(out / f"{n}-status.log")
    m = _FILES.search(status)
    return {"secs": (int(e) - int(s)) / 1e9, "ok": True, "files": int(m.group(1).replace(",", "")) if m else None,
            "err": f"ccc init + ccc index (wall, cold, daemon start and model load included); status: {' '.join(status.split())[:300]}"[:500]}


def _cites(text: str) -> list[list]:
    """(rel_path, start_line) from the `search` result's JSON (`file_path`,
    `start_line` per result); an absolute path under the project copy is made
    relative; anything unparseable cites nothing."""
    try:
        doc = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    rows = doc.get("results") if isinstance(doc, dict) else None
    found: list[list] = []
    for r in rows or []:
        if not isinstance(r, dict) or not isinstance(r.get("file_path"), str):
            continue
        fp = r["file_path"]
        for prefix in (PROJECT + "/", "/corpus/"):
            if fp.startswith(prefix):
                fp = fp[len(prefix):]
        row = [fp, int(r.get("start_line") or 0)]
        if row not in found:
            found.append(row)
    return found


def make(sandbox_mode: str = "docker") -> CocoIndex:
    return CocoIndex(sandbox_mode)
