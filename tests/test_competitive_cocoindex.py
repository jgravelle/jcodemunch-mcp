"""The CocoIndex Code adapter over the MCP stdio driver, the embedding
representative (docs/competitive/DESIGN.md s1.3, s2;
docs/competitive/fairness/cocoindex.md).

What each test pins, and why (for docs/harness/ARCHAEOLOGY.md):

- the adapter's parser reads the tool's REAL `search` result JSON, captured
  2026-09-05 from 0.2.41 in the sandbox over the PINNED self corpus
  (tests/fixtures/competitive/cocoindex_mcp.json): each result's
  `file_path`/`start_line` is one citation, a project-absolute path is made
  relative, and unparseable text cites nothing; a release that changes the
  shape fails here, not silently as an F1 of 0;
- the index report is `init`+`index`'s wall and the `Files:` line of the
  tool's own status; the first parser matched "7205 chunks" as the file
  count, so the chunk line is asserted NOT to be read;
- the call plan is one `search` per P1 or T task at the defaults (no limit,
  no refresh flag passed), charged; P2 and P4 get no call (no documented
  tool answers them: the fairness note's scope statement);
- prepare() reads the driver's file into the index report and one answer
  per task; a task with no call in the file is an error, never an empty
  answer;
- the captured tools/list is the one documented tool in the
  name/description/inputSchema shape; the pin's digest is the wheel hash
  the lockfile makes pip require; the Dockerfile installs with
  --require-hashes on Python 3.12 and carries the offline switches; a
  competitor refuses the `none` sandbox.
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
FIX = json.loads((REPO / "tests" / "fixtures" / "competitive" / "cocoindex_mcp.json").read_text(encoding="utf-8"))
sys.path.insert(0, str(COMPETE))
sys.path.insert(0, str(COMPETE / "sandbox"))

from adapter import Corpus, Task, count_tokens  # noqa: E402
from adapters import cocoindex as cc  # noqa: E402
from sandbox import RunResult  # noqa: E402


def _call(cid: str) -> dict:
    return next(c for c in FIX["calls"] if c["id"] == cid)


def test_search_results_cite_each_file_path_at_its_start_line():
    text = _call("p1_cache_put")["result_text"]
    doc = json.loads(text)
    assert doc["success"] and len(doc["results"]) == 5
    cited = cc._cites(text)
    expected: list[list] = []
    for r in doc["results"]:
        if [r["file_path"], r["start_line"]] not in expected:
            expected.append([r["file_path"], r["start_line"]])
    assert cited == expected and cited[0] == ["src/jcodemunch_mcp/config.py", 425]
    assert all(not f.startswith("/") for f, _ in cited)
    # an absolute path under the project copy is made relative; garbage cites nothing
    abs_doc = json.dumps({"success": True, "results": [{"file_path": cc.PROJECT + "/src/a.py", "start_line": 7}]})
    assert cc._cites(abs_doc) == [["src/a.py", 7]]
    assert cc._cites("not json") == [] and cc._cites(json.dumps({"success": False, "message": "x"})) == []


def test_the_index_report_reads_the_wall_and_the_tools_own_files_line(tmp_path):
    (tmp_path / "0-index.txt").write_text(FIX["index_txt"], encoding="utf-8")
    (tmp_path / "0-init.log").write_text(FIX["init_log"], encoding="utf-8")
    (tmp_path / "0-index.log").write_text(FIX["index_log"], encoding="utf-8")
    (tmp_path / "0-status.log").write_text(FIX["status_log"], encoding="utf-8")
    rep = cc._index_report(tmp_path, 0)
    assert rep["ok"] and rep["files"] == 274 and rep["files"] != 7205  # the chunk count is not a file count
    rc, s, e = FIX["index_txt"].split()
    assert rep["secs"] == (int(e) - int(s)) / 1e9 and rep["secs"] > 100
    assert "Files: 274" in rep["err"] and "daemon start and model load included" in rep["err"]  # whitespace collapsed
    assert cc._index_report(tmp_path / "nowhere", 0)["ok"] is False


def test_the_call_plan_is_one_search_at_the_defaults_for_p1_and_t_only():
    assert cc.CocoIndex.categories == frozenset({"P1", "T"})
    p1 = cc.CocoIndex._calls(Task(id="p", corpus="c", category="P1", query="cache_put", expected=(("a.py", 0),), tolerance_lines=1))
    assert p1 == [{"id": "p|search", "tool": "search", "args": {"query": "cache_put"}, "charged": True}]
    t = cc.CocoIndex._calls(Task(id="t", corpus="c", category="T", query="router route handler"))
    assert t[0]["args"] == {"query": "router route handler"} and "limit" not in t[0]["args"] and "refresh_index" not in t[0]["args"]
    for cat in ("P2", "P4"):
        assert cc.CocoIndex._calls(Task(id="x", corpus="c", category=cat, query="q", expected=(("a.py", 0),), tolerance_lines=1)) == []
    assert "ccc init" in cc.SERVER and "ccc index" in cc.SERVER and "ccc mcp" in cc.SERVER and "global_settings.yml" in cc.SERVER
    assert "provider: sentence-transformers" in cc.SETTINGS_YML and cc.MODEL in cc.SETTINGS_YML


def test_prepare_reads_the_drivers_file_into_index_and_answers(tmp_path, monkeypatch):
    def fake_run(tag, args, corpus, out, timeout, workdir="/corpus", extra_env=None, private_home=False):
        assert private_home and tag == cc.TAG and args[0] == "-c" and extra_env == {"MCP_DRIVER_TIMEOUT_S": "300"}
        calls = json.loads((out / "0-calls.json").read_text(encoding="utf-8"))
        # three calls planned (the third task's answer never comes back from the server: the "call not run" path below)
        assert [c["id"] for c in calls] == ["self-P1-cache_put|search", "self-T-router|search", "self-P1-missing|search"]
        for name in ("index_txt", "init_log", "index_log", "status_log"):
            (out / ("0-" + name.replace("_", "."))).write_text(FIX[name], encoding="utf-8")
        d = {"calls": [{**_call("p1_cache_put"), "id": "self-P1-cache_put|search"}, {**_call("t_router"), "id": "self-T-router|search"}],
             "tools_list_json": FIX["tools_list_json"], "error": None}
        (out / "0-mcp.json").write_text(json.dumps(d), encoding="utf-8")
        return RunResult(rc=0, stdout="", stderr="", seconds=200.0)

    monkeypatch.setattr(cc.sandbox, "run", fake_run)
    monkeypatch.setattr(cc.CocoIndex, "image", lambda self: None)
    g = cc.CocoIndex.__new__(cc.CocoIndex)
    g._cache = {}
    corpus = Corpus(id="self@x", path=tmp_path, sha256="0" * 64, files=())
    tasks = [Task(id="self-P1-cache_put", corpus="self@x", category="P1", query="cache_put", expected=(("a.py", 0),), tolerance_lines=1),
             Task(id="self-T-router", corpus="self@x", category="T", query="router route handler"),
             Task(id="self-P1-missing", corpus="self@x", category="P1", query="nothing", expected=(("a.py", 0),), tolerance_lines=1)]
    g.prepare(corpus, tmp_path, tasks[:2] + tasks[2:])
    rep = g.index(corpus, tmp_path)
    assert rep.ok and rep.files_indexed == 274 and rep.seconds > 100
    a = g.answer(corpus, tasks[0], tmp_path)
    assert a.calls == 1 and a.latency_ms == [849.08] and ("src/jcodemunch_mcp/config.py", 425) in a.cited and a.error is None
    assert a.tokens == count_tokens(_call("p1_cache_put")["result_text"]) and a.tokens > 500
    missing = g.answer(corpus, tasks[2], tmp_path)
    assert missing.error == "call not run" and missing.calls == 0
    assert g.tools_list_tokens() == count_tokens(FIX["tools_list_json"]) and g.version() == "0.2.41"


def test_the_captured_tools_list_is_the_one_documented_tool_and_the_pin_is_the_lockfiles_hash():
    tl = json.loads(FIX["tools_list_json"])
    assert [t["name"] for t in tl] == ["search"] and {"name", "description", "inputSchema"} <= set(tl[0])
    assert set(tl[0]["inputSchema"]["properties"]) >= {"query", "limit", "offset", "refresh_index"}
    assert FIX["server_info"] == {"name": "cocoindex-code", "version": "0.2.41"}
    with pytest.raises(RuntimeError):
        cc.make("none")
    req = (COMPETE / "sandbox" / "cocoindex.requirements.txt").read_text(encoding="utf-8")
    own = "\n".join(ln for ln in req.split("cocoindex-code==0.2.41", 1)[1].split("\n")[1:4] if ln.strip().startswith("--hash="))
    assert f"--hash=sha256:{cc.CocoIndex.pin.digest}" in own and len(cc.CocoIndex.pin.digest) == 64
    df = (COMPETE / "sandbox" / "cocoindex.Dockerfile").read_text(encoding="utf-8")
    assert "python:3.12-slim-bookworm@sha256:" in df and "--require-hashes" in df and "cocoindex.requirements.txt" in df
    assert "HF_HUB_OFFLINE=1" in df and "TRANSFORMERS_OFFLINE=1" in df and "COCOINDEX_DISABLE_USAGE_TRACKING=1" in df and cc.MODEL in df
