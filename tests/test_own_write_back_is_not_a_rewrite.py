"""A write-back this process made is not a rewrite of the index.

A search that computes the BM25 statistics, or a credit that queues token
counts, has them written into the index's .db after the call. That moves the
.db's mtime and changes no answer. ``subject_state.changed`` compared the mtime
alone, so a cached absence read as degraded the moment its own write-back ran.
Only a move this process recorded as its own write-back is forgiven; any other
move, or a chain with a gap, is still a rewrite.
"""

from __future__ import annotations

from jcodemunch_mcp.retrieval import subject_state
from jcodemunch_mcp.storage import generation


def _state(db_path, mtime):
    return {"generation": "g1", "db_mtime_ns": mtime, "db_path": db_path, "live_sha": None}


def test_a_recorded_chain_of_own_write_backs_is_not_a_change(tmp_path):
    db = str(tmp_path / "a.db")
    generation.note_benign_rewrite(db, 100, 200)
    generation.note_benign_rewrite(db, 200, 300)
    assert generation.only_benign_rewrites(db, 100, 300)
    assert subject_state.changed(_state(db, 100), _state(db, 300)) is None


def test_a_move_this_process_did_not_make_is_still_a_rewrite(tmp_path):
    db = str(tmp_path / "b.db")
    generation.note_benign_rewrite(db, 100, 200)
    # 200 -> 250 was someone else's write.
    assert not generation.only_benign_rewrites(db, 100, 250)
    assert subject_state.changed(_state(db, 100), _state(db, 250)) == (
        f"the index was rewritten {subject_state.WHEN_CACHED}"
    )


def test_a_gap_in_the_chain_is_a_rewrite(tmp_path):
    db = str(tmp_path / "c.db")
    generation.note_benign_rewrite(db, 100, 200)
    generation.note_benign_rewrite(db, 300, 400)
    assert not generation.only_benign_rewrites(db, 100, 400)


def test_a_state_without_a_path_is_never_forgiven(tmp_path):
    db = str(tmp_path / "d.db")
    generation.note_benign_rewrite(db, 100, 200)
    before = {"generation": "g1", "db_mtime_ns": 100}
    after = {"generation": "g1", "db_mtime_ns": 200}
    assert subject_state.changed(before, after) is not None


def test_another_index_s_write_back_forgives_nothing_here(tmp_path):
    a, b = str(tmp_path / "e.db"), str(tmp_path / "f.db")
    generation.note_benign_rewrite(a, 100, 200)
    assert not generation.only_benign_rewrites(b, 100, 200)
