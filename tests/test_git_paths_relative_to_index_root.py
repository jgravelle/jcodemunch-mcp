"""Every reader of git's changed-path output agrees with the index about paths (#685).

`git log --name-only` and `git diff --name-only` print paths from the git TOP
LEVEL. With the default identity the index is rooted there and the two agree.
`index_folder(..., identity_mode="local")` on a folder BELOW the top level
roots the index at that folder with folder-relative paths (`m.py`, not
`services/api/m.py`), so every name git printed missed: `get_hotspots` scored a
file changed four times as churn 0, which reads as cold rather than as an error.

#667 fixed the same mismatch in `get_tectonic_map` and left five readers; the
last test here scans `src/` for the property so a sixth inherits the rule.

What each test pins (for docs/harness/ARCHAEOLOGY.md):
- get_hotspots and winnow_symbols count churn for a sub-rooted index;
- get_delivery_metrics counts only files under the index root;
- the git-blame context provider keys blame by index path;
- get_changed_symbols reports changed symbols under index paths and can read
  both versions (`git show <sha>:<path>` is top-level relative, so the path
  needs the `./` form once it is folder-relative);
- a failing git is logged, never a silent `{}`;
- every `--name-only` git call under src/ passes `--relative`.
"""

from __future__ import annotations

import ast
import logging
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jcodemunch_mcp.parser.context.git_blame import GitBlameProvider
from jcodemunch_mcp.tools import get_hotspots as hot
from jcodemunch_mcp.tools import winnow_symbols as win
from jcodemunch_mcp.tools._git_history import _CACHE as _HISTORY_CACHE
from jcodemunch_mcp.tools.get_changed_symbols import get_changed_symbols
from jcodemunch_mcp.tools.get_delivery_metrics import get_delivery_metrics
from jcodemunch_mcp.tools.index_folder import index_folder

SRC = Path(__file__).resolve().parents[1] / "src" / "jcodemunch_mcp"
SUB = "services/api"
BODY = "def f(x):\n    if x:\n        return 1\n    if x > 2:\n        return 2\n    return 3\n"


def _git(args, cwd, when=None):
    env = None
    if when is not None:
        stamp = when.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        env = {**os.environ, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
    r = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"] + args,
        cwd=str(cwd), capture_output=True, text=True, stdin=subprocess.DEVNULL, env=env,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout


def _mono(root: Path, edits: int = 4) -> Path:
    """`services/api/m.py` committed `edits` times, `other/x.py` once, all in window."""
    root.mkdir(parents=True)
    _git(["init", "-q", "-b", "main"], root)
    pkg = root / SUB
    pkg.mkdir(parents=True)
    (root / "other").mkdir()
    (root / "other" / "x.py").write_text("def g():\n    return 0\n", encoding="utf-8")
    now = datetime.now(timezone.utc)
    for i in range(edits):
        (pkg / "m.py").write_text(BODY + f"# {i}\n", encoding="utf-8")
        _git(["add", "-A"], root)
        _git(["commit", "-q", "-m", f"edit {i}"], root, when=now - timedelta(days=10 - i))
    return pkg


@pytest.fixture(autouse=True)
def _clear_history_memo():
    _HISTORY_CACHE.clear()
    yield
    _HISTORY_CACHE.clear()


@pytest.mark.parametrize("reader", [hot._get_file_churn, win._get_file_churn], ids=["get_hotspots", "winnow_symbols"])
def test_churn_is_counted_under_index_paths(tmp_path, reader):
    pkg = _mono(tmp_path / "r")
    churn = reader(str(pkg), 90)
    assert churn.get("m.py") == 4, churn
    assert not any(k.startswith(("services/", "other/")) for k in churn), churn


def test_get_hotspots_scores_churn_for_a_sub_rooted_index(tmp_path):
    pkg = _mono(tmp_path / "r")
    store = tmp_path / "store"
    res = index_folder(str(pkg), use_ai_summaries=False, storage_path=str(store), identity_mode="local")
    assert res["success"] is True
    out = hot.get_hotspots(res["repo"], min_complexity=1, storage_path=str(store))
    rows = {h["name"]: h for h in out["hotspots"]}
    assert rows["f"]["churn"] == 4, out


def test_delivery_metrics_count_only_files_under_the_index_root(tmp_path):
    root = tmp_path / "r"
    pkg = _mono(root)
    # A commit that changes only a file outside the index root.
    (root / "other" / "x.py").write_text("def g():\n    return 7\n", encoding="utf-8")
    _git(["add", "-A"], root)
    _git(["commit", "-q", "-m", "outside only"], root, when=datetime.now(timezone.utc) - timedelta(days=2))
    store = tmp_path / "store"
    res = index_folder(str(pkg), use_ai_summaries=False, storage_path=str(store), identity_mode="local")
    out = get_delivery_metrics(res["repo"], storage_path=str(store))
    assert "error" not in out, out
    # other/x.py sits outside the index root and must not be counted, and the
    # commit that touched only it is not this corpus's commit: `--relative`
    # alone listed it with an empty file set, which counted as durable.
    assert out["files_touched"] == 1, out
    assert out["commits_total"] == 4, out


def test_git_blame_is_keyed_by_index_path(tmp_path):
    pkg = _mono(tmp_path / "r")
    prov = GitBlameProvider()
    prov.load(pkg)
    ctx = prov.get_file_context("m.py")
    assert ctx is not None and ctx.properties["last_author"] == "t", prov.get_metadata()
    assert "services/api/m.py" not in prov.get_metadata()["git_blame"]
    assert "other/x.py" not in prov.get_metadata()["git_blame"]


def test_get_changed_symbols_reads_index_paths_for_a_sub_rooted_index(tmp_path):
    pkg = _mono(tmp_path / "r", edits=1)
    store = tmp_path / "store"
    res = index_folder(str(pkg), use_ai_summaries=False, storage_path=str(store), identity_mode="local")
    assert res["success"] is True
    (pkg / "m.py").write_text(BODY + "\ndef added():\n    return 9\n", encoding="utf-8")
    _git(["add", "-A"], tmp_path / "r")
    _git(["commit", "-q", "-m", "add a symbol"], tmp_path / "r")
    out = get_changed_symbols(res["repo"], storage_path=str(store))
    assert "error" not in out, out
    added = [s for s in out["added_symbols"] if s["name"] == "added"]
    assert added and added[0]["file"] == "m.py", out


@pytest.mark.parametrize("reader", [hot._get_file_churn, win._get_file_churn], ids=["get_hotspots", "winnow_symbols"])
def test_a_failing_git_is_logged(tmp_path, reader, caplog, monkeypatch):
    # A git that cannot run at all (its cwd does not exist), not the unborn
    # branch's rc 128: a failure must reach WARNING, never read as no churn.
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    missing = tmp_path / "gone"
    with caplog.at_level(logging.DEBUG):
        assert reader(str(missing), 90) == {}
    warned = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("churn unavailable" in m for m in warned), [r.getMessage() for r in caplog.records]


@pytest.mark.parametrize("reader", [hot._get_file_churn, win._get_file_churn], ids=["get_hotspots", "winnow_symbols"])
def test_a_repo_with_no_commits_is_no_churn_and_no_warning(tmp_path, reader, caplog, monkeypatch):
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    _git(["init", "-q", "-b", "main"], fresh)
    with caplog.at_level(logging.DEBUG):
        assert reader(str(fresh), 90) == {}
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING], [r.getMessage() for r in caplog.records]


