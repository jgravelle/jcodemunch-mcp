"""zvec-grep 0.2.2 through the competitive interface
(docs/competitive/fairness/zvec_grep.md): set row 9, the ninth adapter.

purpose:  the ripgrep + BM25 + local-vector competitor (FIELD.md §3.2 D,
          §5.2 row 9): a workspace index under `<root>/.zvec-grep/`, a CLI
          (`zg`) an agent runs as a subprocess, and an MCP server that is
          HTTP behind a daemon; the adapter takes the CLI in `--mode
          direct` (in-process, no daemon, no port), so every answer is one
          process start plus the tool's own work
invokes:  the image built from sandbox/zvec_grep.Dockerfile (npm ci from a
          lockfile that pins the package and its dependency tree by
          integrity hash; the local embedding model warmed into
          /opt/zg-models at build), one container per (corpus, run): the
          corpus copied to the uid-owned tmpfs (the index lives inside the
          workspace and the mount is read-only), `zg index` timed (the
          index time), `zg status` (its own file count), then every task's
          charged call from a shell script this adapter writes, each timed
          inside the container; last, UNCHARGED, `zg server run` on the
          container's loopback for one `tools/list` over its HTTP MCP
          endpoint (the schema weight of its one-tool default surface),
          best effort: a failure there leaves tools_list_tokens None and
          changes no row
produces: IndexReport from the index wall and the `N scanned, M added, ...`
          line of `zg index`; one Answer per task whose payload is the
          tool's DEFAULT agent-mode output and whose citations are parsed
          from the `#<rank> matchedBy=<x> <path>:<start>-<end>` headers
          (indexed routes) or the per-file blocks of its `--rg` route
          (file-level, line 0)
refuses:  the `none` sandbox (a competitor runs only in the container,
          DESIGN D2)
pinned:   npm @zvec/zvec-grep 0.2.2, integrity
          sha512-6xsF21zgUh98W3BmH7+Nz/Esdx4Ps3ibs+DHluh/uo2O45xCMeM+pkmdRlcagkONHRO2pM2KiGgKHVmRGKxBtA==
          (the hash `npm ci` enforces from sandbox/zvec_grep.package-lock.json);
          model minishlab/potion-code-16M-v2 at the revision the tool's own
          catalog pins (e9d2a44c…), downloaded at build
fairness: docs/competitive/fairness/zvec_grep.md. P1 `zg query <name>
          --prefer-symbol` (its indexed hybrid route with the symbol
          preference its CLI documents); T `zg query <words>`; P2 `zg query
          --rg -w <name>` and P4 `zg query --rg -F <module>` (its
          documented exhaustive route, which its agent guidance names for
          exact identifiers). Every call charged; the model, the limit and
          the preview at the tool's defaults.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

from adapter import Answer, Corpus, IndexReport, Pin, Task, count_tokens
import sandbox

HERE = Path(__file__).resolve().parent
TAG = "jcm-compete/zvec-grep:0.2.2"
TIMEOUT_S = 20 * 60  # DESIGN s9.2: the per-tool ceiling
PROJECT = "/private/project"
MODEL = "local/potion-code-16m-v2"
INTEGRITY = "sha512-6xsF21zgUh98W3BmH7+Nz/Esdx4Ps3ibs+DHluh/uo2O45xCMeM+pkmdRlcagkONHRO2pM2KiGgKHVmRGKxBtA=="
ENV = {
    "ZVEC_GREP_MODE": "direct",          # in-process; no daemon, no port (docs/06-server.md)
    "ZVEC_GREP_HOME": "/private/zg-home",  # the tool's own home (config, logs) on the tmpfs
    "ZVEC_GREP_MODEL_CACHE": "/opt/zg-models",  # the model warmed at build; nothing to download under --network none
    "ZVEC_GREP_EMBEDDING": MODEL,
}


def _cmds(task: Task) -> list[list[str]]:
    q = task.query
    if task.category == "P1":
        return [["zg", "query", "--prefer-symbol", q]]
    if task.category == "T":
        return [["zg", "query", q]]
    if task.category == "P2":
        return [["zg", "query", "--rg", "-w", q]]
    if task.category == "P4":
        return [["zg", "query", "--rg", "-F", Path(q).stem]]
    return []


class ZvecGrep:
    name = "zvec_grep"
    interface = "cli"
    categories = frozenset({"P1", "P2", "P4", "T"})
    pin = Pin(registry="npm", package="@zvec/zvec-grep", version="0.2.2", digest=INTEGRITY)

    def __init__(self, sandbox_mode: str = "docker") -> None:
        if sandbox_mode != "docker":
            raise RuntimeError("zvec-grep runs only in the container (DESIGN D2)")
        self._image = None
        self._cache: dict[tuple[str, str], dict] = {}

    def image(self) -> sandbox.BuildResult:
        if self._image is None:
            df = HERE.parent / "sandbox" / "zvec_grep.Dockerfile"
            self._image = sandbox.build(TAG, df, df.parent, timeout=1800)
            self.pin = Pin(**{**self.pin.__dict__, "dockerfile_sha256": self._image.dockerfile_sha256})
        return self._image

    def prepare(self, corpus: Corpus, scratch: Path, tasks: list[Task]) -> None:
        key = (corpus.id, str(scratch))
        if key in self._cache:
            return
        self.image()
        out = scratch / "zvec_grep-out"
        out.mkdir(parents=True, exist_ok=True)
        (out / "run.sh").write_text(_script(tasks), encoding="utf-8", newline="\n")
        (out / "tools_list.py").write_text(TOOLS_LIST_PY, encoding="utf-8", newline="\n")
        res = sandbox.run(TAG, ["/out/run.sh"], corpus.path, out, timeout=TIMEOUT_S, private_home=True, extra_env=ENV)
        timings = _timings(out / "timings.txt")
        idx_rc, idx_ms = timings.get("index", (1, None))
        index_log = _read(out / "index.txt")
        index = {"secs": (idx_ms / 1000.0) if idx_ms is not None else None,
                 "ok": idx_rc == 0 and idx_ms is not None and not res.timed_out,
                 "files": _files_indexed(index_log),
                 "err": (f"zg index (wall, cold, direct mode; model from the build cache); {_scan_line(index_log) or 'no scanned line'}"
                         if idx_rc == 0 and idx_ms is not None else (index_log[-400:] or res.stderr[-400:] or "index did not run"))[:500]}
        answers = {}
        for t in tasks:
            payload, lat, cited, err = [], [], [], None
            for i, cmd in enumerate(_cmds(t)):
                base = f"{t.id}.{i}"
                rc, ms = timings.get(base, (1, None))
                if ms is None:
                    err = "not run (container timed out or the script stopped)"
                    break
                lat.append(float(ms))
                text = _read(out / f"{base}.txt")
                if rc != 0 and not text.strip():
                    text = _read(out / f"{base}.err")  # what the agent sees on a miss
                payload.append(text)
                for row in (_cite_rg(text) if "--rg" in cmd else _cite_indexed(text)):
                    if row not in cited:
                        cited.append(row)
            answers[t.id] = {"payload": "".join(payload), "calls": len(lat), "latency_ms": lat, "cited": cited, "error": err}
        tl = None
        d = _load(out / "tools_list.json")
        if isinstance(d, dict) and isinstance(d.get("tools_list_json"), str):
            tl = d["tools_list_json"]
        self._cache[key] = {"index": index, "answers": answers, "timed_out": res.timed_out, "tools_list_json": tl}

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
        """The CLI pays no schema per call; the figure is the weight of the MCP
        surface an agent using the server would pay, captured uncharged
        (None when the capture failed: the fairness note says so)."""
        for v in self._cache.values():
            tl = v.get("tools_list_json")
            if tl:
                return count_tokens(tl)
        return None

    def version(self) -> str:
        return self.pin.version


# ---- the container script ------------------------------------------------------

def _script(tasks: list[Task]) -> str:
    lines = ["set +e",
             'ms() { s=$(date +%s%N); "$@"; r=$?; e=$(date +%s%N); echo "$LABEL rc=$r ms=$(( (e-s)/1000000 ))" >> /out/timings.txt; return $r; }',
             f"cp -r /corpus {PROJECT} && cd {PROJECT} || exit 1",
             f"LABEL=index; ms zg index . --embedding {MODEL} > /out/index.txt 2>&1",
             "zg status . > /out/status.txt 2>&1"]
    for t in tasks:
        for i, cmd in enumerate(_cmds(t)):
            base = f"{t.id}.{i}"
            lines.append(f"LABEL={shlex.quote(base)}; ms {shlex.join(cmd)} > /out/{shlex.quote(base)}.txt 2>/out/{shlex.quote(base)}.err")
    # Uncharged, after every answer: the schema weight of the MCP surface.
    lines.append("python3 /out/tools_list.py > /out/tools_list.log 2>&1")
    return "\n".join(lines) + "\n"


# The MCP endpoint is HTTP behind `zg server` (docs/03-mcp.md: 127.0.0.1:7999/mcp);
# inside the container the loopback exists under --network none. Streamable
# HTTP: one POST per JSON-RPC message; the reply is JSON or one SSE event.
TOOLS_LIST_PY = r'''
import json, os, subprocess, sys, time, urllib.request

PORT = "7999"
URL = f"http://127.0.0.1:{PORT}/mcp"
TOKEN_FILE = "/private/zg-token"
env = dict(os.environ, ZVEC_GREP_MODE="server", ZVEC_GREP_SERVER_URL=f"http://127.0.0.1:{PORT}")
# `--token-file` is READ, not created (first capture: "ENOENT ... /private/zg-token"
# and a refused connection); the file carries the bearer token the server expects.
import secrets
with open(TOKEN_FILE, "w") as fh:
    fh.write(secrets.token_hex(16))
srv = subprocess.Popen(["zg", "server", "run", "--listen", f"127.0.0.1:{PORT}", "--token-file", TOKEN_FILE, "--mcp-toolset", "agent"],
                       stdout=open("/out/server.log", "w"), stderr=subprocess.STDOUT, env=env)
token = ""
deadline = time.time() + 60
while time.time() < deadline:
    try:
        token = open(TOKEN_FILE).read().strip()
    except OSError:
        token = ""
    if subprocess.run(["zg", "server", "status", "--check-ready", "--token-file", TOKEN_FILE], env=env, capture_output=True).returncode == 0:
        break
    time.sleep(1)
session = None


def post(msg):
    global session
    data = json.dumps(msg).encode()
    hdr = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if token:
        hdr["Authorization"] = f"Bearer {token}"
    if session:
        hdr["Mcp-Session-Id"] = session
    req = urllib.request.Request(URL, data=data, headers=hdr, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        session = r.headers.get("Mcp-Session-Id") or session
        body = r.read().decode("utf-8", "replace")
        ct = r.headers.get("Content-Type", "")
    if "text/event-stream" in ct:
        for ln in body.splitlines():
            if ln.startswith("data:"):
                try:
                    d = json.loads(ln[5:].strip())
                except json.JSONDecodeError:
                    continue
                if isinstance(d, dict) and d.get("id") == msg.get("id"):
                    return d
        return None
    return json.loads(body) if body.strip() else None


out = {"tools_list_json": None, "error": None}
try:
    init = post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "jcm-compete", "version": "0"}}})
    try:
        post({"jsonrpc": "2.0", "method": "notifications/initialized"})
    except Exception:
        pass
    tl = post({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    tools = (tl or {}).get("result", {}).get("tools")
    if isinstance(tools, list):
        out["tools_list_json"] = json.dumps([{"name": t.get("name"), "description": t.get("description"), "inputSchema": t.get("inputSchema")} for t in tools])
    else:
        out["error"] = f"no tools in reply: {str(tl)[:300]}"
    out["serverInfo"] = (init or {}).get("result", {}).get("serverInfo")
except Exception as e:
    out["error"] = repr(e)[:300]
finally:
    subprocess.run(["zg", "server", "off", "--token-file", TOKEN_FILE], env=env, capture_output=True)
    try:
        srv.wait(timeout=10)
    except Exception:
        srv.kill()
json.dump(out, open("/out/tools_list.json", "w"))
print(out.get("error") or "ok")
'''


# ---- readers and parsers ----------------------------------------------------------

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _read(p: Path) -> str:
    try:
        return _ANSI.sub("", p.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return ""


def _load(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _timings(p: Path) -> dict[str, tuple[int, int]]:
    timings: dict[str, tuple[int, int]] = {}
    for ln in _read(p).splitlines():
        m = re.match(r"^(\S+) rc=(\d+) ms=(\d+)$", ln)
        if m:
            timings[m.group(1)] = (int(m.group(2)), int(m.group(3)))
    return timings


_SCAN = re.compile(r"(\d[\d,]*) scanned, (\d[\d,]*) added, (\d[\d,]*) modified")  # printIndexResult (src/cli/format/status.ts)


def _scan_line(text: str) -> str:
    m = _SCAN.search(_ANSI.sub("", text))
    return m.group(0) if m else ""


def _files_indexed(text: str) -> int | None:
    """The tool's own `M added` count on a cold run: the files it indexed
    (`N scanned` is what it looked at before its own skip rules). The theme
    wraps the numbers in colour codes on a TTY; stripped here as well as in
    `_read`, so the parser does not depend on where the text came from."""
    m = _SCAN.search(_ANSI.sub("", text))
    return int(m.group(2).replace(",", "")) if m else None


# Default agent-mode header (src/cli/format/context.ts):
#   `#${rank}${selection} matchedBy=${matchedBy}${score} ${relativePath}:${start}-${end}`
_HEADER = re.compile(r"(?m)^#\d+\S*\s+matchedBy=\S+(?:\s+score=\S+)?\s+(\S+?):(\d+)(?:-\d+)?\s*$")


def _cite_indexed(text: str) -> list[list]:
    found: list[list] = []
    for f, ln in _HEADER.findall(text):
        row = [_rel(f), int(ln)]
        if row not in found:
            found.append(row)
    return found


# `--rg` output: a file path line, then that file's symbol blocks
# (`${range} [${label}]`) with source lines indented four spaces. A P2/P4
# answer is scored at file level (line 0), the graft find_all shape.
_RG_FILE = re.compile(r"(?m)^(?![ \t#])([^\s:#\[]+\.[A-Za-z0-9_]+)\s*$")


def _cite_rg(text: str) -> list[list]:
    found: list[list] = []
    for f in _RG_FILE.findall(text):
        row = [_rel(f), 0]
        if row not in found:
            found.append(row)
    return found


def _rel(f: str) -> str:
    for prefix in (PROJECT + "/", "./"):
        if f.startswith(prefix):
            f = f[len(prefix):]
    return f


def make(sandbox_mode: str = "docker") -> ZvecGrep:
    return ZvecGrep(sandbox_mode)
