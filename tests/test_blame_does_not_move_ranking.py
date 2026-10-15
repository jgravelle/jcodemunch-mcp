"""Search ranking does not depend on whether git blame loaded, or on today's date (L-188).

`GitBlameProvider` gave every symbol of a file it covered the search keywords
`last_author` and `last_modified` (the property NAMES, through
`FileContext.search_keywords`), and BM25 scores those. Which files it covered
depended on two things a pinned corpus does not fix: whether one `git log`
finished inside a 10 s budget (a timeout keeps no blame at all), and a
`2.years.ago` window measured from the day of indexing. Measured on fastapi at a
pinned commit: two fresh indexes of one release, a cold clone and a warm one,
moved two of the five benchmark queries' top 3.

Blame still annotates each file (`ecosystem_context`); it no longer adds search
terms, and its window is measured back from the corpus's own HEAD commit.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from jcodemunch_mcp.parser.context import base as context_base
from jcodemunch_mcp.parser.context import git_blame
from jcodemunch_mcp.parser.context.base import ContextProvider, FileContext, enrich_symbols
from jcodemunch_mcp.parser.context.git_blame import GitBlameProvider
from jcodemunch_mcp.parser.symbols import Symbol


def _git(args, cwd, when=None):
    env = None
    if when is not None:
        stamp = when.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        env = {**os.environ, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}
    r = subprocess.run(
        ["git", "-c", "user.name=Ada Author", "-c", "user.email=a@example.invalid", "-c", "commit.gpgsign=false"]
        + args,
        cwd=str(cwd), capture_output=True, text=True, stdin=subprocess.DEVNULL, env=env,
    )
    assert r.returncode == 0, r.stderr
    return r.stdout


def _commit(repo: Path, rel: str, body: str, when: datetime) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    _git(["add", rel], repo)
    _git(["commit", "-q", "-m", rel], repo, when=when)


def _symbol(file: str, name: str) -> Symbol:
    return Symbol(id=f"{file}::{name}#function", file=file, name=name, qualified_name=name,
                  kind="function", language="python", signature=f"def {name}():")


def _blame_for(file: str) -> GitBlameProvider:
    prov = GitBlameProvider()
    prov._blame = {file: ("Ada Author", "2026-05-05")}
    return prov


# --- blame adds no search terms -------------------------------------------------- #


def test_blame_gives_a_symbol_no_search_keywords():
    sym = _symbol("app/handlers.py", "handle_error")
    enrich_symbols([sym], [_blame_for("app/handlers.py")])
    assert "last_author" not in sym.keywords
    assert "last_modified" not in sym.keywords
    assert sym.keywords == []


def test_blame_still_annotates_the_file():
    sym = _symbol("app/handlers.py", "handle_error")
    enrich_symbols([sym], [_blame_for("app/handlers.py")])
    assert "git_blame" in sym.ecosystem_context
    assert "Ada Author" in sym.ecosystem_context


def test_a_blame_timeout_leaves_the_keywords_identical(monkeypatch, tmp_path):
    """The two states the defect switched between: blame loaded, and blame timed out."""
    loaded = _symbol("app/handlers.py", "handle_error")
    enrich_symbols([loaded], [_blame_for("app/handlers.py")])

    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=git_blame.GIT_BLAME_TIMEOUT_S)

    empty = GitBlameProvider()
    monkeypatch.setattr(git_blame.subprocess, "run", timed_out)
    empty.load(tmp_path)
    lost = _symbol("app/handlers.py", "handle_error")
    enrich_symbols([lost], [empty])
    assert loaded.keywords == lost.keywords


class _Tagged(ContextProvider):
    """A provider whose properties ARE vocabulary, as a framework provider's are."""

    @property
    def name(self) -> str:
        return "tagged"

    def detect(self, folder_path: Path) -> bool:
        return True

    def load(self, folder_path: Path) -> None:
        pass

    def get_file_context(self, file_path: str):
        return FileContext(tags=["route"], properties={"materialized": "table"})

    def stats(self) -> dict:
        return {}


def test_other_providers_keep_their_properties_as_search_terms():
    sym = _symbol("models/orders.sql", "orders")
    enrich_symbols([sym], [_Tagged()])
    assert sym.keywords == ["route", "materialized"]


def test_only_providers_that_opt_out_lose_their_property_keywords():
    assert context_base.ContextProvider.properties_are_search_terms is True
    assert GitBlameProvider.properties_are_search_terms is False


# --- the window is the corpus's, not the calendar's ---------------------------- #


