"""The `index` CLI can choose the index identity (#1017, @sKlat-beep).

`index_folder` has taken `identity_mode` since v1.108.6, and the CLI handler
never passed it, so `jcodemunch-mcp index <path>` always ran in `config` mode
and a script that needed a path keyed a particular way had to call the Python
function. Each test drives the real subcommand in a subprocess against a
store it owns.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def _git(*args, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def _run(args, storage: Path):
    env = {
        **os.environ,
        "CODE_INDEX_PATH": str(storage),
        "JCODEMUNCH_USE_AI_SUMMARIES": "false",
    }
    env.pop("JCODEMUNCH_GIT_ROOT_IDENTITY", None)
    return subprocess.run(
        [sys.executable, "-m", "jcodemunch_mcp", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=120,
        stdin=subprocess.DEVNULL,
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
