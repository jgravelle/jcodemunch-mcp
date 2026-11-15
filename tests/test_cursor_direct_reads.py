"""Cursor's agent reads of indexed code, counted beside jCodeMunch lookups.

A licensed buyer asked how often Cursor's agent opened indexed code itself
instead of asking jCodeMunch. `hook-cursor` answers Cursor's `beforeReadFile`
and `afterMCPExecution` hooks (https://cursor.com/docs/hooks). It only counts:
every read is allowed, nothing reaches the conversation, nothing is sent, and
the tally holds counts and path hashes, never a path or file text.
"""

from __future__ import annotations

import io
import json
import os
import sys

import pytest

from jcodemunch_mcp.cli.hooks import cursor as cursor_hook
from jcodemunch_mcp.storage import direct_reads

TODAY = "2026-11-15"


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
    out = capsys.readouterr().out
    return rc, out


def _read(path, conv="c1"):
    return {
        "hook_event_name": "beforeReadFile",
        "conversation_id": conv,
        "cursor_version": "2.1.0",
        "file_path": str(path),
        "content": path.read_text(encoding="utf-8") if path.exists() else "",
        "attachments": [],
    }


def _lookup(result, conv="c1", server="jcodemunch"):
    return {
        "hook_event_name": "afterMCPExecution",
        "conversation_id": conv,
        "cursor_version": "2.1.0",
        "tool_name": "search_symbols",
        "tool_input": "{}",
        "mcp_server_name": server,
        "result_json": json.dumps(result),
        "duration": 12,
    }


def _today(store):
    s = direct_reads.summary(str(store), today=TODAY)
    return s["today"] if s else None


@pytest.fixture(autouse=True)
def _pin_today(monkeypatch):
    monkeypatch.setattr(direct_reads, "_today", lambda: TODAY)


def test_a_read_of_indexed_code_counts_and_is_allowed(store, repo, monkeypatch, capsys):
    rc, out = _run(monkeypatch, capsys, _read(repo / "pkg" / "mod.py"))
    assert rc == 0
    assert json.loads(out) == {"permission": "allow"}
    assert _today(store) == {"direct_reads": 1, "lookups": 0, "reads_after_lookup": 0}


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        "[]",
        {"hook_event_name": "beforeReadFile"},
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


def test_reads_outside_the_index_or_of_non_code_count_nothing(
    store, repo, tmp_path, monkeypatch, capsys
):
    other = tmp_path / "elsewhere.py"
    other.write_text("x = 1\n", encoding="utf-8")
    for path in (other, repo / "README.md"):
        rc, out = _run(monkeypatch, capsys, _read(path))
        assert rc == 0 and json.loads(out) == {"permission": "allow"}
    assert direct_reads.summary(str(store), today=TODAY) is None


def test_a_tally_that_cannot_be_written_still_allows(store, repo, monkeypatch, capsys):
    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(direct_reads, "record_read", boom)
    rc, out = _run(monkeypatch, capsys, _read(repo / "pkg" / "mod.py"))
    assert rc == 0
    assert json.loads(out) == {"permission": "allow"}


def test_a_jcodemunch_call_counts_a_lookup_and_another_server_does_not(
    store, repo, monkeypatch, capsys
):
    rc, _ = _run(monkeypatch, capsys, _lookup({"results": []}))
    assert rc == 0
    rc, _ = _run(monkeypatch, capsys, _lookup({"results": []}, server="github"))
    assert rc == 0
    assert _today(store) == {"direct_reads": 0, "lookups": 1, "reads_after_lookup": 0}


def test_a_read_after_a_lookup_named_the_file_is_counted_apart(
    store, repo, monkeypatch, capsys
):
    named = {
        "content": [
            {
                "type": "text",
                "text": json.dumps({"results": [{"file": "pkg/mod.py", "line": 1}]}),
            }
        ]
    }
    _run(monkeypatch, capsys, _lookup(named, conv="c1"))
    _run(monkeypatch, capsys, _read(repo / "pkg" / "mod.py", conv="c1"))
    _run(monkeypatch, capsys, _read(repo / "pkg" / "mod.py", conv="c2"))
    assert _today(store) == {"direct_reads": 2, "lookups": 1, "reads_after_lookup": 1}


def test_the_tally_holds_no_path_and_no_file_text(store, repo, monkeypatch, capsys):
    named = {"results": [{"file": "pkg/mod.py"}]}
    _run(monkeypatch, capsys, _lookup(named))
    _run(monkeypatch, capsys, _read(repo / "pkg" / "mod.py"))
    text = (store / direct_reads.FILE_NAME).read_text(encoding="utf-8")
    assert "mod.py" not in text and "pkg" not in text
    assert "return 1" not in text and str(repo) not in text


def test_summary_windows_and_labels(store):
    direct_reads.record_read(
        conversation_id="a",
        key=None,
        cursor_version="2.1.0",
        storage_path=str(store),
        today="2026-11-01",
    )
    direct_reads.record_read(
        conversation_id="a",
        key=None,
        cursor_version="2.1.0",
        storage_path=str(store),
        today="2026-11-12",
    )
    direct_reads.record_lookup(
        conversation_id="a",
        keys=set(),
        cursor_version="2.1.1",
        storage_path=str(store),
        today=TODAY,
    )
    s = direct_reads.summary(str(store), today=TODAY)
    assert s["coverage"] == "cursor_ide" and s["floor"] is True
    assert s["today"]["lookups"] == 1 and s["today"]["direct_reads"] == 0
    assert s["last_7_days"]["direct_reads"] == 1
    assert s["all_time"]["direct_reads"] == 2
    assert s["cursor_version"] == "2.1.1"


def test_session_stats_shows_the_block_only_when_a_tally_exists(store):
    from jcodemunch_mcp.tools.get_session_stats import get_session_stats

    assert "direct_reads" not in get_session_stats(storage_path=str(store))
    direct_reads.record_read(
        conversation_id="a",
        key=None,
        cursor_version=None,
        storage_path=str(store),
        today=TODAY,
    )
    block = get_session_stats(storage_path=str(store))["direct_reads"]
    assert block["coverage"] == "cursor_ide"


def test_the_cli_prints_the_counts(store, capsys):
    from jcodemunch_mcp.cli.direct_reads import run_direct_reads

    assert run_direct_reads(as_json=False) == 0
    assert "No direct-read count yet" in capsys.readouterr().out
    direct_reads.record_read(
        conversation_id="a",
        key=None,
        cursor_version="2.1.0",
        storage_path=str(store),
        today=TODAY,
    )
    assert run_direct_reads(as_json=True) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["all_time"]["direct_reads"] == 1


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
    left = json.loads(path.read_text(encoding="utf-8"))
    assert left["hooks"] == {"stop": [{"command": "say done"}]}


def test_install_skips_without_a_cursor_directory(tmp_path, monkeypatch):
    from jcodemunch_mcp.cli.init import install_cursor_hooks

    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    assert "not found" in install_cursor_hooks(backup=False)
    assert not (tmp_path / ".cursor").exists()
