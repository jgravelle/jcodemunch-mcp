"""jCodeMunch through the same interface as everyone else (DESIGN s1.4).

purpose:  our own row, driven the way our docs say (ARCHAEOLOGY R27, R28):
          search_symbols(max_results=5) then get_symbol_source on the top 3
          for P1 and T; find_references for P2; find_importers for P4;
          shipped defaults (context providers ON, as index_folder ships them;
          the self-latency harness turns them off and this adapter does not),
          AI summaries off (R28), no config file
invokes:  sandbox/jcm_worker.py, ONE code path in two places: inside the
          container built from sandbox/jcodemunch.Dockerfile (the D2 shape
          every competitor runs in) when the sandbox is `docker`, or in a
          fresh host subprocess under PYTHONPATH=src when it is `none`
          (tests, a box without Docker). Either way a run is cold: a new
          store per run. The image's build context is `git ls-files
          --cached --others --exclude-standard`, i.e. what a commit would
          contain, so a dirty tree builds too and is stamped `tree_dirty`
          in the result header (CF-9)
produces: IndexReport (cold index wall seconds) and one Answer per task
          whose payload is the serialised JSON of every tool response, the
          shape an agent receives (R15, R17: _meta kept); tools_list_tokens
          measured LIVE from server._build_tools_list in the worker (CF-6)
refuses:  a corpus the index step did not index completely; a task category
          outside its set
pinned:   registry "tree", HEAD's commit
fairness: DESIGN s1.4; the same sandbox flags as every competitor when the
          sandbox is `docker`; a `none` run is labelled in the result header
          and a competitor adapter refuses that mode, so the two never share
          a file.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from adapter import Answer, Corpus, IndexReport, Pin, Task, count_tokens
import sandbox

REPO = Path(__file__).resolve().parents[3]
WORKER = REPO / "benchmarks" / "competitive" / "sandbox" / "jcm_worker.py"
TAG_PREFIX = "jcm-compete/jcodemunch:"


def _build_context() -> Path:
    """What a commit of the working tree would contain: tracked files plus
    untracked-not-ignored ones. Never .venv, never .git, never state
    (settings.local.json, .claude/state/ and *.bak are gitignored). The tree
    is a layer of the image's BUILD stage only; the final image holds the
    wheel, the pinned requirements and the worker."""
    ctx = Path(tempfile.mkdtemp(prefix="jcm-image-ctx-"))
    files = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=REPO).decode("utf-8").split("\0")
    for rel in files:
        if not rel or rel == ".dockerignore":  # the repo's own ignore file would prune benchmarks/ from the context
            continue
        src = REPO / rel
        if src.is_file():
            dst = ctx / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    # The runtime dependency set, pinned from uv.lock (DESIGN s9.3): the image
    # installs exactly these, never a fresh resolution.
    subprocess.run(["uv", "export", "--frozen", "--no-dev", "--no-emit-project", "--no-hashes", "-q", "-o", str(ctx / "requirements.txt")], cwd=REPO, check=True)
    return ctx


class JCodeMunch:
    name = "jcodemunch"
    interface = "python"
    categories = frozenset({"P1", "P2", "P4", "T"})

    def __init__(self, sandbox_mode: str = "docker") -> None:
        self.sandbox_mode = sandbox_mode
        commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, text=True, encoding="utf-8").strip()
        self.pin = Pin(registry="tree", package="jcodemunch-mcp", version=commit)
        self._cache: dict[tuple[str, str], dict] = {}
        self._image = None

    def image(self) -> sandbox.BuildResult:
        if self._image is None:
            ctx = _build_context()
            df = REPO / "benchmarks" / "competitive" / "sandbox" / "jcodemunch.Dockerfile"
            try:
                self._image = sandbox.build(TAG_PREFIX + self.pin.version, df, ctx, timeout=1800)
            finally:
                shutil.rmtree(ctx, ignore_errors=True)
            self.pin = Pin(**{**self.pin.__dict__, "dockerfile_sha256": self._image.dockerfile_sha256})
        return self._image

    def _run(self, corpus: Corpus, scratch: Path, tasks: list[Task]) -> dict:
        key = (corpus.id, str(scratch))
        if key in self._cache:
            return self._cache[key]
        out = scratch / "jcm-out"
        out.mkdir(parents=True, exist_ok=True)
        (out / "tasks.json").write_text(json.dumps([{"id": t.id, "category": t.category, "query": t.query} for t in tasks]), encoding="utf-8")
        if self.sandbox_mode == "docker":
            self.image()
            res = sandbox.run(TAG_PREFIX + self.pin.version, ["/corpus", "/out/jcm-store", "/out/tasks.json", "/out/answers.json"],
                              corpus.path, out, timeout=20 * 60)
            rc, tail = res.rc, (res.stderr or res.stdout)[-2000:]
        else:
            store = out / "jcm-store"
            store.mkdir(exist_ok=True)
            env = dict(os.environ, CODE_INDEX_PATH=str(store), PYTHONPATH=str(REPO / "src"),
                       JCODEMUNCH_TRUSTED_FOLDERS=str(corpus.path), JCODEMUNCH_LIVE_JOURNAL="0")
            proc = subprocess.run([sys.executable, str(WORKER), str(corpus.path), str(store), str(out / "tasks.json"), str(out / "answers.json")],
                                  env=env, text=True, capture_output=True, encoding="utf-8", errors="replace", timeout=1200)
            rc, tail = proc.returncode, (proc.stderr or proc.stdout)[-2000:]
        ap = out / "answers.json"
        if rc != 0 or not ap.exists():
            result = {"index": {"secs": None, "success": False, "error": tail}, "answers": {}}
        else:
            result = json.loads(ap.read_text(encoding="utf-8"))
        self._cache[key] = result
        return result

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
        return Answer(payload=a["payload"], tokens=count_tokens(a["payload"]), calls=a["calls"], latency_ms=a["latency_ms"],
                      cited=frozenset((f, int(ln)) for f, ln in a["cited"]), error=a.get("error"))

    def tools_list_tokens(self):
        """LIVE: the worker serialises server._build_tools_list() at the shipped
        default surface; counted here with cl100k (the zhang-liz shape,
        DESIGN s2). None until a run has happened."""
        for out in self._cache.values():
            tl = out.get("tools_list_json")
            if tl:
                return count_tokens(tl)
        return None

    def version(self) -> str:
        return self.pin.version


def make(sandbox_mode: str = "docker") -> JCodeMunch:
    return JCodeMunch(sandbox_mode)
