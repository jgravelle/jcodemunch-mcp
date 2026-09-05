"""Aider RepoMap (aider-chat 0.86.2) through the competitive interface
(docs/competitive/fairness/aider.md): the TOKEN AXIS ONLY.

purpose:  the map-shaped competitor: one ranked text per repository that
          the tool sends with every change request, so its row is the cost
          of that text at the default budget, never an answer to a task
invokes:  the image built from sandbox/aider.Dockerfile (pip from a hash
          lockfile, Python 3.12), one container per (corpus, run): the
          corpus copied to the uid-owned tmpfs (the tags cache lives in the
          repo root), then `aider --show-repo-map` once cold (the index
          time) and once per T task (charged), each timed inside the
          container
produces: IndexReport from the first invocation; one Answer per T task
          whose payload is the printed map, with NO citations (F1 is NOT
          COMPARABLE for a map: DESIGN s1.3) and NO query, because the tool
          takes none
refuses:  every category but T (an F1 for a map would be an invented
          number); the `none` sandbox (a competitor runs only in the
          container, DESIGN D2)
pinned:   pypi aider-chat 0.86.2, wheel sha256
          64f6a0c66c9f4633ad9f479bca3e64ebcba02b9da03c6b604b74a44736b2416e,
          the same hash the lockfile makes pip require
fairness: docs/competitive/fairness/aider.md. `--model gpt-4o` names the
          tokenizer that sizes the budget (no key, no model call);
          `--map-tokens` and the no-files multiplier at their defaults.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from adapter import Answer, Corpus, IndexReport, Pin, Task, count_tokens
import sandbox

HERE = Path(__file__).resolve().parent
TAG = "jcm-compete/aider:0.86.2"
TIMEOUT_S = 20 * 60  # DESIGN s9.2: the per-tool ceiling
CMD = ["aider", "--model", "gpt-4o", "--show-repo-map", "--no-analytics", "--no-check-update",
       "--no-gitignore", "--no-show-model-warnings", "--yes-always"]
PROJECT = "/private/project"  # the corpus copy the tool may write its cache beside


class Aider:
    name = "aider"
    interface = "cli"
    categories = frozenset({"T"})
    pin = Pin(registry="pypi", package="aider-chat", version="0.86.2",
              digest="64f6a0c66c9f4633ad9f479bca3e64ebcba02b9da03c6b604b74a44736b2416e")

    def __init__(self, sandbox_mode: str = "docker") -> None:
        if sandbox_mode != "docker":
            raise RuntimeError("aider runs only in the container (DESIGN D2)")
        self._image = None
        self._cache: dict[tuple[str, str], dict] = {}

    def image(self) -> sandbox.BuildResult:
        if self._image is None:
            df = HERE.parent / "sandbox" / "aider.Dockerfile"
            self._image = sandbox.build(TAG, df, df.parent, timeout=1800)
            self.pin = Pin(**{**self.pin.__dict__, "dockerfile_sha256": self._image.dockerfile_sha256})
        return self._image

    @staticmethod
    def script(tasks: list[Task]) -> str:
        """The container's shell script: copy, then the cold map (index), then
        one charged map per T task; every invocation timed by the same clock."""
        lines = ["set +e",
                 'ms() { s=$(date +%s%N); "$@"; r=$?; e=$(date +%s%N); echo "$LABEL rc=$r ms=$(( (e-s)/1000000 ))" >> /out/timings.txt; return $r; }',
                 f"cp -r /corpus {PROJECT} && cd {PROJECT} || exit 97",
                 f"LABEL=index; ms {shlex.join(CMD)} > /out/index.txt 2>/out/index.err"]
        for t in tasks:
            if t.category != "T":
                continue
            q = shlex.quote(t.id)
            lines.append(f"LABEL={q}; ms {shlex.join(CMD)} > /out/{q}.txt 2>/out/{q}.err")
        lines.append(f"ls -a {PROJECT} > /out/ls.txt 2>&1")
        return "\n".join(lines) + "\n"

    def prepare(self, corpus: Corpus, scratch: Path, tasks: list[Task]) -> None:
        key = (corpus.id, str(scratch))
        if key in self._cache:
            return
        self.image()
        out = scratch / "aider-out"
        out.mkdir(parents=True, exist_ok=True)
        (out / "run.sh").write_text(self.script(tasks), encoding="utf-8", newline="\n")
        res = sandbox.run(TAG, ["/out/run.sh"], corpus.path, out, timeout=TIMEOUT_S, private_home=True)
        timings = _timings(out / "timings.txt")
        idx_rc, idx_ms = timings.get("index", (1, None))
        announce, index_map = split_map(_read(out / "index.txt"))
        (out / "index.announce.txt").write_text(announce, encoding="utf-8")
        index = {"secs": (idx_ms / 1000.0) if idx_ms is not None else None,
                 "ok": idx_rc == 0 and not res.timed_out and bool(index_map.strip()),
                 "err": (_read(out / "index.err") or res.stderr)[-500:] if (idx_rc != 0 or not index_map.strip())
                 else f"aider --show-repo-map (wall, cold); {' | '.join(_tool_lines(announce)) or 'no budget line'}",
                 "files": None}  # the tool's "Git repo: N files" counts every tracked file, not what it mapped; not reported as an index count
        answers = {}
        for t in tasks:
            if t.category != "T":
                continue
            rc, ms = timings.get(t.id, (1, None))
            if ms is None:
                answers[t.id] = {"payload": "", "calls": 0, "latency_ms": [], "error": "not run (container timed out or the script stopped)"}
                continue
            announce, text = split_map(_read(out / f"{t.id}.txt"))
            (out / f"{t.id}.announce.txt").write_text(announce, encoding="utf-8")
            err = None if rc == 0 and text.strip() else (_read(out / f"{t.id}.err")[-200:] or f"rc={rc}, no map after the preface")
            answers[t.id] = {"payload": text, "calls": 1, "latency_ms": [float(ms)], "error": err}
        self._cache[key] = {"index": index, "answers": answers, "timed_out": res.timed_out}

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
                      cited=frozenset(), error=a["error"])

    def tools_list_tokens(self):
        return None  # a CLI: no tools/list, no schema cost (DESIGN s2)

    def version(self) -> str:
        return self.pin.version


PREFACE = "Here are summaries of some files present in my git repository."
"""The first line of the text the tool sends the LLM (`repomap.py`'s
repo_content_prefix); everything stdout carries before it is the CLI's
announce block for a human (fairness note, "Payload")."""


def split_map(stdout: str) -> tuple[str, str]:
    """(announce block, map) from one `--show-repo-map` run's stdout; a run that
    never reached the preface is (everything, "") and reads as no map."""
    i = stdout.find(PREFACE)
    if i < 0:
        return stdout, ""
    return stdout[:i], stdout[i:]


_TOOL_LINE = re.compile(r"^(Repo-map: .*|Git repo: .*|Aider v.*)$", re.M)


def _tool_lines(announce: str) -> list[str]:
    """The tool's own lines about the run (version, repo size, map budget),
    kept for the index report; the sandbox's fetch failures are not among them."""
    return _TOOL_LINE.findall(announce)


_TIMING = re.compile(r"^(\S+) rc=(\d+) ms=(\d+)$")


def _timings(p: Path) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for ln in _read(p).splitlines():
        m = _TIMING.match(ln)
        if m:
            out[m.group(1)] = (int(m.group(2)), int(m.group(3)))
    return out


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def make(sandbox_mode: str = "docker") -> Aider:
    return Aider(sandbox_mode)
