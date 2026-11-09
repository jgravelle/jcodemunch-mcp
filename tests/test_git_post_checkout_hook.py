"""`init --git-hook`: a git post-checkout hook that re-indexes changed files.

After a branch switch the index describes the branch you left until something
re-indexes it. The hook asks git which files differ between the two commits and
re-indexes only those, in the background. These tests pin the four places it
can go wrong:

* the installer: where the file goes (`core.hooksPath`), and that a user's own
  hook keeps every line, including one that ends in `exit`;
* the command the hook runs: which paths it hands the indexer, and that it
  never builds a first index from a partial list;
* the hook as git runs it, through a real `git checkout`;
* `init` and `uninstall`: nothing is written unless `--git-hook` is typed.
"""

from __future__ import annotations

import os
import shlex
import stat
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jcodemunch_mcp.cli import git_hook
from jcodemunch_mcp.cli import init as init_module


@pytest.fixture(autouse=True)
def _isolated_git_config(monkeypatch, tmp_path):
    # A developer's global core.hooksPath would move every hook these tests
    # write into the developer's own hooks directory.
    empty = tmp_path / "empty.gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(var, raising=False)


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@example.com",
            "-c",
            "user.name=t",
            "-c",
            "commit.gpgsign=false",
            *args,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    return repo


def _hook_path(repo: Path) -> Path:
    return repo / ".git" / "hooks" / "post-checkout"


# ---------------------------------------------------------------- installer


def test_install_writes_one_executable_block(tmp_path):
    repo = _repo(tmp_path)
    msg = git_hook.install_post_checkout(str(repo))
    hook = _hook_path(repo)
    assert hook.exists(), msg
    text = hook.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh\n")
    assert text.count(git_hook.BLOCK_BEGIN) == 1
    assert "hook-post-checkout" in text
    if os.name != "nt":
        assert hook.stat().st_mode & stat.S_IXUSR

    git_hook.install_post_checkout(str(repo))
    assert hook.read_text(encoding="utf-8").count(git_hook.BLOCK_BEGIN) == 1


def test_install_honors_core_hooks_path(tmp_path):
    repo = _repo(tmp_path)
    _git(repo, "config", "core.hooksPath", "githooks")
    git_hook.install_post_checkout(str(repo))
    assert (repo / "githooks" / "post-checkout").exists()
    assert not _hook_path(repo).exists()


def test_install_keeps_a_user_hook_and_runs_before_its_exit(tmp_path):
    repo = _repo(tmp_path)
    hook = _hook_path(repo)
    hook.parent.mkdir(parents=True, exist_ok=True)
    user = "#!/usr/bin/env bash\necho user-line-1\nexit 0\n"
    hook.write_text(user, encoding="utf-8")

    git_hook.install_post_checkout(str(repo), backup=False)
    text = hook.read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash\n")
    assert "echo user-line-1\nexit 0\n" in text
    # The block sits ahead of the user's `exit`, or the exit would skip it.
    assert text.index(git_hook.BLOCK_BEGIN) < text.index("exit 0")


def test_install_leaves_a_non_shell_hook_alone(tmp_path):
    repo = _repo(tmp_path)
    hook = _hook_path(repo)
    hook.parent.mkdir(parents=True, exist_ok=True)
    user = "#!/usr/bin/env python3\nprint('hi')\n"
    hook.write_text(user, encoding="utf-8")

    msg = git_hook.install_post_checkout(str(repo))
    assert hook.read_text(encoding="utf-8") == user
    assert "python3" in msg


def test_install_outside_a_git_repo_writes_nothing(tmp_path):
    folder = tmp_path / "plain"
    folder.mkdir()
    msg = git_hook.install_post_checkout(str(folder))
    assert "not a git" in msg.lower()
    assert not list(folder.rglob("post-checkout"))


