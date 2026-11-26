"""Every meta-row field is answered by a selective view without a full load (LEDGER L-344).

`racket_reparse_reason` runs on every `index_folder` call and reads
`racket_reader_generation`. That field is a META row, but it was missing from
`SelectiveIndexView.EXACT_FIELDS`, so on a repository holding `.rkt` files every
run promoted the view to a full `load_index` to read one integer. Its two
siblings (`parser_generation`, `racket_config_digest`) were listed for exactly
this reason. The guard here is over the property: every field
`_build_index_from_rows` builds the index with is copied exactly, unless it is
declared corpus-wide.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

from jcodemunch_mcp.storage import selective, sqlite_store
from jcodemunch_mcp.storage.sqlite_store import SQLiteIndexStore
from jcodemunch_mcp.tools._utils import needs_parser_upgrade, racket_reparse_reason
from jcodemunch_mcp.tools.index_folder import index_folder


def _code_index_keywords(source: str) -> set[str]:
    """Every keyword of every `CodeIndex(...)` call in `source`."""
    tree = ast.parse(textwrap.dedent(source))
    return {
        kw.arg
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "CodeIndex"
        for kw in node.keywords
        if kw.arg
    }


def _not_copied(keywords: set[str], exact, corpus_wide) -> list[str]:
    """Keywords a selective view would promote to read: not copied exactly and
    not declared corpus-wide."""
    return sorted(k for k in keywords if k not in exact and k not in corpus_wide)


def test_every_field_the_index_is_built_with_is_copied_or_declared_corpus_wide():
    """The property, whatever the spelling: a field `_build_index_from_rows`
    passes to `CodeIndex` is answered from the view's own copy unless it is
    declared corpus-wide (today `symbols`). A meta value read through a local,
    a helper or an alias is covered the same as a direct `meta[...]`."""
    keywords = _code_index_keywords(inspect.getsource(SQLiteIndexStore._build_index_from_rows))
    assert {"parser_generation", "racket_config_digest", "racket_reader_generation",
            "coverage", "symbols"} <= keywords, keywords
    missing = _not_copied(keywords, selective.EXACT_FIELDS, selective.CORPUS_WIDE)
    assert missing == [], f"fields a selective view promotes to read: {missing}"


def test_the_property_fails_when_any_copied_field_is_dropped():
    """Non-vacuity: drop each copied field in turn and the check names it."""
    keywords = _code_index_keywords(inspect.getsource(SQLiteIndexStore._build_index_from_rows))
    for field in sorted(keywords & set(selective.EXACT_FIELDS)):
        exact = tuple(f for f in selective.EXACT_FIELDS if f != field)
        assert _not_copied(keywords, exact, selective.CORPUS_WIDE) == [field], field


def _racket_repo(tmp_path):
    repo, store_dir = tmp_path / "repo", tmp_path / "store"
    repo.mkdir()
    (repo / "main.rkt").write_text(
        "#lang racket\n(define (add a b) (+ a b))\n(provide add)\n", encoding="utf-8"
    )
    (repo / "helper.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    r = index_folder(path=str(repo), use_ai_summaries=False, storage_path=str(store_dir))
    assert r.get("success"), r
    store = SQLiteIndexStore(base_path=str(store_dir))
    owner, name = store.list_repos()[0]["repo"].split("/", 1)
    return repo, store_dir, store, owner, name


def test_the_reparse_predicates_answer_from_a_selective_view(tmp_path):
    _repo, _sd, store, owner, name = _racket_repo(tmp_path)
    sqlite_store._cache_clear()
    view = store.open_selective(owner, name)
    assert isinstance(view, selective.SelectiveIndexView)
    assert "racket" in (view.languages or {}), view.languages
    assert view.racket_reader_generation is not None
    racket_reparse_reason(view)
    needs_parser_upgrade(view)
    assert not view.promoted, view._promoted_by


def test_an_incremental_run_on_a_racket_repo_does_not_load_the_whole_index(tmp_path, monkeypatch):
    repo, store_dir, _store, _owner, _name = _racket_repo(tmp_path)
    sqlite_store._cache_clear()
    promoted = []
    real = selective.SelectiveIndexView._promoted

    def recording(self, because="unknown"):
        promoted.append(because)
        return real(self, because)

    monkeypatch.setattr(selective.SelectiveIndexView, "_promoted", recording)
    (repo / "helper.py").write_text("def helper():\n    return 2\n", encoding="utf-8")
    r = index_folder(path=str(repo), use_ai_summaries=False, storage_path=str(store_dir))
    assert r.get("success") and r.get("performed_incremental"), r
    assert promoted == [], promoted
