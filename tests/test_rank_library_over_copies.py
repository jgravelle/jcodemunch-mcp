"""A question about a library ranks the library above its teaching copies.

fastapi's ``docs_src/`` holds ``GzipRoute.get_route_handler`` tutorials beside
the one ``APIRoute.get_route_handler`` in ``fastapi/routing.py``, and "router
route handler" on the pinned benchmark corpus returned three tutorials.
``rank_factor`` in ``retrieval/scoring.py`` now weights a symbol under an
example or tutorial directory. A query that asks for an example keeps the old
weight, and a demoted symbol still comes back when nothing else matches.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.retrieval import scoring
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.search_symbols import search_symbols


# --- the predicate -------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "docs_src/custom_request_and_route/tutorial001.py",
        "examples/error-pages/index.js",
        "Example/app.go",
        "pkg/samples/basic.rs",
        "demo/main.ts",
        "site/tutorials/step1.py",
    ],
)
def test_example_directories_are_recognised(path):
    assert scoring.is_example_path(path)


@pytest.mark.parametrize(
    "path",
    [
        "fastapi/routing.py",
        "lib/router/route.js",
        "examples.py",  # the filename is never read
        "src/example_utils.py",
        "docs/conf.py",  # a docs directory can hold a site's own source
        "lib/demonstrate.js",
        # A directory whose name only starts or ends with an example word.
        "exampleservice/x.py",
        "docs_srcx/a.py",
        "samplers/run.py",
        "src/demonstration/app.js",
        "my_examples/x.py",
    ],
)
def test_library_paths_are_not_examples(path):
    assert not scoring.is_example_path(path)


# --- rank_factor, both directions -------------------------------------------


EXAMPLE = {
    "file": "docs_src/route/tutorial001.py",
    "kind": "method",
    "language": "python",
}


def test_an_example_is_weighted_on_a_library_question():
    assert scoring.rank_factor(
        EXAMPLE, scoring.query_terms("router route handler")
    ) == (scoring.EXAMPLE_WEIGHT, "example_file")


@pytest.mark.parametrize(
    "q",
    [
        "route handler example",
        "gzip route examples",
        "route tutorial",
        "custom route docs",
        "demo app",
    ],
)
def test_an_example_is_not_weighted_when_the_query_asks_for_one(q):
    assert scoring.rank_factor(EXAMPLE, scoring.query_terms(q)) == (1.0, None)


def test_a_test_file_keeps_its_own_reason():
    sym = {"file": "tests/examples/test_a.py", "kind": "function", "language": "python"}
    assert scoring.rank_factor(sym, ["route"])[1] == "test_file"


# --- through search_symbols ---------------------------------------------------


LIBRARY = """
class APIRoute:
    def get_route_handler(self):
        # No docstring, as in fastapi/routing.py: the name is the only match.
        return None
"""

TUTORIAL = '''
from pkg.routing import APIRoute


class GzipRoute(APIRoute):
    def get_route_handler(self):
        """Route handler for a gzip route: wrap the route handler."""
        return super().get_route_handler()
'''


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "proj"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "routing.py").write_text(LIBRARY, encoding="utf-8")
    docs = root / "docs_src" / "custom_route"
    docs.mkdir(parents=True)
    for i in range(1, 4):
        (docs / f"tutorial00{i}.py").write_text(TUTORIAL, encoding="utf-8")
    store = tmp_path / "store"
    out = index_folder(
        str(root),
        use_ai_summaries=False,
        storage_path=str(store),
        identity_mode="local",
    )
    assert out.get("success"), out
    return out["repo"], str(store)


def _ids(rows):
    return [r["id"] for r in rows]


def test_the_library_definition_ranks_above_its_tutorial_copies(repo):
    rid, store = repo
    rows = search_symbols(
        repo=rid, query="route handler", max_results=5, storage_path=store
    )["results"]
    assert rows and rows[0]["file"] == "pkg/routing.py", _ids(rows)


def test_a_query_for_the_example_still_gets_the_example(repo):
    rid, store = repo
    rows = search_symbols(
        repo=rid, query="gzip route handler example", max_results=5, storage_path=store
    )["results"]
    assert rows and rows[0]["file"].startswith("docs_src/"), _ids(rows)


def test_a_demoted_example_still_comes_back_when_nothing_else_matches(repo):
    """Only the tutorials define a gzip route; the weight reorders, it never drops."""
    rid, store = repo
    rows = search_symbols(repo=rid, query="gzip", max_results=5, storage_path=store)[
        "results"
    ]
    assert rows and all(r["file"].startswith("docs_src/") for r in rows), _ids(rows)


def test_a_served_debug_row_names_the_example_weight(repo):
    """The CHANGELOG's debug label, checked on a served row, not only at rank_factor."""
    rid, store = repo
    rows = search_symbols(
        repo=rid, query="route handler", max_results=5, storage_path=store, debug=True
    )["results"]
    tutorial = [r for r in rows if r["file"].startswith("docs_src/")]
    assert tutorial, _ids(rows)
    assert all(
        r["score_breakdown"].get("rank_weight_reason") == "example_file"
        for r in tutorial
    ), tutorial
    library = [r for r in rows if r["file"] == "pkg/routing.py"]
    assert library and all(
        r["score_breakdown"].get("rank_weight_reason") != "example_file"
        for r in library
    ), library
