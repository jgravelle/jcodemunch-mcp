"""v1.108.107 — audit W1: the watcher fast path re-enriches changed symbols.

The watcher fast path set active_providers=[] to skip the ~hundreds-of-ms
provider discovery walk, but that also skipped enrich_symbols, so a watched edit
dropped a symbol's provider enrichment (git-blame keywords / ecosystem_context)
until the next full reindex. Discovery is the expensive part and providers don't
change between edits, so the discovered set is now cached per folder and reused
on the fast path; enrichment itself is cheap.
"""

from __future__ import annotations

import subprocess

import pytest

from jcodemunch_mcp.reindex_state import WatcherChange
from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.tools import index_folder as _if
from jcodemunch_mcp.tools.index_folder import index_folder


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, stdin=subprocess.DEVNULL)


def _git_repo(tmp_path):
    _git(["init"], tmp_path)
    _git(["config", "user.email", "t@t.co"], tmp_path)
    _git(["config", "user.name", "Tester"], tmp_path)
    f = tmp_path / "mod.py"
    f.write_text("def alpha():\n    return 1\n")
    _git(["add", "-A"], tmp_path)
    _git(["commit", "-m", "init"], tmp_path)
    return f


def _load(store_path):
    st = IndexStore(base_path=store_path)
    repos = st.list_repos()
    assert repos, "no repo indexed"
    owner, name = repos[0]["repo"].split("/", 1)
    return {s["name"]: s for s in st.load_index(owner, name).symbols}


@pytest.fixture(autouse=True)
def _clear_provider_cache():
    _if._PROVIDER_CACHE.clear()
    yield
    _if._PROVIDER_CACHE.clear()


def test_full_index_populates_provider_cache_and_enriches(tmp_path):
    _git_repo(tmp_path)
    store_path = str(tmp_path / ".code-index")
    r = index_folder(path=str(tmp_path), use_ai_summaries=False,
                     storage_path=store_path, incremental=False)
    assert r["success"]
    # git-blame provider ran → symbols carry its annotation. (Its property names
    # stopped being search keywords in L-188, so the annotation is the witness.)
    syms = _load(store_path)
    assert "git_blame" in syms["alpha"]["ecosystem_context"]
    # and the discovered providers are cached for the fast path
    assert str(tmp_path) in _if._PROVIDER_CACHE
    assert any(p.name == "git_blame" for p in _if._PROVIDER_CACHE[str(tmp_path)])


def test_watched_edit_preserves_git_blame_enrichment(tmp_path):
    f = _git_repo(tmp_path)
    store_path = str(tmp_path / ".code-index")
    index_folder(path=str(tmp_path), use_ai_summaries=False,
                 storage_path=store_path, incremental=False)

    # Edit the file (adds a new symbol) and drive the watcher fast path.
    f.write_text("def alpha():\n    return 1\n\ndef beta():\n    return 2\n")
    changes = [WatcherChange("modified", str(f.resolve()), "old_hash_that_differs")]
    r2 = index_folder(path=str(tmp_path), use_ai_summaries=False,
                      storage_path=store_path, incremental=True,
                      changed_paths=changes)
    assert r2["success"]

    syms = _load(store_path)
    # The freshly re-parsed symbols must still carry git-blame enrichment, not
    # lose it on the fast path (the W1 regression).
    assert "beta" in syms, syms.keys()
    assert "git_blame" in syms["beta"]["ecosystem_context"], syms["beta"]["ecosystem_context"]
    assert "git_blame" in syms["alpha"]["ecosystem_context"], syms["alpha"]["ecosystem_context"]


def test_fast_path_providers_discovers_once_on_cache_miss(tmp_path, monkeypatch):
    # Cache miss (e.g. first fast cycle after a restart) discovers + caches,
    # then subsequent calls reuse without re-discovering.
    _git_repo(tmp_path)
    calls = {"n": 0}
    real = _if.discover_providers

    # **kwargs so this stays a call COUNTER, not a signature pin: v1.108.182
    # added budget_seconds/skipped, and a stub that rejects them would fail
    # here for a reason that has nothing to do with caching.
    def counting(folder, **kwargs):
        calls["n"] += 1
        return real(folder, **kwargs)

    monkeypatch.setattr(_if, "discover_providers", counting)
    p1 = _if._fast_path_providers(tmp_path, True)
    p2 = _if._fast_path_providers(tmp_path, True)
    assert calls["n"] == 1  # discovered once, then served from cache
    assert p1 is p2
    assert any(p.name == "git_blame" for p in p1)
