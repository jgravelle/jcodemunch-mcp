"""The shared tool-result cache never serves an answer from an index another process replaced.

`find_references` and `get_blast_radius` keep their answers in the session
tracker's shared LRU (`storage/token_tracker.py`). The key held only the
query arguments, and the cache was emptied only by index-mutating tools
running in THIS process. A write from anywhere else (the file watcher, the
PostToolUse `index-file` spawn, `refresh`, a second server, a script calling
`incremental_save`) left the entry in place, so this process kept answering
from the index as it was before the write, for as long as the entry lived.

The interleaving is pinned: the parent fills its cache, a child process runs
the real `index_folder` against the same store and exits, and only then does
the parent ask again. In-process writes are kept out of the test on purpose,
because they evict the cache themselves and would hide the defect.

Each entry now records the load identity of the index its answer was
computed from (`storage.generation.load_token`), and a lookup hits only when
the index the caller holds now has that identity. `load_index` keeps its copy
across a write only when the write changed nothing it holds (the
`content_generation` rule), so the cache inherits that authority instead of
building a second one.
"""

from __future__ import annotations

import copy
import inspect
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from jcodemunch_mcp.storage import IndexStore, token_tracker
from jcodemunch_mcp.tools.find_references import find_references
from jcodemunch_mcp.tools.get_blast_radius import get_blast_radius
from jcodemunch_mcp.tools.index_folder import index_folder

SRC = Path(__file__).resolve().parents[1] / "src"

CHILD = """
import sys
from jcodemunch_mcp.tools.index_folder import index_folder
root, store = sys.argv[1:3]
res = index_folder(root, use_ai_summaries=False, storage_path=store)
print("OK" if res.get("success") else "FAIL " + repr(res))
"""


@pytest.fixture
def repo(tmp_path, monkeypatch):
    store = tmp_path / "store"
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    root = tmp_path / "src_repo"
    root.mkdir()
    (root / "a.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (root / "b.py").write_text("from a import helper\n\nhelper()\n", encoding="utf-8")
    token_tracker.result_cache_invalidate()
    res = index_folder(str(root), use_ai_summaries=False, storage_path=str(store))
    assert res.get("success"), res
    yield res["repo"], root, store
    token_tracker.result_cache_invalidate()


def _write_in_another_process(root: Path, store: Path) -> None:
    """Add an importer of `helper` and re-index it from a child process."""
    (root / "c.py").write_text("from a import helper\n\nhelper()\n", encoding="utf-8")
    env = dict(os.environ, CODE_INDEX_PATH=str(store), PYTHONPATH=str(SRC))
    out = subprocess.run(
        [sys.executable, "-c", CHILD, str(root), str(store)],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1] == "OK", out.stdout + out.stderr


def _ref_files(result: dict) -> set:
    return {r["file"] for r in result.get("references", [])}


def _blast_files(result: dict) -> set:
    return {r["file"] for r in result.get("confirmed", [])}


def test_find_references_serves_the_index_another_process_wrote(repo):
    repo_id, root, store = repo
    first = find_references(repo_id, identifier="helper", storage_path=str(store))
    assert _ref_files(first) == {"b.py"}, first
    # The cache is filled: an unchanged index answers from it.
    again = find_references(repo_id, identifier="helper", storage_path=str(store))
    assert again["_meta"].get("cache_hit") is True, again

    _write_in_another_process(root, store)

    second = find_references(repo_id, identifier="helper", storage_path=str(store))
    assert _ref_files(second) == {"b.py", "c.py"}, json.dumps(second)
    assert not second["_meta"].get("cache_hit"), second


def test_get_blast_radius_serves_the_index_another_process_wrote(repo):
    repo_id, root, store = repo
    first = get_blast_radius(repo_id, "helper", storage_path=str(store))
    assert _blast_files(first) == {"b.py"}, first
    again = get_blast_radius(repo_id, "helper", storage_path=str(store))
    assert again["_meta"].get("cache_hit") is True, again

    _write_in_another_process(root, store)

    second = get_blast_radius(repo_id, "helper", storage_path=str(store))
    assert _blast_files(second) == {"b.py", "c.py"}, json.dumps(second)
    assert not second["_meta"].get("cache_hit"), second


# --- the cache contract itself ------------------------------------------------


def _index(repo_id):
    owner, name = repo_id.split("/", 1)
    return IndexStore().load_index(owner, name)


def test_an_entry_from_another_load_of_the_index_is_a_miss(repo):
    from jcodemunch_mcp.storage.generation import load_token

    repo_id = repo[0]
    held = _index(repo_id)
    token_tracker.result_cache_put("t", repo_id, ("k",), {"v": 1}, index=held)
    assert token_tracker.result_cache_get("t", repo_id, ("k",), index=held) == {"v": 1}
    # A different object is a different load, even with the same content.
    other = copy.copy(held)
    assert load_token(other) != load_token(held)
    assert token_tracker.result_cache_get("t", repo_id, ("k",), index=other) is None


def test_an_unknown_load_identity_is_a_miss_and_is_never_stored(repo):
    from jcodemunch_mcp.storage.generation import load_token

    repo_id = repo[0]
    held = _index(repo_id)
    assert load_token(None) is None
    token_tracker.result_cache_put("t", repo_id, ("u",), {"v": 1}, index=None)
    assert token_tracker.result_cache_get("t", repo_id, ("u",), index=None) is None
    assert token_tracker.result_cache_get("t", repo_id, ("u",), index=held) is None


def test_every_served_hit_counts_as_validated_fresh(repo):
    repo_id = repo[0]
    held = _index(repo_id)
    token_tracker.result_cache_put("t_counted", repo_id, ("k",), {"v": 1}, index=held)
    token_tracker.result_cache_get("t_counted", repo_id, ("k",), index=held)
    token_tracker.result_cache_get("t_counted", repo_id, ("k",), index=copy.copy(held))
    row = token_tracker.result_cache_stats()["by_tool"]["t_counted"]
    assert row["hits"] == 1 and row["misses"] == 1, row
    assert row["hits_validated_fresh"] == 1, row
    assert row["hits_unvalidated"] == 0, row


@pytest.mark.parametrize("fn", ["result_cache_get", "result_cache_put"])
def test_the_load_identity_is_a_required_argument(fn):
    """A consumer written next cannot reach the cache without naming its index."""
    param = inspect.signature(getattr(token_tracker, fn)).parameters["index"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY
    assert param.default is inspect.Parameter.empty
