"""A separate copy of an indexed repo says why the index is silent.

When the working folder is a second checkout of a repo indexed from another
folder, `resolve_repo` flags `working_tree_mismatch`. The server's startup
check and the PreToolUse hook both refused to treat that folder as indexed,
which is right, and then said nothing, so an agent there saw no index and no
reason. Both now read the flag through `resolve_repo.separate_copy` and say
which folder the index reads and the call that indexes this one.

The mismatch answers here are built by `_attach_working_tree_mismatch`, the
producer, never written by hand, so a change to its shape reaches these tests.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

from jcodemunch_mcp import server
from jcodemunch_mcp.cli.hooks import steering
from jcodemunch_mcp.tools import resolve_repo as rr


def _mismatch_answer(indexed: Path, queried: Path) -> dict:
    result = {"indexed": True, "repo": "gh/proj", "file_count": 7, "source_root": str(indexed), "_meta": {}}
    rr._attach_working_tree_mismatch(result, queried.resolve())
    assert result.get("working_tree_mismatch") is True  # the producer flagged it
    return result


@pytest.fixture
def two_checkouts(tmp_path):
    indexed, copy = tmp_path / "checkout_a", tmp_path / "checkout_b"
    indexed.mkdir()
    copy.mkdir()
    return indexed, copy


# --- the shared rule ---------------------------------------------------------


def test_separate_copy_reads_the_producers_flag(two_checkouts):
    indexed, copy = two_checkouts
    assert rr.separate_copy(_mismatch_answer(indexed, copy)) == ("gh/proj", str(indexed))


@pytest.mark.parametrize(
    "answer",
    [
        None,
        {"indexed": False},
        {"indexed": True, "repo": "gh/proj", "source_root": "x"},  # a match: no flag
        {"indexed": True, "working_tree_mismatch": True},  # no repo, no root
        {"indexed": "true", "working_tree_mismatch": True, "repo": "r", "source_root": "x"},
    ],
)
def test_anything_else_is_not_a_separate_copy(answer):
    assert rr.separate_copy(answer) is None


def test_the_sentence_names_the_indexed_folder_and_the_call(two_checkouts):
    indexed, _ = two_checkouts
    text = rr.separate_copy_sentence("gh/proj", str(indexed))
    assert str(indexed) in text and "gh/proj" in text
    assert '"identity_mode": "local"' in text and "allow_state_change=true" in text


# --- the server's startup text -----------------------------------------------


def test_counter_says_it_and_stays_in_budget():
    text = server._mcp_instructions("counter", separate_copy=True)
    assert text.startswith("Repo indexed elsewhere; index this copy with")
    assert 'order("index_folder", {"path":".","identity_mode":"local"}, allow_state_change=true)' in text
    assert len(text) <= server._MCP_INSTRUCTIONS_MAX_CHARS


def test_full_keeps_its_text_because_it_is_at_its_budget():
    assert server._mcp_instructions("full", separate_copy=True) == server._mcp_instructions("full")


def test_an_indexed_folder_is_never_called_a_copy():
    a = server._mcp_instructions("counter", indexed_files=12, separate_copy=True)
    assert a == server._mcp_instructions("counter", indexed_files=12)


def test_startup_reads_one_lookup_for_both_answers(monkeypatch, two_checkouts, caplog):
    indexed, copy = two_checkouts
    calls = []

    def fake(path, storage_path=None):
        calls.append(path)
        return _mismatch_answer(indexed, copy)

    monkeypatch.setattr("jcodemunch_mcp.tools.resolve_repo.resolve_repo", fake)
    monkeypatch.chdir(copy)
    looked = server._startup_lookup(timeout=10)
    with caplog.at_level("WARNING"):
        assert server._indexed_here(looked=looked) is None
        assert server._separate_copy_here(looked) is True
    assert len(calls) == 1
    assert str(indexed) in caplog.text  # the log names the folder the text cannot


def test_startup_says_nothing_new_for_an_unindexed_folder(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(
        "jcodemunch_mcp.tools.resolve_repo.resolve_repo", lambda path, storage_path=None: {"indexed": False}
    )
    monkeypatch.chdir(tmp_path)
    with caplog.at_level("WARNING"):
        assert server._separate_copy_here(server._startup_lookup(timeout=10)) is False
    assert "separate copy" not in caplog.text


# --- the PreToolUse hook -----------------------------------------------------


def _hook(payload: dict) -> str:
    out = io.StringIO()
    with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))), mock.patch.object(sys, "stdout", out):
        assert steering.run_pretooluse() == 0
    return out.getvalue()


@pytest.fixture
def hook_env(monkeypatch, tmp_path, two_checkouts):
    indexed, copy = two_checkouts
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / "store"))
    monkeypatch.setenv("JCODEMUNCH_ENFORCE", "advisory")
    monkeypatch.setattr(steering, "_indexed_source_roots", lambda: [steering._norm_path(str(indexed))])
    calls = []

    def fake(path, storage_path=None):
        calls.append(path)
        return _mismatch_answer(indexed, Path(path))

    monkeypatch.setattr("jcodemunch_mcp.tools.resolve_repo.resolve_repo", fake)
    return indexed, copy, calls


def _grep(cwd: Path, session: str = "s1") -> dict:
    return {"session_id": session, "cwd": str(cwd), "tool_name": "Grep", "tool_input": {"pattern": "x"}}


def test_the_hook_says_it_once_per_session_and_folder(hook_env):
    indexed, copy, calls = hook_env
    first = _hook(_grep(copy))
    assert str(indexed) in json.loads(first)["hookSpecificOutput"]["additionalContext"]
    assert _hook(_grep(copy)) == ""  # said already, and no second lookup
    assert len(calls) == 1
    again = json.loads(_hook(_grep(copy, session="s2")))  # a new session hears it once
    assert str(indexed) in again["hookSpecificOutput"]["additionalContext"]
    assert len(calls) == 2


def test_the_hook_skips_the_lookup_inside_an_indexed_folder(hook_env):
    indexed, _, calls = hook_env
    out = _hook(_grep(indexed))
    assert "separate copy" not in out
    _hook(_grep(indexed))
    assert calls == []
    # SECURITY.md says a marker is written in every folder, indexed or not: hold it to that.
    assert steering._separate_copy_marker("s1", str(indexed)).exists()


def test_the_hook_without_a_session_id_does_nothing(hook_env):
    _, copy, calls = hook_env
    payload = _grep(copy)
    del payload["session_id"]
    assert _hook(payload) == ""
    assert calls == []


def test_off_means_silent(hook_env, monkeypatch):
    _, copy, calls = hook_env
    monkeypatch.setenv("JCODEMUNCH_ENFORCE", "off")
    assert _hook(_grep(copy)) == ""
    assert calls == []


def test_old_markers_are_pruned(hook_env, tmp_path):
    import os

    _, copy, _ = hook_env
    folder = tmp_path / "store" / "hook_state" / "separate_copy"
    folder.mkdir(parents=True)
    stale = folder / "stale"
    stale.write_text("")
    old = 1_000_000_000
    os.utime(stale, (old, old))
    _hook(_grep(copy))
    assert not stale.exists()


# --- end to end: two real git checkouts, the real resolve_repo ------------------


def _real_copy(tmp_path, monkeypatch):
    """An indexed git checkout and a second copy of it elsewhere, nothing mocked."""
    import shutil
    import subprocess

    from jcodemunch_mcp.tools.index_folder import index_folder

    store = tmp_path / "store"
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    monkeypatch.setenv("JCODEMUNCH_TRUSTED_FOLDERS", str(tmp_path))
    first = tmp_path / "proj"
    first.mkdir()
    (first / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    ident = ["-c", "user.email=t@example.invalid", "-c", "user.name=t"]
    for args in (["init", "-q"], ident + ["add", "."], ident + ["commit", "-qm", "x"],
                 ["remote", "add", "origin", "https://github.com/example/proj.git"]):
        subprocess.run(["git", *args], cwd=first, check=True, capture_output=True)
    out = index_folder(str(first), use_ai_summaries=False, storage_path=str(store))
    assert out.get("success"), out
    second = tmp_path / "elsewhere" / "proj"
    shutil.copytree(first, second)
    return first, second


def test_a_real_second_checkout_is_named_at_startup(tmp_path, monkeypatch, caplog):
    first, second = _real_copy(tmp_path, monkeypatch)
    monkeypatch.chdir(second)
    looked = server._startup_lookup(timeout=30)
    with caplog.at_level("WARNING"):
        assert server._indexed_here(looked=looked) is None
        assert server._separate_copy_here(looked) is True
    assert str(first.resolve()) in caplog.text or str(first) in caplog.text


def test_a_real_second_checkout_is_named_by_the_hook(tmp_path, monkeypatch):
    first, second = _real_copy(tmp_path, monkeypatch)
    monkeypatch.setenv("JCODEMUNCH_ENFORCE", "advisory")
    monkeypatch.setattr(steering, "_SEPARATE_COPY_TIMEOUT", 30)
    note = json.loads(_hook(_grep(second)))["hookSpecificOutput"]["additionalContext"]
    assert "separate copy of example/proj" in note
    assert _hook(_grep(second)) == ""
