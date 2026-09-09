"""Empty full discovery reconciles deletions without masking discovery failures."""

from contextlib import closing
import importlib
import os
import sqlite3

import pytest

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.security import is_binary_file
from jcodemunch_mcp.retrieval.verdict import index_coverage_meta
from jcodemunch_mcp.tools import refresh
from jcodemunch_mcp.tools._corpus_adequacy import UNPROVEN_CEILING, assess_corpus
from jcodemunch_mcp.tools.index_folder import discover_local_files, index_folder


@pytest.fixture
def indexed_tree(tmp_path):
    root = tmp_path / "project"
    child = root / "child"
    child.mkdir(parents=True)
    (child / "code.py").write_text("def direct_symbol(): pass\n")
    (child / "deep").mkdir()
    (child / "deep" / "code.py").write_text("def nested_symbol(): pass\n")
    kwargs = dict(path=str(root), storage_path=str(tmp_path / "index"),
                  use_ai_summaries=False, context_providers=False, identity_mode="local")
    result = index_folder(**kwargs)
    assert result["success"], result
    owner, name = result["repo"].split("/", 1)
    database = IndexStore(base_path=kwargs["storage_path"]).load_index(owner, name)._db_path
    return root, kwargs, database


def persisted_symbols(database):
    with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as connection:
        return connection.execute("SELECT name, file FROM symbols ORDER BY name, file").fetchall()


@pytest.mark.parametrize("paths", [None, ["."]], ids=["full", "explicit-root"])
def test_move_out_all_sources_clears_persisted_index(indexed_tree, tmp_path, paths):
    root, kwargs, database = indexed_tree
    assert persisted_symbols(database) == [
        ("direct_symbol", "child/code.py"),
        ("nested_symbol", "child/deep/code.py"),
    ]
    (root / "child").rename(tmp_path / "moved_out")
    result = index_folder(**kwargs, paths=paths)
    assert result["success"], result
    assert result["incremental"] is True
    assert result["deleted"] == 2
    assert result["symbol_count"] == 0
    assert persisted_symbols(database) == []
    repeated = index_folder(**kwargs, paths=paths)
    assert repeated["success"], repeated
    assert repeated["deleted"] == 0


@pytest.mark.parametrize("incremental", [False, True])
def test_initial_empty_folder_remains_an_error(tmp_path, incremental):
    root = tmp_path / "empty"
    root.mkdir()
    result = index_folder(str(root), storage_path=str(tmp_path / "index"),
                          use_ai_summaries=False, context_providers=False,
                          incremental=incremental, identity_mode="local")
    assert result == {"success": False, "error": "No source files found"}


@pytest.mark.parametrize("failure", ["root_scan", "child_scan", "exception"])
def test_failed_discovery_preserves_persisted_symbols(indexed_tree, monkeypatch, failure):
    root, kwargs, database = indexed_tree
    before = persisted_symbols(database)
    module = importlib.import_module("jcodemunch_mcp.tools.index_folder")
    if failure in ("root_scan", "child_scan"):
        original_scandir = os.scandir
        blocked = root if failure == "root_scan" else root / "child"

        def scandir(path):
            if os.fspath(path) == str(blocked):
                raise PermissionError("discovery denied")
            return original_scandir(path)

        monkeypatch.setattr(os, "scandir", scandir)
    else:
        def discover(*args, **kwargs):
            raise OSError("discovery failed")

        monkeypatch.setattr(module, "discover_local_files", discover)
    result = index_folder(**kwargs)
    assert result["success"] is False, result
    assert "error" in result
    assert persisted_symbols(database) == before


def test_size_limited_empty_discovery_preserves_persisted_symbols(indexed_tree):
    root, kwargs, database = indexed_tree
    before = persisted_symbols(database)
    assert len(before) == 2
    files, _, counts = discover_local_files(root, max_size=1)
    assert files == []
    assert counts["too_large"] == 2
    result = index_folder(**kwargs, max_size=1)
    assert result["success"] is False, result
    assert result["error"] == "No source files found"
    assert persisted_symbols(database) == before


