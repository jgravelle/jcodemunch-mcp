"""Each repository's index stays its own.

The on-disk slot for an index is ``f"{owner}-{name}"`` after sanitising, and
both halves may contain hyphens, so two different repositories can map to one
slot (``foo-bar/baz`` and ``foo/bar-baz``). The store used to let the second
save overwrite the first, and served the slot to either name. What is pinned:

  - a save never replaces an index that belongs to another repository;
  - a read never serves another repository's index under the requested name,
    in either order of indexing;
  - a delete never removes another repository's index;
  - the same repository re-indexed, and an index with no stored identity
    (written before the identity was recorded), behave as before.

"""

import sqlite3

import pytest

from jcodemunch_mcp.storage.index_store import IndexStore
from jcodemunch_mcp.tools.get_file_content import get_file_content

try:
    from jcodemunch_mcp.storage.sqlite_store import IndexIdentityConflict
except ImportError:  # the unfixed tree: let the red run fail on behaviour, not on import
    IndexIdentityConflict = type("IndexIdentityConflictMissing", (Exception,), {})


def _save(store, owner, name, text):
    store.save_index(owner=owner, name=name, source_files=["app.py"], symbols=[],
                     raw_files={"app.py": text})


def _content(tmp, repo):
    return get_file_content(repo=repo, file_path="app.py", storage_path=str(tmp)).get("content")


def test_a_second_repository_cannot_replace_the_first(tmp_path):
    s = IndexStore(base_path=str(tmp_path))
    _save(s, "foo-bar", "baz", "# FIRST\n")
    with pytest.raises(IndexIdentityConflict) as e:
        _save(s, "foo", "bar-baz", "# SECOND\n")
    assert "foo-bar/baz" in str(e.value) and "foo/bar-baz" in str(e.value)
    assert _content(tmp_path, "foo-bar/baz") == "# FIRST\n"


def test_the_other_name_is_never_served_this_repositorys_index(tmp_path):
    s = IndexStore(base_path=str(tmp_path))
    _save(s, "foo-bar", "baz", "# FIRST\n")
    assert s.load_index("foo", "bar-baz") is None
    assert _content(tmp_path, "foo/bar-baz") != "# FIRST\n"


def test_the_selective_read_path_refuses_too(tmp_path):
    """open_selective reads meta without load_index; it must not hand back a
    view of another repository."""
    from jcodemunch_mcp.storage import sqlite_store

    s = IndexStore(base_path=str(tmp_path))
    _save(s, "foo-bar", "baz", "# FIRST\n")
    sqlite_store._index_cache.clear()
    raw = sqlite_store.SQLiteIndexStore(base_path=str(tmp_path))
    assert raw.open_selective("foo", "bar-baz", files=["app.py"]) is None
    assert raw.open_selective("foo-bar", "baz", files=["app.py"]) is not None


def test_reverse_order_is_refused_the_same_way(tmp_path):
    s = IndexStore(base_path=str(tmp_path))
    _save(s, "foo", "bar-baz", "# OTHER\n")
    assert s.load_index("foo-bar", "baz") is None
    with pytest.raises(IndexIdentityConflict):
        _save(s, "foo-bar", "baz", "# MINE\n")
    assert _content(tmp_path, "foo/bar-baz") == "# OTHER\n"


def test_a_delete_never_removes_another_repositorys_index(tmp_path):
    s = IndexStore(base_path=str(tmp_path))
    _save(s, "foo-bar", "baz", "# FIRST\n")
    with pytest.raises(IndexIdentityConflict):
        s.delete_index("foo", "bar-baz")
    assert s.load_index("foo-bar", "baz") is not None


def test_an_incremental_save_never_writes_into_another_repositorys_index(tmp_path):
    s = IndexStore(base_path=str(tmp_path))
    _save(s, "foo-bar", "baz", "# FIRST\n")
    with pytest.raises(IndexIdentityConflict):
        s.incremental_save(owner="foo", name="bar-baz", changed_files=["app.py"], new_files=[],
                           deleted_files=[], new_symbols=[], raw_files={"app.py": "# SECOND\n"})
    assert _content(tmp_path, "foo-bar/baz") == "# FIRST\n"


def test_the_same_repository_reindexed_is_unaffected(tmp_path):
    s = IndexStore(base_path=str(tmp_path))
    _save(s, "foo-bar", "baz", "# ONE\n")
    _save(s, "foo-bar", "baz", "# TWO\n")
    assert _content(tmp_path, "foo-bar/baz") == "# TWO\n"
    # GitHub names are case-insensitive: another casing is never a conflict.
    # Whether it shares the file depends on the filesystem (Windows and macOS
    # fold case, Linux does not), so assert only that it is not refused.
    _save(s, "Foo-Bar", "Baz", "# THREE\n")
    assert s.delete_index("foo-bar", "baz") is True


def test_an_index_with_no_stored_identity_still_loads(tmp_path):
    """UNKNOWN is never a conflict: an index from before the identity was
    recorded must keep working, or the fix breaks every old install."""
    s = IndexStore(base_path=str(tmp_path))
    _save(s, "foo-bar", "baz", "# OLD\n")
    db = next(tmp_path.glob("*.db"))
    conn = sqlite3.connect(str(db))
    conn.execute("DELETE FROM meta WHERE key = 'repo'")
    conn.commit()
    conn.close()
    from jcodemunch_mcp.storage import sqlite_store
    sqlite_store._index_cache.clear()
    assert s.load_index("foo-bar", "baz") is not None


