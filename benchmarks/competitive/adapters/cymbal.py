"""cymbal 0.14.0 through the competitive interface (docs/competitive/fairness/cymbal.md).

purpose:  the CLI-shaped competitor: a Go binary an agent runs as a
          subprocess per question, so every call pays a process start and
          the index lives in the tool's own cache
invokes:  the image built from sandbox/cymbal.Dockerfile (release tarball
          verified against the published checksum), one container per
          (corpus, run) that indexes then answers every task from a shell
          script this adapter writes, timing each command inside the
          container with the tool's own default output as the payload
produces: IndexReport from `cymbal index /corpus`; one Answer per task whose
          payload is the DEFAULT output (the "frontmatter" format its docs
          call cheaper than JSON) and whose citations come from a second,
          UNCHARGED `--json` call of the same command
refuses:  a corpus that is not a git repository (its queries answer
          "not inside a git repository" and return nothing, measured
          2026-09-05; FINDINGS CF-10); the `none` sandbox (a competitor
          runs only in the container, DESIGN D2)
pinned:   github-release 1broseidon/cymbal v0.14.0,
          cymbal_v0.14.0_linux_x86_64.tar.gz
          sha256 bfc951722b773b5f07c3a291530684ea737b012ad866505c6971a92d6bd9810d
fairness: docs/competitive/fairness/cymbal.md. Commands follow the README's
          agent policy: P1 `investigate <symbol>` (their "start here"),
          T `search <terms>` then `show` on the top 3 (a phrase is not a
          symbol), P2 `refs <symbol>`, P4 `importers <path>`.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

from adapter import Answer, Corpus, IndexReport, Pin, Task, count_tokens
import sandbox

HERE = Path(__file__).resolve().parent
TAG = "jcm-compete/cymbal:0.14.0"
SHOW_TOP = 3
TIMEOUT_S = 20 * 60  # DESIGN s9.2: the per-tool ceiling


def _cmds(task: Task) -> list[list[str]]:
    q = task.query
    if task.category == "P1":
        return [["cymbal", "investigate", q]]
    if task.category == "T":
        return [["cymbal", "search", *q.split()]]  # `show` on the top 3 is appended after search resolves
    if task.category == "P2":
        return [["cymbal", "refs", q]]
    if task.category == "P4":
        return [["cymbal", "importers", q]]
    return []


class Cymbal:
    name = "cymbal"
    interface = "cli"
    categories = frozenset({"P1", "P2", "P4", "T"})
    pin = Pin(registry="github-release", package="1broseidon/cymbal", version="0.14.0",
              digest="bfc951722b773b5f07c3a291530684ea737b012ad866505c6971a92d6bd9810d")

    def __init__(self, sandbox_mode: str = "docker") -> None:
        if sandbox_mode != "docker":
            raise RuntimeError("cymbal runs only in the container (DESIGN D2)")
        self._image = None
        self._cache: dict[tuple[str, str], dict] = {}

    def image(self) -> sandbox.BuildResult:
        if self._image is None:
            df = HERE.parent / "sandbox" / "cymbal.Dockerfile"
            self._image = sandbox.build(TAG, df, df.parent)
            self.pin = Pin(**{**self.pin.__dict__, "dockerfile_sha256": self._image.dockerfile_sha256})
        return self._image

    def prepare(self, corpus: Corpus, scratch: Path, tasks: list[Task]) -> None:
        key = (corpus.id, str(scratch))
        if key in self._cache:
            return
        if not (corpus.path / ".git").exists():
            self._cache[key] = {"index": {"secs": None, "ok": False, "err": "corpus is not a git repository (CF-10)"}, "answers": {}}
            return
        self.image()
        out = scratch / "cymbal-out"
        out.mkdir(parents=True, exist_ok=True)
        # One shell script: index, then every task's charged call (default output)
        # followed by its uncharged --json twin; each timed inside the container.
        lines = ["set +e", 'ms() { s=$(date +%s%N); "$@"; r=$?; e=$(date +%s%N); echo "$LABEL rc=$r ms=$(( (e-s)/1000000 ))" >> /out/timings.txt; return $r; }']
        lines.append('LABEL=index; ms cymbal index /corpus > /out/index.txt 2>&1')
        for t in tasks:
            for i, cmd in enumerate(_cmds(t)):
                base = f"{t.id}.{i}"
                lines.append(f"LABEL={shlex.quote(base)}; ms {shlex.join(cmd)} > /out/{shlex.quote(base)}.txt 2>/out/{shlex.quote(base)}.err")
                lines.append(f"LABEL={shlex.quote(base + '.json')}; ms {shlex.join(cmd + ['--json'])} > /out/{shlex.quote(base)}.json 2>/dev/null")
                if t.category == "T":
                    # top-3 names from the JSON twin, then `show` each (charged) + its JSON twin
                    lines.append(
                        f"NAMES=$(python3 -c 1 2>/dev/null; sed -n 's/^ *\"name\": \"\\([^\"]*\\)\",*$/\\1/p' /out/{shlex.quote(base)}.json | head -{SHOW_TOP})")
                    lines.append(f"J=0; for n in $NAMES; do LABEL={shlex.quote(base)}.show$J; ms cymbal show \"$n\" > /out/{shlex.quote(base)}.show$J.txt 2>/dev/null; "
                                 f"LABEL={shlex.quote(base)}.show$J.json; ms cymbal show \"$n\" --json > /out/{shlex.quote(base)}.show$J.json 2>/dev/null; J=$((J+1)); done")
        (out / "run.sh").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        res = sandbox.run(TAG, ["/out/run.sh"], corpus.path, out, timeout=TIMEOUT_S)
        timings = {}
        tp = out / "timings.txt"
        if tp.exists():
            for ln in tp.read_text(encoding="utf-8").splitlines():
                m = re.match(r"^(\S+) rc=(\d+) ms=(\d+)$", ln)
                if m:
                    timings[m.group(1)] = (int(m.group(2)), int(m.group(3)))
        idx_rc, idx_ms = timings.get("index", (1, None))
        index = {"secs": (idx_ms / 1000.0) if idx_ms is not None else None, "ok": idx_rc == 0 and not res.timed_out,
                 "err": (out / "index.txt").read_text(encoding="utf-8", errors="replace")[-500:] if (out / "index.txt").exists() else res.stderr[-500:]}
        m = re.search(r"(\d+) indexed", index["err"])
        index["files"] = int(m.group(1)) if m else None
        answers = {}
        for t in tasks:
            payload, lat, cited, err = [], [], [], None
            for i, _ in enumerate(_cmds(t)):
                base = f"{t.id}.{i}"
                rc, ms = timings.get(base, (1, None))
                if ms is None:
                    err = "not run (container timed out or the script stopped)"
                    break
                lat.append(float(ms))
                payload.append(_read(out / f"{base}.txt"))
                cited.extend(_cite(_read(out / f"{base}.json")))
                if rc != 0 and not payload[-1].strip():
                    payload[-1] = _read(out / f"{base}.err")  # what the agent sees on a miss
                j = 0
                while (out / f"{base}.show{j}.txt").exists():
                    _, sms = timings.get(f"{base}.show{j}", (1, None))
                    if sms is not None:
                        lat.append(float(sms))
                    payload.append(_read(out / f"{base}.show{j}.txt"))
                    cited.extend(_cite(_read(out / f"{base}.show{j}.json")))
                    j += 1
            answers[t.id] = {"payload": "".join(payload), "calls": len(lat), "latency_ms": lat, "cited": cited, "error": err}
        self._cache[key] = {"index": index, "answers": answers, "timed_out": res.timed_out}

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
        return None  # a CLI: no tools/list, no schema cost (DESIGN s2)

    def version(self) -> str:
        return self.pin.version


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _cite(text: str) -> list[list]:
    """(rel_path, line) pairs from cymbal's --json output: `rel_path` with
    `start_line` (symbols) or `line` (refs); importers carry rel_path only."""
    try:
        doc = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []
    found: list[list] = []

    def walk(o):
        if isinstance(o, dict):
            rp = o.get("rel_path")
            if isinstance(rp, str):
                ln = o.get("start_line") if o.get("start_line") is not None else o.get("line", 0)
                found.append([rp, int(ln or 0)])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(doc)
    return found


def make(sandbox_mode: str = "docker") -> Cymbal:
    return Cymbal(sandbox_mode)
