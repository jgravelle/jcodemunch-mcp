"""A linked worktree's git-mode index resolves to itself (#882).

A linked worktree is keyed `local/<name>-<hash>` in git mode (#372), which is
also the key the local-identity probe looks up. Both probes of
`resolve_index_identity` therefore found the SAME index, and the resolver read
that as two indexes: config mode raised `IdentityModeAmbiguous`, and explicit
git mode raised `IdentityModeConflict` naming the git index as a local one.
Every re-index of the worktree after its first failed, the watcher's included.

Two DIFFERENT indexes matching one path stay ambiguous; that is
`test_identity_mode.py::test_both_identity_forms_are_ambiguous`.
"""

import subprocess
from pathlib import Path

import pytest

from jcodemunch_mcp import config as config_module
from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.storage import git_root


def _git(*args, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


@pytest.fixture
def worktree(tmp_path, monkeypatch):
    monkeypatch.setattr(
        config_module,
        "get",
        lambda key, default=None, repo=None: None if key == "identity_mode"
        else False if key == "git_root_identity" else default,
    )
    main = tmp_path / "main-checkout"
    main.mkdir()
    _git("init", cwd=main)
    _git("remote", "add", "origin", "https://github.com/elastic/kibana.git", cwd=main)
    (main / "main.py").write_text("def hello(): pass\n", encoding="utf-8")
    _git("add", "main.py", cwd=main)
    _git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "init", cwd=main)
    wt = tmp_path / "feature-wt"
    _git("worktree", "add", str(wt), cwd=main)
    assert git_root.is_linked_worktree(wt.resolve())
    return wt.resolve(), tmp_path / "store"


def _index(path: Path, store_path: Path, mode: str) -> dict:
    from jcodemunch_mcp.tools.index_folder import index_folder

    return index_folder(
        str(path),
        use_ai_summaries=False,
        storage_path=str(store_path),
        context_providers=False,
        identity_mode=mode,
    )


def _first_git_index(wt: Path, store_path: Path) -> str:
    first = _index(wt, store_path, "git")
    assert first["success"] is True, first
    assert first["repo"].startswith("local/feature-wt-")
    return first["repo"]


def test_config_mode_resolves_the_worktree_index(worktree):
    wt, store_path = worktree
    repo = _first_git_index(wt, store_path)

    decision = git_root.resolve_index_identity(
        str(wt), mode="config", store=IndexStore(base_path=str(store_path))
    )

    assert f"{decision.owner}/{decision.name}" == repo
    assert decision.mode == "git"
    assert decision.git_root == str(wt)


def test_explicit_git_mode_resolves_the_worktree_index(worktree):
    wt, store_path = worktree
    repo = _first_git_index(wt, store_path)

    decision = git_root.resolve_index_identity(
        str(wt), mode="git", store=IndexStore(base_path=str(store_path))
    )

    assert f"{decision.owner}/{decision.name}" == repo
    assert decision.mode == "git"


@pytest.mark.parametrize("mode", ["config", "git"])
def test_reindexing_the_worktree_succeeds(worktree, mode):
    wt, store_path = worktree
    repo = _first_git_index(wt, store_path)
    (wt / "extra.py").write_text("def more(): pass\n", encoding="utf-8")

    second = _index(wt, store_path, mode)

    assert second["success"] is True, second
    assert second["repo"] == repo


def test_explicit_local_mode_still_refuses_and_names_git(worktree):
    wt, store_path = worktree
    _first_git_index(wt, store_path)

    with pytest.raises(git_root.IdentityModeConflict, match="uses git identity"):
        git_root.resolve_index_identity(
            str(wt), mode="local", store=IndexStore(base_path=str(store_path))
        )
