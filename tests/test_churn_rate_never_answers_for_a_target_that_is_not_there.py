"""`get_churn_rate` never answers for a target that is not there (LEDGER L-41).

The tool takes a file path OR a symbol id. An id it could not find fell
through to the file branch, `git log -- <id>` matched nothing, and the reply
was `commits: 0`, `assessment: "stable"`, `confidence_level: "high"`: a
confident measurement of a file that does not exist. A misspelt path got the
same answer. The symbol-shaped half is gated with every other symbol tool in
`test_a_near_miss_symbol_id_names_its_candidates.py`; this file holds the
path half and the targets that must KEEP being answered.
"""

from __future__ import annotations

import subprocess

import pytest

from jcodemunch_mcp.tools.get_churn_rate import get_churn_rate
from jcodemunch_mcp.tools.index_folder import index_folder


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, check=True)


@pytest.fixture
def repo(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "utils.py").write_text("class C:\n    def pick(self):\n        return 1\n", encoding="utf-8")
    (src / "gone.py").write_text("def g():\n    return 2\n", encoding="utf-8")
    (src / "NOTES.txt").write_text("not source\n", encoding="utf-8")
    try:
        _git(src, "init", "-q")
        _git(src, "config", "user.email", "t@t.t")
        _git(src, "config", "user.name", "T")
        _git(src, "add", ".")
        _git(src, "commit", "-q", "-m", "init")
        _git(src, "rm", "-q", "gone.py")
        _git(src, "commit", "-q", "-m", "drop gone.py")
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git not available")
    store = tmp_path / "store"
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert r["success"] is True
    return r["repo"], str(store)


def test_a_path_that_is_not_there_is_refused(repo):
    name, store = repo
    result = get_churn_rate(repo=name, target="utlis.py", storage_path=store)

    assert "error" in result, result
    assert "utlis.py" in result["error"]
    assert "assessment" not in result and "commits" not in result


def test_a_near_miss_symbol_id_is_refused_with_its_candidates(repo):
    name, store = repo
    result = get_churn_rate(repo=name, target="utils.py::pick#method", storage_path=store)

    assert "assessment" not in result, result
    assert result.get("near_miss_ids") == ["utils.py::C.pick#method"], result


@pytest.mark.parametrize(
    "target,target_type,commits",
    [
        ("utils.py", "file", 1),
        ("utils.py::C.pick#method", "symbol", 1),
        # Not indexed (not source), but in the tree: git can measure it.
        ("NOTES.txt", "file", 1),
        # Deleted, so neither indexed nor on disk, but git has its history.
        ("gone.py", "file", 2),
    ],
)
def test_a_target_git_can_measure_is_still_answered(repo, target, target_type, commits):
    name, store = repo
    result = get_churn_rate(repo=name, target=target, storage_path=store)

    assert "error" not in result, result
    assert result["target_type"] == target_type
    assert result["commits"] == commits
