"""A repeated SQL-stem lookup does not re-read every indexed path.

resolve_specifier falls back to SQL stems for every bare name it cannot place
otherwise, and the import-graph builders call it once per import. The stem
cache was keyed by the set of .sql paths, so finding the cached entry meant
reading every indexed path to build that key, once per import of every file.
A frozenset caller now finds its stems without that scan.
"""

from __future__ import annotations

from jcodemunch_mcp.parser import imports as imp


class _CountingFrozenset(frozenset):
    """A frozenset that counts how often it is iterated."""

    iterations = 0

    def __iter__(self):
        type(self).iterations += 1
        return super().__iter__()


def _files():
    return ["models/dim_client.sql", "models/fct_orders.sql", "app/main.py"] + [
        f"pkg/mod_{i}.py" for i in range(200)
    ]


def test_a_repeated_lookup_does_not_iterate_the_file_set(monkeypatch):
    monkeypatch.setattr(imp, "_sql_stem_cache", {})
    monkeypatch.setattr(imp, "_sql_stem_by_files", {}, raising=False)
    files = _CountingFrozenset(_files())
    first = imp._get_sql_stems(files)
    _CountingFrozenset.iterations = 0
    for _ in range(50):
        assert imp._get_sql_stems(files) is first
    assert _CountingFrozenset.iterations == 0


def test_the_answer_is_unchanged(monkeypatch):
    monkeypatch.setattr(imp, "_sql_stem_cache", {})
    monkeypatch.setattr(imp, "_sql_stem_by_files", {}, raising=False)
    files = frozenset(_files())
    expected = {"dim_client": "models/dim_client.sql", "fct_orders": "models/fct_orders.sql"}
    assert imp._get_sql_stems(files) == expected
    assert imp._get_sql_stems(set(files)) == expected
    assert imp.resolve_specifier("dim_client", "models/x.sql", files) == "models/dim_client.sql"


def test_invalidate_cache_clears_both_maps(monkeypatch, tmp_path):
    from jcodemunch_mcp.tools.invalidate_cache import invalidate_cache

    imp._get_sql_stems(frozenset(_files()))
    assert imp._sql_stem_by_files and imp._sql_stem_cache
    invalidate_cache("no/such-repo", storage_path=str(tmp_path))
    assert not imp._sql_stem_by_files and not imp._sql_stem_cache
