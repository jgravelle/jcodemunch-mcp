"""A question about how code works ranks the code above its tests.

"How does dependency injection work?" scored "how" and "does" as content, so
on fastapi every ``test_*_does_not_*`` and ``test_*_works`` outranked the
functions that implement dependency resolution, and the top 10 was mostly
tests. Two rules, both in ``retrieval/scoring.py`` so every lexical scorer
inherits them:

- ``query_idf`` counts function words ("how", "does", "the", ...) at
  ``FUNCTION_WORD_WEIGHT`` in a multi-word query, never in a name, so they
  can no longer carry a match on their own but still separate ``is_valid``
  from ``valid_values`` when the query is "is valid";
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


# --- query_terms / function_word_terms / query_idf ---------------------------


def test_query_terms_keep_every_token():
    terms = scoring.query_terms("How does dependency injection work?")
    assert "how" in terms and "does" in terms and "dependency" in terms


def test_function_words_of_a_question_are_named():
    assert scoring.function_word_terms("How does dependency injection work?") == {"how", "does"}


@pytest.mark.parametrize("name", ["is_valid", "ndcg_at_k", "getTheUser", "os.path", "do"])
def test_a_one_word_query_has_no_function_words(name):
    """A one-word query is a name; the "at" in ndcg_at_k is content, not filler."""
    assert scoring.function_word_terms(name) == frozenset()
    assert scoring.query_terms(name) == scoring._tokenize(name)


def test_identifier_parts_are_not_function_words_inside_a_question():
    assert scoring.function_word_terms("how does ndcg_at_k work") == {"how", "does"}


def test_a_camel_case_word_is_not_a_plain_word():
    fw = scoring.function_word_terms("where is getTheUser called")
    assert "the" not in fw and {"is", "where"} <= fw


def test_a_query_of_only_function_words_keeps_full_weight():
    """An identifier such as ``is`` or ``do`` must still resolve."""
    assert scoring.function_word_terms("do it") == frozenset()


def test_query_idf_weights_function_words_and_nothing_else():
    idf = {"how": 2.0, "does": 3.0, "dependency": 5.0, "injection": 7.0}
    q = scoring.query_idf(idf, "How does dependency injection work?")
    assert q["how"] == 2.0 * scoring.FUNCTION_WORD_WEIGHT
    assert q["does"] == 3.0 * scoring.FUNCTION_WORD_WEIGHT
    assert q["dependency"] == 5.0 and q["injection"] == 7.0
    assert 0 < scoring.FUNCTION_WORD_WEIGHT < 1
    assert idf["how"] == 2.0  # the shared corpus map is not edited


def test_the_empty_query_contract_holds():
    assert scoring.query_terms("") == []
    assert scoring.query_terms("???") == ["???"]
    assert "" not in scoring.query_terms("how does  work")


# --- test_file_factor ------------------------------------------------------


@pytest.mark.parametrize("path", ["tests/test_deps.py", "pkg/tests/helpers.py", "test_api.py"])
def test_a_test_symbol_is_demoted_for_a_non_test_query(path):
    assert scoring.test_file_factor({"file": path}, ["dependency"]) == scoring.TEST_SYMBOL_WEIGHT < 1.0


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


@pytest.fixture
def names_repo(tmp_path):
    """Identifiers whose parts are function words, beside their neighbours."""
    root = tmp_path / "names"
    (root / "app").mkdir(parents=True)
    (root / "app" / "core.py").write_text(
        "def is_valid(x):\n    return x\n\ndef validate(x):\n    return x\n\n"
        "def valid_values():\n    return 1\n\ndef value_of(x):\n    return x\n\n"
        "def value(x):\n    return x\n"
        + "".join(f"def filler_{i}():\n    '''alpha {i}'''\n    return {i}\n" for i in range(20)),
        encoding="utf-8",
    )
    store = tmp_path / "store_names"
    out = index_folder(str(root), use_ai_summaries=False, storage_path=str(store), identity_mode="local")
    assert out.get("success"), out
    return out["repo"], str(store)


@pytest.mark.parametrize("q,want", [("is valid", "is_valid"), ("value of", "value_of")])
def test_a_name_typed_as_words_still_finds_the_exact_name_first(names_repo, q, want):
    """The identity channel reads every word; only the lexical score drops function words."""
    rid, store = names_repo
    rows = search_symbols(repo=rid, query=q, max_results=4, storage_path=store)["results"]
    assert rows and rows[0]["name"] == want, _names(rows)


@pytest.fixture
def test_only_repo(tmp_path):
    """A feature that exists only in tests: 30 test functions mention it, no source does."""
    root = tmp_path / "web"
    (root / "app").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "app" / "core.py").write_text(
        "".join(f"def filler_{i}():\n    '''unrelated words alpha beta {i}'''\n    return {i}\n" for i in range(40)),
        encoding="utf-8",
    )
    (root / "tests" / "test_web.py").write_text(
        "".join(f"def check_case_{i}(resp):\n    '''Check the cookie on case {i}.'''\n    return 1\n" for i in range(30)),
        encoding="utf-8",
    )
    store = tmp_path / "store_web"
    out = index_folder(str(root), use_ai_summaries=False, storage_path=str(store), identity_mode="local")
    assert out.get("success"), out
    return out["repo"], str(store)


def test_demoting_tests_does_not_make_a_test_only_match_read_as_absent(test_only_repo):
    """Demotion re-orders results; it must not trip the low-confidence warning that
    says the feature may not exist, nor lower the confidence."""
    from jcodemunch_mcp.retrieval import scoring as sc

    rid, store = test_only_repo
    res = search_symbols(repo=rid, query="cookie", max_results=3, storage_path=store)
    assert res["results"], res
    assert not any("warning" in k for k in res), {k: v for k, v in res.items() if "warning" in k}
    # Non-vacuity: the rows really are demoted test rows.
    assert sc.test_file_factor(res["results"][0], ["cookie"]) == sc.TEST_SYMBOL_WEIGHT


def test_a_query_about_tests_still_returns_tests_first(repo):
    rid, store = repo
    rows = search_symbols(repo=rid, query="tests for the endpoint that works", max_results=5, storage_path=store)["results"]
    assert rows and all(r["file"].startswith("tests/") for r in rows), _names(rows)