def test_dry_run_writes_nothing(tmp_path):
    repo = _repo(tmp_path)
    msg = git_hook.install_post_checkout(str(repo), dry_run=True)
    assert "would" in msg
    assert not _hook_path(repo).exists()


def test_uninstall_removes_the_block_and_keeps_user_lines(tmp_path):
    repo = _repo(tmp_path)
    hook = _hook_path(repo)
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
    git_hook.install_post_checkout(str(repo), backup=False)

    git_hook.uninstall_post_checkout(str(repo), backup=False)
    assert hook.read_text(encoding="utf-8") == "#!/bin/sh\necho mine\n"


def test_uninstall_deletes_a_file_it_created(tmp_path):
    repo = _repo(tmp_path)
    git_hook.install_post_checkout(str(repo))
    git_hook.uninstall_post_checkout(str(repo), backup=False)
    assert not _hook_path(repo).exists()


# ------------------------------------------------------- hook-post-checkout


def _two_branches(tmp_path: Path) -> tuple[Path, str, str]:
    repo = _repo(tmp_path)
    (repo / "keep.py").write_text("def keep():\n    return 1\n", encoding="utf-8")
    (repo / "gone.py").write_text("def gone():\n    return 2\n", encoding="utf-8")
    (repo / "old_name.py").write_text("def moved():\n    return 3\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")

    _git(repo, "switch", "-q", "-c", "feature")
    (repo / "keep.py").write_text(
        "def keep():\n    return 1\n\ndef added():\n    return 4\n", encoding="utf-8"
    )
    _git(repo, "rm", "-q", "gone.py")
    _git(repo, "mv", "old_name.py", "new_name.py")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feature")
    feature = _git(repo, "rev-parse", "HEAD")
    _git(repo, "switch", "-q", "main")
    return repo, base, feature


def _index(repo: Path, store: Path):
    from jcodemunch_mcp.tools.index_folder import index_folder

    result = index_folder(
        path=str(repo), use_ai_summaries=False, storage_path=str(store)
    )
    assert result.get("success"), result


def test_hook_hands_the_indexer_exactly_the_changed_paths(tmp_path):
    repo, base, feature = _two_branches(tmp_path)
    store = tmp_path / "store"
    _index(repo, store)
    _git(repo, "switch", "-q", "feature")

    spy = MagicMock(return_value={"success": True})
    rc = git_hook.run_post_checkout(
        base, feature, "1", cwd=str(repo), storage_path=str(store), indexer=spy
    )
    assert rc == 0
    assert spy.call_count == 1
    kwargs = spy.call_args.kwargs
    assert sorted(kwargs["paths"]) == [
        "gone.py",
        "keep.py",
        "new_name.py",
        "old_name.py",
    ]
    assert kwargs["use_ai_summaries"] is False
    assert Path(kwargs["path"]).resolve() == repo.resolve()


def test_hook_brings_the_index_to_the_checked_out_branch(tmp_path):
    repo, base, feature = _two_branches(tmp_path)
    store = tmp_path / "store"
    _index(repo, store)
    _git(repo, "switch", "-q", "feature")

    assert (
        git_hook.run_post_checkout(
            base, feature, "1", cwd=str(repo), storage_path=str(store)
        )
        == 0
    )

    from jcodemunch_mcp.storage.sqlite_store import SQLiteIndexStore

    st = SQLiteIndexStore(base_path=str(store))
    rows = [
        r
        for r in st.list_repos()
        if Path(r.get("source_root") or "").resolve() == repo.resolve()
    ]
    assert len(rows) == 1
    owner, name = rows[0]["repo"].split("/", 1)
    index = st.load_index(owner, name)
    files = set(index.source_files)
    assert files == {"keep.py", "new_name.py"}
    names = {s["name"] for s in index.symbols}
    assert {"keep", "added", "moved"} <= names
    assert "gone" not in names


