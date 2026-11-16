"""Cursor's agent reads of indexed code, counted beside jCodeMunch lookups.

A licensed buyer asked how often Cursor's agent opened indexed code itself
instead of asking jCodeMunch. `hook-cursor` answers Cursor's `beforeReadFile`
and `afterMCPExecution` hooks (https://cursor.com/docs/hooks). It only counts:
every read is allowed, nothing reaches the conversation, nothing is sent, and
the counts file holds daily numbers, never a path or file text.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from jcodemunch_mcp.cli.hooks import cursor as cursor_hook
from jcodemunch_mcp.storage import direct_reads

TODAY = "2026-11-15"
SRC = str(Path(__file__).resolve().parents[1] / "src")


@pytest.fixture(autouse=True)
def _pin_today(monkeypatch):
    monkeypatch.setattr(direct_reads, "_today", lambda: TODAY)


@pytest.fixture
def store(tmp_path, monkeypatch):
    root = tmp_path / "store"
    root.mkdir()
    monkeypatch.setenv("CODE_INDEX_PATH", str(root))
    return root


@pytest.fixture
def repo(tmp_path, monkeypatch):
    src = tmp_path / "repo"
    (src / "pkg").mkdir(parents=True)
    (src / "pkg" / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (src / "README.md").write_text("# readme\n", encoding="utf-8")
    nested = src / "vendor" / "other"
    (nested / ".git").mkdir(parents=True)
    (nested / "lib.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(
        cursor_hook,
        "_indexed_source_roots",
        lambda: [os.path.normcase(os.path.realpath(str(src)))],
    )
    return src


def _run(monkeypatch, capsys, payload):
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    monkeypatch.setattr(sys, "stdin", io.StringIO(raw))
    rc = cursor_hook.run_cursor_hook()
    return rc, capsys.readouterr().out


def _read(path):
    return {
        "hook_event_name": "beforeReadFile",
        "conversation_id": "c1",
        "cursor_version": "2.1.0",
        "file_path": str(path),
        "content": path.read_text(encoding="utf-8") if path.exists() else "",
        "attachments": [],
    }


def _lookup(server="jcodemunch"):
    return {
        "hook_event_name": "afterMCPExecution",
        "conversation_id": "c1",
        "cursor_version": "2.1.0",
        "tool_name": "search_symbols",
        "tool_input": "{}",
        "mcp_server_name": server,
        "result_json": json.dumps({"results": [{"file": "pkg/mod.py"}]}),
        "duration": 12,
    }


def _today(store):
    s = direct_reads.summary(str(store), today=TODAY)
    return s["today"] if s else None


def test_a_read_of_indexed_code_counts_and_is_allowed(store, repo, monkeypatch, capsys):
    rc, out = _run(monkeypatch, capsys, _read(repo / "pkg" / "mod.py"))
    assert rc == 0
    assert json.loads(out) == {"permission": "allow"}
    assert _today(store) == {"direct_reads": 1, "lookups": 0}


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        "[]",
        {"hook_event_name": "beforeReadFile"},
        {"hook_event_name": "beforeReadFile", "file_path": 7},
        {"conversation_id": "c1"},
    ],
)
def test_a_payload_it_cannot_read_counts_nothing_and_still_allows(
    store, repo, monkeypatch, capsys, payload
):
    rc, out = _run(monkeypatch, capsys, payload)
    assert rc == 0
    assert json.loads(out) == {"permission": "allow"}
    assert direct_reads.summary(str(store), today=TODAY) is None


def test_reads_outside_the_index_count_nothing(
    store, repo, tmp_path, monkeypatch, capsys
):
    other = tmp_path / "elsewhere.py"
    other.write_text("x = 1\n", encoding="utf-8")
    for path in (other, repo / "README.md", repo / "vendor" / "other" / "lib.py"):
        rc, out = _run(monkeypatch, capsys, _read(path))
        assert rc == 0 and json.loads(out) == {"permission": "allow"}
    assert direct_reads.summary(str(store), today=TODAY) is None


def test_a_count_that_fails_still_allows(store, repo, monkeypatch, capsys):
    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(direct_reads, "record_read", boom)
    rc, out = _run(monkeypatch, capsys, _read(repo / "pkg" / "mod.py"))
    assert rc == 0
    assert json.loads(out) == {"permission": "allow"}


def test_a_jcodemunch_call_counts_a_lookup_and_another_server_does_not(
    store, repo, monkeypatch, capsys
):
    rc, out = _run(monkeypatch, capsys, _lookup())
    assert rc == 0 and json.loads(out) == {}
    rc, out = _run(monkeypatch, capsys, _lookup(server="github"))
    assert rc == 0 and json.loads(out) == {}
    assert _today(store) == {"direct_reads": 0, "lookups": 1}


def test_the_counts_file_holds_no_path_and_no_file_text(
    store, repo, monkeypatch, capsys
):
    _run(monkeypatch, capsys, _lookup())
    _run(monkeypatch, capsys, _read(repo / "pkg" / "mod.py"))
    text = (store / direct_reads.FILE_NAME).read_text(encoding="utf-8")
    for leak in ("mod.py", "pkg", "return 1", "c1", str(repo)):
        assert leak not in text


def test_an_unreadable_counts_file_is_not_zero_and_is_not_overwritten(store):
    path = store / direct_reads.FILE_NAME
    path.write_text("{not json", encoding="utf-8")
    s = direct_reads.summary(str(store), today=TODAY)
    assert "unreadable" in s and "today" not in s and "all_time" not in s
    assert direct_reads.record_read(storage_path=str(store), today=TODAY) is False
    assert path.read_text(encoding="utf-8") == "{not json"


@pytest.mark.parametrize(
    "text",
    [
        '{"schema": 1, "days": {"2026-11-15": "many"}}',
        '{"schema": 1, "days": {"2026-11-15": {"direct_reads": "3"}}}',
    ],
)
def test_a_counts_file_with_a_bad_day_is_unreadable(store, capsys, text):
    from jcodemunch_mcp.cli.direct_reads import run_direct_reads

    (store / direct_reads.FILE_NAME).write_text(text, encoding="utf-8")
    assert "unreadable" in direct_reads.summary(str(store), today=TODAY)
    assert run_direct_reads(as_json=False) == 1
    assert run_direct_reads(as_json=True) == 1
    capsys.readouterr()


def test_uninstall_cursor_removes_the_cursor_hooks(home, monkeypatch, tmp_path):
    from jcodemunch_mcp.cli import init as init_mod

    init_mod.install_cursor_hooks(backup=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(init_mod, "_detect_clients", lambda: [])
    init_mod.run_uninstall(target="cursor", no_backup=True, yes=True)
    data = json.loads((home / ".cursor" / "hooks.json").read_text(encoding="utf-8"))
    assert data["hooks"] == {}


def test_parallel_writers_lose_no_count(store):
    def one():
        direct_reads.record_read(storage_path=str(store), today=TODAY)

    threads = [threading.Thread(target=one) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert (
        direct_reads.summary(str(store), today=TODAY)["all_time"]["direct_reads"] == 10
    )


def test_a_lock_being_released_is_waited_for_not_lost(store, monkeypatch):
    # On Windows, creating a lock its holder is deleting this instant raises
    # PermissionError, not FileExistsError. That is a held lock: the writer
    # waits and counts, on every platform this test runs on.
    real_open = os.open
    raised = []

    def flaky_open(path, flags, *args, **kwargs):
        if str(path).endswith(".lock") and flags & os.O_EXCL and not raised:
            raised.append(path)
            raise PermissionError(13, "Permission denied", str(path))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", flaky_open)
    assert direct_reads.record_read(storage_path=str(store), today=TODAY) is True
    assert raised, "the injected PermissionError never fired"
    assert direct_reads.summary(str(store), today=TODAY)["all_time"]["direct_reads"] == 1


def _pending(store):
    return sorted(store.glob(direct_reads.FILE_NAME + ".*.pending"))


def _hold_lock(store):
    lock = store / (direct_reads.FILE_NAME + ".lock")
    lock.write_text("", encoding="utf-8")
    return lock


def _all_time(store):
    return direct_reads.summary(str(store), today=TODAY)["all_time"]["direct_reads"]


def test_a_count_whose_lock_wait_runs_out_is_kept_not_dropped(store, monkeypatch):
    # The lock is held past the wait. The count goes to a pending file of its
    # own, is shown at once, and the next writer that takes the lock folds it in.
    monkeypatch.setattr(direct_reads, "_LOCK_WAIT_SECONDS", 0.05)
    lock = _hold_lock(store)
    assert direct_reads.record_read(storage_path=str(store), today=TODAY) is True
    assert len(_pending(store)) == 1
    assert _all_time(store) == 1
    lock.unlink()
    assert direct_reads.record_read(storage_path=str(store), today=TODAY) is True
    assert _pending(store) == []
    data = json.loads((store / direct_reads.FILE_NAME).read_text(encoding="utf-8"))
    assert data["days"][TODAY]["direct_reads"] == 2
    assert _all_time(store) == 2


def test_slow_parallel_writers_whose_waits_run_out_lose_no_count(store, monkeypatch):
    # A slow runner: each write takes 30 ms and each wait is 50 ms, so with ten
    # writers several waits run out. Every count is still there.
    monkeypatch.setattr(direct_reads, "_LOCK_WAIT_SECONDS", 0.05)
    real_dumps = json.dumps

    def slow_dumps(*args, **kwargs):
        time.sleep(0.03)
        return real_dumps(*args, **kwargs)

    monkeypatch.setattr(direct_reads.json, "dumps", slow_dumps)
    real_write_pending = direct_reads._write_pending
    kept_aside = []

    def counting_write_pending(*args, **kwargs):
        kept_aside.append(args)
        return real_write_pending(*args, **kwargs)

    monkeypatch.setattr(direct_reads, "_write_pending", counting_write_pending)
    results = []

    def one():
        results.append(direct_reads.record_read(storage_path=str(store), today=TODAY))

    threads = [threading.Thread(target=one) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [True] * 10
    assert kept_aside, "no wait ran out, so the pending path was not exercised"
    assert _all_time(store) == 10


def test_a_pending_count_it_cannot_remove_is_not_folded_in_twice(store, monkeypatch):
    # A pending count is folded in only once its file is removed. One that
    # cannot be removed stays pending and is counted once, never twice.
    monkeypatch.setattr(direct_reads, "_LOCK_WAIT_SECONDS", 0.05)
    lock = _hold_lock(store)
    direct_reads.record_read(storage_path=str(store), today=TODAY)
    lock.unlink()
    (pending,) = _pending(store)
    real_unlink = Path.unlink

    def stuck(self, *args, **kwargs):
        if self.name.endswith(".pending"):
            raise PermissionError(13, "in use", str(self))
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", stuck)
    assert direct_reads.record_read(storage_path=str(store), today=TODAY) is True
    assert pending.exists()
    assert _all_time(store) == 2
    monkeypatch.setattr(Path, "unlink", real_unlink)
    assert direct_reads.record_read(storage_path=str(store), today=TODAY) is True
    assert _pending(store) == []
    assert _all_time(store) == 3


def test_a_failed_counts_write_keeps_every_count_it_carried(store, monkeypatch):
    # One count in the file and two pending. The next write folds the two in,
    # then its replace fails, as it does on Windows while a reader has the
    # file open. All four counts survive, and a later write folds them in.
    direct_reads.record_read(storage_path=str(store), today=TODAY)
    monkeypatch.setattr(direct_reads, "_LOCK_WAIT_SECONDS", 0.05)
    lock = _hold_lock(store)
    direct_reads.record_read(storage_path=str(store), today=TODAY)
    direct_reads.record_read(storage_path=str(store), today=TODAY)
    lock.unlink()
    assert len(_pending(store)) == 2
    tally = store / direct_reads.FILE_NAME
    real_replace = os.replace
    failed = []

    def replace_fails_on_the_counts(src, dst):
        if Path(dst) == tally and not failed:
            failed.append(dst)
            raise PermissionError(13, "in use", str(dst))
        return real_replace(src, dst)

    monkeypatch.setattr(direct_reads.os, "replace", replace_fails_on_the_counts)
    assert direct_reads.record_read(storage_path=str(store), today=TODAY) is True
    assert failed, "the injected replace failure never fired"
    assert _all_time(store) == 4
    assert direct_reads.record_read(storage_path=str(store), today=TODAY) is True
    assert _pending(store) == []
    assert _all_time(store) == 5


def test_a_fold_between_the_reports_two_reads_never_counts_twice(store, monkeypatch):
    # Pinned interleaving: a writer folds the pending count in at the moment
    # the report reads the counts file. The report shows the true total.
    monkeypatch.setattr(direct_reads, "_LOCK_WAIT_SECONDS", 0.05)
    lock = _hold_lock(store)
    direct_reads.record_read(storage_path=str(store), today=TODAY)
    lock.unlink()
    assert len(_pending(store)) == 1
    real_load = direct_reads._load
    fired = []

    def load_after_a_fold(path):
        if not fired:
            fired.append(path)
            assert direct_reads.record_read(storage_path=str(store), today=TODAY) is True
        return real_load(path)

    monkeypatch.setattr(direct_reads, "_load", load_after_a_fold)
    s = direct_reads.summary(str(store), today=TODAY)
    assert fired, "the fold never ran"
    assert _pending(store) == []
    assert s["all_time"]["direct_reads"] == 2


def test_a_folded_count_does_not_replace_the_files_cursor_version(store, monkeypatch):
    direct_reads.record_read(cursor_version="2.1.1", storage_path=str(store), today=TODAY)
    monkeypatch.setattr(direct_reads, "_LOCK_WAIT_SECONDS", 0.05)
    lock = _hold_lock(store)
    direct_reads.record_read(cursor_version="2.1.0", storage_path=str(store), today=TODAY)
    lock.unlink()
    assert direct_reads.summary(str(store), today=TODAY)["cursor_version"] == "2.1.1"
    direct_reads.record_read(storage_path=str(store), today=TODAY)
    assert _pending(store) == []
    s = direct_reads.summary(str(store), today=TODAY)
    assert s["cursor_version"] == "2.1.1"
    assert s["all_time"]["direct_reads"] == 3


def test_a_pending_file_it_cannot_read_counts_nothing_and_is_left(store):
    junk = store / (direct_reads.FILE_NAME + ".x.pending")
    junk.write_text("not a count", encoding="utf-8")
    assert direct_reads.record_read(storage_path=str(store), today=TODAY) is True
    assert _all_time(store) == 1
    assert junk.read_text(encoding="utf-8") == "not a count"


def test_a_pending_count_shows_before_any_counts_file_exists(store, monkeypatch):
    monkeypatch.setattr(direct_reads, "_LOCK_WAIT_SECONDS", 0.05)
    _hold_lock(store)
    direct_reads.record_lookup(cursor_version="2.1.0", storage_path=str(store), today=TODAY)
    assert not (store / direct_reads.FILE_NAME).exists()
    s = direct_reads.summary(str(store), today=TODAY)
    assert s["all_time"]["lookups"] == 1
    assert s["cursor_version"] == "2.1.0"


def test_summary_windows_and_labels(store):
    direct_reads.record_read(
        cursor_version="2.1.0", storage_path=str(store), today="2026-11-01"
    )
    direct_reads.record_read(
        cursor_version="2.1.0", storage_path=str(store), today="2026-11-12"
    )
    direct_reads.record_lookup(
        cursor_version="2.1.1", storage_path=str(store), today=TODAY
    )
    s = direct_reads.summary(str(store), today=TODAY)
    assert s["coverage"] == "cursor_ide" and s["floor"] is True
    assert s["today"] == {"direct_reads": 0, "lookups": 1}
    assert s["last_7_days"]["direct_reads"] == 1
    assert s["all_time"]["direct_reads"] == 2
    assert s["cursor_version"] == "2.1.1"


def test_session_stats_shows_the_block_only_when_counts_exist(store):
    from jcodemunch_mcp.tools.get_session_stats import get_session_stats

    assert "direct_reads" not in get_session_stats(storage_path=str(store))
    direct_reads.record_read(storage_path=str(store), today=TODAY)
    assert (
        get_session_stats(storage_path=str(store))["direct_reads"]["coverage"]
        == "cursor_ide"
    )


def test_the_cli_prints_the_counts(store, capsys):
    from jcodemunch_mcp.cli.direct_reads import run_direct_reads

    assert run_direct_reads(as_json=False) == 0
    assert "No direct-read count yet" in capsys.readouterr().out
    direct_reads.record_read(
        cursor_version="2.1.0", storage_path=str(store), today=TODAY
    )
    assert run_direct_reads(as_json=True) == 0
    assert json.loads(capsys.readouterr().out)["all_time"]["direct_reads"] == 1
    assert run_direct_reads(as_json=False) == 0
    assert (
        "1 direct reads of indexed code against 0 jCodeMunch lookups"
        in capsys.readouterr().out
    )


@pytest.mark.parametrize(
    "payload,reply",
    [
        (
            '{"hook_event_name": "beforeReadFile", "file_path": "/nowhere/x.py", "content": ""}',
            '{"permission": "allow"}',
        ),
        ("garbage", '{"permission": "allow"}'),
        (
            '{"hook_event_name": "afterMCPExecution", "mcp_server_name": "jcodemunch"}',
            "{}",
        ),
    ],
)
def test_the_real_entry_point_prints_exactly_one_reply(tmp_path, payload, reply):
    """Through cli_entry, as Cursor runs it: stdout is the reply and nothing else."""
    env = {**os.environ, "CODE_INDEX_PATH": str(tmp_path), "PYTHONPATH": SRC}
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "from jcodemunch_mcp.cli_entry import cli; cli(['hook-cursor'])",
        ],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout == reply


def test_hook_cursor_takes_the_hook_fast_path():
    from jcodemunch_mcp import cli_entry

    assert cli_entry.HOOK_RUNNERS["hook-cursor"] == "run_cursor_hook"


# ---- install ---------------------------------------------------------------


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".cursor").mkdir(parents=True)
    monkeypatch.setattr("pathlib.Path.home", lambda: h)
    from jcodemunch_mcp.cli import init as init_mod

    monkeypatch.setattr(init_mod, "_hook_invocation", lambda: "/opt/bin/jcodemunch-mcp")
    return h


def test_install_merges_both_hooks_and_keeps_others(home):
    from jcodemunch_mcp.cli.init import install_cursor_hooks

    path = home / ".cursor" / "hooks.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "hooks": {
                    "beforeReadFile": [{"command": "other-tool check"}],
                    "stop": [{"command": "say done"}],
                },
            }
        ),
        encoding="utf-8",
    )
    install_cursor_hooks(backup=False)
    data = json.loads(path.read_text(encoding="utf-8"))
    cmds = {e: [r["command"] for r in rules] for e, rules in data["hooks"].items()}
    assert "other-tool check" in cmds["beforeReadFile"]
    assert cmds["stop"] == ["say done"]
    for event in ("beforeReadFile", "afterMCPExecution"):
        assert sum("hook-cursor" in c for c in cmds[event]) == 1
    before = path.read_text(encoding="utf-8")
    assert "already" in install_cursor_hooks(backup=False)
    assert path.read_text(encoding="utf-8") == before


def test_install_writes_a_new_file_and_uninstall_removes_only_ours(home):
    from jcodemunch_mcp.cli.init import install_cursor_hooks, uninstall_cursor_hooks

    path = home / ".cursor" / "hooks.json"
    install_cursor_hooks(backup=False)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert set(data["hooks"]) == {"beforeReadFile", "afterMCPExecution"}
    data["hooks"]["stop"] = [{"command": "say done"}]
    path.write_text(json.dumps(data), encoding="utf-8")
    uninstall_cursor_hooks(backup=False)
    assert json.loads(path.read_text(encoding="utf-8"))["hooks"] == {
        "stop": [{"command": "say done"}]
    }
    uninstall_cursor_hooks(backup=False)
    assert path.exists()


def test_a_quoted_windows_path_gets_the_powershell_call_operator(home, monkeypatch):
    from jcodemunch_mcp.cli import init as init_mod

    monkeypatch.setattr(init_mod.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        init_mod,
        "_hook_invocation",
        lambda: '"C:/Program Files/jcm/jcodemunch-mcp.exe"',
    )
    init_mod.install_cursor_hooks(backup=False)
    data = json.loads((home / ".cursor" / "hooks.json").read_text(encoding="utf-8"))
    assert (
        data["hooks"]["beforeReadFile"][0]["command"]
        == '& "C:/Program Files/jcm/jcodemunch-mcp.exe" hook-cursor'
    )
    assert "already" in init_mod.install_cursor_hooks(backup=False)


def test_install_skips_without_a_cursor_directory(tmp_path, monkeypatch):
    from jcodemunch_mcp.cli.init import install_cursor_hooks

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    assert "not found" in install_cursor_hooks(backup=False)
    assert not (tmp_path / ".cursor").exists()
