"""The code-review-graph adapter over the MCP stdio driver
(docs/competitive/DESIGN.md s1.3, s2; docs/competitive/fairness/code_review_graph.md).

What each test pins, and why (for docs/harness/ARCHAEOLOGY.md):

- the adapter's parsers read the tool's REAL shapes, captured 2026-09-05 from
  v2.3.8 in the sandbox (tests/fixtures/competitive/code_review_graph_mcp.json):
  semantic_search_nodes_tool rows (absolute /corpus file_path, line_start),
  the build summary (files_parsed), the `ambiguous` reply with candidates,
  a zero-result query; a release that changes a shape fails here, not
  silently as an F1 of 0;
- the call plan follows the fairness note: semantic_search_nodes_tool limit 3
  for P1/T, callers_of for P2 (then one re-run per candidate named exactly
  the query, the disambiguation the tool asks for), importers_of for P4,
  every call charged, repo_root passed on every call;
- the tools/list the driver captured is the 30-tool default surface in the
  name/description/inputSchema shape (DESIGN s2), so the schema-weight row
  measures what the README ships;
- a competitor refuses the `none` sandbox; the pinned wheel digest in the
  adapter is the one the hashed lockfile installs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
COMPETE = REPO / "benchmarks" / "competitive"
FIX = json.loads((REPO / "tests" / "fixtures" / "competitive" / "code_review_graph_mcp.json").read_text(encoding="utf-8"))
sys.path.insert(0, str(COMPETE))
sys.path.insert(0, str(COMPETE / "sandbox"))

from adapter import Task  # noqa: E402
from adapters import code_review_graph as crg  # noqa: E402


def _call(cid: str) -> dict:
    return next(c for c in FIX["calls"] if c["id"] == cid)


def test_search_rows_cite_the_repo_relative_file_and_line_start():
    cited = crg._cites(_call("ss_hits")["result_text"])
    assert ["src/jcodemunch_mcp/tools/search_symbols.py", 146] in cited
    assert all(not f.startswith("/corpus") for f, _ in cited) and len(cited) == 3
    assert crg._cites(_call("ss_empty")["result_text"]) == []
    assert crg._cites(_call("importers_zero")["result_text"]) == []
    assert crg._cites("not json") == []


def test_an_ambiguous_reply_yields_the_candidates_named_exactly_the_query():
    text = _call("callers_ambiguous")["result_text"]
    assert crg._exact_candidates(text, "cache_put") == ["/corpus/src/jcodemunch_mcp/storage/token_tracker.py::_State.cache_put"]
    assert crg._exact_candidates(text, "nothing_named_this") == []
    assert crg._cites(text) == []  # candidates are not citations
    # the re-run on the qualified name cites the caller's file and line
    assert crg._cites(_call("callers_qn")["result_text"]) == [["src/jcodemunch_mcp/storage/token_tracker.py", 1620]]


def test_the_build_summary_yields_files_parsed():
    assert crg._build_files(_call("build")["result_text"]) == 277
    assert crg._build_files("Full build complete: parsed 12 files") == 12
    assert crg._build_files("{}") is None


def test_the_call_plan_follows_the_fairness_note_and_charges_every_call():
    tasks = [Task(id="a", corpus="c", category="P1", query="cache_put"),
             Task(id="t", corpus="c", category="T", query="router route handler"),
             Task(id="b", corpus="c", category="P2", query="cache_put"),
             Task(id="d", corpus="c", category="P4", query="src/jcodemunch_mcp/storage/token_tracker.py")]
    plan = crg.CodeReviewGraph._plan(tasks)
    by = {c["id"]: c for c in plan}
    assert by["a|ss"]["tool"] == "semantic_search_nodes_tool" and by["a|ss"]["args"]["limit"] == 3
    assert by["t|ss"]["args"]["query"] == "router route handler"
    assert by["b|callers"]["args"] == {"pattern": "callers_of", "target": "cache_put", "repo_root": "/corpus"}
    assert by["d|importers"]["args"]["pattern"] == "importers_of"
    assert all(c["charged"] for c in plan) and all(c["args"]["repo_root"] == "/corpus" for c in plan)


def test_the_captured_tools_list_is_the_thirty_tool_default_in_the_zhang_liz_shape():
    tl = json.loads(FIX["tools_list_json"])
    assert len(tl) == 30 and {"name", "description", "inputSchema"} == set(tl[0])
    names = {t["name"] for t in tl}
    assert {"semantic_search_nodes_tool", "query_graph_tool", "build_or_update_graph_tool"} <= names
    assert FIX["server_info"]["name"] == "code-review-graph"


def test_a_competitor_refuses_the_none_sandbox():
    with pytest.raises(RuntimeError):
        crg.make("none")


def test_the_pinned_wheel_digest_is_the_one_the_lockfile_installs():
    lock = (COMPETE / "sandbox" / "code_review_graph.requirements.txt").read_text(encoding="utf-8")
    i = lock.index("code-review-graph==2.3.8")
    block = lock[i:lock.index("\n\n", i) if "\n\n" in lock[i:] else len(lock)]
    assert crg.CodeReviewGraph.pin.digest in block
    assert "--require-hashes" in (COMPETE / "sandbox" / "code_review_graph.Dockerfile").read_text(encoding="utf-8")
