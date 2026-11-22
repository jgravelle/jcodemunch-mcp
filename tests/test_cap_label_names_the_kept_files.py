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
LABEL = "max_folder_files=3 on the last run; 2 more files kept from an earlier index"


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


def test_the_label_helper(cli_five):
    from jcodemunch_mcp.security import cap_setting

    assert cap_setting({"max_folder_files": 3, "files_indexed": 3}) == "max_folder_files=3"
    assert cap_setting({"max_folder_files": 3, "files_indexed": 5}) == LABEL
    assert cap_setting({"max_folder_files": 3, "files_indexed": 4}) == (
        "max_folder_files=3 on the last run; 1 more file kept from an earlier index"
    )
    assert cap_setting({"max_folder_files": 5, "files_indexed": 5, "cap_key": "cli_max_folder_files"}) == (
        "cli_max_folder_files=5"
    )
    # A count it can't read is not a claim about kept files.
    assert cap_setting({"max_folder_files": 3}) == "max_folder_files=3"
    assert cap_setting({"max_folder_files": 3, "files_indexed": "5"}) == "max_folder_files=3"


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
