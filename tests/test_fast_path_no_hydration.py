"""The watcher fast path answers its questions from metadata, not from symbols.

⚠⚠ `index_folder`'s fast path opened with an unconditional
``store.load_index(owner, repo_name)  # always load base for branch check`` --
inside the block whose entire purpose is to avoid loading the index, and three
lines above the ``use_memory_hash_cache`` flag that exists to make the store's
hashes unnecessary. Reported as part of #557 (@Ticki84).

⚠ Everything that path asks of the base index is METADATA: ``branch``,
``git_head``, ``file_hashes``, ``has_source_file``, and the two re-parse stamps
``parser_generation`` / ``racket_config_digest``. Measured on this repo's own
index, cold: 0.172 s for 13,906 symbol rows versus under a millisecond and ZERO
symbol rows for the selective read.

⚠⚠ These tests assert the OUTCOME -- that nothing on the path promotes the view
-- rather than the mechanism. A test that asserted "``open_selective`` is
called" would pass while a newly added ``existing_index.symbols`` quietly
hydrated the whole corpus behind it, which is the only failure worth catching
here (Practice 9).
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.storage.selective import EXACT_FIELDS, SelectiveIndexView


@pytest.mark.parametrize("field", ["parser_generation", "racket_config_digest"])
def test_reparse_stamps_are_answered_exactly(field):
    """⚠ Both are meta rows. Absent from ``EXACT_FIELDS`` they fall through
    ``__getattr__``, which promotes -- so the per-event upgrade check would load
    every symbol in the repository to read one integer.
    """
    assert field in EXACT_FIELDS


def _index_once(tmp_path, storage):
    from jcodemunch_mcp.tools.index_folder import index_folder

    (tmp_path / "a.py").write_text("def a():\n    return 1\n")
    (tmp_path / "b.py").write_text("def b():\n    return 2\n")
    r = index_folder(
        path=str(tmp_path), use_ai_summaries=False,
        storage_path=str(storage), incremental=False, context_providers=False,
    )
    assert r["success"], r
    return r


def test_a_fast_path_event_never_hydrates_the_base_index(tmp_path, monkeypatch):
    """The property: one watcher event reads no symbol rows for the base index.

    ⚠ ``promoted`` is the honest witness. It flips the moment anything reaches
    for a corpus-wide attribute, whoever added it and whyever.
    """
    from jcodemunch_mcp.storage import index_store as store_mod
    from jcodemunch_mcp.tools.index_folder import index_folder
    from jcodemunch_mcp.reindex_state import WatcherChange

    storage = tmp_path / ".code-index"
    _index_once(tmp_path, storage)
    # The event arrives in a watcher process that holds no warm copy of the
    # index. Since L-343 the save's warm copy stays usable in THIS process, and
    # `open_selective` rightly returns it, so drop it to model that process.
    from jcodemunch_mcp.storage import sqlite_store

    sqlite_store._cache_clear()

    seen: list = []
    real = store_mod.IndexStore.open_selective

    def spy(self, owner, name, **kw):
        view = real(self, owner, name, **kw)
        seen.append(view)
        return view

    monkeypatch.setattr(store_mod.IndexStore, "open_selective", spy)

    (tmp_path / "a.py").write_text("def a():\n    return 99\n")
    r = index_folder(
        path=str(tmp_path), use_ai_summaries=False,
        storage_path=str(storage), incremental=True, context_providers=False,
        changed_paths=[WatcherChange(
            "modified", str((tmp_path / "a.py").resolve()), "__cache_miss__"
        )],
    )
    assert r["success"], r
    assert r.get("fast_path") is True, "fast path did not engage; test proves nothing"

    views = [v for v in seen if isinstance(v, SelectiveIndexView)]
    assert views, "the fast path took no selective read of the base index"
    for v in views:
        assert not v.promoted, (
            f"the fast path hydrated the full index to answer {v.promoted_by!r} "
            "-- every question it asks of the base index is metadata"
        )


def test_phase_timings_split_the_duration(tmp_path):
    """⚠ `duration_seconds` alone cannot say WHERE a slow event went (#557).

    Asserts the phases exist and are a decomposition, not a second total: their
    sum cannot exceed the duration it decomposes.
    """
    from jcodemunch_mcp.tools.index_folder import index_folder
    from jcodemunch_mcp.reindex_state import WatcherChange

    storage = tmp_path / ".code-index"
    _index_once(tmp_path, storage)

    (tmp_path / "b.py").write_text("def b():\n    return 42\n")
    r = index_folder(
        path=str(tmp_path), use_ai_summaries=False,
        storage_path=str(storage), incremental=True, context_providers=False,
        changed_paths=[WatcherChange(
            "modified", str((tmp_path / "b.py").resolve()), "__cache_miss__"
        )],
    )
    assert r.get("fast_path") is True, r
    phases = r.get("phase_seconds")
    assert isinstance(phases, dict) and phases, "no phase breakdown on a fast-path result"
    for name in ("base_index", "classify", "read_hash", "parse", "save"):
        assert name in phases, f"phase {name!r} missing: {sorted(phases)}"
    # +0.05 tolerance: the phases are sampled inside the window `duration_seconds`
    # rounds to 2dp, so an exact <= can lose to rounding on a sub-10ms run.
    assert sum(phases.values()) <= r["duration_seconds"] + 0.05, (
        f"phases {phases} sum past the duration {r['duration_seconds']} they split"
    )


def test_phases_are_absent_when_the_fast_path_is_not_taken(tmp_path):
    """⚠⚠ Their ABSENCE is a signal, so it must be a real absence.

    A full walk that emitted an empty or zeroed breakdown would read as "the
    fast path ran and cost nothing", which is the opposite of what happened.
    """
    from jcodemunch_mcp.tools.index_folder import index_folder

    storage = tmp_path / ".code-index"
    r = _index_once(tmp_path, storage)
    assert "phase_seconds" not in r
    assert not r.get("fast_path")
