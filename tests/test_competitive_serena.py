"""The Serena adapter over the MCP stdio driver
(docs/competitive/DESIGN.md s1.3, s2; docs/competitive/fairness/serena.md).

What each test pins, and why (for docs/harness/ARCHAEOLOGY.md):

- the adapter's parsers read the tool's REAL shapes, captured 2026-09-05 from
  v1.7.0 in the sandbox (tests/fixtures/competitive/serena_mcp.json): a flat
  find_symbol list, a find_referencing_symbols result grouped by
  relative_path then kind, body_location lines that are LSP 0-based and are
  cited 1-based; a release that changes a shape fails here, not silently as
  an F1 of 0;
- the call plan follows the fairness note: find_symbol for P1 and P2,
  search_for_pattern over the query words for T, every call charged, and
  the follow-ups (a body read per match, references per exact-name match)
  named by the first answers; a P2 answer cites the references only;
- P4 is not in the adapter's categories (NOT COMPARABLE, never a zero);
- the captured tools/list is the 29-tool default surface in the
  name/description/inputSchema shape; a competitor refuses the `none`
  sandbox; the pinned wheel digest is the one the hashed lockfile installs.
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
FIX = json.loads((REPO / "tests" / "fixtures" / "competitive" / "serena_mcp.json").read_text(encoding="utf-8"))
sys.path.insert(0, str(COMPETE))
sys.path.insert(0, str(COMPETE / "sandbox"))

from adapter import Task  # noqa: E402
from adapters import serena  # noqa: E402


def _call(cid: str) -> dict:
    return next(c for c in FIX["calls"] if c["id"] == cid)


def test_find_symbol_rows_yield_name_path_relative_path_and_one_based_line():
    syms = serena._symbols(_call("fs_cache_put")["result_text"])
    assert syms == [{"name_path": "_State/cache_put", "relative_path": "src/jcodemunch_mcp/storage/token_tracker.py", "start_line": 369}]
    assert serena._cites(_call("fs_validate_path")["result_text"]) == [["src/jcodemunch_mcp/security.py", 108]]
    assert serena._symbols("not json") == [] and serena._symbols("Depth 0 overview:\n[]") == []


def test_grouped_references_inherit_the_path_from_the_enclosing_key():
    refs = serena._symbols(_call("refs_cache_put")["result_text"])
    assert refs == [{"name_path": "result_cache_put", "relative_path": "src/jcodemunch_mcp/storage/token_tracker.py", "start_line": 1620}]


def test_a_body_read_cites_the_same_definition_once():
    assert serena._cites(_call("fs_body_validate_path")["result_text"]) == [["src/jcodemunch_mcp/security.py", 108]]
    assert "body" in serena._parse_json(_call("fs_body_validate_path")["result_text"])[0]


def test_a_pattern_search_result_is_charged_but_cites_nothing():
    text = _call("sp_middleware")["result_text"]
    assert "server.py" in text and serena._cites(text) == []


def test_the_call_plan_follows_the_fairness_note_and_charges_every_call():
    tasks = [Task(id="a", corpus="c", category="P1", query="cache_put"),
             Task(id="t", corpus="c", category="T", query="router route handler"),
             Task(id="b", corpus="c", category="P2", query="cache_put")]
    plan = serena.Serena._plan(tasks)
    by = {c["id"]: c for c in plan}
    assert by["a|fs"]["tool"] == "find_symbol" and by["a|fs"]["args"] == {"name_path_pattern": "cache_put"}
    assert by["t|sp"]["tool"] == "search_for_pattern" and by["t|sp"]["args"]["substring_pattern"] == "router|route|handler"
    assert by["t|sp"]["args"]["restrict_search_to_code_files"] is True
    assert by["b|fs"]["args"] == {"name_path_pattern": "cache_put"}
    assert all(c["charged"] for c in plan)
    assert "P4" not in serena.Serena.categories and serena.Serena.categories == {"P1", "P2", "T"}


def test_the_captured_tools_list_is_the_default_surface_in_the_zhang_liz_shape():
    tl = json.loads(FIX["tools_list_json"])
    assert len(tl) == 29 and {"name", "description", "inputSchema"} == set(tl[0])
    assert {"find_symbol", "find_referencing_symbols", "search_for_pattern", "get_current_config"} <= {t["name"] for t in tl}
    assert FIX["server_info"]["name"] == "Serena"


def test_a_competitor_refuses_the_none_sandbox():
    with pytest.raises(RuntimeError):
        serena.make("none")


def _take_while_indented(lines):
    for ln in lines:
        if not ln.startswith((" ", "\t")):
            return
        yield ln


def test_the_pinned_wheel_digest_is_the_one_the_lockfile_installs():
    lock = (COMPETE / "sandbox" / "serena.requirements.txt").read_text(encoding="utf-8")
    i = lock.index("serena-agent==1.7.0")
    lines = lock[i:].splitlines()
    block = [lines[0]] + list(_take_while_indented(lines[1:]))
    assert any(serena.Serena.pin.digest in ln for ln in block)
    assert serena.Serena.pin.digest not in lock[i + len("\n".join(block)):]
    assert "pyright==1.1.403" in lock
    df = (COMPETE / "sandbox" / "serena.Dockerfile").read_text(encoding="utf-8")
    assert "--require-hashes" in df and "PYRIGHT_PYTHON_NODE_VERSION=\n" in df  # unset for the run (fairness note)
    cfg = (COMPETE / "sandbox" / "serena_config.yml").read_text(encoding="utf-8")
    assert "web_dashboard: False" in cfg and "ls_path: /usr/local/bin/pyright-langserver" in cfg and "/out/serena-projects/" in cfg
