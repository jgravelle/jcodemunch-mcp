"""Resolving the same import specifier twice does the work once.

The import-graph builders resolve every import of every file, and a large
repository repeats the same specifiers across thousands of files. For any
importer that is not a Gleam file, the absolute, Python-module, alias and
SQL-stem steps of resolve_specifier read only the specifier, the file set and
the alias map, so their answer is memoized per file set. The steps that read
the importer (relative paths, Python script directories, Racket, Web Forms,
Gleam) still run per call.

The memo is only correct if an answer computed for one importer is right for
every other importer, so the equivalence test fills it from one importer and
reads it from all the others.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.parser import imports as imp

FILES = frozenset({
    # Python: a top-level package at the root, a package under a src/ root,
    # and a package that only a package-parents lookup can find.
    "app/__init__.py",
    "app/core.py",
    "app/util/__init__.py",
    "app/util/helpers.py",
    "src/proj/__init__.py",
    "src/proj/mod.py",
    "deep/__init__.py",
    "deep/inner/__init__.py",
    "deep/inner/pkgx/__init__.py",
    "deep/inner/pkgx/leaf.py",
    "scripts/run.py",
    "scripts/checks.py",
    # TypeScript: a root-level path and a same-named path under scripts/.
    "lib/thing.ts",
    "scripts/lib/thing.ts",
    "src/lib/button.ts",
    "src/pages/index.ts",
    # Go, PHP, SQL, Racket, Web Forms, Gleam.
    "pkg/server.go",
    "app/Models/User.php",
    "models/dim_client.sql",
    "racket/main.rkt",
    "web/Default.aspx",
    "pkg/src/pkg/config.gleam",
    "pkg/src/pkg/main.gleam",
})
ALIASES = {"@/*": ["src/*"]}
PSR4 = {"App\\": "app/"}

# Each reaches the importer-independent tail; together they cover its steps.
TAIL_SPECIFIERS = [
    "lib/thing",           # absolute, direct match
    "pkg/server",          # absolute, Go-style path
    "app.core",            # Python module path at the repo root
    "app.util.helpers",
    "proj.mod",            # Python module under a detected source root
    "pkgx.leaf",           # Python module found through its package's parent
    "@/lib/button",        # tsconfig alias
    "dim_client",          # SQL stem
    "App\\Missing\\Thing",  # PSR-4 miss that falls through to the tail
    "no.such.module",      # misses everywhere
    "os",
]
IMPORTERS = [
    "app/core.py",
    "app/util/helpers.py",
    "scripts/run.py",
    "src/proj/mod.py",
    "deep/inner/pkgx/leaf.py",
    "src/pages/index.ts",
    "scripts/x.ts",
    "pkg/server.go",
    "app/Models/User.php",
    "models/x.sql",
    "racket/main.rkt",
    "web/Default.aspx",
]


@pytest.fixture(autouse=True)
def _fresh_memo(monkeypatch):
    monkeypatch.setattr(imp, "_tail_memos", {}, raising=False)


def _direct(spec, importer, aliases):
    """The answer with no memo at all."""
    real = getattr(imp, "_tail_memo", None)
    imp._tail_memo = lambda *a: None
    try:
        return imp.resolve_specifier(spec, importer, FILES, aliases, PSR4)
    finally:
        if real is None:
            del imp._tail_memo
        else:
            imp._tail_memo = real


@pytest.mark.parametrize("aliases", [ALIASES, None], ids=["aliases", "no-aliases"])
@pytest.mark.parametrize("spec", TAIL_SPECIFIERS)
def test_an_answer_cached_for_one_importer_is_right_for_every_other(spec, aliases):
    want = {imp_path: _direct(spec, imp_path, aliases) for imp_path in IMPORTERS}
    for first in IMPORTERS:
        imp._tail_memos.clear()
        imp.resolve_specifier(spec, first, FILES, aliases, PSR4)
        got = {i: imp.resolve_specifier(spec, i, FILES, aliases, PSR4) for i in IMPORTERS}
        assert got == want, f"memo filled from {first}"


def test_the_cases_are_not_vacuous():
    resolved = {s: _direct(s, "app/core.py", ALIASES) for s in TAIL_SPECIFIERS}
    assert resolved["lib/thing"] == "lib/thing.ts"
    assert resolved["proj.mod"] == "src/proj/mod.py"
    assert resolved["pkgx.leaf"] == "deep/inner/pkgx/leaf.py"
    assert resolved["@/lib/button"] == "src/lib/button.ts"
    assert resolved["dim_client"] == "models/dim_client.sql"
    assert resolved["no.such.module"] is None


def test_a_repeated_specifier_does_its_path_work_once(monkeypatch):
    calls = []
    real = imp._candidates

    def counting(base):
        calls.append(base)
        return real(base)

    monkeypatch.setattr(imp, "_candidates", counting)
    imp.resolve_specifier("app.util.helpers", "app/a0.py", FILES, ALIASES)
    once = len(calls)
    assert once > 0
    for i in range(1, 50):
        assert imp.resolve_specifier("app.util.helpers", f"app/a{i}.py", FILES, ALIASES) == (
            "app/util/helpers.py"
        )
    assert len(calls) == once


def test_a_gleam_importer_is_resolved_every_time(monkeypatch):
    calls = []
    real = imp._gleam_source_roots
    monkeypatch.setattr(imp, "_gleam_source_roots", lambda fs: calls.append(1) or real(fs))
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


def test_empty_alias_maps_share_one_memo():
    for _ in range(10):
        imp.resolve_specifier("app.core", "app/x.py", FILES, {})
    imp.resolve_specifier("app.core", "app/x.py", FILES, None)
    (entries,) = imp._tail_memos.values()
    assert len(entries) == 1


def test_invalidate_cache_clears_the_memo(tmp_path):
    from jcodemunch_mcp.tools.invalidate_cache import invalidate_cache

    imp.resolve_specifier("app.core", "app/x.py", FILES, ALIASES)
    assert imp._tail_memos
    invalidate_cache("no/such-repo", storage_path=str(tmp_path))
    assert not imp._tail_memos
