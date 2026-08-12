"""v1.108.56 — issue batch: jcm#330, jcm#331, jcm#333 (all reported by @mmashwani).

#330: index_folder's no-change return paths never refreshed the stored git_head.
A commit that changed only non-indexed files advanced live HEAD while the index
kept the old SHA, so FreshnessProbe flagged otherwise-current symbols stale_index
right after a successful "No changes detected" run. All three no-change paths now
advance the stored git_head when live HEAD moved.

#331: a search_symbols cache hit assumed the cached result carried a _meta dict,
even though _result_cache_get explicitly tolerates one without it — so a cache
entry missing _meta raised KeyError("_meta"). Worse, the dispatcher rendered ANY
KeyError (including ones raised inside tool code) as "Missing required argument",
making an internal bug look like a caller schema error. Cache hits now synthesize
_meta, and the dispatcher only reports missing-argument for KeyErrors raised in
its own argument-extraction frame.

#333: index_folder(paths=[...]) diffed the supplied subset against the ENTIRE
stored index, so every unlisted indexed file landed in `deleted` and was pruned.
Deletions are now scoped to exactly the listed subset (mirroring jdocmunch #31),
while a listed file that was deleted on disk is still removed, and listing the
root ('.') preserves full-corpus diff semantics.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from jcodemunch_mcp.tools.index_folder import index_folder, _refresh_git_head_if_advanced
from jcodemunch_mcp.storage import IndexStore


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #

def _have_git() -> bool:
    return shutil.which("git") is not None


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    )


def _init_git_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "commit", "--allow-empty", "-m", "root")


def _head(repo: Path) -> str:
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo), check=True, capture_output=True, text=True,
    )
    return out.stdout.strip()


def _load(result: dict, store_path: str):
    store = IndexStore(base_path=store_path)
    owner, name = result["repo"].split("/", 1)
    return store.load_index(owner, name)


# --------------------------------------------------------------------------- #
# jcm#330 — no-change index_folder advances stored git_head                    #
# --------------------------------------------------------------------------- #

@pytest.mark.skipif(not _have_git(), reason="git not available")
class TestNoChangeRefreshesGitHead:
    def _seed(self, tmp_path: Path):
        repo = tmp_path / "repo"
        _init_git_repo(repo)
        (repo / "app.py").write_text("def app():\n    return 1\n")
        (repo / "README.md").write_text("# hello\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "initial")
        store_path = str(tmp_path / "idx")
        first = index_folder(
            path=str(repo), use_ai_summaries=False,
            storage_path=store_path, incremental=True, identity_mode="local",
        )
        assert first.get("success") is True, first
        return repo, store_path

    def test_commit_touching_only_non_indexed_file_advances_head(self, tmp_path):
        repo, store_path = self._seed(tmp_path)
        head_a = _head(repo)
        idx = _load({"repo": index_folder(
            path=str(repo), use_ai_summaries=False, storage_path=store_path,
            incremental=True, identity_mode="local")["repo"]}, store_path)
        assert idx.git_head == head_a

        # Commit a change to a NON-indexed file only. (Was README.md, but
        # markdown is indexed now; an extensionless LICENSE stays outside
        # every language mapping.)
        (repo / "LICENSE").write_text("hello world\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "docs only")
        head_b = _head(repo)
        assert head_b != head_a

        # Incremental run reports no source-file changes...
        res = index_folder(
            path=str(repo), use_ai_summaries=False, storage_path=store_path,
            incremental=True, identity_mode="local",
        )
        assert res.get("success") is True, res
        assert res.get("changed", 0) == 0
        assert res.get("new", 0) == 0
        assert res.get("deleted", 0) == 0

        # ...but the stored git_head must now track live HEAD, so freshness
        # stops flagging unchanged app.py symbols stale_index.
        idx2 = _load({"repo": res["repo"]}, store_path)
        assert idx2.git_head == head_b

    def test_helper_advances_head_and_is_idempotent(self, tmp_path):
        repo, store_path = self._seed(tmp_path)
        store = IndexStore(base_path=store_path)
        owner, name = index_folder(
            path=str(repo), use_ai_summaries=False, storage_path=store_path,
            incremental=True, identity_mode="local")["repo"].split("/", 1)

        (repo / "README.md").write_text("# changed\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "more docs")
        head_b = _head(repo)

        # Stored head is still the old one — helper advances it.
        wrote = _refresh_git_head_if_advanced(store, owner, name, repo, "deadbeef")
        assert wrote == head_b
        assert store.load_index(owner, name).git_head == head_b

        # Idempotent: already current → no write, returns "".
        again = _refresh_git_head_if_advanced(store, owner, name, repo, head_b)
        assert again == ""


# --------------------------------------------------------------------------- #
# jcm#331 — search_symbols cache hit + dispatcher KeyError                     #
# --------------------------------------------------------------------------- #

class TestSearchSymbolsCacheMeta:
    def _seed(self, tmp_path: Path):
        (tmp_path / "a.py").write_text(
            "def run_status():\n    return 1\n\n"
            "def verify_adapter():\n    return 2\n"
        )
        store_path = str(tmp_path / "idx")
        res = index_folder(
            path=str(tmp_path), use_ai_summaries=False,
            storage_path=store_path, incremental=False, identity_mode="local",
        )
        return res["repo"], store_path

    def test_cache_hit_without_meta_synthesizes_envelope(self, tmp_path):
        from jcodemunch_mcp.tools import search_symbols as ss
        repo, store_path = self._seed(tmp_path)

        kwargs = dict(repo=repo, query="run_status", storage_path=store_path)
        first = ss.search_symbols(**kwargs)
        assert "error" not in first, first

        # Simulate a legacy/restored cache entry that lacks _meta entirely.
        with ss._result_cache_lock:
            assert ss._result_cache, "expected the first call to populate the cache"
            for entry in ss._result_cache.values():
                entry.pop("_meta", None)

        # Second identical call must NOT raise KeyError('_meta').
        second = ss.search_symbols(**kwargs)
        assert "error" not in second, second
        assert second["_meta"]["cache_hit"] is True
        assert "timing_ms" in second["_meta"]


class TestDispatcherKeyError:
    def test_internal_keyerror_is_not_reported_as_missing_argument(self, tmp_path, monkeypatch):
        from jcodemunch_mcp.server import call_tool
        from jcodemunch_mcp.tools import search_symbols as ss

        (tmp_path / "a.py").write_text("def alpha():\n    return 1\n")
        store_path = str(tmp_path / "idx")
        repo = index_folder(
            path=str(tmp_path), use_ai_summaries=False,
            storage_path=store_path, incremental=False, identity_mode="local",
        )["repo"]

        def _boom(*a, **k):
            raise KeyError("internal_cache_key")

        monkeypatch.setattr(ss, "search_symbols", _boom)
        out = asyncio.run(call_tool("search_symbols", {"repo": repo, "query": "alpha"}))
        payload = json.loads(out.content[0].text)  # internal error → CallToolResult (isError, F-P01)
        assert out.isError is True
        assert "Internal error" in payload.get("error", ""), payload
        assert "Missing required argument" not in payload.get("error", ""), payload

    def test_internal_keyerror_carries_tool_name_and_diagnostic(self, tmp_path, monkeypatch):
        # The internal-error envelope must name the tool and surface the key, so
        # an operator can tell a dict-shape bug from a caller schema problem.
        from jcodemunch_mcp.server import call_tool
        from jcodemunch_mcp.tools import search_symbols as ss

        (tmp_path / "a.py").write_text("def alpha():\n    return 1\n")
        store_path = str(tmp_path / "idx")
        repo = index_folder(
            path=str(tmp_path), use_ai_summaries=False,
            storage_path=store_path, incremental=False, identity_mode="local",
        )["repo"]

        def _boom(*a, **k):
            raise KeyError("internal_cache_key")

        monkeypatch.setattr(ss, "search_symbols", _boom)
        out = asyncio.run(call_tool("search_symbols", {"repo": repo, "query": "alpha"}))
        payload = json.loads(out.content[0].text)  # internal error → CallToolResult (isError, F-P01)
        assert out.isError is True
        assert payload.get("error") == "Internal error processing search_symbols", payload
        assert "internal_cache_key" in payload.get("summary", ""), payload


# --------------------------------------------------------------------------- #
# jcm#333 — index_folder(paths=[...]) scopes deletions to the listed subset    #
# --------------------------------------------------------------------------- #

class TestSubsetRefreshScopesDeletions:
    def _full_index(self, root: Path, store_path: str):
        return index_folder(
            path=str(root), use_ai_summaries=False,
            storage_path=store_path, incremental=True, identity_mode="local",
        )

    def test_single_file_subset_preserves_unlisted_file(self, tmp_path):
        (tmp_path / "a.py").write_text("def alpha():\n    return 1\n")
        (tmp_path / "b.py").write_text("def beta():\n    return 2\n")
        store_path = str(tmp_path / "idx")
        first = self._full_index(tmp_path, store_path)
        assert first.get("success") is True, first

        second = index_folder(
            path=str(tmp_path), use_ai_summaries=False, storage_path=store_path,
            incremental=True, paths=["a.py"], identity_mode="local",
        )
        assert second.get("success") is True, second
        assert second.get("deleted", 0) == 0, second

        idx = _load(second, store_path)
        files = set(idx.file_hashes.keys())
        assert "a.py" in files and "b.py" in files, files
        names = {(s.name if hasattr(s, "name") else s["name"]) for s in idx.symbols}
        assert {"alpha", "beta"} <= names, names

    def test_directory_subset_only_diffs_that_subtree(self, tmp_path):
        (tmp_path / "dir").mkdir()
        (tmp_path / "dir" / "a.py").write_text("def alpha():\n    return 1\n")
        (tmp_path / "other").mkdir()
        (tmp_path / "other" / "b.py").write_text("def beta():\n    return 2\n")
        store_path = str(tmp_path / "idx")
        assert self._full_index(tmp_path, store_path).get("success") is True

        res = index_folder(
            path=str(tmp_path), use_ai_summaries=False, storage_path=store_path,
            incremental=True, paths=["dir"], identity_mode="local",
        )
        assert res.get("success") is True, res
        assert res.get("deleted", 0) == 0, res

        idx = _load(res, store_path)
        files = set(idx.file_hashes.keys())
        assert "other/b.py" in files, files
        assert "dir/a.py" in files, files

    def test_listed_file_deleted_on_disk_is_pruned(self, tmp_path):
        (tmp_path / "a.py").write_text("def alpha():\n    return 1\n")
        (tmp_path / "b.py").write_text("def beta():\n    return 2\n")
        store_path = str(tmp_path / "idx")
        assert self._full_index(tmp_path, store_path).get("success") is True

        (tmp_path / "a.py").unlink()
        res = index_folder(
            path=str(tmp_path), use_ai_summaries=False, storage_path=store_path,
            incremental=True, paths=["a.py"], identity_mode="local",
        )
        # Must NOT return "No source files found" — it should prune a.py.
        assert res.get("success") is True, res
        assert res.get("error") != "No source files found", res

        idx = _load(res, store_path)
        files = set(idx.file_hashes.keys())
        assert "a.py" not in files, files
        assert "b.py" in files, files

    def test_listing_root_preserves_full_corpus_deletion(self, tmp_path):
        (tmp_path / "a.py").write_text("def alpha():\n    return 1\n")
        (tmp_path / "b.py").write_text("def beta():\n    return 2\n")
        store_path = str(tmp_path / "idx")
        assert self._full_index(tmp_path, store_path).get("success") is True

        # Delete b.py and refresh with the root explicitly listed.
        (tmp_path / "b.py").unlink()
        res = index_folder(
            path=str(tmp_path), use_ai_summaries=False, storage_path=store_path,
            incremental=True, paths=["."], identity_mode="local",
        )
        assert res.get("success") is True, res

        idx = _load(res, store_path)
        files = set(idx.file_hashes.keys())
        assert "b.py" not in files, files
        assert "a.py" in files, files
