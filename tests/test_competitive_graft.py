"""The Graft adapter over the MCP stdio driver, deterministic path only
(docs/competitive/DESIGN.md s1.3, s2; docs/competitive/fairness/graft.md).

What each test pins, and why (for docs/harness/ARCHAEOLOGY.md):

- the adapter's parsers read the tool's REAL text shapes, captured 2026-09-05
  from 0.16.0 in the sandbox over the PINNED self corpus
  (tests/fixtures/competitive/graft_mcp.json): a ranked node's
  `path:Lstart-Lend` span line, a trace hit's `(path:Lx-Ly)`, a find_all
  symbol header, and the "no indexed callers" answer that names the tool's
  own fallback; a release that changes a shape fails here, not silently as
  an F1 of 0;
- a P2 answer cites the references only, never the queried symbol's own
  header; a P4 find_all answer cites each file once;
- the P4 follow-up exists ONLY when the first answer says "no indexed
  callers": the tool's instruction, not a harness choice;
- the index report is the build's wall time and its own "parsed: N of M"
  line; the captured tools/list is the six documented tools in the
  name/description/inputSchema shape; a competitor refuses the `none`
  sandbox; the lockfile pins the package by the integrity the adapter names,
  and that integrity is the pin's digest (not a hash nothing verifies);
- a follow-up session that fails marks the P4 answer with an error (UNKNOWN
  is never rendered as a quieter answer).
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
COMPETE = REPO / "benchmarks" / "competitive"
if not COMPETE.is_dir():  # excluded from the sdist (pyproject); the tests are meaningless without it
    pytest.skip("benchmarks/competitive is not in this tree (not shipped in the sdist)", allow_module_level=True)
FIX = json.loads((REPO / "tests" / "fixtures" / "competitive" / "graft_mcp.json").read_text(encoding="utf-8"))
sys.path.insert(0, str(COMPETE))
sys.path.insert(0, str(COMPETE / "sandbox"))

from adapter import Task  # noqa: E402
from adapters import graft  # noqa: E402


def _call(cid: str) -> dict:
    return next(c for c in FIX["calls"] if c["id"] == cid)


def _cites(cid: str, category: str) -> list[list]:
    c = _call(cid)
    return graft._cites(c["tool"], category, c["result_text"])


def test_find_code_cites_each_ranked_node_span_start():
    rows = _cites("find_p1", "P1")
    assert rows[:3] == [["src/jcodemunch_mcp/storage/sqlite_store.py", 408], ["src/jcodemunch_mcp/storage/token_tracker.py", 1620],
                        ["src/jcodemunch_mcp/tools/search_symbols.py", 146]]
    assert len(rows) == 5  # the tool's default limit
    assert "[graft] tokens saved" in _call("find_p1")["result_text"]  # the preamble is part of what an agent receives: charged


def test_trace_calls_cites_the_hits_and_not_the_queried_symbol_header():
    assert _cites("trace_p2", "P2") == [["src/jcodemunch_mcp/storage/token_tracker.py", 1620]]
    assert "token_tracker.py:L369-L376" in _call("trace_p2")["result_text"]  # the header, deliberately not cited


def test_a_no_callers_answer_cites_nothing_and_names_the_follow_up():
    c = _call("trace_p4")
    assert _cites("trace_p4", "P4") == [] and graft.NO_CALLERS in c["result_text"]
    t = Task(id="d", corpus="c", category="P4", query="src/jcodemunch_mcp/storage/token_tracker.py")
    assert graft.Graft._follow_up(t, c) == [{"id": "d|grep", "tool": "graft_find_all", "args": {"pattern": "token_tracker", "fixed": True}, "charged": True}]
    assert graft.Graft._follow_up(t, _call("trace_p2")) == []  # an answer with callers gets no follow-up
    assert graft.Graft._follow_up(Task(id="b", corpus="c", category="P2", query="x"), c) == []  # only P4


def test_find_all_cites_each_file_once_at_line_zero():
    rows = _cites("p4_all", "P4")
    assert rows and all(ln == 0 for _, ln in rows) and len({f for f, _ in rows}) == len(rows)
    assert ["src/jcodemunch_mcp/server.py", 0] in rows and "29 hits in 20 symbols across 13 files" in _call("p4_all")["result_text"]
    assert len(rows) == 13 and ["src/jcodemunch_mcp/tools/search_columns.py", 0] in rows  # a module-level hit has its own header shape


def test_the_call_plan_follows_the_fairness_note_and_charges_every_call():
    tasks = [Task(id="a", corpus="c", category="P1", query="cache_put"), Task(id="t", corpus="c", category="T", query="router route handler"),
             Task(id="b", corpus="c", category="P2", query="cache_put"), Task(id="d", corpus="c", category="P4", query="src/x.py")]
    plan = {c["id"]: c for t in tasks for c in graft.Graft._calls(t)}
    assert plan["a|find"]["args"] == {"query": "cache_put"} and plan["t|find"]["tool"] == "graft_find_code"
    assert plan["b|trace"]["args"] == {"symbol": "cache_put"} and plan["d|trace"]["args"] == {"symbol": "src/x.py"}
    assert all(c["charged"] for c in plan.values()) and graft.Graft.categories == {"P1", "P2", "P4", "T"}
    assert "--no-gitignore --no-ignore" in graft.BUILD and "--dir /private/graft" in graft.BUILD and "mcp /corpus" in graft.SERVER


def test_the_index_report_reads_the_build_wall_and_its_parsed_line(tmp_path):
    (tmp_path / "0-build.txt").write_text(FIX["build_txt"], encoding="utf-8")
    (tmp_path / "0-build.log").write_text(FIX["build_log_tail"], encoding="utf-8")
    r = graft._index_report(tmp_path, 0)
    rc, s, e = FIX["build_txt"].split()
    assert r["ok"] and r["files"] == 277 and abs(r["secs"] - (int(e) - int(s)) / 1e9) < 1e-6
    (tmp_path / "0-build.txt").write_text("1 5 9", encoding="utf-8")
    assert graft._index_report(tmp_path, 0)["ok"] is False


def test_the_captured_tools_list_is_the_six_documented_tools():
    tl = json.loads(FIX["tools_list_json"])
    assert {t["name"] for t in tl} == {"graft_find_code", "graft_file_api", "graft_check_freshness", "graft_trace_calls", "graft_find_all", "graft_repo_map"}
    assert {"name", "description", "inputSchema"} == set(tl[0]) and FIX["server_info"] == {"name": "graft", "version": "0.16.0"}


def test_a_competitor_refuses_the_none_sandbox_and_the_lockfile_pins_the_named_integrity():
    with pytest.raises(RuntimeError):
        graft.make("none")
    lock = json.loads((COMPETE / "sandbox" / "graft.package-lock.json").read_text(encoding="utf-8"))
    g = lock["packages"]["node_modules/@nanonets/graft"]
    assert g["version"] == "0.16.0"
    integrity = "sha512-L3E5F1aDYJDCARgfR7O2VaMt8xwO1XNYyHiW2n1WhKnj87gPqoxoZJGNbGXfw6XeA9JSJX3naA36RZ+jDf4AcQ=="
    assert g["integrity"] == integrity and integrity in Path(graft.__file__).read_text(encoding="utf-8")
    assert len(base64.b64decode(integrity[7:])) == 64  # a real sha512, not a truncated one
    df = (COMPETE / "sandbox" / "graft.Dockerfile").read_text(encoding="utf-8")
    assert "npm ci" in df and "DO_NOT_TRACK=1" in df and "CI=1" in df and "graft.package-lock.json" in df


def test_the_pin_digest_is_the_integrity_npm_ci_enforces():
    lock = json.loads((COMPETE / "sandbox" / "graft.package-lock.json").read_text(encoding="utf-8"))
    assert graft.Graft.pin.digest == lock["packages"]["node_modules/@nanonets/graft"]["integrity"]
    assert graft.Graft.pin.digest.startswith("sha512-")


def test_a_follow_up_session_that_fails_marks_the_p4_answer_unknown(tmp_path, monkeypatch):
    """Review round 1, finding 4: a timed-out follow-up used to drop silently and the
    P4 row read as the first call alone with no error; UNKNOWN is never a quieter answer."""
    from adapter import Corpus  # noqa: E402
    from sandbox import RunResult  # noqa: E402

    trace = _call("trace_p4")
    assert graft.NO_CALLERS in trace["result_text"]
    sessions = []

    def fake_session(self, corpus, out, n, calls):
        sessions.append(n)
        if n == 0:
            return RunResult(rc=0, stdout="", stderr="", seconds=1.0), {"calls": [{**trace, "id": "t|trace"}], "tools_list_json": FIX["tools_list_json"]}
        return RunResult(rc=1, stdout="", stderr="", seconds=1.0, timed_out=True), None

    monkeypatch.setattr(graft.Graft, "_session", fake_session)
    monkeypatch.setattr(graft.Graft, "image", lambda self: None)
    g = graft.Graft.__new__(graft.Graft)
    g._cache = {}
    task = Task(id="t", corpus="self@x", category="P4", query="src/jcodemunch_mcp/storage/token_tracker.py")
    corpus = Corpus(id="self@x", path=tmp_path, sha256="0" * 64, files=())
    g.prepare(corpus, tmp_path, [task])
    a = g.answer(corpus, task, tmp_path)
    assert sessions == [0, 1] and a.calls == 1 and a.cited == frozenset()
    assert a.error is not None and "follow-up session timed out" in a.error