@pytest.fixture
def old_repo(tmp_path):
    """A corpus whose whole history is years old: HEAD in mid-2020."""
    repo = tmp_path / "corpus"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(repo, "ancient.py", "def a():\n    return 1\n", datetime(2017, 3, 1, tzinfo=timezone.utc))
    _commit(repo, "recent.py", "def r():\n    return 2\n", datetime(2020, 1, 15, tzinfo=timezone.utc))
    _commit(repo, "head.py", "def h():\n    return 3\n", datetime(2020, 6, 1, tzinfo=timezone.utc))
    return repo


def test_files_inside_the_window_from_head_keep_blame_on_any_day(old_repo):
    prov = GitBlameProvider()
    prov.load(old_repo)
    assert prov.get_file_context("recent.py") is not None, "changed 4.5 months before HEAD"
    assert prov.get_file_context("head.py") is not None


def test_files_older_than_the_window_before_head_have_none(old_repo):
    prov = GitBlameProvider()
    prov.load(old_repo)
    assert prov.get_file_context("ancient.py") is None, "changed over 3 years before HEAD"


def test_an_unreadable_head_falls_back_to_the_relative_window(tmp_path, monkeypatch):
    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(git_blame.subprocess, "run", fake_run)
    GitBlameProvider().load(tmp_path)
    log = seen[-1]
    assert f"--since={git_blame.GIT_BLAME_SINCE}" in log


# --- an existing index loses the old keywords ------------------------------------ #


def _keywords(store: Path, repo_id: str) -> dict[str, list[str]]:
    from jcodemunch_mcp.storage import IndexStore

    owner, name = repo_id.split("/", 1)
    idx = IndexStore(base_path=str(store)).load_index(owner, name)
    return {s["name"]: list(s.get("keywords") or []) for s in idx.symbols}


def test_an_index_written_by_the_old_release_is_reparsed_without_the_keywords(tmp_path, monkeypatch):
    """Re-enrichment runs only on re-parsed files, so without a generation bump an
    edited file would lose the keywords while untouched ones kept them: one index
    in both states, the same skew. 21 is the generation the old keywords shipped at.
    """
    from jcodemunch_mcp.storage import sqlite_store
    from jcodemunch_mcp.tools.index_folder import index_folder

    repo = tmp_path / "svc"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _commit(repo, "errors.py", "def error_handler(e):\n    return str(e)\n", datetime.now(timezone.utc))
    store = tmp_path / "store"

    sqlite_store._ensure_index_store_deps()
    with monkeypatch.context() as old_release:
        old_release.setattr(sqlite_store, "_PARSER_GENERATION", 21)
        old_release.setattr(GitBlameProvider, "properties_are_search_terms", True)
        res = index_folder(str(repo), use_ai_summaries=False, storage_path=str(store), identity_mode="local")
    assert "last_author" in _keywords(store, res["repo"])["error_handler"], "the old index must carry them"

    index_folder(str(repo), use_ai_summaries=False, storage_path=str(store), identity_mode="local")
    assert "last_author" not in _keywords(store, res["repo"])["error_handler"]


# --- at the user's entry point ---------------------------------------------------- #


def _index_and_search(repo: Path, store: Path, blame_times_out: bool, monkeypatch):
    from jcodemunch_mcp.tools.index_folder import index_folder
    from jcodemunch_mcp.tools.search_symbols import search_symbols

    real_load = GitBlameProvider.load

    def load(self, folder_path):
        if blame_times_out:
            self._folder = folder_path
            return
        real_load(self, folder_path)

    monkeypatch.setattr(GitBlameProvider, "load", load)
    res = index_folder(str(repo), use_ai_summaries=False, storage_path=str(store), identity_mode="local")
    assert res.get("success", True), res
    out = search_symbols(repo=res["repo"], query="error handler", max_results=5, storage_path=str(store), debug=True)
    return [(r["id"], r.get("score")) for r in out["results"]]


def test_search_scores_are_the_same_whether_blame_loaded_or_timed_out(tmp_path, monkeypatch):
    repo = tmp_path / "svc"
    repo.mkdir()
    _git(["init", "-q"], repo)
    # One file changed long ago and one changed now, so blame covers one and not
    # the other: the shape that moved fastapi's ranking.
    _commit(repo, "errors.py", "def error_handler(e):\n    return str(e)\n", datetime(2015, 1, 5, tzinfo=timezone.utc))
    _commit(repo, "views.py", "def handler(request):\n    return request\n\n\ndef render_error():\n    return 1\n",
            datetime.now(timezone.utc))
    with_blame = _index_and_search(repo, tmp_path / "store-a", False, monkeypatch)
    without = _index_and_search(repo, tmp_path / "store-b", True, monkeypatch)
    assert with_blame, "the search must find something for the comparison to mean anything"
    assert with_blame == without
