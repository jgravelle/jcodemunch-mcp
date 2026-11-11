"""A call that names `paths=` never replaces the rest of the index (#1397, @dkiaulakis).

#333 settled the contract for `paths=`: a subset means refresh a subset, not
replace the corpus. It held for the incremental path only. Any route to a FULL
re-parse saved the listed files as the whole index, because with `paths=` the
discovery step returns the listed files and nothing else:

- the caller's own `incremental=False` (the reported spelling);
- the one-off re-parse a `PARSER_GENERATION` bump forces;
- the forced rebuild `invalidate_cache` asks for;
- a subdir walk with `paths=`, whose merge carried only the files OUTSIDE the
  walked folder;
- the root-marker migration and the v1.95 legacy rebuild, which rebuild the
  whole corpus from the walk.

The rule now lives at the full save itself (every stored file the caller did
not list is kept), so a route added later inherits it. The git-root cases below
exist because a plain directory cannot express the subdir and marker shapes.

Each returned `success: true` with every unlisted file gone. The run now
re-parses the listed files and keeps the rest, says so, and leaves a due full
re-parse due.
"""

import sqlite3
import subprocess
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


def test_racket_reparse_under_paths_keeps_the_corpus(repo, monkeypatch):
    from jcodemunch_mcp.tools import index_folder as mod

    monkeypatch.setattr(mod, "_racket_reparse_reason", lambda index: "racket_config_changed")
    out = _refresh_small(repo)
    assert out.get("success"), out
    assert _files(repo) == ["big.js", "small.js", "util.py"]
    assert "rebuild_reason" not in out
    assert any("racket_config_changed" in w for w in out.get("warnings") or [])


# ── git-root shapes ──────────────────────────────────────────────────────────


@pytest.fixture
def git_repo(tmp_path):
    root = tmp_path / "g"
    (root / "sub").mkdir(parents=True)
    (root / "a.py").write_text("def a_fn():" + NEWLINE + "    return 1" + NEWLINE, encoding="utf-8")
    (root / "sub" / "b.py").write_text("def b_fn():" + NEWLINE + "    return 2" + NEWLINE, encoding="utf-8")
    (root / "sub" / "c.py").write_text("def c_fn():" + NEWLINE + "    return 3" + NEWLINE, encoding="utf-8")
    git = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(git + ["add", "."], cwd=root, check=True)
    subprocess.run(git + ["commit", "-qm", "init"], cwd=root, check=True)
    store = tmp_path / "gstore"
    store.mkdir()
    return {"root": root, "store": str(store)}


def _gfiles(g, repo_id):
    owner, name = repo_id.split("/", 1)
    idx = IndexStore(base_path=g["store"]).load_index(owner, name)
    return sorted(idx.source_files) if idx else None


@pytest.mark.parametrize("incremental", [True, False])
def test_subdir_walk_with_paths_keeps_the_walked_folders_other_files(git_repo, incremental):
    g = git_repo
    full = index_folder(path=str(g["root"]), use_ai_summaries=False, storage_path=g["store"])
    assert full.get("success"), full
    assert _gfiles(g, full["repo"]) == ["a.py", "sub/b.py", "sub/c.py"]
    out = index_folder(
        path=str(g["root"] / "sub"), paths=["b.py"], incremental=incremental,
        use_ai_summaries=False, storage_path=g["store"],
    )
    assert out.get("success"), out
    assert _gfiles(g, full["repo"]) == ["a.py", "sub/b.py", "sub/c.py"]


def test_paths_over_a_partial_marker_keeps_the_corpus(git_repo):
    g = git_repo
    first = index_folder(path=str(g["root"] / "sub"), use_ai_summaries=False, storage_path=g["store"])
    assert first.get("success"), first
    assert _gfiles(g, first["repo"]) == ["sub/b.py", "sub/c.py"]
    out = index_folder(
        path=str(g["root"]), paths=["sub/b.py"],
        use_ai_summaries=False, storage_path=g["store"],
    )
    assert out.get("success"), out
    assert _gfiles(g, first["repo"]) == ["sub/b.py", "sub/c.py"]


def test_first_index_with_paths_claims_nothing_was_kept(git_repo):
    g = git_repo
    out = index_folder(
        path=str(g["root"]), paths=["a.py"], incremental=False,
        use_ai_summaries=False, storage_path=g["store"],
    )
    assert out.get("success"), out
    assert _gfiles(g, out["repo"]) == ["a.py"]
    assert not any("kept" in w for w in out.get("warnings") or []), out.get("warnings")


def test_a_failed_paths_call_claims_nothing_was_kept(git_repo):
    g = git_repo
    out = index_folder(
        path=str(g["root"]), paths=["zzz.py"], incremental=False,
        use_ai_summaries=False, storage_path=g["store"],
    )
    assert out.get("success") is False
    assert not any("kept" in w for w in out.get("warnings") or []), out.get("warnings")


def test_a_legacy_index_refuses_paths_and_changes_nothing(git_repo):
    """A v1.95 index (subdir-relative source_root) is rebuilt by deleting it first."""
    g = git_repo
    full = index_folder(path=str(g["root"]), use_ai_summaries=False, storage_path=g["store"])
    assert full.get("success"), full
    dbs = list(Path(g["store"]).glob("*.db"))
    assert len(dbs) == 1, dbs
    _set_meta(dbs[0], "source_root", str(g["root"] / "sub"))
    out = index_folder(
        path=str(g["root"]), paths=["a.py"], incremental=False,
        use_ai_summaries=False, storage_path=g["store"],
    )
    assert out.get("success") is False, out
    assert "without paths=" in out.get("error", "")
    assert _gfiles(g, full["repo"]) == ["a.py", "sub/b.py", "sub/c.py"]
