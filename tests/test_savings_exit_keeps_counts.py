"""The savings meter keeps a batch it could not write at exit.

The exit flush of `_savings.json` waits 3 s for `_savings.lock`
(`token_tracker._SAVINGS_LOCK_WAIT_AT_EXIT`). A write that failed there (the
wait ran out, or the read or the replace was refused) put the batch back in
memory for a next write that, at exit, never comes. The tool counters and the
Cursor read counter had the same loss and keep such counts in a pending file
(`storage/pending_files.py`); the savings meter uses the same protocol now.

⚠ Every case pins its failure: a held lock file, or a patched replace. None
relies on timing.
"""

from __future__ import annotations

import json
import os
import pathlib
from pathlib import Path

import pytest

from jcodemunch_mcp.storage import pending_files
from jcodemunch_mcp.storage import token_tracker as tt


@pytest.fixture(autouse=True)
def _no_share(monkeypatch):
    # Nothing here may reach the community meter.
    monkeypatch.setattr(tt, "_share_savings", lambda *a, **k: None)


def _file(root: Path) -> Path:
    return root / tt._SAVINGS_FILE


def _ledger(root: Path) -> dict:
    return json.loads(_file(root).read_text(encoding="utf-8"))


def _pending(root: Path) -> list:
    return pending_files.paths(_file(root))


def _state_with(
    root: Path, saved: int, *, tool: str = "search_symbols", encoding: int = 0
) -> "tt._State":
    st = tt._State()
    st._ensure_loaded(str(root))
    st._unflushed = saved
    st._total += saved
    st._pending_telemetry = saved
    st._hb_calls = 1
    st._tool_unflushed = {tool: saved} if saved else {}
    st._encoding_unflushed = encoding
    return st


def _hold_lock(root: Path) -> Path:
    lock = root / tt._SAVINGS_LOCK_FILE
    lock.write_text("", encoding="utf-8")  # another server holds it
    return lock


def test_a_batch_the_exit_flush_cannot_write_is_kept_on_disk(tmp_path):
    _file(tmp_path).write_text(json.dumps({"total_tokens_saved": 40}), encoding="utf-8")
    st = _state_with(tmp_path, 5)
    lock = _hold_lock(tmp_path)
    st.flush(wait=0.0, state_wait=0.0, last_chance=True)
    assert _ledger(tmp_path)["total_tokens_saved"] == 40
    assert len(_pending(tmp_path)) == 1, "the exit batch was not kept"
    lock.unlink()
    nxt = _state_with(tmp_path, 1)
    nxt._flush(lock_wait=0.0)
    data = _ledger(tmp_path)
    assert data["total_tokens_saved"] == 46, "the next write folds the kept batch in"
    assert data["by_tool"]["search_symbols"] == 6
    assert _pending(tmp_path) == []


def test_a_failed_replace_at_exit_is_kept_on_disk(tmp_path, monkeypatch):
    _file(tmp_path).write_text(json.dumps({"total_tokens_saved": 10}), encoding="utf-8")
    st = _state_with(tmp_path, 3, encoding=2)
    real = os.replace

    def refuse(src, dst, *a, **k):
        if Path(dst).name == tt._SAVINGS_FILE:
            raise PermissionError("Windows refused the replace")
        return real(src, dst, *a, **k)

    monkeypatch.setattr(os, "replace", refuse)
    st.flush(wait=0.0, state_wait=0.0, last_chance=True)
    monkeypatch.setattr(os, "replace", real)
    assert len(_pending(tmp_path)) == 1
    tt._State()._ensure_loaded(str(tmp_path))
    nxt = _state_with(tmp_path, 0)
    nxt._flush(lock_wait=0.0)
    data = _ledger(tmp_path)
    assert data["total_tokens_saved"] == 13
    assert data["total_encoding_tokens_saved"] == 2


def test_a_serving_flush_keeps_its_batch_in_memory(tmp_path):
    st = _state_with(tmp_path, 5)
    lock = _hold_lock(tmp_path)
    st._flush(lock_wait=0.0)
    assert _pending(tmp_path) == [], "a running server retries from memory"
    assert st._unflushed == 5
    lock.unlink()


def test_the_registered_exit_flush_is_a_last_chance(tmp_path, monkeypatch):
    seen = {}

    def spy(self, wait=None, state_wait=None, *, last_chance=False):
        seen["last_chance"] = last_chance

    monkeypatch.setattr(tt._State, "flush", spy)
    tt._exit_flush()
    assert seen == {"last_chance": True}


def test_a_new_process_reports_kept_counts_in_its_lifetime_total(tmp_path):
    _file(tmp_path).write_text(json.dumps({"total_tokens_saved": 40}), encoding="utf-8")
    st = _state_with(tmp_path, 5)
    lock = _hold_lock(tmp_path)
    st.flush(wait=0.0, state_wait=0.0, last_chance=True)
    lock.unlink()
    fresh = tt._State()
    fresh._ensure_loaded(str(tmp_path))
    assert fresh._total == 45
    assert fresh._tool_totals.get("search_symbols") == 5


