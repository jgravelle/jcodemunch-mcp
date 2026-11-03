"""A resolved import is computed once per file set, not once per tool call (#1317).

Reported by @skibadubskiybadubs on OpenStudio (4,507 files, 39,167 imports):
every graph tool resolved every import of the repository again on every call,
2.3 to 5 s per call, while tools that do not touch the graph answered in 0.01
to 0.1 s. 1.108.396 (#1224) memoized the importer-independent tail; the steps
that read the importer (relative paths, C includes, Python script folders,
Racket, XAML, ASPX) still ran each time.

`resolve_specifier` is a pure function of its arguments, so the whole answer is
memoized per (file set, alias map, PSR-4 map), keyed by importer and
specifier. Every builder that passes the index's own frozenset inherits it.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.parser import imports
from jcodemunch_mcp.parser.imports import clear_resolution_caches, resolve_specifier


@pytest.fixture(autouse=True)
def _fresh():
    clear_resolution_caches()
    yield
    clear_resolution_caches()


@pytest.fixture
def spy(monkeypatch):
    """Every path a resolve tries goes through `_candidates`, which predates the
    memo, so its calls measure how much resolving work a call did."""
    calls = []
    real = imports._candidates

    def counting(base, *rest):
        calls.append(base)
        return real(base, *rest)

    monkeypatch.setattr(imports, "_candidates", counting)
    return calls


FILES = frozenset({"src/a.py", "src/b.py", "lib/b.py", "web/app.js", "web/util.js"})


def test_a_repeat_call_is_answered_from_the_memo(spy):
    assert resolve_specifier("./b", "src/a.py", FILES) == "src/b.py"
    first = len(spy)
    assert first > 0
    assert resolve_specifier("./b", "src/a.py", FILES) == "src/b.py"
    assert len(spy) == first


def test_the_importer_is_part_of_the_key(spy):
    assert resolve_specifier("./b", "src/a.py", FILES) == "src/b.py"
    first = len(spy)
    assert resolve_specifier("./b", "lib/x.py", FILES) == "lib/b.py"
    assert len(spy) > first


def test_a_miss_is_remembered_too(spy):
    assert resolve_specifier("./nothing_here", "src/a.py", FILES) is None
    first = len(spy)
    assert first > 0
    assert resolve_specifier("./nothing_here", "src/a.py", FILES) is None
    assert len(spy) == first


def test_another_psr4_map_is_another_memo():
    files = frozenset({"app/Models/User.php", "src/Models/User.php", "x.php"})
    one = {"App\\": "app/"}
    two = {"App\\": "src/"}
    for _ in range(2):
        assert (
            resolve_specifier("App\\Models\\User", "x.php", files, None, one)
            == "app/Models/User.php"
        )
        assert (
            resolve_specifier("App\\Models\\User", "x.php", files, None, two)
            == "src/Models/User.php"
        )


def test_a_plain_set_is_not_memoized(spy):
    """Only a frozenset can key the memo cheaply; a set is resolved as before."""
    files = set(FILES)
    resolve_specifier("./b", "src/a.py", files)
    first = len(spy)
    resolve_specifier("./b", "src/a.py", files)
    assert len(spy) == 2 * first


def test_clearing_the_caches_clears_the_memo(spy):
    resolve_specifier("./b", "src/a.py", FILES)
    first = len(spy)
    clear_resolution_caches()
    resolve_specifier("./b", "src/a.py", FILES)
    assert len(spy) == 2 * first


def test_a_second_graph_call_resolves_nothing(tmp_path, spy):
    """The user's entry point: the second get_dependency_graph call on one
    loaded index runs no resolution at all."""
    from jcodemunch_mcp.tools.get_dependency_graph import get_dependency_graph
    from jcodemunch_mcp.tools.index_folder import index_folder

    proj = tmp_path / "proj"
    (proj / "pkg").mkdir(parents=True)
    (proj / "pkg" / "a.py").write_text("from . import b\nimport os\n", encoding="utf-8")
    (proj / "pkg" / "b.py").write_text("from .c import f\n", encoding="utf-8")
    (proj / "pkg" / "c.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (proj / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    store = tmp_path / "store"
    res = index_folder(str(proj), use_ai_summaries=False, storage_path=str(store))
    assert res["success"] is True

    first = get_dependency_graph(res["repo"], "pkg/a.py", storage_path=str(store))
    assert spy, "the first call must resolve something for this test to mean anything"
    spy.clear()
    second = get_dependency_graph(res["repo"], "pkg/a.py", storage_path=str(store))
    assert spy == []
    assert second.get("edges") == first.get("edges")


def test_memoized_answers_equal_uncached_ones_over_a_mixed_corpus():
    """The memo may not change one answer. Every (importer, specifier) pair over
    a corpus mixing languages and forms resolves the same from a cleared memo
    and from a memo filled by every other pair."""
    files = frozenset(
        {
            "src/a.py",
            "src/b.py",
            "src/pkg/__init__.py",
            "src/pkg/m.py",
            "web/app.js",
            "web/util.ts",
            "web/index.js",
            "inc/x.h",
            "src/x.cpp",
            "src/y.hpp",
            "include/api/z.h",
            "lib/helper.rb",
            "app/main.rb",
            "views/Main.xaml",
            "views/Main.xaml.cs",
            "rkt/main.rkt",
            "rkt/util.rkt",
        }
    )
    specs = [
        "./b",
        "..",
        "pkg.m",
        ".pkg.m",
        "./util",
        "util",
        "x.h",
        "y.hpp",
        "api/z.h",
        "../lib/helper",
        "lib/helper",
        "Main.xaml.cs",
        "util.rkt",
        "nope",
        "os",
    ]
    want = {}
    for importer in sorted(files):
        for spec in specs:
            clear_resolution_caches()
            want[(importer, spec)] = resolve_specifier(spec, importer, files)
    # The memo now fills from every other pair before each one is read back.
    clear_resolution_caches()
    for _ in range(2):
        for (importer, spec), answer in want.items():
            assert resolve_specifier(spec, importer, files) == answer, (importer, spec)
    assert any(want.values()) and None in want.values()
