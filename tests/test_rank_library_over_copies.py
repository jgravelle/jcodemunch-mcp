"""A question about a library ranks the library above its teaching copies and
above the lines that only load it.

Two shapes outranked the definition on the pinned benchmark corpora:

- copies in example and tutorial directories. fastapi's ``docs_src/`` holds
  dozens of ``GzipRoute.get_route_handler`` tutorials beside the one
  ``APIRoute.get_route_handler`` in ``fastapi/routing.py``, and "router route
  handler" returned three tutorials; express's ``examples/error-pages`` EJS
  views answered "error exception";
- module-load bindings. ``var Route = require('./router/route');`` carries the
  definition's name and none of its code, and ``var middleware =
  require('./middleware/init')`` was promoted to first place as an exact-name
  match for "middleware".

``rank_factor`` in ``retrieval/scoring.py`` now weights both, and
``_declaration_rank`` no longer promotes a binding. A query that asks for an
example, or about imports and exports, keeps the old weights, and a demoted
symbol still comes back when nothing else matches.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.retrieval import scoring
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.search_symbols import _declaration_rank, search_symbols


# --- the two predicates -------------------------------------------------------


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


@pytest.mark.parametrize(
    "sig",
    [
        "var Route = require('./router/route');",
        "var debug = require('debug')('express:application');",
        "var resolve = require('path').resolve;",
        "const mod = await import('./mod.js')",
    ],
)
def test_module_load_bindings_are_recognised(sig):
    assert scoring.is_binding_symbol({"kind": "variable", "signature": sig})
    assert scoring.is_binding_symbol({"kind": "constant", "signature": sig})


@pytest.mark.parametrize(
    "sym",
    [
        {"kind": "variable", "signature": "var app = module.exports = express();"},
        {
            "kind": "variable",
            "signature": "var hasOwnProperty = Object.prototype.hasOwnProperty",
        },
        {"kind": "constant", "signature": "const required = true"},
        {"kind": "function", "signature": "function require(path)"},
        {"kind": "variable", "signature": "var same = x == require('a')"},
        {
            "kind": "constant",
            "signature": "const settings = { db: 1, store: x = require('./store') };",
        },
        {"kind": "variable"},
    ],
)
def test_ordinary_values_are_not_bindings(sym):
    assert not scoring.is_binding_symbol(sym)


def test_an_import_row_is_a_binding():
    assert scoring.is_binding_symbol(
        {"kind": "import", "signature": "include('error_header')"}
    )


# --- rank_factor, both directions -------------------------------------------


EXAMPLE = {
    "file": "docs_src/route/tutorial001.py",
    "kind": "method",
    "language": "python",
}
BINDING = {
    "file": "lib/express.js",
    "kind": "variable",
    "language": "javascript",
    "signature": "var Route = require('./router/route');",
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


def test_a_binding_is_weighted_below_an_example():
    weight, reason = scoring.rank_factor(BINDING, scoring.query_terms("route"))
    assert reason == "binding" and weight < scoring.EXAMPLE_WEIGHT


@pytest.mark.parametrize(
    "q", ["where is Route required", "Route import", "what does express export"]
)
def test_a_binding_is_not_weighted_when_the_query_is_about_imports(q):
    assert scoring.rank_factor(BINDING, scoring.query_terms(q)) == (1.0, None)


def test_a_test_file_keeps_its_own_reason():
    sym = {"file": "tests/examples/test_a.py", "kind": "function", "language": "python"}
    assert scoring.rank_factor(sym, ["route"])[1] == "test_file"


def test_an_exact_name_binding_is_not_promoted_as_a_declaration():
    needles = ("middleware", "middleware")
    binding = {
        "id": "lib/application.js::middleware#variable",
        "name": "middleware",
        "kind": "variable",
        "signature": "var middleware = require('./middleware/init');",
    }
    value = {
        "id": "lib/application.js::middleware#variable",
        "name": "middleware",
        "kind": "variable",
        "signature": "var middleware = []",
    }
    assert _declaration_rank(binding, None, needles) == 0
    assert _declaration_rank(value, None, needles) == 1


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

APPLICATION_JS = """
var middleware = require('./middleware/init');
var Route = require('./router/route');

/**
 * Mount the middleware function fn at path.
 */
function use(fn) {
  return fn;
}

module.exports = use;
"""

ROUTE_JS = """
/**
 * A route: the handlers for one path.
 */
function Route(path) {
  this.path = path;
}

module.exports = Route;
"""


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
    (root / "lib" / "router").mkdir(parents=True)
    (root / "lib" / "application.js").write_text(APPLICATION_JS, encoding="utf-8")
    (root / "lib" / "router" / "route.js").write_text(ROUTE_JS, encoding="utf-8")
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


def test_a_require_binding_does_not_answer_a_name_question(repo):
    rid, store = repo
    rows = search_symbols(
        repo=rid, query="middleware", max_results=5, storage_path=store
    )["results"]
    assert rows and rows[0]["id"] != "lib/application.js::middleware#variable", _ids(
        rows
    )
    assert "lib/application.js::use#function" in _ids(rows[:2]), _ids(rows)


def test_a_binding_still_ranks_when_nothing_else_matches(repo):
    rid, store = repo
    rows = search_symbols(repo=rid, query="init", max_results=5, storage_path=store)[
        "results"
    ]
    assert "lib/application.js::middleware#variable" in _ids(rows), _ids(rows)


def test_a_routing_question_ranks_the_definition_above_its_binding(repo):
    """express: "router route" returned `var Route = require('./router/route')`
    first. Fails on the unfixed tree, where the binding is row 1."""
    rid, store = repo
    rows = search_symbols(
        repo=rid, query="router route", max_results=5, storage_path=store
    )["results"]
    ids = _ids(rows)
    binding = "lib/application.js::Route#variable"
    assert ids and ids[0] != binding, ids
    definition = "lib/router/route.js::Route#function"
    assert definition in ids, ids
    assert binding not in ids or ids.index(definition) < ids.index(binding), ids
