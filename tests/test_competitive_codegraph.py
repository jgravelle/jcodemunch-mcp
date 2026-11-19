"""The CodeGraph adapter over the MCP stdio driver
(docs/competitive/DESIGN.md s1.3, s2; docs/competitive/fairness/codegraph.md).

What each test pins, and why (for docs/harness/ARCHAEOLOGY.md):

- the adapter's parsers read the tool's REAL text shapes, captured 2026-09-05
  from v1.6.0 in the sandbox (tests/fixtures/competitive/codegraph_mcp.json):
  a search hit's `path:line` line, a node's `**Location:**`, a caller row's
  trailing `path:line`, and a file's `used by N files: a, b, +K more` list;
  a release that changes a shape fails here, not silently as an F1 of 0;
- the call plan follows the fairness note: search (limit 3) and node for
  P1, explore for T, callers for P2, node in file mode for P4, every call
  charged, and every T task in its own session because explore elides
  lines it sent earlier in a session;
- the index report is `init`'s wall time and `status`'s file count;
- the captured tools/list is the README's example allowlist (4 tools) in the
  name/description/inputSchema shape, and the default surface is one tool;
  a competitor refuses the `none` sandbox; the pinned bundle digest is the
  one the Dockerfile verifies.
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
FIX = json.loads((REPO / "tests" / "fixtures" / "competitive" / "codegraph_mcp.json").read_text(encoding="utf-8"))
sys.path.insert(0, str(COMPETE))
sys.path.insert(0, str(COMPETE / "sandbox"))

from adapter import Task  # noqa: E402
from adapters import codegraph  # noqa: E402


def _call(cid: str) -> dict:
    return next(c for c in FIX["calls"] if c["id"] == cid)


def _cites(cid: str) -> list[list]:
    c = _call(cid)
    return codegraph._cites(c["tool"], c["args"], c["result_text"])


def test_search_and_node_cite_the_definition_line():
    assert _cites("search_p1") == [["src/jcodemunch_mcp/storage/token_tracker.py", 369],
                                   ["src/jcodemunch_mcp/tools/search_symbols.py", 146],
                                   ["src/jcodemunch_mcp/storage/sqlite_store.py", 408]]
    assert _cites("node_p1") == [["src/jcodemunch_mcp/storage/token_tracker.py", 369]]
    assert "def cache_put" in _call("node_p1")["result_text"]  # includeCode: the body travels with the location


def test_callers_cite_each_caller_row():
    assert _cites("callers_p2") == [["src/jcodemunch_mcp/storage/token_tracker.py", 1620], ["tests/test_cache_hit_rate_basis.py", 41]]


def test_a_file_node_cites_the_dependents_it_lists_and_drops_the_more_marker():
    rows = _cites("node_p4")
    assert len(rows) == 8 and all(ln == 0 for _, ln in rows) and rows[0] == ["benchmarks/harness/capture_token_baseline.py", 0]
    assert not any(f.startswith("+") for f, _ in rows) and "+75 more" in _call("node_p4")["result_text"]  # the tool truncates; the parser does not invent


def test_explore_is_charged_but_cites_nothing():
    text = _call("explore_t")["result_text"]
    assert "Exploration:" in text and _cites("explore_t") == []


def test_the_call_plan_follows_the_fairness_note_and_charges_every_call():
    tasks = [Task(id="a", corpus="c", category="P1", query="cache_put"),
             Task(id="t1", corpus="c", category="T", query="router route handler"),
             Task(id="t2", corpus="c", category="T", query="middleware"),
             Task(id="b", corpus="c", category="P2", query="cache_put"),
             Task(id="d", corpus="c", category="P4", query="src/x.py")]
    plan = {c["id"]: c for t in tasks for c in codegraph.CodeGraph._calls(t)}
    assert plan["a|search"]["args"] == {"query": "cache_put", "limit": 3} and plan["a|node"]["args"] == {"symbol": "cache_put", "includeCode": True}
    assert plan["t1|explore"]["tool"] == "codegraph_explore" and plan["b|callers"]["tool"] == "codegraph_callers"
    assert plan["d|deps"]["args"] == {"file": "src/x.py", "symbolsOnly": True}
    assert all(c["charged"] for c in plan.values())
    sessions = codegraph.CodeGraph._sessions(tasks)
    assert [[t.id for t in s] for s in sessions] == [["a", "b", "d"], ["t1"], ["t2"]]
    assert codegraph.CodeGraph.categories == {"P1", "P2", "P4", "T"}


def test_the_index_report_reads_init_wall_and_status_file_count(tmp_path):
    (tmp_path / "0-init.txt").write_text(FIX["init_txt"], encoding="utf-8")
    (tmp_path / "0-init.log").write_text("Done", encoding="utf-8")
    (tmp_path / "0-status.log").write_text(FIX["status_log"], encoding="utf-8")
    r = codegraph._index_report(tmp_path, 0)
    rc, s, e = FIX["init_txt"].split()
    assert r["ok"] and r["files"] == 1022 and abs(r["secs"] - (int(e) - int(s)) / 1e9) < 1e-6
    (tmp_path / "0-init.txt").write_text("1 5 9", encoding="utf-8")
    assert codegraph._index_report(tmp_path, 0)["ok"] is False


def test_the_captured_tools_list_is_the_readme_allowlist_and_the_default_is_one_tool():
    tl = json.loads(FIX["tools_list_json"])
    assert {t["name"] for t in tl} == {"codegraph_explore", "codegraph_node", "codegraph_search", "codegraph_callers"}
    assert {"name", "description", "inputSchema"} == set(tl[0])
    assert [t["name"] for t in json.loads(FIX["default_surface_tools_list_json"])] == ["codegraph_explore"]
    assert FIX["server_info"] == {"name": "codegraph", "version": "1.6.0"}
    assert codegraph.MCP_TOOLS == "explore,node,search,callers"


def test_a_competitor_refuses_the_none_sandbox_and_the_bundle_digest_is_the_dockerfile_s():
    with pytest.raises(RuntimeError):
        codegraph.make("none")
    df = (COMPETE / "sandbox" / "codegraph.Dockerfile").read_text(encoding="utf-8")
    assert f"CG_SHA256={codegraph.CodeGraph.pin.digest}" in df and "sha256sum -c" in df
    assert "DO_NOT_TRACK=1" in df and "CODEGRAPH_NO_DAEMON=1" in df
    assert "--no-watch" in codegraph.SERVER and "codegraph init --yes" in codegraph.SERVER