def test_folded_counts_are_kept_when_the_write_after_folding_fails(
    tmp_path, monkeypatch
):
    _file(tmp_path).write_text(json.dumps({"total_tokens_saved": 40}), encoding="utf-8")
    pending_files.write(
        _file(tmp_path),
        {"saved": 9, "calls": 1, "by_tool": {"x": 9}, "encoding": 0, "telemetry": 9},
    )
    st = _state_with(tmp_path, 1)
    real = os.replace

    def refuse(src, dst, *a, **k):
        if Path(dst).name == tt._SAVINGS_FILE:
            raise PermissionError("refused")
        return real(src, dst, *a, **k)

    monkeypatch.setattr(os, "replace", refuse)
    st._flush(lock_wait=0.0)
    monkeypatch.setattr(os, "replace", real)
    assert st._unflushed == 1, "its own batch goes back to memory"
    st._flush(lock_wait=0.0)
    assert _ledger(tmp_path)["total_tokens_saved"] == 50, (
        "the folded 9 survived the failed write"
    )
    assert _pending(tmp_path) == []


def test_folded_counts_the_disk_refuses_twice_stay_in_memory(tmp_path, monkeypatch):
    _file(tmp_path).write_text(json.dumps({"total_tokens_saved": 40}), encoding="utf-8")
    pending_files.write(
        _file(tmp_path),
        {"saved": 9, "calls": 1, "by_tool": {}, "encoding": 0, "telemetry": 0},
    )
    st = _state_with(tmp_path, 1)
    real = os.replace

    def refuse(src, dst, *a, **k):
        raise PermissionError("refused")

    monkeypatch.setattr(os, "replace", refuse)
    st._flush(lock_wait=0.0)
    monkeypatch.setattr(os, "replace", real)
    assert _pending(tmp_path) == []
    assert st._unflushed == 10, "the folded 9 rides with this process's own batch"
    st._flush(lock_wait=0.0)
    assert _ledger(tmp_path)["total_tokens_saved"] == 50


def test_an_unremovable_pending_file_is_not_counted_twice(tmp_path, monkeypatch):
    _file(tmp_path).write_text(json.dumps({"total_tokens_saved": 40}), encoding="utf-8")
    pending_files.write(
        _file(tmp_path),
        {"saved": 9, "calls": 1, "by_tool": {}, "encoding": 0, "telemetry": 0},
    )
    monkeypatch.setattr(pending_files, "claim", lambda p: False)
    st = _state_with(tmp_path, 1)
    st._flush(lock_wait=0.0)
    st._hb_calls = 1
    st._flush(lock_wait=0.0)
    assert _ledger(tmp_path)["total_tokens_saved"] == 41


def test_a_damaged_pending_file_is_ignored(tmp_path):
    _file(tmp_path).write_text(json.dumps({"total_tokens_saved": 40}), encoding="utf-8")
    pending_files.write(_file(tmp_path), {"saved": "lots", "calls": 1})
    pending_files.write(_file(tmp_path), {"saved": -3, "calls": 1})
    st = _state_with(tmp_path, 1)
    st._flush(lock_wait=0.0)
    assert _ledger(tmp_path)["total_tokens_saved"] == 41


def test_an_unreadable_ledger_folds_nothing(tmp_path):
    _file(tmp_path).write_text("{not json", encoding="utf-8")
    pending_files.write(
        _file(tmp_path),
        {"saved": 9, "calls": 1, "by_tool": {}, "encoding": 0, "telemetry": 0},
    )
    st = _state_with(tmp_path, 1)
    st._flush(lock_wait=0.0)
    assert len(_pending(tmp_path)) == 1, (
        "a refused write leaves the kept batch where it is"
    )


def test_the_savings_meter_uses_the_shared_protocol():
    src = pathlib.Path(tt.__file__).read_text(encoding="utf-8")
    assert "_pending_files.claim(" in src
    assert "_pending_files.write(" in src
    assert "uuid.uuid4().hex}.pending" not in src


def test_folded_counts_a_stopping_server_cannot_put_back_go_to_disk_with_its_batch(
    tmp_path, monkeypatch
):
    """Review round 1: a last-chance flush that claimed another server's kept
    batch, and whose write and re-keep both failed, held it only in memory."""
    _file(tmp_path).write_text(json.dumps({"total_tokens_saved": 40}), encoding="utf-8")
    pending_files.write(
        _file(tmp_path),
        {"saved": 9, "calls": 1, "by_tool": {"x": 9}, "encoding": 0, "telemetry": 0},
    )
    st = _state_with(tmp_path, 1)
    real_replace = os.replace
    real_write = pending_files.write
    refused = []

    def refuse_ledger(src, dst, *a, **k):
        if Path(dst).name == tt._SAVINGS_FILE:
            raise PermissionError("refused")
        return real_replace(src, dst, *a, **k)

    def refuse_once(main, payload):
        if not refused:
            refused.append(payload)
            return False
        return real_write(main, payload)

    monkeypatch.setattr(os, "replace", refuse_ledger)
    monkeypatch.setattr(pending_files, "write", refuse_once)
    st.flush(wait=0.0, state_wait=0.0, last_chance=True)
    monkeypatch.setattr(os, "replace", real_replace)
    monkeypatch.setattr(pending_files, "write", real_write)
    assert refused, "the refused re-keep never happened; the test proves nothing"
    assert len(_pending(tmp_path)) == 1
    nxt = _state_with(tmp_path, 0)
    nxt._flush(lock_wait=0.0)
    data = _ledger(tmp_path)
    assert data["total_tokens_saved"] == 50, (
        "the other server's 9 survived this server's exit"
    )
    assert data["by_tool"]["x"] == 9


def test_the_savings_meter_writes_no_pending_temp_of_its_own():
    """The tool counters' and Cursor reads' protocol ratchet, extended to this store."""
    import inspect

    src = inspect.getsource(tt)
    assert ".ptmp" not in src, "token_tracker writes its own pending temp files"
    assert "_pending_files.claim(" in src
