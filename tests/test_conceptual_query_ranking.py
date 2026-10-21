"""A question about how code works ranks the code above its tests.

"How does dependency injection work?" scored "how" and "does" as content, so
on fastapi every ``test_*_does_not_*`` and ``test_*_works`` outranked the
functions that implement dependency resolution, and the top 10 was mostly
tests. Two rules, both in ``retrieval/scoring.py`` so every lexical scorer
inherits them:

- ``query_terms`` drops function words ("how", "does", "the", ...) from the
  QUERY, unless that would leave nothing;
- ``test_file_factor`` demotes a symbol in a test file (``_test_paths``'s one
  rule) when the query is not about tests.

The pinned fastapi case these were measured on is outside the test suite: it
needs a clone. The fixture below has the same shape in miniature.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jcodemunch_mcp.retrieval import scoring
from jcodemunch_mcp.tools.get_ranked_context import get_ranked_context
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.search_symbols import search_symbols


# --- query_terms ---------------------------------------------------------


def test_function_words_are_dropped_from_a_question():
    terms = scoring.query_terms("How does dependency injection work?")
    assert "how" not in terms and "does" not in terms
    assert "dependency" in terms and "injection" in terms


def test_a_query_of_only_function_words_keeps_them():
    """An identifier such as ``is`` or ``do`` must still resolve."""
    assert scoring.query_terms("is") == ["is"]
    assert scoring.query_terms("do it") == scoring._tokenize("do it")


def test_identifier_parts_survive():
    assert scoring.query_terms("is_valid") == [t for t in scoring._tokenize("is_valid") if t != "is"]


def test_the_empty_query_contract_holds():
    assert scoring.query_terms("") == []
    assert scoring.query_terms("???") == ["???"]
    assert "" not in scoring.query_terms("how does  work")


# --- test_file_factor ------------------------------------------------------


@pytest.mark.parametrize("path", ["tests/test_deps.py", "pkg/tests/helpers.py", "test_api.py"])
def test_a_test_symbol_is_demoted_for_a_non_test_query(path):
    assert scoring.test_file_factor({"file": path}, ["dependency"]) == scoring.TEST_FILE_FACTOR < 1.0


@pytest.mark.parametrize("path", ["pkg/dependencies/utils.py", "src/app.py"])
def test_source_is_not_demoted(path):
    assert scoring.test_file_factor({"file": path}, ["dependency"]) == 1.0


@pytest.mark.parametrize("word", ["test", "tests", "fixture", "mock", "spec"])
def test_a_query_about_tests_demotes_nothing(word):
    assert scoring.test_file_factor({"file": "tests/test_deps.py"}, ["dependency", word]) == 1.0


# --- end to end ---------------------------------------------------------------

SOURCE = '''
class Depends:
    """Declare a dependency for a path operation."""

    def __init__(self, dependency=None):
        self.dependency = dependency


def get_dependant(call):
    """Build the dependency tree for a callable."""
    return [call]


def solve_dependencies(request, dependant):
    """Resolve every dependency of a path operation for one request."""
    return [d for d in dependant]


def analyze_param(name, annotation):
    """Decide whether a parameter is a dependency, a query or a body."""
    return name
'''

# The fastapi shape: most tests match the question through "does", "works"
# and "not", and a few name the concept itself.
TESTS = "\n".join(
    f"def test_route_{i}_does_not_match_websocket():\n    pass\n\n"
    f"def test_endpoint_{i}_works():\n    pass\n"
    for i in range(12)
) + "\ndef test_response_without_depends():\n    pass\n\ndef test_response_dependency_chain():\n    pass\n"

WANT = {"solve_dependencies", "get_dependant", "Depends", "analyze_param"}


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "proj"
    (root / "app" / "dependencies").mkdir(parents=True)
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "app" / "dependencies" / "utils.py").write_text(SOURCE, encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_dependencies.py").write_text(TESTS, encoding="utf-8")
    store = tmp_path / "store"
    out = index_folder(str(root), use_ai_summaries=False, storage_path=str(store), identity_mode="local")
    assert out.get("success"), out
    return out["repo"], str(store)


def _names(rows):
    return [r.get("name") for r in rows]


@pytest.mark.parametrize("q", ["How does dependency injection work?", "how are dependencies resolved"])
def test_search_symbols_ranks_the_implementation_above_its_tests(repo, q):
    rid, store = repo
    rows = search_symbols(repo=rid, query=q, max_results=10, storage_path=store)["results"]
    assert rows and rows[0]["name"] in WANT, _names(rows)
    assert not any(r["file"].startswith("tests/") for r in rows[:4]), _names(rows)


def test_get_ranked_context_leads_with_the_implementation(repo):
    rid, store = repo
    items = get_ranked_context(repo=rid, query="How does dependency injection work?",
                               token_budget=2000, storage_path=store)["context_items"]
    assert items and items[0]["name"] in WANT, _names(items)


def test_a_query_about_tests_still_returns_tests_first(repo):
    rid, store = repo
    rows = search_symbols(repo=rid, query="tests for the endpoint that works", max_results=5, storage_path=store)["results"]
    assert rows and all(r["file"].startswith("tests/") for r in rows), _names(rows)
