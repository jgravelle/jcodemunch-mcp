"""`search_symbols` cuts to `max_results` with a bounded heap keyed on BM25
alone, so eviction cannot tell an exact-name match from a lexical near-miss —
or a real definition from a same-named local.

The reported case was zod's `partial`: six `constant` rows in test files filled
the window and pushed a real definition out of the response entirely, at no
rank. The same query shape does it here with everything one kind: a dozen
helpers named `execute`, each nested inside a test function, evict the only
module-level `execute` in the tree.

The property is not kind and not path. A local is a symbol declared inside a
FUNCTION BODY; a method declared inside a class is a real definition and must
not be demoted, which is what a "prefer non-test paths" rule would get wrong on
a fixture. Kind alone does not separate the case below — every row in it is a
`function`.

`_meta.exact_match` is the second half: it is computed from the rows that
survived the cut, so it answers "how many exact matches did you return" while
reading as "how many exist" (#559's shape, one axis over).
"""

import pytest

from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.search_symbols import search_symbols

# More than the default cap of 10, so the cut has to choose.
_LOCAL_COUNT = 12


@pytest.fixture(scope="module")
def crowded_repo(tmp_path_factory):
    """One real definition, twelve same-named locals nested in test functions."""
    tmp = tmp_path_factory.mktemp("crowded")
    src, store = tmp / "proj", tmp / "store"
    (src / "src").mkdir(parents=True)
    (src / "tests").mkdir()
    store.mkdir()

    (src / "src" / "runner.py").write_text(
        "def execute():\n"
        '    """Run the configured pipeline and return its exit code."""\n'
        "    return 0\n"
    )
    # A class-owned method of the same name. Nested, but nested in a CLASS, so
    # it is a real definition and the rule must not demote it.
    (src / "src" / "engine.py").write_text(
        "class Engine:\n"
        "    def execute(self):\n"
        '        """Execute one step."""\n'
        "        return 1\n"
    )
    # `execute_step` carries an underscore, so `is_identifier_query` accepts it
    # and the `_meta.exact_match` report is attached; `execute` does not, and is
    # the shape the defect was reported against. Both are crowded identically.
    (src / "src" / "steps.py").write_text(
        "def execute_step():\n"
        '    """Execute one configured step."""\n'
        "    return 0\n"
    )
    for i in range(_LOCAL_COUNT):
        (src / "tests" / f"test_case_{i}.py").write_text(
            f"class TestExecute{i}:\n"
            f"    def test_execute_path_{i}(self):\n"
            f"        def execute():\n"
            f"            return {i}\n"
            f"        def execute_step():\n"
            f"            return {i}\n"
            f"        assert execute() == execute_step()\n"
        )

    result = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert result["success"] is True
    return result["repo"], str(store)


def _search(repo, store, **kw):
    return search_symbols(repo=repo, query="execute", storage_path=store,
                          detail_level="compact", **kw)


def _exact(rows):
    return [r for r in rows if r["name"] == "execute"]


def test_the_fixture_really_overflows_the_cap(crowded_repo):
    """Non-vacuity for every test below: if the fixture stopped exceeding the
    default cap, they would all pass while measuring nothing."""
    repo, store = crowded_repo
    rows = _search(repo, store, max_results=100).get("results", [])
    assert len(_exact(rows)) > 10, "fixture no longer overflows the default cap"


def test_a_module_level_definition_is_not_evicted_by_locals(crowded_repo):
    """The reported defect: the only real answer is absent at any rank."""
    repo, store = crowded_repo
    rows = _search(repo, store).get("results", [])
    files = {r["file"].replace("\\", "/") for r in _exact(rows)}
    assert any(f.endswith("src/runner.py") for f in files), (
        f"module-level definition evicted; returned {sorted(files)}"
    )


def test_a_class_owned_method_is_not_evicted_either(crowded_repo):
    """Nested in a class is a definition, not a local.

    A rule keyed on nesting depth, or on "prefer non-test paths", would get
    this one wrong in opposite directions.
    """
    repo, store = crowded_repo
    rows = _search(repo, store).get("results", [])
    ids = {r["id"].replace("\\", "/") for r in _exact(rows)}
    assert any("Engine.execute" in i for i in ids), (
        f"class-owned method evicted; returned {sorted(ids)}"
    )


def _is_local(row):
    return row["file"].replace("\\", "/").startswith("tests/")


def test_locals_are_ranked_below_but_still_returned(crowded_repo):
    """The fix demotes; it must not filter.

    A local is a legitimate answer to "where is this name" and dropping it
    would trade one silent omission for another.
    """
    repo, store = crowded_repo
    exact = _exact(_search(repo, store, max_results=100).get("results", []))
    assert len([r for r in exact if _is_local(r)]) == _LOCAL_COUNT, (
        "locals were filtered out rather than demoted"
    )

    ranked = _exact(_search(repo, store).get("results", []))
    defs = [i for i, r in enumerate(ranked) if not _is_local(r)]
    locals_ = [i for i, r in enumerate(ranked) if _is_local(r)]
    assert defs and locals_, "expected both kinds inside the default window"
    assert max(defs) < min(locals_), (
        "definitions must sort above locals, not merely survive the cut -- the "
        "eviction key and the sort key have to be the same key"
    )


def test_exact_match_counts_what_exists_not_what_survived(crowded_repo):
    """`_meta.exact_match.exact` read from the post-cut rows, so a caller could
    not tell a complete answer from a truncated one.

    Asked with `execute_step` rather than `execute`: the report is gated on
    `is_identifier_query`, which refuses a single lower-case word with no
    underscore. That gate is right for whether to ATTACH a report and wrong for
    what to KEEP, which is why the cut does not share it -- asserted below.
    """
    repo, store = crowded_repo
    paged = search_symbols(repo=repo, query="execute_step", storage_path=store,
                           detail_level="compact")
    report = paged.get("_meta", {}).get("exact_match")
    assert report is not None, "identifier query lost its exact_match report"

    full = search_symbols(repo=repo, query="execute_step", storage_path=store,
                          detail_level="compact", max_results=100)
    existing = [r for r in full.get("results", []) if r["name"] == "execute_step"]
    assert len(existing) > 10, "fixture no longer overflows the cap for this name"
    assert report["exact"] == len(existing), (
        f"reported {report['exact']} exact matches, {len(existing)} exist"
    )
    assert report["exact_returned"] < report["exact"]
    assert report["exact_truncated"] is True


def test_the_cut_is_not_gated_on_identifier_shape(crowded_repo):
    """`execute` is a single lower-case word, so `is_identifier_query` refuses
    it -- and it is exactly the shape of the reported names (`partial`, `pick`,
    `run`). If the cut inherited that gate, the defect would be unfixed for
    every case that reported it.
    """
    repo, store = crowded_repo
    assert _search(repo, store).get("_meta", {}).get("exact_match") is None, (
        "fixture assumption changed: 'execute' now reads as an identifier query"
    )
    files = {r["file"].replace("\\", "/") for r in
             _exact(_search(repo, store).get("results", []))}
    assert any(f.endswith("src/runner.py") for f in files), (
        "the cut is gated on identifier shape, so the reported cases stay broken"
    )
