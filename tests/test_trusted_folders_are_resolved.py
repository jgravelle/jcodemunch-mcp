"""A trusted_folders entry is compared as the folder it names.

``index_folder`` resolves the folder it is asked to index (symlinks, ``..``)
and then compared it with the trusted_folders entries exactly as written. An
entry that reaches the folder through a symlink (macOS's ``/var`` is a link to
``/private/var``, and so is every ``tempfile`` directory there) or through a
``..`` segment never matched: in the default whitelist mode a folder the user
trusted was refused, and in blacklist mode a folder the user listed as
untrusted was indexed. The entries are resolved the same way now.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from jcodemunch_mcp.tools import index_folder as index_folder_module
from jcodemunch_mcp.tools.index_folder import _is_trusted


@pytest.fixture(autouse=True)
def _fresh_cache():
    _is_trusted.cache_clear()
    yield
    _is_trusted.cache_clear()


def _project(tmp_path):
    proj = tmp_path / "work" / "proj"
    (proj / "x").mkdir(parents=True)
    (proj / "main.py").write_text("def hello():\n    return 1\n", encoding="utf-8")
    return proj


def _symlink_or_skip(link, target):
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"this platform cannot create a directory symlink here: {exc}")


def test_an_entry_with_a_dotdot_segment_trusts_the_folder_it_names(tmp_path):
    proj = _project(tmp_path)
    entry = str(proj / "x" / "..")
    assert _is_trusted(proj.resolve(), (entry,), True)
    assert not _is_trusted(proj.resolve(), (entry,), False)


def test_an_entry_through_a_symlink_trusts_the_folder_it_names(tmp_path):
    proj = _project(tmp_path)
    link = tmp_path / "link"
    _symlink_or_skip(link, proj)
    assert _is_trusted(proj.resolve(), (str(link),), True)
    assert _is_trusted((proj / "x").resolve(), (str(link),), True)


def test_blacklist_mode_refuses_a_folder_listed_through_a_symlink(tmp_path):
    proj = _project(tmp_path)
    link = tmp_path / "link"
    _symlink_or_skip(link, proj)
    assert not _is_trusted(proj.resolve(), (str(link),), False)


def test_an_unrelated_entry_still_does_not_trust_the_folder(tmp_path):
    proj = _project(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    assert not _is_trusted(proj.resolve(), (str(other),), True)
    assert not _is_trusted(proj.resolve(), (str(other / ".." / "elsewhere"),), True)


@pytest.mark.parametrize("entry", ["", ".", "src", "x/..", "C:rel"])
def test_a_relative_entry_trusts_nothing(tmp_path, monkeypatch, entry):
    # Resolving it would read it against the server's working directory.
    proj = _project(tmp_path)
    monkeypatch.chdir(proj)
    assert not _is_trusted(proj.resolve(), (entry,), True)
    assert not _is_trusted((proj / "x").resolve(), (entry,), True)
    assert _is_trusted(proj.resolve(), (entry,), False)


def test_an_entry_naming_no_home_directory_trusts_nothing(tmp_path, monkeypatch):
    # POSIX raises RuntimeError for a `~name` naming no user; Windows maps it to
    # a sibling profile instead, so the raise is forced here to run everywhere.
    proj = _project(tmp_path).resolve()

    def no_home(self):
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(index_folder_module.Path, "expanduser", no_home)
    entry = "~no-such-user-jcm-test/proj"
    assert not _is_trusted(proj, (entry,), True)
    assert _is_trusted(proj, (entry,), False)


def test_env_entries_reach_the_comparison_as_written(tmp_path, monkeypatch):
    # The file path resolves entries at load; the env path does not, which is
    # why the comparison has to.
    from jcodemunch_mcp import config as config_module

    proj = _project(tmp_path)
    entry = str(proj / "x" / "..")
    monkeypatch.setenv("JCODEMUNCH_TRUSTED_FOLDERS", entry)
    monkeypatch.setattr(config_module, "_GLOBAL_CONFIG", {})
    config_module._apply_env_var_fallback(set())
    entries = config_module._GLOBAL_CONFIG["trusted_folders"]
    assert entries == [entry]
    assert _is_trusted(proj.resolve(), tuple(entries), True)


def _config_get(entries, whitelist):
    def get(key, default=None, repo=None):
        if key == "trusted_folders":
            return entries
        if key == "trusted_folders_whitelist_mode":
            return whitelist
        return default
    return get


@pytest.mark.parametrize("whitelist,expect_success", [(True, True), (False, False)])
def test_index_folder_honours_an_entry_that_names_the_folder_another_way(tmp_path, whitelist, expect_success):
    proj = _project(tmp_path)
    entry = str(proj / "x" / "..")
    with (
        patch.object(index_folder_module._config, "load_project_config", return_value=None),
        patch.object(index_folder_module._config, "get", side_effect=_config_get([entry], whitelist)),
    ):
        result = index_folder_module.index_folder(
            str(proj), use_ai_summaries=False, storage_path=str(tmp_path / "store")
        )
    assert result["success"] is expect_success, result
    if not expect_success:
        assert "trusted_folders" in result["error"]