def test_unreadable_sources_preserve_persisted_symbols(indexed_tree):
    root, kwargs, database = indexed_tree
    before = persisted_symbols(database)
    assert len(before) == 2
    modes = {source: source.stat().st_mode for source in root.rglob("*.py")}
    try:
        for source in modes:
            source.chmod(0)
            assert source.stat().st_size > 0
            try:
                with source.open("rb") as stream:
                    stream.read(1)
            except PermissionError:
                pass
            else:
                pytest.skip("File permissions do not enforce read denial")
            assert is_binary_file(source) is True

        result = index_folder(**kwargs)
        assert result["success"] is False, result
        assert persisted_symbols(database) == before
        files, _, counts = discover_local_files(root)
        assert files == []
        assert counts["unreadable"] == 2
        assert counts["binary"] == 0
    finally:
        for source, mode in modes.items():
            source.chmod(mode)


def test_binary_sources_allow_empty_index_reconciliation(indexed_tree):
    root, kwargs, database = indexed_tree
    assert len(persisted_symbols(database)) == 2
    for source in root.rglob("*.py"):
        source.write_bytes(b"def former_source(): pass\n\x00")
    files, _, counts = discover_local_files(root)
    assert files == []
    assert counts["binary"] == 2
    assert counts["unreadable"] == 0
    result = index_folder(**kwargs)
    assert result["success"], result
    assert result["deleted"] == 2
    assert persisted_symbols(database) == []


def test_binary_file_read_errors_are_opt_in(tmp_path):
    missing = tmp_path / "missing.py"
    assert is_binary_file(missing, 16) is True
    with pytest.raises(FileNotFoundError):
        is_binary_file(missing, 16, raise_on_error=True)


def test_unreadable_child_directory_is_counted_and_indexing_continues(indexed_tree):
    root, kwargs, database = indexed_tree
    denied = root / "child" / "deep"
    mode = denied.stat().st_mode
    try:
        denied.chmod(0)
        try:
            with os.scandir(denied):
                pass
        except PermissionError:
            pass
        else:
            pytest.skip("Directory permissions do not enforce traversal denial")

        files, warnings, counts = discover_local_files(root)
        assert [f.relative_to(root).as_posix() for f in files] == ["child/code.py"]
        assert counts["unreadable"] == 1
        assert any("child/deep" in warning for warning in warnings)

        result = index_folder(**kwargs)
        assert result["success"], result
        assert result["discovery_skip_counts"]["unreadable"] == 1
        assert any("child/deep" in warning for warning in result["warnings"])
        assert ("direct_symbol", "child/code.py") in persisted_symbols(database)

        campaign = refresh.run(str(root), storage_path=kwargs["storage_path"], reset=True)
        assert campaign["success"], campaign

        store = IndexStore(base_path=kwargs["storage_path"])
        owner, name = result["repo"].split("/", 1)
        coverage = index_coverage_meta(store.load_index(owner, name))
        assert coverage["complete"] is False
        assert coverage["withheld"]["unreadable"] == 1
        adequacy = assess_corpus(store.load_index(owner, name))
        assert adequacy.adequate is False
        assert "withheld_files" in adequacy.blockers
        assert adequacy.ceiling == UNPROVEN_CEILING
    finally:
        denied.chmod(mode)
    recovered = index_folder(**kwargs)
    assert recovered["success"], recovered
    assert recovered["discovery_skip_counts"]["unreadable"] == 0
    coverage = index_coverage_meta(store.load_index(owner, name))
    assert coverage["complete"] is True
    assert "withheld" not in coverage
    assert assess_corpus(store.load_index(owner, name)).ceiling == 1.0
    assert ("nested_symbol", "child/deep/code.py") in persisted_symbols(database)
