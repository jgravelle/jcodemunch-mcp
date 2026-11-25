"""A subdirectory walk applies the git root's .gitignore (#1563, @rknighton).

`index_folder(<repo>/sub)` walked from `sub` with an empty .gitignore list, so
it read only the .gitignore files from `sub` down. The index is keyed from the
git root, and the next root walk applies the root's .gitignore, so a file the
subdirectory walk added was dropped there with no message. `paths=` from the
subdirectory had the same gap, and `index_file` mirrored the subdirectory walk.

Reported reproduction on 1.108.488: `.gitignore` holds `private/`;
index root -> ['kept.py']; index root/private -> ['kept.py',
'private/hidden.py']; index root again -> ['kept.py'].

⚠ NON-VACUITY. The subdirectory, subdirectory-paths= and index_file tests fail
on the pre-fix tree. The controls (the root walk, a file the root .gitignore
does not exclude, a .gitignore inside the subdirectory) pass on both sides.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.tools.index_file import index_file
from jcodemunch_mcp.tools.index_folder import index_folder


def _write(path: Path, text: str = "def f():\n    return 1\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _repo(tmp_path: Path) -> Path:
    repo = (tmp_path / "repo").resolve()
    _write(repo / "kept.py")
    _write(repo / "private" / "hidden.py")
    _write(repo / "pub" / "open.py")
    _write(repo / ".gitignore", "private/\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    return repo


def _files(store: Path, out: dict) -> list[str]:
    owner, name = out["repo"].split("/", 1)
    return sorted(IndexStore(base_path=str(store)).load_index(owner, name).source_files)


def _index(path: Path, store: Path, **kw) -> dict:
    return index_folder(str(path), use_ai_summaries=False, storage_path=str(store), **kw)


def test_a_subdirectory_walk_skips_what_the_root_gitignore_excludes(tmp_path):
    repo = _repo(tmp_path)
    store = tmp_path / "store"
    _index(repo, store)
    out = _index(repo / "private", store)
    assert "private/hidden.py" not in _files(store, out), "the subdir walk indexed a root-ignored file"


def test_the_three_routes_agree(tmp_path):
    """The report's sequence: subdir, then root, never changes the file set."""
    repo = _repo(tmp_path)
    store = tmp_path / "store"
    first = _files(store, _index(repo, store))
    after_sub = _files(store, _index(repo / "private", store))
    after_root = _files(store, _index(repo, store))
    assert first == after_sub == after_root, (first, after_sub, after_root)


def test_paths_from_the_subdirectory_skips_a_root_ignored_file(tmp_path):
    repo = _repo(tmp_path)
    store = tmp_path / "store"
    _index(repo, store)
    out = _index(repo / "private", store, paths=["hidden.py"])
    assert "private/hidden.py" not in _files(store, out)
    assert any("gitignored" in w for w in out.get("warnings", [])), out.get("warnings")


def test_paths_naming_the_subdirectory_itself_skips_its_files(tmp_path):
    """A listed directory is walked; the chain above it comes from the git root."""
    repo = _repo(tmp_path)
    store = tmp_path / "store"
    _index(repo, store)
    out = _index(repo / "private", store, paths=["."])
    assert "private/hidden.py" not in _files(store, out)


def test_index_file_refuses_a_root_ignored_file_after_a_subdirectory_walk(tmp_path):
    repo = _repo(tmp_path)
    store = tmp_path / "store"
    _index(repo, store)
    _index(repo / "private", store)
    target = _write(repo / "private" / "hidden.py", "def g():\n    return 2\n")
    res = index_file(path=str(target), use_ai_summaries=False, storage_path=str(store))
    assert res.get("skipped") == "gitignore", res


# ── controls: pass on both sides ─────────────────────────────────────────────

def test_a_subdirectory_walk_keeps_a_file_the_root_does_not_ignore(tmp_path):
    repo = _repo(tmp_path)
    store = tmp_path / "store"
    out = _index(repo / "pub", store)
    assert "pub/open.py" in _files(store, out)


def test_a_gitignore_inside_the_subdirectory_still_applies(tmp_path):
    repo = _repo(tmp_path)
    _write(repo / "pub" / ".gitignore", "gen/\n")
    _write(repo / "pub" / "gen" / "made.py")
    store = tmp_path / "store"
    out = _index(repo / "pub", store)
    files = _files(store, out)
    assert "pub/open.py" in files and "pub/gen/made.py" not in files, files


def test_the_root_walk_is_unchanged(tmp_path):
    repo = _repo(tmp_path)
    store = tmp_path / "store"
    assert _files(store, _index(repo, store)) == ["kept.py", "pub/open.py"]
