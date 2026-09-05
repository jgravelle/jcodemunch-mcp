"""The Aider RepoMap adapter, token axis only
(docs/competitive/DESIGN.md s1.3; docs/competitive/fairness/aider.md).

What each test pins, and why (for docs/harness/ARCHAEOLOGY.md):

- the payload boundary is the tool's own repo-map preface, captured
  2026-09-05 from 0.86.2 in the sandbox over the PINNED self corpus
  (tests/fixtures/competitive/aider_map.json): what stdout carries before
  it is the CLI's announce block (version, "Git repo: N files", the budget
  line) plus the sandbox's fetch-failure lines, none of it the map; a
  release that moves the preface fails here, not silently as a charged
  announce block or an empty map;
- the tool's own lines about the run are read from the announce block for
  the index report, never invented (no file count is reported);
- the adapter answers T tasks only: a map has no F1 (an invented one would
  be a number), so no other category is accepted and no citation is made;
- the script copies the corpus to the uid-owned tmpfs (the tags cache lives
  in the repo root), runs the map once cold as the index and once per T
  task charged, and the query is passed nowhere because the tool takes none;
- prepare() reads the container's files: the index report is the cold
  invocation's wall, each answer is the map after the preface with one call
  and one latency; a run that never reached the preface is an error, not a
  small map;
- the pin's digest is the wheel hash the lockfile makes pip require; the
  Dockerfile installs with --require-hashes on Python 3.12 and caches the
  tokenizer assets at build; a competitor refuses the `none` sandbox.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
COMPETE = REPO / "benchmarks" / "competitive"
if not COMPETE.is_dir():  # excluded from the sdist (pyproject); the tests are meaningless without it
    pytest.skip("benchmarks/competitive is not in this tree (not shipped in the sdist)", allow_module_level=True)
FIX = json.loads((REPO / "tests" / "fixtures" / "competitive" / "aider_map.json").read_text(encoding="utf-8"))
sys.path.insert(0, str(COMPETE))
sys.path.insert(0, str(COMPETE / "sandbox"))

from adapter import Corpus, Task, count_tokens  # noqa: E402
from adapters import aider  # noqa: E402
from sandbox import RunResult  # noqa: E402


def test_the_payload_is_the_map_after_the_tools_own_preface():
    announce, body = aider.split_map(FIX["index_stdout"])
    assert body.startswith(aider.PREFACE)
    assert "Aider v0.86.2" in announce and "Repo-map: using" in announce and "raw.githubusercontent.com" in announce
    assert aider.PREFACE not in announce and "Repo-map: using" not in body
    # the announce block is real cost on stdout and is NOT charged: the map alone is the payload
    assert count_tokens(announce) > 100 and count_tokens(body) > 1000
    assert aider.split_map("no map at all") == ("no map at all", "")


def test_the_tool_lines_are_read_from_the_announce_block_not_invented():
    announce, _ = aider.split_map(FIX["index_stdout"])
    lines = aider._tool_lines(announce)
    assert lines == ["Aider v0.86.2", "Git repo: .git with 277 files", "Repo-map: using 4096 tokens, auto refresh"]
    assert not any("HTTPSConnectionPool" in ln for ln in lines)  # the sandbox's failure is not the tool's line


def test_the_adapter_answers_t_only_and_the_script_follows_the_fairness_note():
    assert aider.Aider.categories == frozenset({"T"})
    tasks = [Task(id="self-T-router", corpus="c", category="T", query="router route handler"),
             Task(id="self-P1-x", corpus="c", category="P1", query="x", expected=(("a.py", 0),), tolerance_lines=1)]
    s = aider.Aider.script(tasks)
    assert s.startswith("set +e\n") and f"cp -r /corpus {aider.PROJECT} && cd {aider.PROJECT}" in s
    assert s.count("--show-repo-map") == 2  # index (cold) + one T task; the P1 task gets no invocation
    assert "LABEL=index; ms aider --model gpt-4o --show-repo-map" in s and "LABEL=self-T-router; ms aider" in s
    assert "router" not in s.split("LABEL=self-T-router")[1].split("\n")[0].replace("self-T-router", "")  # the query goes nowhere
    for flag in ("--no-analytics", "--no-check-update", "--no-gitignore", "--no-show-model-warnings", "--yes-always", "--model gpt-4o"):
        assert flag in s
    assert "--map-tokens" not in s  # the budget stays at the tool's default


def test_prepare_reads_the_container_files_into_index_and_answers(tmp_path, monkeypatch):
    def fake_run(tag, args, corpus, out, timeout, workdir="/corpus", extra_env=None, private_home=False):
        assert private_home and tag == aider.TAG and args == ["/out/run.sh"]
        (out / "timings.txt").write_text(FIX["timings_txt"], encoding="utf-8")
        (out / "index.txt").write_text(FIX["index_stdout"], encoding="utf-8")
        for tid, text in FIX["task_stdout"].items():
            (out / f"{tid}.txt").write_text(text, encoding="utf-8")
        (out / "self-T-broken.txt").write_text("Aider v0.86.2\nsomething went wrong\n", encoding="utf-8")
        return RunResult(rc=0, stdout="", stderr="", seconds=30.0)

    monkeypatch.setattr(aider.sandbox, "run", fake_run)
    monkeypatch.setattr(aider.Aider, "image", lambda self: None)
    g = aider.Aider.__new__(aider.Aider)
    g._cache = {}
    corpus = Corpus(id="self@x", path=tmp_path, sha256="0" * 64, files=())
    tasks = [Task(id=t, corpus="self@x", category="T", query="q") for t in ("self-T-router", "self-T-context", "self-T-broken")]
    g.prepare(corpus, tmp_path, tasks)
    rep = g.index(corpus, tmp_path)
    assert rep.ok and rep.seconds == 6.91 and rep.files_indexed is None
    assert "Repo-map: using 4096 tokens" in rep.stderr_tail and "Git repo: .git with 277 files" in rep.stderr_tail
    a = g.answer(corpus, tasks[0], tmp_path)
    assert a.calls == 1 and a.latency_ms == [4679.0] and a.cited == frozenset() and a.error is None
    assert a.payload.startswith(aider.PREFACE) and a.tokens == count_tokens(aider.split_map(FIX["task_stdout"]["self-T-router"])[1])
    broken = g.answer(corpus, tasks[2], tmp_path)
    assert broken.error is not None and broken.tokens == 0 and broken.payload == ""  # never a small map
    assert (tmp_path / "aider-out" / "index.announce.txt").exists()
    assert g.tools_list_tokens() is None and g.version() == "0.86.2"


def test_the_pin_is_the_wheel_hash_pip_requires_and_the_image_is_offline_at_run():
    with pytest.raises(RuntimeError):
        aider.make("none")
    req = (COMPETE / "sandbox" / "aider.requirements.txt").read_text(encoding="utf-8")
    # the hash lines that belong to aider-chat itself: from its requirement line to the next package's
    own = req.split("aider-chat==0.86.2", 1)[1].split("\n", 1)[1]
    own = "\n".join(ln for ln in own.split("\n")[:8] if ln.startswith("    --hash="))
    assert f"--hash=sha256:{aider.Aider.pin.digest}" in own, "the pin's digest must be a hash pip requires for aider-chat itself"
    assert len(aider.Aider.pin.digest) == 64
    df = (COMPETE / "sandbox" / "aider.Dockerfile").read_text(encoding="utf-8")
    assert "python:3.12-slim-bookworm@sha256:" in df and "--require-hashes" in df and "aider.requirements.txt" in df
    assert "TIKTOKEN_CACHE_DIR" in df and "tiktoken.get_encoding" in df and "AIDER_ANALYTICS=false" in df