@pytest.mark.parametrize(
    "prev,new,flag",
    [
        ("A", "B", "0"),  # a file checkout, not a branch checkout
        ("A", "A", "1"),  # nothing moved
        ("0" * 40, "B", "1"),  # a clone: no previous commit
    ],
)
def test_hook_skips_what_is_not_a_branch_move(tmp_path, prev, new, flag):
    spy = MagicMock()
    assert (
        git_hook.run_post_checkout(
            prev,
            new,
            flag,
            cwd=str(tmp_path),
            storage_path=str(tmp_path / "s"),
            indexer=spy,
        )
        == 0
    )
    assert not spy.called


def test_hook_never_builds_a_first_index(tmp_path):
    repo, base, feature = _two_branches(tmp_path)
    _git(repo, "switch", "-q", "feature")
    spy = MagicMock()
    assert (
        git_hook.run_post_checkout(
            base,
            feature,
            "1",
            cwd=str(repo),
            storage_path=str(tmp_path / "store"),
            indexer=spy,
        )
        == 0
    )
    assert not spy.called


def test_hook_exits_zero_when_the_indexer_raises(tmp_path):
    repo, base, feature = _two_branches(tmp_path)
    store = tmp_path / "store"
    _index(repo, store)
    _git(repo, "switch", "-q", "feature")
    boom = MagicMock(side_effect=RuntimeError("boom"))
    assert (
        git_hook.run_post_checkout(
            base, feature, "1", cwd=str(repo), storage_path=str(store), indexer=boom
        )
        == 0
    )


# --------------------------------------------- the hook as git runs it


def test_a_real_checkout_runs_the_installed_hook(tmp_path, monkeypatch):
    """git runs the block, and sh reads the executable path as one word.

    The stub lives under a directory with a space, an apostrophe and a `$`,
    the characters a hand-quoted path gets wrong, and the block calls it by path
    the way it calls the real executable.
    """
    repo, base, feature = _two_branches(tmp_path)
    out = tmp_path / "called.txt"
    odd = tmp_path / "it's a $dir"
    odd.mkdir()
    stub = odd / "jcodemunch-mcp"
    stub.write_text(f'#!/bin/sh\necho "$@" > "{out.as_posix()}"\n', encoding="utf-8")
    stub.chmod(0o755)
    monkeypatch.setattr(git_hook, "_resolve_exe", lambda: stub.as_posix())
    git_hook.install_post_checkout(str(repo))

    _git(repo, "switch", "-q", "feature")
    deadline = time.monotonic() + 15
    while not out.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert out.exists(), "the post-checkout hook did not run the command"
    assert out.read_text(encoding="utf-8").split() == [
        "hook-post-checkout",
        base,
        feature,
    ]


def _lf_only(data: bytes) -> bool:
    return b"\n" in data.replace(b"\r\n", b"")


def test_a_hook_without_a_shebang_comes_back_byte_identical(tmp_path):
    repo = _repo(tmp_path)
    hook = _hook_path(repo)
    hook.parent.mkdir(parents=True, exist_ok=True)
    user = b"echo no-shebang\r\nexit 0\r\n"
    hook.write_bytes(user)

    git_hook.install_post_checkout(str(repo), backup=False)
    installed = hook.read_bytes()
    assert not installed.startswith(b"#!")
    assert installed.index(git_hook.BLOCK_BEGIN.encode()) == 0
    # The block follows the file's own line endings.
    assert not _lf_only(installed)

    git_hook.uninstall_post_checkout(str(repo), backup=False)
    assert hook.read_bytes() == user


def test_crlf_endings_survive_install_and_uninstall(tmp_path):
    repo = _repo(tmp_path)
    hook = _hook_path(repo)
    hook.parent.mkdir(parents=True, exist_ok=True)
    user = b"#!/bin/sh\r\necho mine\r\n"
    hook.write_bytes(user)
    git_hook.install_post_checkout(str(repo), backup=False)
    assert not _lf_only(hook.read_bytes())
    git_hook.uninstall_post_checkout(str(repo), backup=False)
    assert hook.read_bytes() == user


