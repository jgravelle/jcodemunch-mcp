"""Resolving the same import specifier twice does the work once.

The import-graph builders resolve every import of every file, and a large
repository repeats the same specifiers across thousands of files. For any
importer that is not a Gleam file, the absolute, Python-module, alias and
SQL-stem steps of resolve_specifier read only the specifier, the file set and
the alias map, so their answer is memoized per file set. The steps that read
the importer (relative paths, Python script directories, Racket, Web Forms,
Gleam) still run per call.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.parser import imports as imp

FILES = frozenset({
    "app/__init__.py",
    "app/core.py",
    "app/util/helpers.py",
    "app/util/__init__.py",
    "src/lib/button.ts",
    "src/pages/index.ts",
    "models/dim_client.sql",
    "pkg/src/pkg/config.gleam",
    "pkg/src/pkg/main.gleam",
    "scripts/run.py",
    "scripts/checks.py",
})
ALIASES = {"@/*": ["src/*"]}

CASES = [
    ("app.core", "app/util/helpers.py"),
    ("app.util.helpers", "scripts/run.py"),
    (".helpers", "app/util/__init__.py"),
    ("..core", "app/util/helpers.py"),
    ("checks", "scripts/run.py"),
    ("@/lib/button", "src/pages/index.ts"),
    ("./../lib/button", "src/pages/index.ts"),
    ("dim_client", "models/x.sql"),
    ("pkg/config", "pkg/src/pkg/main.gleam"),
    ("no.such.module", "app/core.py"),
    ("os", "app/core.py"),
]


@pytest.fixture(autouse=True)
def _fresh_memo(monkeypatch):
    monkeypatch.setattr(imp, "_tail_memos", {}, raising=False)


def _unmemoized(spec, importer):
    real = imp._tail_memo
    imp._tail_memo = lambda *a: None
    try:
        return imp.resolve_specifier(spec, importer, FILES, ALIASES)
    finally:
        imp._tail_memo = real


@pytest.mark.parametrize("spec,importer", CASES)
def test_the_memoized_answer_matches_the_direct_one(spec, importer):
    want = _unmemoized(spec, importer)
    assert imp.resolve_specifier(spec, importer, FILES, ALIASES) == want
    assert imp.resolve_specifier(spec, importer, FILES, ALIASES) == want


def test_a_repeated_specifier_is_resolved_once(monkeypatch):
    calls = []
    real = imp._resolve_tail

    def counting(*args):
        calls.append(args[0])
        return real(*args)

    monkeypatch.setattr(imp, "_resolve_tail", counting, raising=False)
    importers = [f"app/m{i}.py" for i in range(50)]
    for importer in importers:
        assert imp.resolve_specifier("app.core", importer, FILES, ALIASES) == "app/core.py"
    assert calls == ["app.core"]


def test_a_gleam_importer_is_never_memoized(monkeypatch):
    calls = []
    real = imp._resolve_tail
    monkeypatch.setattr(imp, "_resolve_tail", lambda *a: calls.append(a[0]) or real(*a), raising=False)
    for _ in range(3):
        assert imp.resolve_specifier("pkg/config", "pkg/src/pkg/main.gleam", FILES) == (
            "pkg/src/pkg/config.gleam"
        )
    assert len(calls) == 3


def test_a_different_alias_map_gets_its_own_answers():
    assert imp.resolve_specifier("@/lib/button", "src/pages/index.ts", FILES, ALIASES) == (
        "src/lib/button.ts"
    )
    assert imp.resolve_specifier("@/lib/button", "src/pages/index.ts", FILES, {}) is None


def test_invalidate_cache_clears_the_memo(tmp_path):
    from jcodemunch_mcp.tools.invalidate_cache import invalidate_cache

    imp.resolve_specifier("app.core", "app/x.py", FILES, ALIASES)
    assert imp._tail_memos
    invalidate_cache("no/such-repo", storage_path=str(tmp_path))
    assert not imp._tail_memos