def test_inspect_and_has_index_do_not_report_another_repositorys_index(tmp_path):
    """Four runtime tools gate on inspect_index and then open the slot's .db
    themselves; a 'loadable' answer there would hand them the wrong data."""
    s = IndexStore(base_path=str(tmp_path))
    _save(s, "foo-bar", "baz", "# FIRST\n")
    status = s.inspect_index("foo", "bar-baz")
    assert status.loadable is False and status.status == "identity_conflict"
    assert s.has_index("foo", "bar-baz") is False
    assert s.inspect_index("foo-bar", "baz").loadable is True


def test_the_content_directory_is_guarded_like_the_index(tmp_path):
    s = IndexStore(base_path=str(tmp_path))
    _save(s, "foo-bar", "baz", "# FIRST\n")
    with pytest.raises(IndexIdentityConflict):
        s._content_dir("foo", "bar-baz")


def test_index_folder_cannot_rewrite_another_repositorys_files(tmp_path):
    """End to end: indexing a folder whose git origin is the
    colliding repository wrote its raw files into the slot's content directory
    BEFORE the save refused, so the first repository kept its index and served
    the second one's text. A guard at the store's entry points missed it."""
    import subprocess

    from jcodemunch_mcp.tools.index_folder import index_folder

    store = tmp_path / "store"
    store.mkdir()
    s = IndexStore(base_path=str(store))
    _save(s, "foo-bar", "baz", "# FIRST\n")
    src = tmp_path / "other"
    src.mkdir()
    (src / "app.py").write_text("# SECOND\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=src, check=True)
    subprocess.run(["git", "remote", "add", "origin", "https://github.com/foo/bar-baz.git"], cwd=src, check=True)
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store), identity_mode="git")
    assert r.get("success") is False
    assert "foo-bar/baz" in str(r.get("error"))
    assert _content(store, "foo-bar/baz") == "# FIRST\n"


def test_a_save_that_slips_past_the_slot_check_is_still_refused(tmp_path, monkeypatch):
    """Two colliding identities hold different index-write locks, so the slot
    check alone can race. The stored identity is re-read inside the write
    transaction, under SQLite's per-file lock: switch the slot check off and
    the save must still refuse."""
    from jcodemunch_mcp.storage import sqlite_store

    s = IndexStore(base_path=str(tmp_path))
    _save(s, "foo-bar", "baz", "# FIRST\n")
    monkeypatch.setattr(sqlite_store.SQLiteIndexStore, "_guard_slot", lambda *a, **k: None)
    with pytest.raises(IndexIdentityConflict):
        _save(s, "foo", "bar-baz", "# SECOND\n")
    monkeypatch.undo()
    assert _content(tmp_path, "foo-bar/baz") == "# FIRST\n"


def _refuse_every_open(monkeypatch):
    """Both openers the probe tries raise: the identity cannot be read at all."""
    from jcodemunch_mcp.storage import generation, sqlite_store

    def refuse(*a, **k):
        raise OSError("open refused")

    monkeypatch.setattr(generation, "connect_readonly", refuse)
    monkeypatch.setattr(sqlite_store.sqlite3, "connect", refuse)


def test_one_refused_read_never_opens_the_slot(tmp_path, monkeypatch):
    """An unreadable identity is refused and never cached: a cached UNKNOWN
    would leave the slot open to the next writer until the .db changed."""
    from jcodemunch_mcp.storage import sqlite_store

    s = IndexStore(base_path=str(tmp_path))
    _save(s, "foo-bar", "baz", "# FIRST\n")
    sqlite_store.SQLiteIndexStore._slot_identity_cache.clear()
    with monkeypatch.context() as m:
        _refuse_every_open(m)
        with pytest.raises(IndexIdentityConflict):
            s._content_dir("foo", "bar-baz")
        with pytest.raises(IndexIdentityConflict) as e:
            s._content_dir("foo-bar", "baz")  # even the owner: unreadable is unreadable
        # The owner is told the file could not be read, not that a second
        # repository holds its slot.
        assert getattr(e.value, "unreadable", False) is True
        assert "could not read" in str(e.value) and "already holds" not in str(e.value)
    # Once reads work again, nothing stale was cached:
    with pytest.raises(IndexIdentityConflict):
        s._content_dir("foo", "bar-baz")
    with pytest.raises(IndexIdentityConflict):
        s.delete_index("foo", "bar-baz")
    assert s.load_index("foo-bar", "baz") is not None
    assert _content(tmp_path, "foo-bar/baz") == "# FIRST\n"


def test_a_refused_read_only_open_alone_is_retried(tmp_path, monkeypatch):
    """embed_repo writes where it cannot open read-only: the probe retries on
    a plain connection, so the owner still gets through and the other name
    is still refused."""
    from jcodemunch_mcp.storage import generation, sqlite_store

    s = IndexStore(base_path=str(tmp_path))
    _save(s, "foo-bar", "baz", "# FIRST\n")
    sqlite_store.SQLiteIndexStore._slot_identity_cache.clear()

    def refuse(*a, **k):
        raise OSError("read-only open refused")

    monkeypatch.setattr(generation, "connect_readonly", refuse)
    s._content_dir("foo-bar", "baz")
    with pytest.raises(IndexIdentityConflict):
        s._content_dir("foo", "bar-baz")


@pytest.mark.parametrize("body", [b"", b"this is not a sqlite database at all, just bytes" * 40])
def test_a_slot_with_no_readable_index_stays_reindexable(tmp_path, body):
    """A meta-less or corrupt file records no repository: re-indexing over it is
    the repair path and must not be refused."""
    s = IndexStore(base_path=str(tmp_path))
    db = s._sqlite._db_path("foo-bar", "baz")
    db.write_bytes(body)
    s._sqlite._slot_identity_cache.clear()
    s._content_dir("foo-bar", "baz")  # not refused
    s._content_dir("foo", "bar-baz")  # nothing to belong to: not refused either
