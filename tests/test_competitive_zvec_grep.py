"""The zvec-grep adapter, set row 9 (docs/competitive/DESIGN.md s1.3;
docs/competitive/fairness/zvec_grep.md; FINDINGS CF-66).

What each test pins, and why (for docs/harness/ARCHAEOLOGY.md):

- the parsers read the tool's REAL output, captured in the sandbox over the
  PINNED self corpus (tests/fixtures/competitive/zvec_grep/: the container's
  index.txt, one indexed-route answer, one --rg answer, tools_list.json):
  a header `#<rank> matchedBy=<x> <path>:<start>-<end>` is one citation at
  `start`, a --rg answer cites each file once at line 0, and text without
  those shapes cites nothing; a release that changes the shape fails here,
  not silently as an F1 of 0;
- the index report reads the wall from the script's timing line and the
  file count from the tool's own `N scanned, M added` line, colour codes
  stripped (the theme wraps the numbers on a TTY);
- the call plan is the fairness note's: P1 `--prefer-symbol`, T plain, P2
  `--rg -w`, P4 `--rg -F <module stem>`, one call each, every call charged;
  the model, limit and preview are never passed (the tool's defaults);
- prepare() writes the script, runs one container, and reads timings and
  files into one answer per task; a task whose call never ran is an error,
  never an empty answer; a failed tools/list capture leaves the weight None
  and changes no answer;
- the pin's digest is the integrity the lockfile makes `npm ci` enforce;
  the Dockerfile pins the base image by digest, warms the pinned model into
  the cache the adapter points at, and the adapter passes only documented
  environment variables; a competitor refuses the `none` sandbox.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
COMPETE = REPO / "benchmarks" / "competitive"
if not COMPETE.is_dir():  # excluded from the sdist (pyproject); the tests are meaningless without it
    pytest.skip("benchmarks/competitive is not in this tree (not shipped in the sdist)", allow_module_level=True)
FIX = REPO / "tests" / "fixtures" / "competitive" / "zvec_grep"
sys.path.insert(0, str(COMPETE))
sys.path.insert(0, str(COMPETE / "sandbox"))

from adapter import Corpus, Task, validate  # noqa: E402
from adapters import zvec_grep as zg  # noqa: E402
from sandbox import RunResult  # noqa: E402


def _fx(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8", errors="replace")


def _task(cat: str, q: str, tid: str = "t") -> Task:
    return Task(id=tid, corpus="self@x", category=cat, query=q)


# ---- parsers over the captured output -------------------------------------------

def test_an_indexed_answer_cites_each_result_header_at_its_start_line():
    text = _fx("self-P1-cache_put.0.txt")
    cited = zg._cite_indexed(text)
    assert cited, "the captured P1 answer has no `#<rank> matchedBy=` header; the shape moved"
    for f, ln in cited:
        assert not f.startswith("/") and "/private/" not in f, f"a citation is not workspace-relative: {f}"
        assert ln > 0
    headers = re.findall(r"(?m)^#\d+", text)
    assert len(cited) <= len(headers)
    assert zg._cite_indexed("no headers here\nsource:\n12:\tx\n") == []


def test_an_rg_answer_cites_each_file_once_at_line_zero():
    text = _fx("self-P2-cache_put.0.txt")
    cited = zg._cite_rg(text)
    assert cited, "the captured --rg answer has no file line; the shape moved"
    assert all(ln == 0 for _, ln in cited)
    assert len({f for f, _ in cited}) == len(cited)
    assert zg._cite_rg("    12:\tdef cache_put(self):\n") == []  # an indented source line is not a file


def test_the_index_report_reads_the_tools_own_scanned_line_with_colour_stripped():
    text = _fx("index.txt")
    assert zg._files_indexed(text) is not None, "the captured index output has no `N scanned, M added` line"
    assert zg._files_indexed("\x1b[32m12\x1b[0m scanned, \x1b[32m10\x1b[0m added, 0 modified, 0 retried") == 10
    assert zg._files_indexed("Chunks: 7205\nEntities: 12") is None


def test_the_captured_tools_list_is_the_one_default_tool_or_the_capture_says_why():
    d = json.loads(_fx("tools_list.json"))
    if d.get("tools_list_json"):
        tools = json.loads(d["tools_list_json"])
        assert [t["name"] for t in tools] == ["zvec_grep_search"]
        assert all(set(t) == {"name", "description", "inputSchema"} for t in tools)
    else:
        assert d.get("error"), "neither a tools list nor a reason"


# ---- the call plan and the script -----------------------------------------------

def test_the_call_plan_follows_the_fairness_note_and_passes_no_default():
    assert zg._cmds(_task("P1", "cache_put")) == [["zg", "query", "--prefer-symbol", "cache_put"]]
    assert zg._cmds(_task("T", "router route handler")) == [["zg", "query", "router route handler"]]
    assert zg._cmds(_task("P2", "cache_put")) == [["zg", "query", "--rg", "-w", "cache_put"]]
    assert zg._cmds(_task("P4", "src/jcodemunch_mcp/storage/token_tracker.py")) == [["zg", "query", "--rg", "-F", "token_tracker"]]
    for cmds in (zg._cmds(_task(c, "x")) for c in ("P1", "P2", "P4", "T")):
        assert len(cmds) == 1
        assert not any(a.startswith(("--limit", "--preview", "--embedding", "--mode")) for a in cmds[0])


def test_the_script_copies_the_corpus_indexes_once_then_times_every_call():
    s = zg._script([_task("P1", "a", "t1"), _task("P2", "b", "t2")])
    assert f"cp -r /corpus {zg.PROJECT}" in s
    assert s.count("zg index .") == 1 and f"--embedding {zg.MODEL}" in s
    assert "LABEL=t1.0; ms zg query --prefer-symbol a" in s
    assert "LABEL=t2.0; ms zg query --rg -w b" in s
    assert s.index("zg index") < s.index("LABEL=t1.0") < s.index("tools_list.py")


# ---- prepare() over a fake container -------------------------------------------

def test_prepare_reads_timings_and_files_into_answers(tmp_path, monkeypatch):
    a = zg.make("docker")
    monkeypatch.setattr(a, "image", lambda: None)
    corpus = Corpus(id="self@x", path=tmp_path / "c", sha256="0", files=("a.py",))
    (tmp_path / "c").mkdir()
    tasks = [_task("P1", "cache_put", "p1"), _task("P2", "cache_put", "p2"), _task("T", "never ran", "t")]

    def fake_run(tag, args, corpus_path, out, timeout, **kw):
        assert tag == zg.TAG and args == ["/out/run.sh"] and kw.get("private_home") is True
        assert set(kw.get("extra_env", {})) == {"ZVEC_GREP_MODE", "ZVEC_GREP_HOME", "ZVEC_GREP_MODEL_CACHE", "ZVEC_GREP_EMBEDDING"}
        (out / "timings.txt").write_text("index rc=0 ms=4200\np1.0 rc=0 ms=310\np2.0 rc=0 ms=90\n", encoding="utf-8")
        (out / "index.txt").write_text(_fx("index.txt"), encoding="utf-8")
        (out / "p1.0.txt").write_text(_fx("self-P1-cache_put.0.txt"), encoding="utf-8")
        (out / "p2.0.txt").write_text(_fx("self-P2-cache_put.0.txt"), encoding="utf-8")
        (out / "tools_list.json").write_text(json.dumps({"tools_list_json": None, "error": "capture skipped in the test"}), encoding="utf-8")
        return RunResult(rc=0, stdout="", stderr="", seconds=5.0, timed_out=False)

    monkeypatch.setattr(zg.sandbox, "run", fake_run)
    a.prepare(corpus, tmp_path, tasks)
    idx = a.index(corpus, tmp_path)
    assert idx.ok and idx.seconds == 4.2 and idx.files_indexed == zg._files_indexed(_fx("index.txt"))
    p1 = a.answer(corpus, tasks[0], tmp_path)
    assert p1.calls == 1 and p1.latency_ms == [310.0] and p1.cited and p1.error is None and p1.tokens > 0
    p2 = a.answer(corpus, tasks[1], tmp_path)
    assert p2.calls == 1 and all(ln == 0 for _, ln in p2.cited)
    never = a.answer(corpus, tasks[2], tmp_path)
    assert never.calls == 0 and never.error and "not run" in never.error
    assert a.tools_list_tokens() is None


# ---- pins, image, sandbox -------------------------------------------------------

def test_the_pin_digest_is_the_integrity_npm_ci_enforces():
    lock = json.loads((COMPETE / "sandbox" / "zvec_grep.package-lock.json").read_text(encoding="utf-8"))
    entry = lock["packages"]["node_modules/@zvec/zvec-grep"]
    assert entry["version"] == zg.ZvecGrep.pin.version == "0.2.2"
    assert entry["integrity"] == zg.ZvecGrep.pin.digest == zg.INTEGRITY
    pkg = json.loads((COMPETE / "sandbox" / "zvec_grep.package.json").read_text(encoding="utf-8"))
    assert pkg["dependencies"] == {"@zvec/zvec-grep": "0.2.2"}


def test_the_dockerfile_pins_the_base_warms_the_model_and_the_env_is_documented():
    df = (COMPETE / "sandbox" / "zvec_grep.Dockerfile").read_text(encoding="utf-8")
    assert re.search(r"^FROM node:22-bookworm-slim@sha256:[0-9a-f]{64}$", df, re.M)
    assert "npm ci" in df and "zvec_grep.package-lock.json" in df
    assert f"--embedding {zg.MODEL}" in df and "ZVEC_GREP_MODEL_CACHE=/opt/zg-models" in df
    assert zg.ENV["ZVEC_GREP_MODEL_CACHE"] == "/opt/zg-models" and zg.ENV["ZVEC_GREP_MODE"] == "direct"
    assert "USER 65534:65534" in df


def test_a_competitor_refuses_the_none_sandbox_and_satisfies_the_interface():
    with pytest.raises(RuntimeError):
        zg.make("none")
    validate(zg.make("docker"))
