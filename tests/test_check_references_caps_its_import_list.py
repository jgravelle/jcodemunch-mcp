"""`check_references` caps its import list and says so (BM-2 plan 3, cap 4; maintainer ruling 2026-11-27).

`import_references` listed every file that imports the name, with no bound.
On this repository's own index `Path` alone was 706 files and 144,435 bytes,
and five identifiers in one batch came to 384,950 bytes, 279 KB of it import
rows (handoff ENG-bm2-plan3-reply-size-audit-2026-11-27.md).

The property:
- the MCP tool lists at most `max_import_results` importing files (default
  50; 0 lists all), per identifier;
- `import_references_total` is the number of importing files and
  `import_references_truncated` says whether any were left out; both are
  always present;
- `import_count` stays the total and `is_referenced` is decided from it, so
  a cut never turns a referenced name into an unreferenced one;
- the Python function lists all by default, so the preflights that read the
  list to decide a delete (`check_delete_safe`, `check_edit_safe`, the
  deletion investigator) never read a page as the whole, and no caller in
  `src/` other than the dispatcher passes a cap;
- the compact encoding carries both keys.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from jcodemunch_mcp.tools.check_references import check_references
from jcodemunch_mcp.tools.index_folder import index_folder

REPO = Path(__file__).resolve().parents[1]
NAME = "shared_helper"
IMPORTERS = 60


@pytest.fixture(scope="module")
def repo(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("cap4")
    src = tmp / "proj"
    src.mkdir()
    (src / "core.py").write_text(f"def {NAME}():\n    return 1\n", encoding="utf-8")
    for i in range(IMPORTERS):
        (src / f"user{i:02d}.py").write_text(f"from core import {NAME}\n", encoding="utf-8")
    store = tmp / "store"
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert r.get("success"), r
    return r["repo"], str(store)


def test_the_cap_cuts_the_list_and_discloses_the_total(repo):
    repo_id, store = repo
    out = check_references(repo_id, identifier=NAME, search_content=False, max_import_results=50, storage_path=store)
    assert len(out["import_references"]) == 50
    assert out["import_references_total"] == IMPORTERS
    assert out["import_references_truncated"] is True
    assert out["import_count"] == IMPORTERS
    assert out["is_referenced"] is True


def test_a_list_under_the_cap_reads_complete(repo):
    repo_id, store = repo
    out = check_references(repo_id, identifier=NAME, search_content=False, max_import_results=100, storage_path=store)
    assert len(out["import_references"]) == IMPORTERS == out["import_references_total"]
    assert out["import_references_truncated"] is False


@pytest.mark.parametrize("cap", [0, -3])
def test_zero_or_less_lists_every_importer(repo, cap):
    repo_id, store = repo
    out = check_references(repo_id, identifier=NAME, search_content=False, max_import_results=cap, storage_path=store)
    assert len(out["import_references"]) == IMPORTERS
    assert out["import_references_truncated"] is False


def test_the_python_default_lists_every_importer(repo):
    """The preflights call the function without the argument and must read the whole list."""
    repo_id, store = repo
    out = check_references(repo_id, identifier=NAME, search_content=False, storage_path=store)
    assert len(out["import_references"]) == IMPORTERS
    assert out["import_references_truncated"] is False


def test_each_identifier_in_a_batch_is_capped_on_its_own(repo):
    repo_id, store = repo
    out = check_references(repo_id, identifiers=[NAME, "core"], search_content=False, max_import_results=10, storage_path=store)
    first, second = out["results"]
    assert len(first["import_references"]) == 10 and first["import_references_total"] == IMPORTERS
    assert first["import_references_truncated"] is True
    # every user file imports the module `core` too, so its own list is cut on its own count
    assert len(second["import_references"]) == 10 and second["import_references_total"] == IMPORTERS
    assert second["import_references_truncated"] is True
    unique = check_references(repo_id, identifiers=["user00"], search_content=False, max_import_results=10, storage_path=store)
    assert unique["results"][0]["import_references_truncated"] is False


def _server_source() -> str:
    return (REPO / "src" / "jcodemunch_mcp" / "server.py").read_text(encoding="utf-8")


def test_the_mcp_tool_defaults_to_fifty_and_publishes_it():
    from jcodemunch_mcp import server

    tool = next(t for t in server._build_tools_list(surface_override="full") if t.name == "check_references")
    prop = tool.inputSchema["properties"]["max_import_results"]
    assert prop["default"] == 50
    assert "import_references_truncated" in tool.description
    tree = ast.parse(_server_source())
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and any(
            k.arg == "max_import_results" for k in n.keywords
        )
    ]
    assert calls, "the dispatcher never passes max_import_results"
    for c in calls:
        kw = next(k for k in c.keywords if k.arg == "max_import_results")
        assert isinstance(kw.value, ast.Call) and kw.value.args[1].value == 50, ast.dump(kw.value)


def test_no_caller_in_src_but_the_dispatcher_passes_a_cap():
    stray = []
    for path in (REPO / "src").rglob("*.py"):
        if path.name == "server.py" and path.parent.name == "jcodemunch_mcp":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and any(k.arg == "max_import_results" for k in n.keywords):
                fn = n.func
                name = getattr(fn, "id", None) or getattr(fn, "attr", None)
                if name in {"check_references", "_check_single", "_check_batch"}:
                    stray.append((str(path.relative_to(REPO)), n.lineno))
    # check_references.py itself forwards the argument between its own helpers
    stray = [s for s in stray if not s[0].endswith("check_references.py")]
    assert stray == [], stray


def test_the_compact_encoding_carries_the_cut(repo):
    from jcodemunch_mcp.encoding.schemas import check_references as enc

    repo_id, store = repo
    for kwargs in ({"identifier": NAME}, {"identifiers": [NAME, "core"]}):
        out = check_references(repo_id, search_content=False, max_import_results=5, storage_path=store, **kwargs)
        payload, enc_id = enc.encode("check_references", out)
        assert enc_id == enc.ENCODING_ID
        assert enc.decode(payload) == out
    assert "cr1" in enc.LEGACY_ENCODING_IDS and enc.ENCODING_ID != "cr1"