def test_env_dash_s_bash_counts_as_a_shell(tmp_path):
    assert git_hook._shebang_shell("#!/usr/bin/env -S bash -e") == "bash"
    repo = _repo(tmp_path)
    hook = _hook_path(repo)
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text("#!/usr/bin/env -S bash -e\necho mine\n", encoding="utf-8")
    git_hook.install_post_checkout(str(repo), backup=False)
    assert git_hook.BLOCK_BEGIN in hook.read_text(encoding="utf-8")


def test_the_executable_path_is_shell_quoted(monkeypatch):
    monkeypatch.setattr(
        git_hook, "_resolve_exe", lambda: "C:/it's a $dir/jcodemunch-mcp.exe"
    )
    assert git_hook._hook_invocation() == shlex.quote(
        "C:/it's a $dir/jcodemunch-mcp.exe"
    )
    assert git_hook._hook_invocation() in git_hook._block()


# ------------------------------------------------------- init / uninstall


@pytest.fixture
def _quiet_init(monkeypatch):
    spies = {
        name: MagicMock(return_value="  (spy)")
        for name in (
            "install_claude_md",
            "install_cursor_rules",
            "install_windsurf_rules",
            "install_agents_md",
            "install_hooks",
            "install_enforcement_hooks",
            "install_copilot_hooks",
            "run_index",
        )
    }
    spies["run_audit"] = MagicMock(return_value=iter([]))
    for name, mock in spies.items():
        monkeypatch.setattr(init_module, name, mock)
    client = MagicMock()
    client.name = "Claude Code"
    client.config_path = None
    monkeypatch.setattr(init_module, "_detect_clients", lambda: [client])
    monkeypatch.setattr(
        init_module, "configure_client", MagicMock(return_value="  configured")
    )
    hook_spy = MagicMock(return_value="  post-checkout (spy)")
    monkeypatch.setattr(git_hook, "install_post_checkout", hook_spy)
    return hook_spy


def test_init_writes_the_hook_only_when_asked(_quiet_init):
    assert init_module.run_init(yes=True, dry_run=True) == 0
    assert not _quiet_init.called
    assert init_module.run_init(yes=True, dry_run=True, minimal=True) == 0
    assert not _quiet_init.called
    assert init_module.run_init(yes=True, dry_run=True, git_hook=True) == 0
    assert _quiet_init.call_count == 1


def test_uninstall_removes_the_hook_unless_kept(monkeypatch):
    monkeypatch.setattr(init_module, "_detect_clients", lambda: [])
    for name in (
        "uninstall_claude_md",
        "uninstall_cursor_rules",
        "uninstall_windsurf_rules",
        "uninstall_agents_md",
        "uninstall_hooks",
        "uninstall_copilot_hooks",
    ):
        monkeypatch.setattr(init_module, name, MagicMock(return_value="  (spy)"))
    spy = MagicMock(return_value="  post-checkout (spy)")
    monkeypatch.setattr(git_hook, "uninstall_post_checkout", spy)

    init_module.run_uninstall(dry_run=True, skills=False)
    assert spy.call_count == 1
    init_module.run_uninstall(dry_run=True, skills=False, git_hook=False)
    assert spy.call_count == 1


def test_cli_parses_the_flags_and_the_hook_command():
    from jcodemunch_mcp import server

    src = Path(server.__file__).read_text(encoding="utf-8")
    assert '"--git-hook"' in src
    assert '"--keep-git-hook"' in src
    assert '"hook-post-checkout"' in src
    out = subprocess.run(
        [sys.executable, "-m", "jcodemunch_mcp", "hook-post-checkout", "A", "A"],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "PYTHONPATH": str(Path(server.__file__).parents[1])},
    )
    assert out.returncode == 0, out.stderr
