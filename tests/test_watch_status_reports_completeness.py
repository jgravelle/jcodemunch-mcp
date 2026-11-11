"""get_watch_status says when an index holds a small part of the tree (#1395).

`index_freshness` compares the indexed revision with HEAD, so an index built at
HEAD that holds 14 of 9,400 files read `fresh`, and `any_stale` stayed False.
Recency and completeness are separate facts; each repo entry now carries the
measured numbers and a completeness state, and an index the coverage cannot be
counted for says `unknown`, never `complete`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jcodemunch_mcp.tools.get_watch_status import get_watch_status
from jcodemunch_mcp.tools.index_folder import index_folder

NEWLINE = chr(10)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _repo(tmp_path: Path, n: int = 40, extra=None) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    for i in range(n):
        (root / f"mod{i:02d}.py").write_text(
            f"def f{i}():{NEWLINE}    return {i}{NEWLINE}", encoding="utf-8"
        )
    for rel, text in (extra or {}).items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(
        root, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init"
    )
    return root


def _entry(store: Path, root: Path, **kw) -> tuple[dict, dict]:
    out = get_watch_status(storage_path=str(store), **kw)
    rows = [
        r for r in out["repos"] if Path(r["source_root"]).resolve() == root.resolve()
    ]
    assert len(rows) == 1, out
    return out, rows[0]


def _index(root: Path, store: Path, **kw) -> dict:
    out = index_folder(
        path=str(root), use_ai_summaries=False, storage_path=str(store), **kw
    )
    assert out.get("success"), out
    return out


@pytest.fixture
def store(tmp_path):
    s = tmp_path / "store"
    s.mkdir()
    return s


def test_an_index_holding_a_sliver_of_the_tree_reads_incomplete(tmp_path, store):
    root = _repo(tmp_path)
    _index(root, store, max_folder_files=4)
    out, row = _entry(store, root)
    assert (
        row["index_freshness"] == "fresh"
    )  # recency is right; it was never the question
    assert row["index_completeness"] == "incomplete", row
    assert row["indexed_files"] <= 4
    assert row["tracked_source_files"] == 40
    assert row["index_coverage"] < 0.2
    assert out["any_incomplete"] is True


def test_a_full_index_reads_complete(tmp_path, store):
    root = _repo(tmp_path)
    _index(root, store)
    out, row = _entry(store, root)
    assert row["index_completeness"] == "complete", row
    assert row["indexed_files"] == 40
    assert row["index_coverage"] == 1.0
    assert out["any_incomplete"] is False
    assert out["any_completeness_unknown"] is False


def test_files_the_indexer_excludes_on_purpose_do_not_count_against_it(tmp_path, store):
    """Tracked files in skip directories, lockfiles and secrets are not missing files."""
    extra = {
        f"node_modules/pkg/f{i}.js": "module.exports = 1;" + NEWLINE for i in range(60)
    }
    extra.update({"package-lock.json": "{}", ".env": "TOKEN=x" + NEWLINE})
    root = _repo(tmp_path, n=10, extra=extra)
    _index(root, store)
    _, row = _entry(store, root)
    assert row["index_completeness"] == "complete", row
    assert row["tracked_source_files"] == 10, row


def test_a_folder_that_is_not_a_git_repository_reads_unknown(tmp_path, store):
    root = tmp_path / "plain"
    root.mkdir()
    (root / "a.py").write_text(
        "def a():" + NEWLINE + "    return 1" + NEWLINE, encoding="utf-8"
    )
    _index(root, store)
    out, row = _entry(store, root)
    assert row["index_completeness"] == "unknown", row
    assert "tracked_source_files" not in row
    assert out["any_incomplete"] is False
    assert out["any_completeness_unknown"] is True


def test_skipping_the_check_reports_unknown_not_complete(tmp_path, store):
    root = _repo(tmp_path)
    _index(root, store, max_folder_files=4)
    out, row = _entry(store, root, check_freshness=False)
    assert row["index_completeness"] == "unknown", row
    assert out["any_incomplete"] is False
    assert out["any_completeness_unknown"] is True


def test_untracked_indexed_files_do_not_hide_missing_tracked_ones(tmp_path, store):
    """The ratio counts tracked files only, so files git does not know cannot pad it."""
    root = _repo(tmp_path)
    for i in range(60):
        (root / f"scratch{i:02d}.py").write_text(
            f"def s{i}(): pass{NEWLINE}", encoding="utf-8"
        )
    _index(root, store, max_folder_files=20)
    _, row = _entry(store, root)
    assert row["index_completeness"] == "incomplete", row


def test_an_unreadable_index_reads_unknown_not_zero(tmp_path, store, monkeypatch):
    from jcodemunch_mcp.storage import IndexStore

    root = _repo(tmp_path)
    _index(root, store)
    monkeypatch.setattr(
        IndexStore, "open_selective", lambda self, owner, name, **kw: None
    )
    out, row = _entry(store, root)
    assert row["index_completeness"] == "unknown", row
    assert "index_coverage" not in row
    assert out["any_incomplete"] is False
    assert out["any_completeness_unknown"] is True


def test_a_tree_with_nothing_tracked_reads_unknown_not_complete(tmp_path, store):
    root = tmp_path / "fresh"
    root.mkdir()
    for i in range(5):
        (root / f"m{i}.py").write_text(f"def m{i}(): pass{NEWLINE}", encoding="utf-8")
    _git(root, "init", "-q")  # no commit: git tracks nothing
    _index(root, store)
    out, row = _entry(store, root)
    assert row["index_completeness"] == "unknown", row
    assert "index_coverage" not in row
    assert out["any_incomplete"] is False
    assert out["any_completeness_unknown"] is True


def test_a_caller_can_skip_only_the_completeness_count(tmp_path, store):
    root = _repo(tmp_path)
    _index(root, store, max_folder_files=4)
    out, row = _entry(store, root, check_completeness=False)
    assert row["index_freshness"] == "fresh", row  # freshness is still measured
    assert row["index_completeness"] == "unknown", row
    assert out["any_incomplete"] is False
    assert out["any_completeness_unknown"] is True


def test_list_repos_does_not_pay_for_completeness(tmp_path, store, monkeypatch):
    """repos_report drops the completeness keys, so it must not run the count."""
    from jcodemunch_mcp.tools import index_folder as ix
    from jcodemunch_mcp.tools.list_repos import repos_report

    root = _repo(tmp_path)
    _index(root, store)

    calls = []
    real = ix.git_tracked_paths

    def counting(*a, **k):
        # Recorded, not raised: the completeness path catches every exception,
        # so a raising mock would be swallowed and the test could not fail.
        calls.append(a)
        return real(*a, **k)

    monkeypatch.setattr(ix, "git_tracked_paths", counting)
    report = repos_report(str(store))
    assert report, report
    assert calls == [], "repos_report ran git ls-files for completeness"


def test_a_file_whose_eligibility_check_fails_counts_as_missing(tmp_path, monkeypatch):
    """An error must not shrink the denominator, which would raise the ratio."""
    from jcodemunch_mcp.tools import index_folder as ix

    root = _repo(tmp_path, n=3)

    def broken(path, cfg):
        raise OSError("stat failed")

    monkeypatch.setattr(ix, "_should_index_file", broken)
    kept = ix.indexable_paths(root, ["mod00.py", "mod01.py", "mod02.py"])
    assert kept == ["mod00.py", "mod01.py", "mod02.py"]
