"""The competitive tier's sandbox and its first competitor adapter, cymbal
(docs/competitive/DESIGN.md D2, s1.3; docs/competitive/fairness/cymbal.md).

What each test pins, and why (for docs/harness/ARCHAEOLOGY.md):

- every `docker run` the sandbox issues carries the D2 flags (network none,
  read-only rootfs, no capabilities, no new privileges, uid 65534, memory
  and pid ceilings), mounts exactly the corpus read-only and /out, and
  passes no host environment variable through: the flags are the whole
  safety claim, and a flag dropped by a refactor has no symptom;
- cymbal's citation parser reads `rel_path` + `start_line`/`line` from the
  tool's real `--json` output (fixtures captured 2026-09-05 from v0.14.0 in
  the sandbox), so a shape change in a release fails here, not silently as
  an F1 of 0;
- a competitor adapter refuses the `none` sandbox (DESIGN D2: competitor
  code runs only in the container), and the runner refuses `--sandbox
  docker` with no daemon rather than falling back;
- the jcodemunch worker is one file run in both modes, so the `none` and
  `docker` rows are the same code path.
Docker-dependent proof lives in the result file and VERIFICATION, not here.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

REPO = Path(__file__).resolve().parents[1]
COMPETE = REPO / "benchmarks" / "competitive"
FIX = REPO / "tests" / "fixtures" / "competitive" / "cymbal"
if not COMPETE.is_dir():  # excluded from the sdist (pyproject); the tests are meaningless without it
    pytest.skip("benchmarks/competitive is not in this tree (not shipped in the sdist)", allow_module_level=True)
sys.path.insert(0, str(COMPETE))

import sandbox  # noqa: E402
from adapters import cymbal  # noqa: E402


def test_every_run_carries_the_d2_flags_and_no_host_environment(tmp_path, monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setenv("SECRET_FROM_HOST", "x")
    with mock.patch.object(sandbox.subprocess, "run", fake_run):
        sandbox.run("img:1", ["/out/run.sh"], tmp_path / "corpus", tmp_path / "out", timeout=5)
    cmd = seen["cmd"]
    joined = " ".join(cmd)
    for flag in ("--network none", "--read-only", "--cap-drop ALL", "--security-opt no-new-privileges",
                 "--user 65534:65534", "--memory 8g", "--pids-limit 512", "--tmpfs /tmp:rw,size=512m"):
        assert flag in joined, flag
    # the whole list, so a flag added to RUN_FLAGS is pinned here or this goes red
    assert sandbox.RUN_FLAGS == ["--network", "none", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                                 "--user", "65534:65534", "--memory", "8g", "--pids-limit", "512", "--tmpfs", "/tmp:rw,size=512m"]
    mounts = [cmd[i + 1] for i, x in enumerate(cmd) if x == "-v"]
    assert len(mounts) == 2 and mounts[0].endswith(":/corpus:ro") and mounts[1].endswith(":/out:rw")
    envs = [cmd[i + 1] for i, x in enumerate(cmd) if x == "-e"]
    assert envs == ["HOME=/out"]
    assert "SECRET_FROM_HOST" not in joined
    assert cmd[-2:] == ["img:1", "/out/run.sh"]
    # a tool that needs a private home gets a uid-owned 0700 tmpfs, nothing else changes
    with mock.patch.object(sandbox.subprocess, "run", fake_run):
        sandbox.run("img:1", ["/out/run.sh"], tmp_path / "corpus", tmp_path / "out", timeout=5, private_home=True)
    cmd2 = seen["cmd"]
    assert "/private:rw,uid=65534,gid=65534,mode=0700,size=1g" in cmd2
    assert [cmd2[i + 1] for i, x in enumerate(cmd2) if x == "-e"] == ["HOME=/private"]
    assert len([x for x in cmd2 if x == "-v"]) == 2


def test_a_timeout_is_reported_not_raised(tmp_path):
    def slow(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 1))

    with mock.patch.object(sandbox.subprocess, "run", slow):
        r = sandbox.run("img:1", ["x"], tmp_path / "c", tmp_path / "o", timeout=1)
    assert r.timed_out and r.rc == 124


def test_cymbal_citations_come_from_rel_path_and_lines_of_its_real_json():
    inv = cymbal._cite((FIX / "inv_json.json").read_text(encoding="utf-8"))
    assert ["src/jcodemunch_mcp/storage/token_tracker.py", 369] in inv
    search = cymbal._cite((FIX / "search_json.json").read_text(encoding="utf-8"))
    assert ["src/jcodemunch_mcp/storage/token_tracker.py", 369] in search
    assert ["src/jcodemunch_mcp/storage/sqlite_store.py", 408] in search
    refs = cymbal._cite((FIX / "refs_json.json").read_text(encoding="utf-8"))
    assert refs == [["src/jcodemunch_mcp/storage/token_tracker.py", 1622]]
    imps = cymbal._cite((FIX / "imp_json.json").read_text(encoding="utf-8"))
    assert ["src/jcodemunch_mcp/retrieval/tuning.py", 0] in imps
    assert cymbal._cite("not json") == []
    # `show --json` carries only the absolute container path plus per-line rows: file at its first line
    shown = cymbal._cite((FIX / "show_json.json").read_text(encoding="utf-8"))
    assert shown == [["src/jcodemunch_mcp/storage/token_tracker.py", 369]]
    # the absolute `file` field is never used: only rel_path, so no /corpus prefix leaks
    assert all(not f.startswith("/") for f, _ in inv + search + refs + imps)


def test_cymbal_commands_follow_its_documented_agent_policy():
    from adapter import Task

    assert cymbal._cmds(Task(id="a", corpus="c", category="P1", query="cache_put")) == [["cymbal", "investigate", "cache_put"]]
    assert cymbal._cmds(Task(id="b", corpus="c", category="T", query="router route handler")) == [["cymbal", "search", "router", "route", "handler"]]
    assert cymbal._cmds(Task(id="c", corpus="c", category="P2", query="cache_put")) == [["cymbal", "refs", "cache_put"]]
    assert cymbal._cmds(Task(id="d", corpus="c", category="P4", query="src/x.py")) == [["cymbal", "importers", "src/x.py"]]


def test_a_competitor_refuses_the_none_sandbox_and_the_runner_refuses_docker_without_a_daemon(tmp_path):
    with pytest.raises(RuntimeError):
        cymbal.make("none")
    import run as runner

    with mock.patch.object(runner.sandbox, "docker_available", return_value=False):
        rc = runner.main(["--sandbox", "docker", "--out-dir", str(tmp_path), "--runs", "1"])
    assert rc == 4


def test_a_failed_or_timed_out_index_is_a_not_runnable_row_never_partial_means(tmp_path):
    """DESIGN s1.3 / s9.2 (review round 1, finding 2)."""
    import run as runner
    from adapter import Corpus, IndexReport, Pin, Task, corpus_digest

    root = tmp_path / "c"
    root.mkdir()
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    c = Corpus(id="t@0", path=root, sha256=corpus_digest(root, ("a.py",)), files=("a.py",))

    class Broken:
        name = "broken"
        interface = "cli"
        categories = frozenset({"T"})
        pin = Pin(registry="none", package="broken", version="0")

        def index(self, corpus, scratch):
            return IndexReport(seconds=None, ok=False, stderr_tail="timeout")

        def timed_out(self, corpus, scratch):
            return True

        def answer(self, corpus, task, scratch):
            raise AssertionError("a task must never run on a failed index")

        def tools_list_tokens(self):
            return None

        def version(self):
            return "0"

    out = runner.run_once([Broken()], {c.id: c}, [Task(id="t", corpus=c.id, category="T", query="x")], tmp_path / "s")
    row = out["broken"][c.id]
    assert row["not_runnable"] == "timeout" and row["tasks"] == [] and row["axes"]["tokens_per_task"] is None


def test_the_jcodemunch_worker_is_one_file_run_in_both_modes():
    src = (COMPETE / "adapters" / "jcodemunch.py").read_text(encoding="utf-8")
    assert "jcm_worker.py" in src and "_WORKER = " not in src
    df = (COMPETE / "sandbox" / "jcodemunch.Dockerfile").read_text(encoding="utf-8")
    run_lines = [ln for ln in df.splitlines() if ln.lstrip().startswith(("RUN", "&&"))]
    assert "jcm_worker.py" in df and not any("--network" in ln for ln in run_lines)
    cy = (COMPETE / "sandbox" / "cymbal.Dockerfile").read_text(encoding="utf-8")
    assert "sha256sum -c" in cy and "@sha256:" in cy.splitlines()[2]
    # the jcodemunch image installs the lock's pins and never the tree as a final layer
    assert "requirements.txt" in df and "AS build" in df and "COPY --from=build" in df
    src2 = (COMPETE / "adapters" / "jcodemunch.py").read_text(encoding="utf-8")
    assert "uv\", \"export\", \"--frozen" in src2


def test_the_result_header_stamps_sandbox_tree_and_scorer(tmp_path):
    """CF-9: a result file names the code that scored it."""
    import run as runner

    assert len(runner._scorer_sha256()) == 64
    md = runner.render_md({"header": {"date": "d", "jcm_commit": "c", "jcm_version": "v", "runs": 3, "corpora": [], "pins": [],
                                      "sandbox": "none", "tree_dirty": True, "scorer_sha256": "ab" * 32}, "rows": []})
    assert "Sandbox: `none`" in md and "tree dirty: True" in md and "scorer sha256 `abababababab`" in md
    json.dumps(md)
