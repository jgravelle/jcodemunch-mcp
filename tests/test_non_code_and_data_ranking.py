"""A question about code ranks functions above settings files and data fields.

After tests were demoted (``test_conceptual_query_ranking.py``), the pinned
fastapi questions filled their top 5 with other things that share a word with
the question but carry no behaviour: CI workflow keys (``UV_RESOLUTION`` for
"resolution", all five slots on one query), ``pyproject.toml`` tables, CSS
selectors, and dataclass fields named ``dependencies``. ``rank_factor`` in
``retrieval/scoring.py`` weights both down for ranking, beside the test weight:

- a symbol from a ``NON_CODE_LANGUAGES`` file, unless the query names
  configuration ("config", "workflow", "yaml", ...);
- a field or constant, unless the query names values ("field", "constant", ...).

Only ranking reads the weight. The identity channel is untouched, so an exact
name still ranks first, and a match found only in settings files is still a
match. The fixture below is the fastapi shape in miniature.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.retrieval import scoring
from jcodemunch_mcp.tools.get_ranked_context import get_ranked_context
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.search_symbols import search_symbols


# --- rank_factor ---------------------------------------------------------------


@pytest.mark.parametrize("lang", ["yaml", "toml", "json", "xml", "css"])
def test_a_settings_symbol_is_weighted_down_for_a_code_question(lang):
    sym = {"file": f"conf/x.{lang}", "language": lang, "kind": "constant"}
    assert scoring.rank_factor(sym, ["dependency", "resolution"]) == (
        scoring.NON_CODE_WEIGHT,
        "non_code_file",
    )
    assert 0 < scoring.NON_CODE_WEIGHT < 1


@pytest.mark.parametrize(
    "word", ["config", "workflow", "yaml", "ci", "settings", "pyproject"]
)
def test_a_question_about_configuration_weights_no_settings_symbol(word):
    sym = {"file": ".github/workflows/test.yml", "language": "yaml", "kind": "constant"}
    assert scoring.rank_factor(sym, ["resolution", word]) == (1.0, None)


@pytest.mark.parametrize(
    "lang,word",
    [
        ("css", "css"),
        ("scss", "scss"),
        ("sass", "sass"),
        ("less", "less"),
        ("styl", "stylus"),
    ],
)
def test_naming_a_stylesheet_language_lifts_it(lang, word):
    sym = {"file": f"ui/site.{lang}", "language": lang, "kind": "class"}
    assert scoring.rank_factor(sym, ["badge", word]) == (1.0, None)


def test_a_configuration_word_does_not_lift_the_stylesheet():
    css = {"file": "docs/site.css", "language": "css", "kind": "class"}
    assert scoring.rank_factor(css, ["ci", "workflow"])[1] == "non_code_file"
    assert scoring.rank_factor(css, ["badge", "style"]) == (1.0, None)


def test_every_listed_language_is_one_the_parser_produces():
    """A name no file is ever indexed under is a rule that never fires."""
    from jcodemunch_mcp.parser.languages import LANGUAGE_EXTENSIONS

    produced = set(LANGUAGE_EXTENSIONS.values())
    assert scoring.NON_CODE_LANGUAGES <= produced, scoring.NON_CODE_LANGUAGES - produced


@pytest.mark.parametrize(
    "ext", [".css", ".scss", ".sass", ".less", ".yaml", ".yml", ".toml", ".json"]
)
def test_every_stylesheet_and_settings_extension_is_covered(ext):
    from jcodemunch_mcp.parser.languages import LANGUAGE_EXTENSIONS

    assert LANGUAGE_EXTENSIONS[ext] in scoring.NON_CODE_LANGUAGES


@pytest.mark.parametrize(
    "kind,name",
    [
        ("field", "dependency_cache"),
        ("constant", "RESOLUTION_ORDER"),
        ("constant", "_FASTAPI_SCOPE_KEY"),
    ],
)
def test_a_field_or_constant_is_weighted_down_for_a_code_question(kind, name):
    sym = {"file": "app/models.py", "language": "python", "kind": kind, "name": name}
    assert scoring.rank_factor(sym, ["dependency"]) == (
        scoring.DATA_KIND_WEIGHT,
        "data_kind",
    )
    assert 0 < scoring.DATA_KIND_WEIGHT < 1


@pytest.mark.parametrize("name", ["app", "router", "resolver", "useDependency"])
def test_a_lower_case_binding_is_code_not_a_value(name):
    """JS/TS index every module-level ``const`` as a constant; ``const app =
    express()`` is the code a routing question is asking about."""
    sym = {
        "file": "src/server.js",
        "language": "javascript",
        "kind": "constant",
        "name": name,
    }
    assert scoring.rank_factor(sym, ["routing"]) == (1.0, None)


@pytest.mark.parametrize(
    "name,expected",
    [
        ("MAX_SIZE", True),
        ("__VERSION__", True),
        ("HTTP2", True),
        ("X", True),
        ("Config.MAX_SIZE", True),
        ("MAX.size", False),
        ("__all__", False),
        ("_123", False),
        ("名前", False),  # no case in the script: not a convention signal
        ("StatusOK", False),
    ],
)
def test_the_upper_case_rule_on_edge_names(name, expected):
    assert scoring.is_data_symbol({"kind": "constant", "name": name}) is expected


@pytest.mark.parametrize("word", ["field", "constant", "attribute", "property", "value"])
def test_a_question_about_values_weights_no_field(word):
    sym = {"file": "app/models.py", "language": "python", "kind": "field", "name": "x"}
    assert scoring.rank_factor(sym, ["dependency", word]) == (1.0, None)


@pytest.mark.parametrize("kind", ["function", "method", "class"])
def test_code_is_not_weighted(kind):
    sym = {"file": "app/core.py", "language": "python", "kind": kind}
    assert scoring.rank_factor(sym, ["dependency"]) == (1.0, None)


def test_the_test_weight_comes_first_and_is_not_compounded():
    sym = {
        "file": "tests/test_models.py",
        "language": "python",
        "kind": "field",
        "name": "x",
    }
    assert scoring.rank_factor(sym, ["dependency"]) == (
        scoring.TEST_SYMBOL_WEIGHT,
        "test_file",
    )


# --- through the tools ---------------------------------------------------------

SOURCE = '''
from dataclasses import dataclass, field


@dataclass
class Route:
    """A path operation."""
    dependencies: list = field(default_factory=list)
    dependency_overrides: dict = field(default_factory=dict)
    resolution_cache: dict = field(default_factory=dict)


RESOLUTION_ORDER = "depth-first"
DEPENDENCY_MARKER = "__dep__"


def resolve_dependencies(route, request):
    """Resolve every dependency of a route for one request."""
    return [d for d in route.dependencies]


def build_dependency_tree(call):
    """Build the dependency tree for a callable."""
    return [call]
'''

# Settings that each share ONE word with the question, as fastapi's do
# (`UV_RESOLUTION` for "resolution"), repeated the way a CI matrix repeats a key.
WORKFLOW = "jobs:\n" + "".join(
    f"  test{i}:\n    env:\n      UV_RESOLUTION: highest\n" for i in range(12)
)

PYPROJECT = '[project]\nname = "app"\ndependencies = ["httpx"]\n\n[tool.uv]\nresolution = "highest"\n'

CSS = "".join(f".resolution-badge-{i} {{ color: red; }}\n" for i in range(12))

WANT = {"resolve_dependencies", "build_dependency_tree"}


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "proj"
    (root / "app").mkdir(parents=True)
    (root / "app" / "__init__.py").write_text("", encoding="utf-8")
    (root / "app" / "routing.py").write_text(SOURCE, encoding="utf-8")
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "test.yml").write_text(WORKFLOW, encoding="utf-8")
    (root / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "site.css").write_text(CSS, encoding="utf-8")
    store = tmp_path / "store"
    out = index_folder(
        str(root),
        use_ai_summaries=False,
        storage_path=str(store),
        identity_mode="local",
    )
    assert out.get("success"), out
    return out["repo"], str(store)


def _rows(res):
    return [(r.get("name"), r.get("kind"), r.get("file")) for r in res]


@pytest.mark.parametrize(
    "q", ["dependency resolution", "how are dependencies resolved for a request"]
)
def test_search_symbols_ranks_functions_above_settings_and_fields(repo, q):
    rid, store = repo
    rows = search_symbols(repo=rid, query=q, max_results=10, storage_path=store)[
        "results"
    ]
    assert rows and rows[0]["name"] in WANT, _rows(rows)
    assert not any(r["file"].endswith((".yml", ".css")) for r in rows[:3]), _rows(rows)
    # A settings key can still rank high on an exact name (pyproject's
    # `dependencies`), but never ahead of the code the question is about.
    settings = [i for i, r in enumerate(rows) if r["file"].endswith((".yml", ".toml", ".css"))]
    code = [i for i, r in enumerate(rows) if r["name"] in WANT]
    assert code and (not settings or code[0] < settings[0]), _rows(rows)


def test_get_ranked_context_leads_with_a_function(repo):
    rid, store = repo
    items = get_ranked_context(
        repo=rid, query="dependency resolution", token_budget=2000, storage_path=store
    )["context_items"]
    assert items and items[0]["name"] in WANT, _rows(items)


@pytest.mark.parametrize(
    "q,want_file,want_name",
    [
        ("UV_RESOLUTION", ".github/workflows/test.yml", "UV_RESOLUTION"),
        ("dependency_overrides", "app/routing.py", "dependency_overrides"),
        ("RESOLUTION_ORDER", "app/routing.py", "RESOLUTION_ORDER"),
    ],
)
def test_an_exact_name_still_ranks_first(repo, q, want_file, want_name):
    rid, store = repo
    rows = search_symbols(repo=rid, query=q, max_results=5, storage_path=store)[
        "results"
    ]
    assert (
        rows and rows[0]["file"] == want_file and rows[0]["name"].endswith(want_name)
    ), _rows(rows)


@pytest.mark.parametrize("q", ["RESOLUTION_ORDER", "dependency_overrides"])
def test_an_exact_name_still_ranks_first_in_ranked_context_and_plan_turn(repo, q):
    """These two tools score identity inside the weighted sum, so the weight
    reaches it; the name must still win."""
    from jcodemunch_mcp.tools.plan_turn import plan_turn

    rid, store = repo
    items = get_ranked_context(
        repo=rid, query=q, token_budget=3000, storage_path=store
    )["context_items"]
    assert items and items[0]["name"] == q, _rows(items)
    plan = plan_turn(repo=rid, query=q, storage_path=store)
    recs = plan.get("recommended_symbols") or []
    assert recs and recs[0]["name"] == q, recs[:3]


def test_a_question_about_styling_still_returns_the_stylesheet(repo):
    rid, store = repo
    rows = search_symbols(
        repo=rid, query="resolution badge style", max_results=5, storage_path=store
    )["results"]
    assert rows and rows[0]["file"].endswith(".css"), _rows(rows)


def test_a_question_about_the_workflow_still_returns_workflow_keys(repo):
    rid, store = repo
    rows = search_symbols(
        repo=rid, query="ci workflow resolution", max_results=5, storage_path=store
    )["results"]
    assert rows and rows[0]["file"].endswith((".yml", ".toml")), _rows(rows)


def test_a_question_about_fields_still_returns_fields(repo):
    rid, store = repo
    rows = search_symbols(
        repo=rid, query="dependency fields", max_results=5, storage_path=store
    )["results"]
    assert rows and rows[0]["kind"] == "field", _rows(rows)


def test_the_debug_breakdown_names_the_weight_and_its_reason(repo):
    rid, store = repo
    rows = search_symbols(
        repo=rid,
        query="dependency resolution",
        max_results=40,
        debug=True,
        storage_path=store,
    )["results"]
    reasons = {
        r["score_breakdown"].get("rank_weight_reason")
        for r in rows
        if "score_breakdown" in r
    }
    assert {"non_code_file", "data_kind"} <= reasons, reasons
    for r in rows:
        bd = r.get("score_breakdown") or {}
        if "rank_weight" in bd:
            assert "test_weight" not in bd  # that key stays for test rows only


@pytest.fixture
def settings_only_repo(tmp_path):
    """A word that appears only in settings files: nothing in the code mentions it."""
    root = tmp_path / "svc"
    (root / "app").mkdir(parents=True)
    (root / "app" / "core.py").write_text(
        "".join(
            f"def filler_{i}():\n    '''unrelated words alpha beta {i}'''\n    return {i}\n"
            for i in range(40)
        ),
        encoding="utf-8",
    )
    (root / "deploy.yml").write_text(
        "".join(f"replica_{i}:\n  autoscaler_window: {i}\n" for i in range(30)),
        encoding="utf-8",
    )
    store = tmp_path / "store_svc"
    out = index_folder(
        str(root),
        use_ai_summaries=False,
        storage_path=str(store),
        identity_mode="local",
    )
    assert out.get("success"), out
    return out["repo"], str(store)


def _crush(monkeypatch):
    """Make every ranking weight tiny at every site that imports it. A verdict
    that read the weighted score would then see no match at all; one that reads
    the raw score (the contract) is unchanged."""
    import jcodemunch_mcp.retrieval.signal_fusion  # noqa: F401  (imports rank_factor lazily)
    from jcodemunch_mcp.tools import (
        get_ranked_context as grc_mod,
        plan_turn as pt_mod,
        search_symbols as ss_mod,
    )

    def tiny(sym, terms):
        return 1e-6, "test"

    for mod in (ss_mod, grc_mod, pt_mod, scoring):
        monkeypatch.setattr(mod, "rank_factor", tiny)
    with ss_mod._result_cache_lock:  # the unweighted answer must not be replayed
        ss_mod._result_cache.clear()


def test_weighting_settings_down_does_not_make_a_settings_only_match_read_as_absent(
    settings_only_repo,
):
    rid, store = settings_only_repo
    res = search_symbols(
        repo=rid, query="autoscaler window", max_results=3, storage_path=store
    )
    assert res["results"], res
    assert not any("warning" in k for k in res), {
        k: v for k, v in res.items() if "warning" in k
    }
    # The rows really are settings rows, which the weight applies to.
    assert all(r["file"] == "deploy.yml" for r in res["results"]), _rows(res["results"])
    assert (
        scoring.rank_factor({"language": "yaml"}, ["autoscaler", "window"])[1]
        == "non_code_file"
    )


def test_no_verdict_reads_the_weighted_score(settings_only_repo, monkeypatch):
    """The settings-only match scores far above the absence threshold, so half
    of it proves nothing; crush the weight instead and require the same answer."""
    from jcodemunch_mcp.tools.plan_turn import plan_turn

    rid, store = settings_only_repo
    q = "autoscaler window"
    before = search_symbols(repo=rid, query=q, max_results=3, storage_path=store)
    plan_before = plan_turn(repo=rid, query=q, storage_path=store)
    _crush(monkeypatch)
    after = search_symbols(repo=rid, query=q, max_results=3, storage_path=store)
    plan_after = plan_turn(repo=rid, query=q, storage_path=store)
    # Non-vacuity: the crushed weight really reached the ranking.
    dbg = search_symbols(
        repo=rid, query=q, max_results=3, debug=True, storage_path=store
    )["results"]
    assert dbg[0]["score_breakdown"].get("rank_weight") == 1e-6, dbg[0].get(
        "score_breakdown"
    )
    assert after["results"], after
    assert not any("warning" in k for k in after), {
        k: v for k, v in after.items() if "warning" in k
    }
    assert plan_after.get("confidence") == plan_before.get("confidence"), (
        plan_before.get("confidence"),
        plan_after.get("confidence"),
    )
    assert len(after["results"]) == len(before["results"])
