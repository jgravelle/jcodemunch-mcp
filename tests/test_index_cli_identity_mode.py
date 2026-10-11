"""The `index` CLI can choose the index identity (#1017, @sKlat-beep).

`index_folder` has taken `identity_mode` since v1.108.6, and the CLI handler
never passed it, so `jcodemunch-mcp index <path>` always ran in `config` mode
and a script that needed a path keyed a particular way had to call the Python
function. Each test drives the real subcommand in a subprocess against a
store it owns.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def _git(*args, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def _run(args, storage: Path, cwd: Path | None = None):
    # The developer's own JCODEMUNCH_* settings stay out of the subprocess: with
    # JCODEMUNCH_TRUSTED_FOLDERS set, every test that indexes failed (review of #1017).
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("JCODEMUNCH_")}
    env["CODE_INDEX_PATH"] = str(storage)
    env["JCODEMUNCH_USE_AI_SUMMARIES"] = "false"
    return subprocess.run(
        [sys.executable, "-m", "jcodemunch_mcp", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=120,
        stdin=subprocess.DEVNULL,
        cwd=str(cwd) if cwd else None,
    )


def _store(tmp_path: Path, git_root_identity: bool) -> Path:
    storage = tmp_path / "store"
    storage.mkdir()
    (storage / "config.jsonc").write_text(
        json.dumps({"git_root_identity": git_root_identity}), encoding="utf-8"
    )
    return storage


@pytest.fixture
def checkout(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    _git("init", "-q", cwd=repo)
    _git("remote", "add", "origin", "https://github.com/example-owner/example-repo.git", cwd=repo)
    (repo / "alpha.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    return repo


def test_local_mode_keys_by_folder_where_the_config_would_key_by_origin(tmp_path, checkout):
    storage = _store(tmp_path, git_root_identity=True)

    result = _run(["index", str(checkout), "--identity-mode", "local"], storage)

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["repo"].startswith("local/proj-")


def test_git_mode_keys_by_origin_where_the_config_would_key_by_folder(tmp_path, checkout):
    storage = _store(tmp_path, git_root_identity=False)

    result = _run(["index", str(checkout), "--identity-mode", "git"], storage)

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["repo"] == "example-owner/example-repo"


@pytest.mark.parametrize(
    "git_root_identity, expected",
    [(True, "example-owner/example-repo"), (False, "local/proj-")],
)
def test_without_the_option_the_config_still_decides(tmp_path, checkout, git_root_identity, expected):
    storage = _store(tmp_path, git_root_identity=git_root_identity)

    for args in (["index", str(checkout)], ["index", str(checkout), "--identity-mode", "config"]):
        result = _run(args, storage)
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)["repo"].startswith(expected), args


def test_a_value_outside_the_three_is_refused_by_the_parser(tmp_path, checkout):
    storage = _store(tmp_path, git_root_identity=True)

    result = _run(["index", str(checkout), "--identity-mode", "origin"], storage)

    assert result.returncode == 2
    assert "invalid choice" in result.stderr and "--identity-mode" in result.stderr
    assert not list(storage.rglob("*.db")), "nothing is indexed on a refused value"


@pytest.mark.parametrize("mode", ["local", "git"])
def test_a_github_target_refuses_an_explicit_mode_before_any_network_call(tmp_path, mode):
    storage = _store(tmp_path, git_root_identity=True)

    result = _run(["index", "example-owner/example-repo", "--identity-mode", mode], storage)

    assert result.returncode == 1, result.stdout + result.stderr
    body = json.loads(result.stdout)
    assert body["success"] is False
    assert "--identity-mode" in body["error"] and "local" in body["error"]
    assert "example-owner/example-repo" in body["error"], "the refusal names the target"


def test_a_mode_that_conflicts_with_an_existing_index_is_a_refusal_not_a_traceback(tmp_path, checkout):
    storage = _store(tmp_path, git_root_identity=True)
    first = _run(["index", str(checkout), "--identity-mode", "local"], storage)
    assert first.returncode == 0, first.stdout + first.stderr

    second = _run(["index", str(checkout), "--identity-mode", "git"], storage)

    assert second.returncode == 1
    body = json.loads(second.stdout)
    assert body["success"] is False
    assert "local identity" in body["error"]
    assert "Traceback" not in second.stderr


@pytest.mark.parametrize("spelling", ["proj/", "proj/sub", "work/../proj", "~/proj"])
def test_a_directory_that_exists_is_local_however_it_is_spelled(tmp_path, monkeypatch, spelling):
    """Each of these was sent to the GitHub indexer by the spelling rule, where
    an explicit mode would have been refused with a message about GitHub repos."""
    from jcodemunch_mcp.server import _index_target_is_local

    (tmp_path / "proj" / "sub").mkdir(parents=True)
    (tmp_path / "work").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    assert _index_target_is_local(spelling) is True


@pytest.mark.parametrize(
    "target, expected",
    [
        ("example-owner/example-repo", False),
        ("github.com/example-owner/example-repo", False),
        ("https://github.com/example-owner/example-repo", False),
        ("git@github.com:example-owner/example-repo", False),
        (".", True),
        ("./missing", True),
        ("/missing/abs", True),
        (r"C:\missing\abs", True),
        ("missing-bare-name", True),
        ("C://missing/abs", True),
        ("/missing/a://b", True),
        ("./a://b", True),
        ("git@github.com:repo", True),
        ("ssh://git@github.com/example-owner/example-repo", False),
    ],
)
def test_a_target_that_is_no_directory_is_routed_as_before(tmp_path, monkeypatch, target, expected):
    from jcodemunch_mcp.server import _index_target_is_local

    monkeypatch.chdir(tmp_path)

    assert _index_target_is_local(target) is expected


def test_an_explicit_mode_reaches_a_folder_spelled_with_a_trailing_slash(tmp_path, checkout):
    storage = _store(tmp_path, git_root_identity=True)

    result = _run(["index", "proj/", "--identity-mode", "local"], storage, cwd=tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["repo"].startswith("local/proj-")


def test_owner_slash_repo_names_the_local_directory_when_one_exists(tmp_path, monkeypatch):
    """A decision, not a spelling case: `owner/repo` and `github.com/owner/repo`
    are the directory when the directory is there, as `git clone` reads an
    existing path, and a URL is never probed as a path."""
    from jcodemunch_mcp.server import _index_target_is_local

    monkeypatch.chdir(tmp_path)
    assert _index_target_is_local("example-owner/example-repo") is False
    assert _index_target_is_local("github.com/example-owner/example-repo") is False

    (tmp_path / "example-owner" / "example-repo").mkdir(parents=True)

    assert _index_target_is_local("example-owner/example-repo") is True
    assert _index_target_is_local("https://github.com/example-owner/example-repo") is False
    assert _index_target_is_local("git@github.com:example-owner/example-repo") is False

    # A ghq or GOPATH/src root: the host-qualified spelling is a directory too.
    (tmp_path / "github.com" / "example-owner" / "example-repo").mkdir(parents=True)
    assert _index_target_is_local("github.com/example-owner/example-repo") is True

    # A URL whose text is also an existing relative path (possible on POSIX,
    # where a colon is legal in a name) still means the remote.
    monkeypatch.setattr(Path, "is_dir", lambda self: True)
    assert _index_target_is_local("https://github.com/example-owner/example-repo") is False
    assert _index_target_is_local("git@github.com:example-owner/example-repo") is False


def test_a_url_scheme_skips_the_probe_and_a_drive_letter_is_not_a_scheme(tmp_path, monkeypatch):
    """The guard that keeps a URL from being read as a path is anchored on a
    scheme of two or more characters. Unanchored, it sent `C://<existing dir>`
    and a directory named `git@work` to the GitHub indexer (review of #1017)."""
    from jcodemunch_mcp.server import _index_target_is_local

    monkeypatch.chdir(tmp_path)
    (tmp_path / "git@work").mkdir()
    assert _index_target_is_local("git@work") is True

    if os.name == "nt":
        drive, rest = os.path.splitdrive(str(tmp_path))
        doubled = drive + "//" + rest.lstrip("\\/").replace("\\", "/")
    else:
        (tmp_path / "x:" / "y").mkdir(parents=True)
        doubled = "x://y"
    assert Path(doubled).is_dir(), doubled
    assert _index_target_is_local(doubled) is True

    probed = []
    monkeypatch.setattr(Path, "is_dir", lambda self: probed.append(str(self)) or True)
    assert _index_target_is_local("https://github.com/example-owner/example-repo") is False
    assert _index_target_is_local("git@github.com:example-owner/example-repo") is False
    assert probed == [], "a URL is never probed as a path"

    # Anchored at the start, and `git@` needs its `host:`; each of these is
    # local only because the probe ran.
    assert _index_target_is_local("proj/ab://c") is True
    assert _index_target_is_local("git@work/sub") is True
