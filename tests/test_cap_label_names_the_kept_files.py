"""L-305: a capped call over a command-line index names both caps behind its counts.

The command line indexed 5 of 8 files (`cli_max_folder_files` = 5). An agent's
re-index then ran under `max_folder_files` = 3 and kept the 2 files past its cap.
Its warning read "8 files discovered, 5 indexed, 3 dropped (max_folder_files=3)":
5 indexed beside a cap of 3, with nothing saying 2 of the 5 were kept from the
command line's index. The counts now say which cap they come from.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import jcodemunch_mcp.config as config_module

N = 8
LABEL = "max_folder_files=3 on the last whole-tree index; 2 more files kept from an earlier index"


@pytest.fixture
def cli_five(tmp_path, monkeypatch):
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / ".idx"))
    monkeypatch.setenv("JCODEMUNCH_USE_AI_SUMMARIES", "false")
    monkeypatch.delenv("JCODEMUNCH_MAX_FOLDER_FILES", raising=False)
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    for i in range(N):
        (root / "pkg" / f"m{i}.py").write_text(f"def fn{i}():\n    return {i}\n", encoding="utf-8")
    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "max_folder_files", 3)
    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "cli_max_folder_files", 5)
    from jcodemunch_mcp.cli.init import run_index
    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools.resolve_repo import resolve_repo

    cwd = Path.cwd()
    os.chdir(root)
    try:
        run_index()
    finally:
        os.chdir(cwd)
    repo = resolve_repo(str(root))["repo"]
    owner, name = repo.split("/", 1)
    assert len(IndexStore().load_index(owner, name).source_files) == 5, "precondition: the CLI cap cut 3"
    return root


def _agent(root):
    from jcodemunch_mcp.tools.index_folder import index_folder

    return index_folder(str(root), use_ai_summaries=False)


def _cap_line(res):
    lines = [w for w in res.get("warnings") or [] if w.startswith("File cap reached:")]
    assert len(lines) == 1, res.get("warnings")
    return lines[0]


def test_the_index_folder_warning_names_the_kept_files(cli_five):
    line = _cap_line(_agent(cli_five))
    assert "8 files discovered, 5 indexed, 3 dropped" in line, line
    assert f"({LABEL})" in line, line


def test_resolve_repo_names_the_kept_files(cli_five):
    from jcodemunch_mcp.tools.resolve_repo import resolve_repo

    _agent(cli_five)
    warning = resolve_repo(str(cli_five))["truncation_warning"]
    assert f"({LABEL})" in warning, warning


def test_the_label_helper():
    """The label reads the kept count the save recorded, never indexed minus cap."""
    from jcodemunch_mcp.security import cap_setting

    assert cap_setting({"max_folder_files": 3, "files_indexed": 3}) == "max_folder_files=3"
    assert cap_setting({"max_folder_files": 3, "files_indexed": 5, "files_kept": 2}) == LABEL
    assert cap_setting({"max_folder_files": 3, "files_indexed": 4, "files_kept": 1}) == (
        "max_folder_files=3 on the last whole-tree index; 1 more file kept from an earlier index"
    )
    # More indexed than the cap with no kept count: a promoted watcher file, not a kept one.
    assert cap_setting({"max_folder_files": 3, "files_indexed": 4}) == "max_folder_files=3"
    assert cap_setting({"max_folder_files": 5, "files_indexed": 5, "cap_key": "cli_max_folder_files"}) == (
        "cli_max_folder_files=5"
    )
    # A count it can't read is not a claim about kept files.
    assert cap_setting({"max_folder_files": 3}) == "max_folder_files=3"
    assert cap_setting({"max_folder_files": 3, "files_kept": "2"}) == "max_folder_files=3"
    assert cap_setting({"max_folder_files": 3, "files_kept": 0}) == "max_folder_files=3"
    # init's own spacing.
    assert cap_setting({"max_folder_files": 3}, sep=" = ") == "max_folder_files = 3"


def test_an_agent_index_within_its_own_cap_keeps_the_plain_label(tmp_path, monkeypatch):
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / ".idx"))
    monkeypatch.setenv("JCODEMUNCH_USE_AI_SUMMARIES", "false")
    monkeypatch.delenv("JCODEMUNCH_MAX_FOLDER_FILES", raising=False)
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    for i in range(N):
        (root / "pkg" / f"m{i}.py").write_text(f"def fn{i}():\n    return {i}\n", encoding="utf-8")
    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "max_folder_files", 3)
    line = _cap_line(_agent(root))
    assert "8 files discovered, 3 indexed, 5 dropped (max_folder_files=3)." in line, line


def _raise_parser_generation(monkeypatch, before) -> None:
    """A parser upgrade due through the real gate (as in
    test_the_file_cap_limits_a_call_not_the_index.py)."""
    from jcodemunch_mcp.storage import index_store, sqlite_store
    from jcodemunch_mcp.tools import index_folder as mod

    bumped = int(before or 0) + 1
    monkeypatch.setattr(index_store, "PARSER_GENERATION", bumped)
    monkeypatch.setattr(sqlite_store, "_PARSER_GENERATION", bumped)
    monkeypatch.setattr(mod, "PARSER_GENERATION", bumped)


def test_a_promoted_watcher_file_is_not_called_kept(tmp_path, monkeypatch):
    """Review of L-305: an agent index capped at 3 of 8, an upgrade due, one
    watcher event on m6. m6 is parsed on that run, so 4 indexed beside a cap of 3
    is a promotion, and nothing was kept from an earlier index."""
    from jcodemunch_mcp.reindex_state import WatcherChange
    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools.index_folder import index_folder
    from jcodemunch_mcp.tools.resolve_repo import resolve_repo

    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / ".idx"))
    monkeypatch.setenv("JCODEMUNCH_USE_AI_SUMMARIES", "false")
    monkeypatch.delenv("JCODEMUNCH_MAX_FOLDER_FILES", raising=False)
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    for i in range(N):
        (root / "pkg" / f"m{i}.py").write_text(f"def fn{i}():\n    return {i}\n", encoding="utf-8")
    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "max_folder_files", 3)
    repo = index_folder(str(root), use_ai_summaries=False)["repo"]
    owner, name = repo.split("/", 1)
    _raise_parser_generation(monkeypatch, getattr(IndexStore().load_index(owner, name), "parser_generation", None))
    m6 = root / "pkg" / "m6.py"
    m6.write_text(m6.read_text(encoding="utf-8") + "\ndef added6():\n    return 1\n", encoding="utf-8")
    res = index_folder(
        str(root), use_ai_summaries=False, incremental=True,
        changed_paths=[WatcherChange("modified", str(m6), "__cache_miss__")],
    )
    cap = IndexStore().load_index(owner, name).file_cap_status
    assert cap.get("files_indexed") == 4, "precondition: m6 was promoted"
    for text in [w for w in res.get("warnings") or [] if w.startswith("File cap reached:")] + [
        resolve_repo(str(root))["truncation_warning"]
    ]:
        assert "kept from an earlier index" not in text, text
        assert "(max_folder_files=3)" in text, text


def test_init_names_the_kept_files_when_max_folder_files_binds_the_cli(tmp_path, monkeypatch):
    """Review of L-305: the user lowers max_folder_files to 3 and leaves the CLI
    key at its default, so max_folder_files binds `init` too, over an index of
    all 8 files. `init`'s summary said "max_folder_files = 3 ... 7 of 8 files"."""
    from jcodemunch_mcp.cli.init import run_index
    from jcodemunch_mcp.security import DEFAULT_CLI_MAX_FOLDER_FILES, get_cli_folder_cap

    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / ".idx"))
    monkeypatch.setenv("JCODEMUNCH_USE_AI_SUMMARIES", "false")
    monkeypatch.delenv("JCODEMUNCH_MAX_FOLDER_FILES", raising=False)
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    for i in range(N):
        (root / "pkg" / f"m{i}.py").write_text(f"def fn{i}():\n    return {i}\n", encoding="utf-8")
    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "max_folder_files", 3)
    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "cli_max_folder_files", 10)
    cwd = Path.cwd()
    os.chdir(root)
    try:
        run_index()
        monkeypatch.setitem(config_module._GLOBAL_CONFIG, "cli_max_folder_files", DEFAULT_CLI_MAX_FOLDER_FILES)
        assert get_cli_folder_cap(str(root)) == (3, "max_folder_files"), "precondition"
        m6 = root / "pkg" / "m6.py"
        m6.write_text(m6.read_text(encoding="utf-8") + "\ndef added6():\n    return 1\n", encoding="utf-8")
        line = run_index()
    finally:
        os.chdir(cwd)
    assert "the file cap (max_folder_files = 3 on the last whole-tree index; 4 more files kept from an earlier index) was reached" in line, line
