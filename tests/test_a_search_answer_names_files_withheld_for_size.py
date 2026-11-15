"""A search answer says how many files the per-file size limit left out.

A file over ``max_file_size`` is counted in the index's coverage
(``withheld.too_large``), but the count reached an answer only inside
``_meta.verdict``, on an absent or degraded answer, and ``meta_fields: []``,
the shipped default, strips ``_meta``. A search that found something over a
corpus missing a large source file read as a search of the whole repo. The
answer body now carries the count and the setting that moves it, apart from
the file-count cap line (#1336).
"""

from __future__ import annotations

import json

import pytest

from jcodemunch_mcp import server


@pytest.fixture
def repo(tmp_path, monkeypatch):
    from jcodemunch_mcp.tools.index_folder import index_folder

    src = tmp_path / "proj"
    src.mkdir()
    (src / "small.py").write_text("def alpha_beta():\n    return 1\n", encoding="utf-8")
    big = "".join(f"def big_{i}():\n    return {i}\n" for i in range(4000))
    (src / "big.py").write_text(big, encoding="utf-8")
    store = tmp_path / "store"
    r = index_folder(
        str(src), use_ai_summaries=False, storage_path=str(store), max_size=20000
    )
    assert r.get("success"), r
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    return r["repo"], str(store)


async def _call(tool, arguments):
    out = await server.call_tool(tool, arguments)
    content = out.content if hasattr(out, "content") else out
    return json.loads(content[0].text)


def test_the_index_withheld_the_large_file(repo):
    """Non-vacuity: the fixture's large file is over the limit and counted."""
    from jcodemunch_mcp.storage.sqlite_store import SQLiteIndexStore

    repo_id, store = repo
    owner, name = repo_id.split("/", 1)
    index = SQLiteIndexStore(base_path=store).load_index(owner, name)
    assert (index.coverage.get("withheld") or {}).get("too_large") == 1


@pytest.mark.parametrize(
    "tool,args",
    [
        ("search_symbols", {"query": "alpha_beta"}),
        ("search_text", {"query": "alpha_beta"}),
    ],
)
async def test_a_search_that_found_something_names_the_withheld_count_in_its_body(
    repo, tool, args
):
    repo_id, _ = repo
    r = await _call(tool, {"repo": repo_id, **args})
    assert "_meta" not in r, "the shipped default strips _meta; the line must be in the body"
    line = r.get("index_warning", "")
    assert "1 file" in line and "max_file_size" in line and "JCODEMUNCH_MAX_FILE_SIZE" in line
    assert "20,000" in line, "the line names the limit the walk used"


def test_the_withheld_block_rides_in_meta_too(repo):
    from jcodemunch_mcp.tools.search_symbols import search_symbols

    repo_id, store = repo
    r = search_symbols(repo=repo_id, query="alpha_beta", storage_path=store)
    block = r["_meta"]["index_withheld"]
    assert block["files_over_size_limit"] == 1
    assert block["max_file_size"] == 20000


def test_an_index_that_withheld_nothing_carries_no_line(tmp_path, monkeypatch):
    from jcodemunch_mcp.tools.index_folder import index_folder
    from jcodemunch_mcp.tools.search_symbols import search_symbols

    src = tmp_path / "proj"
    src.mkdir()
    (src / "small.py").write_text("def alpha_beta():\n    return 1\n", encoding="utf-8")
    store = tmp_path / "store"
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    out = search_symbols(repo=r["repo"], query="alpha_beta", storage_path=str(store))
    assert "index_withheld" not in out.get("_meta", {})


def test_the_cap_line_and_the_size_line_are_separate_sentences():
    from jcodemunch_mcp.retrieval.verdict import index_withheld_warning

    line = index_withheld_warning({"files_over_size_limit": 3, "max_file_size": 512000})
    assert line.startswith("This index left out 3 files over")
    assert "max_folder_files" not in line
