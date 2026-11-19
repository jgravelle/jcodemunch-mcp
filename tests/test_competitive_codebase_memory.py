"""The codebase-memory-mcp adapter and the MCP stdio driver
(docs/competitive/DESIGN.md s1.3, s2; docs/competitive/fairness/codebase_memory.md).

What each test pins, and why (for docs/harness/ARCHAEOLOGY.md):

- the adapter's parsers read the tool's REAL shapes, captured 2026-09-05 from
  v0.10.8 in the sandbox (tests/fixtures/competitive/codebase_memory_mcp.json):
  search_graph's JSON twin (qn/file/lines columns), get_code_snippet's
  absolute /corpus path, query_graph's tree text; a release that changes a
  shape fails here, not silently as an F1 of 0;
- the call plan follows the fairness note: search_graph limit 3 then a
  snippet per hit for P1/T, trace_path inbound depth 1 for P2, an IMPORTS
  query for P4, and the citation twins are marked uncharged;
- the driver serialises tools/list as name/description/inputSchema (the
  zhang-liz shape DESIGN s2 cites) and turns a tools/call result into the
  concatenated text an agent receives;
- a competitor refuses the `none` sandbox; the P4 truth on the self set is
  the union of textual and re-export-resolved importers (CF-19), computed
  from source, so a tool that resolves re-exports is not graded down.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
COMPETE = REPO / "benchmarks" / "competitive"
FIX = json.loads((REPO / "tests" / "fixtures" / "competitive" / "codebase_memory_mcp.json").read_text(encoding="utf-8"))
if not COMPETE.is_dir():  # excluded from the sdist (pyproject); the tests are meaningless without it
    pytest.skip("benchmarks/competitive is not in this tree (not shipped in the sdist)", allow_module_level=True)
sys.path.insert(0, str(COMPETE))
sys.path.insert(0, str(COMPETE / "sandbox"))

from adapter import Task  # noqa: E402
from adapters import codebase_memory as cbm  # noqa: E402
import mcp_driver  # noqa: E402


def _call(cid: str) -> dict:
    return next(c for c in FIX["calls"] if c["id"] == cid)


def test_search_graph_json_rows_yield_qualified_name_file_and_first_line():
    rows = cbm._search_rows(_call("sg_json")["result_text"])
    assert rows[0] == ("corpus-src.jcodemunch_mcp.storage.sqlite_store._cache_put", "jcodemunch_mcp/storage/sqlite_store.py", 408)
    assert ("corpus-src.jcodemunch_mcp.storage.token_tracker._State.cache_put", "jcodemunch_mcp/storage/token_tracker.py", 369) in rows
    assert cbm._search_rows("not json") == []


def test_cypher_tree_text_yields_paths_with_the_project_prefix_and_optional_line():
    imps = cbm._cypher_rows(_call("cy_imp")["result_text"], "src/")
    assert ["src/jcodemunch_mcp/storage/__init__.py", 0] in imps
    assert ["src/jcodemunch_mcp/server.py", 0] in imps
    assert all(f.startswith("src/") for f, _ in imps) and len(imps) == 28
    callers = cbm._cypher_rows(_call("cy_callers")["result_text"], "src/")
    assert callers == [["src/jcodemunch_mcp/storage/token_tracker.py", 1620]]
    assert cbm._cypher_rows("rows: 0  (cols: a.path)\nhint: nothing", "src/") == []


def test_snippet_citation_strips_the_container_root():
    text = '{"name":"cache_put","file_path":"/corpus/src/jcodemunch_mcp/storage/token_tracker.py","start_line":369}'
    assert cbm._snippet_cite(text, "src/") == [["src/jcodemunch_mcp/storage/token_tracker.py", 369]]
    assert cbm._snippet_cite('{"file_path":"jcodemunch_mcp/x.py","start_line":3}', "src/") == [["src/jcodemunch_mcp/x.py", 3]]


def test_the_call_plan_follows_the_fairness_note_and_marks_citation_twins_uncharged():
    tasks = [Task(id="a", corpus="c", category="P1", query="cache_put"),
             Task(id="b", corpus="c", category="P2", query="cache_put"),
             Task(id="d", corpus="c", category="P4", query="src/jcodemunch_mcp/storage/token_tracker.py")]
    plan = cbm.CodebaseMemory._plan("corpus-src", "src/", tasks)
    by = {c["id"]: c for c in plan}
    assert by["a|sg"]["tool"] == "search_graph" and by["a|sg"]["args"]["limit"] == 3 and by["a|sg"]["charged"]
    assert by["a|sg.json"]["args"]["format"] == "json" and not by["a|sg.json"]["charged"]
    assert by["b|tr"]["args"] == {"project": "corpus-src", "function_name": "cache_put", "direction": "inbound", "depth": 1}
    assert not by["b|tr.cy"]["charged"] and "CALLS" in by["b|tr.cy"]["args"]["query"]
    # the P4 path is relative to the project root (the src/ prefix is stripped) and the query is charged
    assert "jcodemunch_mcp/storage/token_tracker.py" in by["d|cy"]["args"]["query"] and "'src/" not in by["d|cy"]["args"]["query"]
    assert by["d|cy"]["charged"]
    assert cbm._q("it's") == "it\\'s"


def test_the_root_is_tried_first_then_each_top_level_directory(tmp_path):
    from adapter import Corpus

    c = Corpus(id="x", path=tmp_path, sha256="0", files=("src/a.py", "src/b/c.py", "lib/d.py", "README.md"))
    assert cbm.CodebaseMemory._projects(c) == [("/corpus", ""), ("/corpus/lib", "lib/"), ("/corpus/src", "src/")]


def test_the_driver_serialises_tools_list_in_the_zhang_liz_shape_and_flattens_results():
    tl = json.loads(FIX["tools_list_json"])
    assert {"name", "description", "inputSchema"} == set(tl[0])
    assert "search_graph" in {t["name"] for t in tl}
    text, err = mcp_driver.result_text({"result": {"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}})
    assert text == "ab" and err is False
    text, err = mcp_driver.result_text({"error": {"code": -1, "message": "x"}})
    assert err is True and "x" in text
    assert mcp_driver.result_text(None) == ("", True)
    text, err = mcp_driver.result_text({"result": {"content": [], "structuredContent": {"k": 1}, "isError": True}})
    assert text == '{"k":1}' and err is True


def test_a_competitor_refuses_the_none_sandbox():
    with pytest.raises(RuntimeError):
        cbm.make("none")


def test_the_p4_truth_is_the_union_of_direct_and_re_export_resolved_importers():
    tasks = json.loads((COMPETE / "tasks" / "self.json").read_text(encoding="utf-8"))["tasks"]
    p4 = next(t for t in tasks if t["id"] == "self-P4-token_tracker")
    files = {f for f, _ in p4["expected"]}
    assert "src/jcodemunch_mcp/server.py" in files  # direct
    assert "src/jcodemunch_mcp/tools/get_file_outline.py" in files  # via storage/__init__ re-export
    assert "CF-19" in p4["source"] and len(files) >= 29
