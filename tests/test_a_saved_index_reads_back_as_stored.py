"""The index a save pre-warms into the cache reads like the rows it wrote (LEDGER L-224).

`save_index` caches the index it just wrote so the next `load_index` in the
same process skips the disk. That cached copy was built from its own symbol
dict literal, which left out `ecosystem_context`, while every row read from
disk carries it. So a load in the same process returned symbols with no
`ecosystem_context` key until another write moved the .db mtime, and a test
reading it right after `index_folder` failed with `KeyError` only when that
later write had not happened yet: under load.
"""

from __future__ import annotations

from jcodemunch_mcp.parser.symbols import Symbol
from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.storage import sqlite_store


def _save(tmp_path):
    sym = Symbol(
        id="mod.py::alpha#function",
        file="mod.py",
        name="alpha",
        qualified_name="alpha",
        kind="function",
        language="python",
        signature="def alpha():",
        ecosystem_context="git_blame: last_author=Tester",
    )
    store = IndexStore(base_path=str(tmp_path / "store"))
    store.save_index(
        owner="local",
        name="repo",
        source_files=["mod.py"],
        symbols=[sym],
        raw_files={"mod.py": "def alpha():\n    return 1\n"},
    )
    return store


def test_the_cached_copy_keeps_ecosystem_context(tmp_path):
    store = _save(tmp_path)
    sym = store.load_index("local", "repo").symbols[0]
    assert sym.get("ecosystem_context") == "git_blame: last_author=Tester"


def test_the_cached_copy_has_the_same_keys_as_a_disk_read(tmp_path):
    """Every key a disk read returns, the pre-warmed copy returns too."""
    store = _save(tmp_path)
    cached = store.load_index("local", "repo").symbols[0]
    sqlite_store._cache_clear()
    from_disk = (
        IndexStore(base_path=str(tmp_path / "store"))
        .load_index("local", "repo")
        .symbols[0]
    )
    assert set(cached) == set(from_disk)
    assert {k: cached[k] for k in from_disk} == from_disk
