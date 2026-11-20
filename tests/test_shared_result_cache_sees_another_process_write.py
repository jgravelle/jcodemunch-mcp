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
import shutil
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


RUNTIME_CHILD = """
import json, sys
from jcodemunch_mcp.runtime.ingest import ingest_otel_file
db, trace = sys.argv[1:3]
res = ingest_otel_file(db_path=db, file_path=trace)
print("OK" if res.get("mapped") else "FAIL " + json.dumps(res))
"""


def test_a_cached_blast_radius_shows_runtime_data_another_process_ingested(repo, tmp_path):
    """Runtime ingest writes no symbol, so the index load identity does not
    move and the cached answer is served. Its runtime confidence must still be
    today's: the stamp is applied after the cache, on the hit path too."""
    repo_id, _root, store = repo
    first = get_blast_radius(repo_id, "helper", storage_path=str(store))
    assert first["symbol"].get("_runtime_confidence") != "confirmed", first

    owner, name = repo_id.split("/", 1)
    db_path = IndexStore(base_path=str(store))._sqlite._db_path(owner, name)
    trace = tmp_path / "trace.json"
    span = {
        "traceId": "x", "spanId": "y", "name": "helper",
        "startTimeUnixNano": "0", "endTimeUnixNano": "1000000",
        "attributes": [
            {"key": "code.filepath", "value": {"stringValue": "a.py"}},
            {"key": "code.lineno", "value": {"intValue": "1"}},
            {"key": "code.function", "value": {"stringValue": "helper"}},
        ],
    }
    trace.write_text(json.dumps({"resourceSpans": [{"resource": {"attributes": []},
        "scopeSpans": [{"scope": {"name": "t"}, "spans": [span]}]}]}), encoding="utf-8")
    env = dict(os.environ, CODE_INDEX_PATH=str(store), PYTHONPATH=str(SRC))
    out = subprocess.run(
        [sys.executable, "-c", RUNTIME_CHILD, str(db_path), str(trace)],
        env=env, capture_output=True, text=True, timeout=180,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1] == "OK", out.stdout + out.stderr

    second = get_blast_radius(repo_id, "helper", storage_path=str(store))
    assert second["_meta"].get("cache_hit") is True, second
    assert second["symbol"].get("_runtime_confidence") == "confirmed", json.dumps(second)
    assert "runtime_freshness" in second["_meta"], second["_meta"]


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "-c", "commit.gpgsign=false", *args],
        cwd=root, check=True, capture_output=True,
    )


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_a_cached_blast_radius_shows_a_commit_made_after_it(tmp_path, monkeypatch):
    """`decisions` comes from git, and a commit that is not re-indexed leaves
    the index load identity alone. The live HEAD is in the key, so the commit
    reaches the next answer; an unchanged HEAD still hits."""
    store = tmp_path / "store"
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    root = tmp_path / "git_repo"
    root.mkdir()
    (root / "a.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (root / "b.py").write_text("from a import helper\n\nhelper()\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "init")
    token_tracker.result_cache_invalidate()
    repo_id = index_folder(str(root), use_ai_summaries=False, storage_path=str(store))["repo"]

    first = get_blast_radius(repo_id, "helper", storage_path=str(store), include_decisions=True)
    assert first["decisions"]["decision_count"] == 0, first["decisions"]
    again = get_blast_radius(repo_id, "helper", storage_path=str(store), include_decisions=True)
    assert again["_meta"].get("cache_hit") is True, again

    (root / "a.py").write_text("def helper():\n    return 2\n", encoding="utf-8")
    _git(root, "commit", "-qam", "revert: undo the helper change")

    second = get_blast_radius(repo_id, "helper", storage_path=str(store), include_decisions=True)
    subjects = [d["subject"] for d in second["decisions"]["decisions"]]
    assert subjects == ["revert: undo the helper change"], second["decisions"]
    third = get_blast_radius(repo_id, "helper", storage_path=str(store), include_decisions=True)
    assert third["_meta"].get("cache_hit") is True, third
    token_tracker.result_cache_invalidate()


def test_an_unreadable_runtime_table_is_not_remembered_as_empty(tmp_path):
    """UNKNOWN is never none: only a query that ran and found no row is kept."""
    from jcodemunch_mcp.runtime import confidence

    db = tmp_path / "x.db"
    import sqlite3

    sqlite3.connect(str(db)).close()  # no runtime_calls table: the query fails
    assert confidence.attach_runtime_confidence([{"id": "a"}], str(db)) == {}
    assert str(db) not in confidence._EMPTY_AT
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE runtime_calls (symbol_id TEXT)")
    conn.commit()
    conn.close()
    assert confidence.attach_runtime_confidence([{"id": "a"}], str(db)) == {}
    assert str(db) in confidence._EMPTY_AT, "an established empty table is remembered"


def test_a_write_inside_one_mtime_tick_still_ends_the_empty_memo(tmp_path):
    """Two commits in one filesystem tick leave every mtime where it was. The
    tick is pinned by putting both files' mtimes back after the second commit;
    the -wal size still moves, so the memo must not answer "no traces"."""
    import sqlite3

    from jcodemunch_mcp.runtime import confidence

    db = tmp_path / "tick.db"
    wal = Path(str(db) + "-wal")
    writer = sqlite3.connect(str(db))
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE runtime_calls (symbol_id TEXT)")
        writer.commit()
        assert wal.stat().st_size > 0
        assert confidence.attach_runtime_confidence([{"id": "a"}], str(db)) == {}
        assert confidence._empty_known(str(db))[0] is True
        before = {p: p.stat().st_mtime_ns for p in (db, wal)}

        writer.execute("INSERT INTO runtime_calls VALUES ('a')")
        writer.commit()
        for p, ns in before.items():
            os.utime(p, ns=(ns, ns))
        assert {p: p.stat().st_mtime_ns for p in (db, wal)} == before

        assert confidence._empty_known(str(db))[0] is False
    finally:
        writer.close()


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
    before = token_tracker.result_cache_stats()["cached_entries"]
    token_tracker.result_cache_put("t", repo_id, ("u",), {"v": 1}, index=None)
    # Never stored: an entry no lookup can hit would only push a live one out.
    assert token_tracker.result_cache_stats()["cached_entries"] == before
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
