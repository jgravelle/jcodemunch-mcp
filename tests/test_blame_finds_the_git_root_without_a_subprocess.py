"""The blame provider asks the git-root authority instead of spawning git (ledger L-209).

`GitBlameProvider.detect` ran `git rev-parse --show-toplevel` with a hard-coded
5 s timeout inside a bare `except Exception: pass`. On a loaded machine the
probe timed out, `detect()` said "not a repository", and an opted-in blame
silently did not run, with nothing in the log or in `providers_skipped`.
`storage.git_root._find_git_root` already answers the question with a
filesystem walk that spawns nothing, so the provider asks it.

The tests model the loaded machine by making every `git` subprocess time out:
the old probe then read a real repository as no repository.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jcodemunch_mcp import config as config_module
from jcodemunch_mcp.parser.context import git_blame
from jcodemunch_mcp.parser.context.git_blame import GitBlameProvider


def _repo_with_subfolder(root: Path) -> Path:
    (root / ".git").mkdir(parents=True)
    sub = root / "pkg" / "inner"
    sub.mkdir(parents=True)
    return sub


@pytest.fixture
def blame_on(monkeypatch):
    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "git_blame_enabled", True)  # type: ignore[attr-defined]


@pytest.fixture
def git_times_out(monkeypatch):
    real_run = subprocess.run

    def slow_run(args, *a, **k):
        if args and args[0] == "git":
            raise subprocess.TimeoutExpired(args, k.get("timeout", 5))
        return real_run(args, *a, **k)

    monkeypatch.setattr(git_blame.subprocess, "run", slow_run)


def test_a_subfolder_of_a_repository_is_detected_when_git_is_slow(tmp_path, blame_on, git_times_out):
    sub = _repo_with_subfolder(tmp_path / "repo")
    assert GitBlameProvider().detect(sub) is True


def test_a_subfolder_of_a_real_repository_is_detected(tmp_path, blame_on):
    """A real `git init` repo, so this passes on the old code too: it pins that
    the walk agrees with git on the ordinary case."""
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True, stdin=subprocess.DEVNULL)
    sub = root / "pkg" / "inner"
    sub.mkdir(parents=True)
    assert GitBlameProvider().detect(sub) is True


def test_a_folder_in_no_repository_is_not_detected(tmp_path, blame_on):
    """The real walk, unmocked (review: mocking the authority asserted its own
    stub). Skips, saying why, on a box whose temp dir sits inside a checkout."""
    plain = tmp_path / "plain"
    plain.mkdir()
    if any((p / ".git").exists() for p in (plain, *plain.resolve().parents)):
        pytest.skip("the temp dir is inside a git checkout on this box")
    assert GitBlameProvider().detect(plain) is False


def test_blame_off_detects_nothing_and_walks_nothing(tmp_path, monkeypatch):
    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "git_blame_enabled", False)  # type: ignore[attr-defined]
    calls = []
    monkeypatch.setattr(git_blame, "_find_git_root", lambda p: calls.append(p))
    sub = _repo_with_subfolder(tmp_path / "repo")
    assert GitBlameProvider().detect(sub) is False
    assert calls == []


def test_the_provider_spawns_no_process_to_find_the_root(tmp_path, blame_on, monkeypatch):
    spawned = []
    real_run = subprocess.run

    def recording_run(args, *a, **k):
        spawned.append(args)
        return real_run(args, *a, **k)

    monkeypatch.setattr(git_blame.subprocess, "run", recording_run)
    sub = _repo_with_subfolder(tmp_path / "repo")
    GitBlameProvider().detect(sub)
    assert not [a for a in spawned if a and a[0] == "git" and "rev-parse" in a]
