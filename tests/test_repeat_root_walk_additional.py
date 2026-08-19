"""Three cases #513 does not reach.

@lsg1103275794's fix and tests for #504 are correct and merged; these are
additions to that work, written independently while our fallback path was kept
warm, and kept because each pins something their two cases leave open.

⚠ The first is the control that matters most: a no-change path that is simply
*always* taken would satisfy every "second run is incremental" assertion and
index nothing.
"""

import subprocess

import pytest

from jcodemunch_mcp.tools.index_folder import index_folder


def _git(cwd, *args):
    subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True
    )


@pytest.fixture
def repo(tmp_path):
    work = tmp_path / "demo"
    work.mkdir()
    store = tmp_path / "store"
    store.mkdir()
    _git(work, "init", "-q")
    _git(work, "config", "user.email", "repro@example.invalid")
    _git(work, "config", "user.name", "repro")
    _git(work, "remote", "add", "origin", "https://github.com/acme/demo.git")
    (work / "main.py").write_text("def main():\n    return 1\n")
    (work / "pkg").mkdir()
    (work / "pkg" / "mod.py").write_text("def mod():\n    return 2\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "A")

    class Repo:
        path = work
        store_path = str(store)

        @staticmethod
        def index(**kwargs):
            return index_folder(
                str(work), use_ai_summaries=False, storage_path=str(store),
                identity_mode="git", **kwargs,
            )

        @staticmethod
        def index_subdir(rel, **kwargs):
            return index_folder(
                str(work / rel), use_ai_summaries=False, storage_path=str(store),
                identity_mode="git", **kwargs,
            )

    return Repo


class TestCasesBeyond513:
    def test_a_real_edit_is_still_picked_up(self, repo):
        """Control against the obvious wrong fix — taking the no-change path
        unconditionally would satisfy every assertion above and index nothing."""
        repo.index()
        (repo.path / "main.py").write_text("def main_v2():\n    return 99\n")

        result = repo.index()

        assert result["performed_incremental"] is True
        assert result.get("message") != "No changes detected"

        from jcodemunch_mcp.tools.search_symbols import search_symbols

        found = search_symbols(
            repo=result["repo"], query="main_v2", storage_path=repo.store_path,
            detail_level="compact", max_results=5,
        )
        names = {r["name"] for r in (found.get("results") or found.get("symbols") or [])}
        assert "main_v2" in names, "the edit was not indexed"


    def test_a_subdir_walk_still_merges(self, repo):
        """The behaviour the guard was written for must not move: a subdir walk
        carries over files outside its prefix rather than pruning them."""
        repo.index()
        repo.index_subdir("pkg")

        from jcodemunch_mcp.tools.get_file_content import get_file_content

        resolved = repo.index()
        got = get_file_content(
            repo=resolved["repo"], file_path="main.py",
            storage_path=repo.store_path,
        )
        assert got.get("content"), (
            "a subdir walk dropped a file outside its prefix — the carry-over "
            "the collision guard exists for"
        )


    def test_an_index_without_source_roots_rebuilds_once(self, repo, monkeypatch):
        """A legacy index carrying no `source_roots` has unknown coverage, and
        unknown is not full. One rebuild resolves it rather than diffing a full
        corpus against a marker that says nothing."""
        from jcodemunch_mcp.storage import IndexStore

        repo.index()
        store = IndexStore(base_path=repo.store_path)
        result = repo.index()
        assert result["performed_incremental"] is True  # precondition

        real_load = store.__class__.load_index

        def load_without_roots(self, owner, name, *a, **kw):
            index = real_load(self, owner, name, *a, **kw)
            if index is not None:
                index.source_roots = []
            return index

        monkeypatch.setattr(store.__class__, "load_index", load_without_roots)

        degraded = repo.index()

        assert degraded["performed_incremental"] is not True, (
            "an index with unknown coverage was treated as full coverage"
        )
