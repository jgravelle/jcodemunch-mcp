"""A save from a path list keeps the index's stored cap status.

An index the file cap cut short stores `file_cap_status = {"truncated": True,
...}`, and every search over it says the index is partial, so an empty answer
is not read as absence. A call with `paths=[...]` re-reads only the files it
names: it never walks the tree, so it cannot know whether the cap still cuts
it. It used to write its own status anyway, which always reads
`{"truncated": False}`, and one edited file re-saved this way made a partial
index read as complete.

The fixture holds 8 files under `pkg/` and an in-call cap of 3, so the first
index keeps 3 and is truncated.
"""

from __future__ import annotations

import pytest

import jcodemunch_mcp.config as config_module

N = 8


def _index(root, **kw) -> dict:
    from jcodemunch_mcp.tools.index_folder import index_folder

    return index_folder(str(root), use_ai_summaries=False, **kw)


def _repo(root) -> str:
    from jcodemunch_mcp.tools.resolve_repo import resolve_repo

    return resolve_repo(str(root))["repo"]


def _cap(repo: str) -> dict:
    from jcodemunch_mcp.storage import IndexStore

    owner, name = repo.split("/", 1)
    return IndexStore().load_index(owner, name).file_cap_status or {}


@pytest.fixture
def capped(tmp_path, monkeypatch):
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / ".idx"))
    monkeypatch.setenv("JCODEMUNCH_TRUSTED_FOLDERS", str(tmp_path))
    monkeypatch.setenv("JCODEMUNCH_USE_AI_SUMMARIES", "false")
    monkeypatch.delenv("JCODEMUNCH_MAX_FOLDER_FILES", raising=False)
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    for i in range(N):
        (root / "pkg" / f"m{i}.py").write_text(
            f"def fn{i}():\n    return {i}\n", encoding="utf-8"
        )
    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "max_folder_files", 3)
    assert _index(root)["success"]
    repo = _repo(root)
    assert _cap(repo).get("truncated") is True, "precondition: the cap cut the index"
    return root, repo


@pytest.mark.parametrize("spelling", ["pkg/m0.py", "pkg"])
def test_an_edited_file_saved_by_path_keeps_the_index_partial(capped, spelling):
    root, repo = capped
    (root / "pkg" / "m0.py").write_text(
        "def fn0():\n    return 'edited'\n", encoding="utf-8"
    )
    assert _index(root, paths=[spelling])["success"]
    assert _cap(repo).get("truncated") is True


def test_a_search_still_says_the_index_is_partial(capped):
    from jcodemunch_mcp.tools.search_text import search_text

    root, repo = capped
    (root / "pkg" / "m1.py").write_text(
        "def fn1():\n    return 'edited'\n", encoding="utf-8"
    )
    assert _index(root, paths=["pkg/m1.py"])["success"]
    res = search_text(repo, "fn7")
    assert (res.get("_meta") or {}).get("index_truncated"), res.get("_meta")


def test_a_whole_tree_walk_still_clears_a_lifted_cap(capped, monkeypatch):
    # The fix must not freeze the status: a walk that sees the whole tree
    # under a raised cap writes the status it found.
    root, repo = capped
    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "max_folder_files", 100)
    assert _index(root)["success"]
    assert _cap(repo).get("truncated") is not True
