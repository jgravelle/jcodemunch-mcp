"""#458: `identity_type` reported "exact" for a match only tokenization made.

The reproduction is a pytest fixture named `state` outranking the class literally
named `_State` for the query `_State`. Both scored `identity: 50.0,
identity_type: "exact"`, the identity channel could not separate them, and the
tie fell through to BM25 — where the shorter name with a docstring wins. The
margin was 0.355 points out of ~58, so the two were tied by construction and an
unrelated text signal decided it.

⚠ The end-to-end tests here go through `search_symbols`, not through
`_identity_score`. The helper-level tests below are cheap and specific, and they
would ALL have passed against a version where the tool still returned the fixture
first — the ordering is the defect a caller experiences.
"""

from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.search_symbols import (
    _bm25_breakdown,
    _identity_score,
    _tokenize,
    search_symbols,
)

_FIXTURE = {"name": "state", "id": "tests/test_thing.py::state#function"}
_CLASS = {"name": "_State", "id": "src/storage/token_tracker.py::_State#class"}


def _tokenized(query: str) -> str:
    return " ".join(_tokenize(query))


class TestIdentityScoreTiers:
    def test_literal_match_outscores_normalized_match(self):
        query = "_State"
        joined = _tokenized(query)

        literal = _identity_score(_CLASS, joined, query)
        normalized = _identity_score(_FIXTURE, joined, query)

        assert literal == 50.0
        assert normalized == 40.0
        assert literal > normalized

    def test_case_only_difference_is_still_exact(self):
        """Case folding alone stays exact — no defect motivates changing it."""
        sym = {"name": "getUser", "id": "src/api.py::getUser#function"}

        assert _identity_score(sym, _tokenized("getuser"), "getuser") == 50.0

    def test_term_only_caller_keeps_the_exact_grade(self):
        """With no raw spelling there is nothing to be literal about."""
        assert _identity_score(_FIXTURE, "state", raw_query="") == 50.0

    def test_normalized_match_still_outscores_a_prefix(self):
        prefix_sym = {"name": "state_machine", "id": "src/a.py::state_machine#function"}
        query = "_State"
        joined = _tokenized(query)

        assert _identity_score(_FIXTURE, joined, query) > _identity_score(
            prefix_sym, joined, query
        )

    def test_no_match_is_still_zero(self):
        unrelated = {"name": "parse_file", "id": "src/p.py::parse_file#function"}

        assert _identity_score(unrelated, _tokenized("_State"), "_State") == 0.0


class TestIdentityTypeLabel:
    def test_normalized_match_is_not_labelled_exact(self):
        breakdown = _bm25_breakdown(_FIXTURE, _tokenize("_State"), {}, 1.0, raw_query="_State")

        assert breakdown["identity"] == 40.0
        assert breakdown["identity_type"] == "normalized"

    def test_literal_match_is_labelled_exact(self):
        breakdown = _bm25_breakdown(_CLASS, _tokenize("_State"), {}, 1.0, raw_query="_State")

        assert breakdown["identity_type"] == "exact"

    def test_prefix_and_none_labels_are_unchanged(self):
        prefix_sym = {"name": "state_machine", "id": "src/a.py::state_machine#function"}
        unrelated = {"name": "parse_file", "id": "src/p.py::parse_file#function"}

        assert (
            _bm25_breakdown(prefix_sym, _tokenize("state"), {}, 1.0, raw_query="state")[
                "identity_type"
            ]
            == "prefix"
        )
        assert (
            _bm25_breakdown(unrelated, _tokenize("_State"), {}, 1.0, raw_query="_State")[
                "identity_type"
            ]
            == "none"
        )


def _seed_state_collision(tmp_path):
    """The #458 corpus: a private class and a same-after-normalization fixture.

    ⚠ **The class's long docstring is load-bearing and is not decoration.** BM25
    normalizes by document length, so on the real repo `_State` — a large class
    with a rich summary — scored BELOW the two-line fixture on every lexical
    field (name 6.996 vs 8.012, signature 6.153 vs 7.389, summary 0.0 vs 5.992).
    A short synthetic class wins on lexical signals alone, and the ordering
    assertion then passes with or without the fix, testing nothing. The
    docstring's words are deliberately unrelated to the query: they lengthen the
    document without contributing a match, which is what the real class's own
    prose does.
    """
    filler = " ".join(f"detail{i} note{i}" for i in range(30))
    src = tmp_path / "src"
    src.mkdir()
    (src / "token_tracker.py").write_text(
        "class _State:\n"
        f'    """{filler}"""\n'
        "\n"
        "    def __init__(self):\n"
        "        self.rows = []\n"
    )
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_thing.py").write_text(
        "def state():\n"
        '    """Recorder state."""\n'
        "    return {}\n"
    )
    idx = index_folder(
        path=str(tmp_path), use_ai_summaries=False, storage_path=str(tmp_path / "idx")
    )
    return idx["repo"], str(tmp_path / "idx")


class TestEndToEndOrdering:
    def test_exact_name_query_returns_the_class_before_the_fixture(self, tmp_path):
        repo, storage = _seed_state_collision(tmp_path)

        result = search_symbols(
            repo=repo, query="_State", max_results=5, storage_path=storage
        )

        names = [r["name"] for r in result["results"]]
        assert "_State" in names, f"the queried symbol was not returned at all: {names}"
        assert "state" in names, f"the collision corpus did not index: {names}"
        assert names.index("_State") < names.index("state"), (
            f"a test fixture outranked the class the caller named: {names}"
        )

    def test_the_served_row_reports_the_grade_it_measured(self, tmp_path):
        repo, storage = _seed_state_collision(tmp_path)

        result = search_symbols(
            repo=repo, query="_State", max_results=5, debug=True, storage_path=storage
        )

        by_name = {r["name"]: r for r in result["results"]}
        assert by_name["_State"]["score_breakdown"]["identity_type"] == "exact"
        assert by_name["state"]["score_breakdown"]["identity_type"] == "normalized"