def _git_argv_lists(tree: ast.AST):
    """Every list/tuple literal holding the string "--name-only"."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.List, ast.Tuple)):
            strs = [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            if "--name-only" in strs:
                yield node, strs


def _offenders(root: Path) -> list[str]:
    bad = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node, strs in _git_argv_lists(tree):
            if "--relative" not in strs:
                bad.append(f"{path.relative_to(root).as_posix()}:{node.lineno}")
    return bad


def test_every_name_only_git_call_under_src_passes_relative():
    assert _offenders(SRC) == []


def test_the_scan_sees_a_reintroduced_call(tmp_path):
    (tmp_path / "m.py").write_text(
        'import subprocess\nsubprocess.run(["git", "diff", "--name-only", "a", "b"])\n', encoding="utf-8"
    )
    assert _offenders(tmp_path) == ["m.py:2"]


# --- git status --porcelain: the same mismatch in the absence-claim guard ------
#
# Porcelain prints top-level paths even from a subdirectory (it ignores
# `status.relativePaths`), so for a sub-rooted index every dirty path missed
# `file_mtimes` and read as "not in the index": any uncommitted file anywhere
# in the monorepo, inside the index root or not, refused absence claims.


def _dirty_mono(root: Path) -> Path:
    pkg = _mono(root, edits=1)
    (pkg / "m.py").write_text(BODY + "# dirty\n", encoding="utf-8")
    (pkg / "new.py").write_text("def n():\n    return 1\n", encoding="utf-8")
    (root / "other" / "x.py").write_text("def g():\n    return 5\n", encoding="utf-8")
    return pkg


def test_working_tree_paths_are_index_paths_and_stay_under_the_root(tmp_path):
    from jcodemunch_mcp.retrieval import subject_state as subj

    subj._clear_tree_cache()
    pkg = _dirty_mono(tmp_path / "r")
    reading = subj._working_tree_reading(str(pkg))
    assert reading is not None
    assert sorted(reading[1]) == ["m.py", "new.py"], reading


def test_dirt_outside_a_sub_rooted_index_does_not_block(tmp_path):
    from types import SimpleNamespace

    from jcodemunch_mcp.retrieval import subject_state as subj

    subj._clear_tree_cache()
    root = tmp_path / "r"
    pkg = _mono(root, edits=1)
    (root / "other" / "x.py").write_text("def g():\n    return 5\n", encoding="utf-8")
    index = SimpleNamespace(source_root=str(pkg), file_mtimes={"m.py": (pkg / "m.py").stat().st_mtime_ns})
    state = subj.working_tree_state(index)
    assert state["state"] == "clean", state
    assert state["blocks"] is False
