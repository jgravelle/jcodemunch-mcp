"""A call that names `paths=` never replaces the rest of the index (#1397, @dkiaulakis).

#333 settled the contract for `paths=`: a subset means refresh a subset, not
replace the corpus. It held for the incremental path only. Any route to a FULL
re-parse saved the listed files as the whole index, because with `paths=` the
discovery step returns the listed files and nothing else:

- the caller's own `incremental=False` (the reported spelling);
- the one-off re-parse a `PARSER_GENERATION` bump forces;
- the forced rebuild `invalidate_cache` asks for.

Each returned `success: true` with every unlisted file gone. The run now
re-parses the listed files and keeps the rest, says so, and leaves a due full
re-parse due.
"""

import sqlite3
from pathlib import Path

import pytest

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.storage.index_store import PARSER_GENERATION
from jcodemunch_mcp.tools._utils import PARSER_UPGRADE_WARNING
from jcodemunch_mcp.tools.index_folder import index_folder

NEWLINE = chr(10)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "big.js").write_text("function bigOne() { return 1; }" + NEWLINE, encoding="utf-8")
    (root / "small.js").write_text("function smallOne() { return 2; }" + NEWLINE, encoding="utf-8")
    (root / "util.py").write_text("def util_one():" + NEWLINE + "    return 3" + NEWLINE, encoding="utf-8")
    store = tmp_path / "store"
    store.mkdir()
    out = index_folder(path=str(root), use_ai_summaries=False, storage_path=str(store))
    assert out.get("success"), out
    dbs = list(store.glob("*.db"))
    assert len(dbs) == 1, dbs
    return {"root": root, "store": str(store), "db": dbs[0], "repo_id": out["repo"]}


def _load(repo):
    owner, name = repo["repo_id"].split("/", 1)
    return IndexStore(base_path=repo["store"]).load_index(owner, name)


def _files(repo):
    return sorted(_load(repo).source_files)


def _names(repo):
    return sorted(s["name"] for s in _load(repo).symbols)


def _set_meta(db: Path, key: str, value: str) -> None:
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("UPDATE meta SET value = ? WHERE key = ?", (value, key))
        conn.commit()
    finally:
        conn.close()


def _refresh_small(repo, **kw):
    (repo["root"] / "small.js").write_text(
        "function smallOne() { return 2; }" + NEWLINE + "function smallTwo() { return 4; }" + NEWLINE,
        encoding="utf-8",
    )
    return index_folder(
        path=str(repo["root"]), use_ai_summaries=False, storage_path=repo["store"],
        paths=["small.js"], **kw,
    )


def test_incremental_false_keeps_unlisted_files(repo):
    before = _files(repo)
    assert before == ["big.js", "small.js", "util.py"]
    out = _refresh_small(repo, incremental=False)
    assert out.get("success"), out
    assert _files(repo) == before
    names = _names(repo)
    assert "bigOne" in names and "util_one" in names
    assert "smallTwo" in names, "the listed file was not re-parsed"


def test_incremental_false_says_what_it_did(repo):
    out = _refresh_small(repo, incremental=False)
    assert out.get("requested_incremental") is False
    assert out.get("performed_incremental") is True
    text = " ".join(out.get("warnings") or [])
    assert "paths=" in text and "kept" in text, out.get("warnings")


def test_incremental_false_reparses_an_unchanged_listed_file(repo):
    """`incremental=False` is a forced re-parse: an unchanged listed file is re-read.

    Its stored symbol is corrupted with the file left byte-identical (the #414
    shape), so only a re-parse can bring the right name back.
    """
    conn = sqlite3.connect(str(repo["db"]))
    try:
        conn.execute("UPDATE symbols SET name = 'WRONG_NAME' WHERE name = 'util_one'")
        conn.commit()
    finally:
        conn.close()
    assert "util_one" not in _names(repo)
    out = index_folder(
        path=str(repo["root"]), use_ai_summaries=False, storage_path=repo["store"],
        paths=["util.py"], incremental=False,
    )
    assert out.get("success"), out
    assert _files(repo) == ["big.js", "small.js", "util.py"]
    names = _names(repo)
    assert "util_one" in names and "WRONG_NAME" not in names


def test_parser_upgrade_under_paths_keeps_the_corpus_and_stays_due(repo):
    _set_meta(repo["db"], "parser_generation", "0")
    out = _refresh_small(repo)
    assert out.get("success"), out
    assert _files(repo) == ["big.js", "small.js", "util.py"]
    assert "smallTwo" in _names(repo)
    # Only the listed file was re-parsed, so the index must keep claiming the
    # older generation; the next run without `paths=` does the full re-parse.
    assert int(_load(repo).parser_generation or 0) < PARSER_GENERATION
    assert PARSER_UPGRADE_WARNING not in (out.get("warnings") or [])
    assert "rebuild_reason" not in out

    full = index_folder(path=str(repo["root"]), use_ai_summaries=False, storage_path=repo["store"])
    assert full.get("success"), full
    assert int(_load(repo).parser_generation or 0) == PARSER_GENERATION


def test_forced_rebuild_under_paths_keeps_the_corpus_and_stays_pending(repo):
    from jcodemunch_mcp.tools import invalidate_cache

    invalidate_cache._force_full_reindex.add(repo["repo_id"])
    try:
        out = _refresh_small(repo)
        assert out.get("success"), out
        assert _files(repo) == ["big.js", "small.js", "util.py"]
        assert repo["repo_id"] in invalidate_cache._force_full_reindex, (
            "a run that could not do the forced rebuild consumed its request"
        )
    finally:
        invalidate_cache._force_full_reindex.discard(repo["repo_id"])


def test_a_listed_file_deleted_from_disk_is_still_removed(repo):
    """Keeping unlisted files must not keep a listed file that is gone."""
    (repo["root"] / "small.js").unlink()
    out = index_folder(
        path=str(repo["root"]), use_ai_summaries=False, storage_path=repo["store"],
        paths=["small.js"], incremental=False,
    )
    assert out.get("success") is not False, out
    assert _files(repo) == ["big.js", "util.py"]
