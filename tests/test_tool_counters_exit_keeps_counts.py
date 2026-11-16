"""A tool count the exit flush cannot write is kept, not lost.

The counters write at interpreter exit with a 3 s lock wait. Any failure there
(the lock not taken in time, a Windows `PermissionError` on the read or the
replace, a full disk) put the counts back in a dict that dies with the
process. CI on Windows lost three processes' counts of eight this way
(`test_processes_that_stop_together_lose_no_count`, 2026-11-16).

The Cursor read counter had the same defect and its fix (1.108.454) is the
shape here: a count that cannot be written goes to a `.pending` sibling file,
the next write folds it in, and `read` counts it before then.
"""

from __future__ import annotations

import json

import pytest

from jcodemunch_mcp.storage import tool_counters as tc


@pytest.fixture()
def store(tmp_path, monkeypatch):
    root = tmp_path / "store"
    root.mkdir()
    monkeypatch.setenv("CODE_INDEX_PATH", str(root))
    monkeypatch.setattr(tc, "_state", tc._State())
    return root


def _file(root):
    return json.loads((root / tc.FILE).read_text(encoding="utf-8"))


def _new_process(monkeypatch):
    monkeypatch.setattr(tc, "_state", tc._State())


def _pending(root):
    return sorted(root.glob(f"{tc.FILE}.*{tc.PENDING_SUFFIX}"))


def test_an_exit_flush_that_cannot_take_the_lock_keeps_its_counts(store, monkeypatch):
    for _ in range(5):
        tc.record("list_repos", 400)
    real = tc._acquire
    monkeypatch.setattr(tc, "_acquire", lambda lock, wait: False)
    tc._flush_at_exit()
    monkeypatch.setattr(tc, "_acquire", real)
    assert len(_pending(store)) == 1, (
        "the counts the exit flush could not write are on disk"
    )

    _new_process(monkeypatch)
    tc.record("list_repos", 400)
    tc.flush()
    assert _file(store)["tools"]["list_repos"]["calls"] == 6, (
        "the next write folds them in"
    )
    assert _pending(store) == []


def test_an_exit_flush_whose_write_fails_keeps_its_counts(store, monkeypatch):
    tc.record("list_repos", 400)
    tc.flush()  # a file exists
    for _ in range(3):
        tc.record("list_repos", 400)
    real = tc._load

    def busy(path, *, strict=False):
        if strict:
            raise PermissionError("another process is replacing the file")
        return real(path, strict=strict)

    monkeypatch.setattr(tc, "_load", busy)
    tc._flush_at_exit()
    monkeypatch.setattr(tc, "_load", real)
    _new_process(monkeypatch)
    tc.flush()
    assert tc.read()["tools"]["list_repos"]["calls"] == 4


def test_read_counts_a_pending_count_before_it_is_folded(store, monkeypatch):
    tc.record("list_repos", 400)
    tc.flush()
    tc.record("list_repos", 400)
    tc.record("search_symbols", 800, no_match=True)
    monkeypatch.setattr(tc, "_acquire", lambda lock, wait: False)
    tc._flush_at_exit()
    data = tc.read()
    assert data["tools"]["list_repos"] == {
        "calls": 2,
        "tokens_returned": 200,
        "no_match": 0,
    }
    assert data["tools"]["search_symbols"] == {
        "calls": 1,
        "tokens_returned": 200,
        "no_match": 1,
    }
    assert _file(store)["tools"]["list_repos"]["calls"] == 1, (
        "the file itself is not rewritten by a read"
    )


def test_a_failed_write_after_a_fold_keeps_the_folded_counts(store, monkeypatch):
    tc.record("list_repos", 400)
    tc.flush()
    tc.record("list_repos", 400)
    real_acquire = tc._acquire
    monkeypatch.setattr(tc, "_acquire", lambda lock, wait: False)
    tc._flush_at_exit()  # one pending file: 1 call
    monkeypatch.setattr(tc, "_acquire", real_acquire)

    _new_process(monkeypatch)
    tc.record("list_repos", 400)
    from pathlib import Path

    real_replace = Path.replace
    calls = {"n": 0}

    def fail_main_replace(self, target):
        if str(target).endswith(tc.FILE):
            calls["n"] += 1
            raise PermissionError("replace refused")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_main_replace)
    tc._flush_at_exit()  # folds the pending file, then the write fails
    monkeypatch.setattr(Path, "replace", real_replace)
    assert calls["n"] == 1
    assert tc.read()["tools"]["list_repos"]["calls"] == 3, (
        "neither the folded nor the new count is lost"
    )
    _new_process(monkeypatch)
    tc.flush()
    tc.record("list_repos", 400)
    tc.flush()
    assert _file(store)["tools"]["list_repos"]["calls"] == 4
    assert _pending(store) == []


def test_a_pending_file_that_cannot_be_removed_is_not_counted_twice(store, monkeypatch):
    tc.record("list_repos", 400)
    monkeypatch.setattr(tc, "_acquire", lambda lock, wait: False)
    tc._flush_at_exit()
    monkeypatch.undo()
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    _new_process(monkeypatch)
    (p,) = _pending(store)
    real_unlink = type(p).unlink

    def stuck(self, *a, **k):
        if self.name.endswith(tc.PENDING_SUFFIX):
            raise PermissionError("held open")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(type(p), "unlink", stuck)
    for _ in range(2):
        tc.record("list_repos", 400)
        tc.flush()
    monkeypatch.setattr(type(p), "unlink", real_unlink)
    assert _file(store)["tools"]["list_repos"]["calls"] == 2, (
        "a pending file it could not remove is left to a later write"
    )
    assert tc.read()["tools"]["list_repos"]["calls"] == 3
    _new_process(monkeypatch)
    tc.record("list_repos", 400)
    tc.flush()
    assert _file(store)["tools"]["list_repos"]["calls"] == 4
    assert _pending(store) == []


def test_a_damaged_pending_file_is_ignored(store):
    (store / f"{tc.FILE}.junk{tc.PENDING_SUFFIX}").write_text(
        "{not json", encoding="utf-8"
    )
    (store / f"{tc.FILE}.list{tc.PENDING_SUFFIX}").write_text(
        json.dumps({"tools": {"x": [1, "a", 0]}}), encoding="utf-8"
    )
    tc.record("list_repos", 400)
    tc.flush()
    assert _file(store)["tools"] == {
        "list_repos": {"calls": 1, "tokens_returned": 100, "no_match": 0}
    }
    assert tc.read()["tools"] == {
        "list_repos": {"calls": 1, "tokens_returned": 100, "no_match": 0}
    }


def test_a_serving_flush_keeps_its_counts_in_memory_not_on_disk(store, monkeypatch):
    """Only the exit flush has no later chance; a serving flush retries on the next write."""
    tc.record("list_repos", 400)
    monkeypatch.setattr(tc, "_acquire", lambda lock, wait: False)
    tc.flush()
    assert _pending(store) == []
