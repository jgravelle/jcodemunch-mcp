"""#446 — a caller's entry_point_patterns that match nothing now say so.

`entry_point_patterns` is documented as glob patterns and matched with `fnmatch`,
which supports only `*`, `?` and `[seq]`. Brace alternation is not expanded and `**`
does not match zero directories. Both mistakes are easy, both are silent, and **we
made the first one ourselves in shipped profiles and did not notice for a release**
(#445).

⚠⚠ The harm is that a pattern matching nothing is indistinguishable from a repo that
genuinely has no such entry points: same output, same confidence, no marker. So this
reports rather than refuses — a caller may legitimately pass one pattern set across
several repos — and it names every cause at once (braces, `**`, typos, wrong path
root) rather than the one spelling we happened to get wrong.

⚠ Brace *support* is deliberately NOT added here. See #446: it is permanent surface
under the 1.x no-removal contract, it would change the meaning of a pattern
containing literal braces, and it immediately raises whether `**` should gain real
recursive semantics. That decision stays open; this closes the silence.
"""

import pytest

from jcodemunch_mcp.tools.find_dead_code import unmatched_patterns


SOURCE_FILES = [
    "src/main.ts",
    "src/app.module.ts",
    "plugins/auth.ts",
    "plugins/nested/deep.ts",
    "app/page.tsx",
]


def test_a_matching_pattern_is_not_reported():
    assert unmatched_patterns(["src/*.ts"], SOURCE_FILES) == []


def test_brace_alternation_is_reported_because_fnmatch_never_expands_it():
    """The v1.108.271 mistake, now audible instead of silent."""
    assert unmatched_patterns(["src/main.{ts,js}"], SOURCE_FILES) == [
        "src/main.{ts,js}"
    ]


def test_double_star_missing_the_flat_case_is_reported():
    """`plugins/**/*.ts` matches the nested file, so it is NOT reported.

    ⚠ This is the honest limit of the check and the reason it is worth pinning: a
    pattern that matches *something* is not flagged even when it misses the case the
    caller cared about. The check catches patterns that do nothing at all, not
    patterns that do less than intended.
    """
    assert unmatched_patterns(["plugins/**/*.ts"], SOURCE_FILES) == []
    # With no nested file to catch, the same pattern does nothing and is reported.
    assert unmatched_patterns(["plugins/**/*.ts"], ["plugins/auth.ts"]) == [
        "plugins/**/*.ts"
    ]


def test_only_the_dead_patterns_are_named():
    out = unmatched_patterns(
        ["src/*.ts", "src/main.{ts,js}", "nope/*.py"], SOURCE_FILES
    )
    assert out == ["src/main.{ts,js}", "nope/*.py"]


@pytest.mark.parametrize("empty", [None, []])
def test_no_patterns_means_nothing_to_report(empty):
    assert unmatched_patterns(empty, SOURCE_FILES) == []


def test_source_files_may_be_any_iterable_not_just_a_list():
    """Callers pass `index.source_files`; a generator must not be consumed early."""
    assert unmatched_patterns(["nope/*.py"], iter(SOURCE_FILES)) == ["nope/*.py"]
    assert unmatched_patterns(["src/*.ts"], frozenset(SOURCE_FILES)) == []


@pytest.fixture
def indexed_repo(tmp_path, monkeypatch):
    """A real two-file repo, indexed, so the tools run their actual code path."""
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / "store"))
    from jcodemunch_mcp import config as _config

    _config.load_config(storage_path=str(tmp_path / "store"))

    from jcodemunch_mcp.tools.index_folder import index_folder

    project = tmp_path / "proj"
    (project / "src").mkdir(parents=True)
    # main.py imports helper.py so the index has an import graph — without one,
    # get_dead_code_v2 takes its call-graph-only exit, which is a different path
    # with its own case below.
    (project / "src" / "main.py").write_text(
        "from .helper import aux\n\n\ndef go():\n    return aux()\n", encoding="utf-8"
    )
    (project / "src" / "helper.py").write_text("def aux():\n    return 2\n", encoding="utf-8")
    (project / "src" / "__init__.py").write_text("", encoding="utf-8")
    store = str(tmp_path / "store")
    repo = index_folder(
        path=str(project), use_ai_summaries=False, storage_path=store
    )["repo"]
    return repo, store


def test_find_dead_code_warns_end_to_end_and_the_message_teaches(indexed_repo):
    """⚠ Through the shipped tool, not a reconstruction of its message.

    An earlier draft of this test built the string itself and asserted its own
    substrings — which proves nothing about what the tool emits. That is the exact
    spelling-not-effect mistake #445 was about.
    """
    from jcodemunch_mcp.tools.find_dead_code import find_dead_code

    repo, store = indexed_repo
    out = find_dead_code(
        repo=repo,
        entry_point_patterns=["src/main.{py,ts}", "src/*.py"],
        storage_path=store,
    )
    assert out.get("entry_point_patterns_unmatched") == ["src/main.{py,ts}"], out
    warning = out.get("warning", "")
    assert "brace alternation" in warning, warning
    assert "** does not match" in warning, warning
    assert "src/main.{py,ts}" in warning


def test_find_dead_code_is_silent_when_every_pattern_matches(indexed_repo):
    """The control. A warning that always fires is noise, not signal."""
    from jcodemunch_mcp.tools.find_dead_code import find_dead_code

    repo, store = indexed_repo
    out = find_dead_code(
        repo=repo, entry_point_patterns=["src/*.py"], storage_path=store
    )
    assert "entry_point_patterns_unmatched" not in out
    assert "warning" not in out


def test_v2_warns_even_when_standard_entry_points_exist(indexed_repo):
    """⚠⚠ The regression this closes: `src/main.py` made the old gate skip the message.

    This repo deliberately contains a `main.py`, so `entry_point_count` is non-zero
    and the pre-existing `framework_warning` cannot fire. The new warning must fire
    anyway — that is the entire point of ungating it.
    """
    from jcodemunch_mcp.tools.get_dead_code_v2 import get_dead_code_v2

    repo, store = indexed_repo
    out = get_dead_code_v2(
        repo=repo,
        entry_point_patterns=["src/main.{py,ts}"],
        storage_path=store,
    )
    assert out.get("_meta", {}).get("mode") != "call_graph_only", (
        "fixture must exercise the main path; the fallback has its own test"
    )
    assert out.get("entry_point_patterns_unmatched") == ["src/main.{py,ts}"], out
    assert "brace alternation" in out.get("entry_point_patterns_warning", "")


def test_v2_call_graph_only_exit_says_the_patterns_were_ignored(tmp_path, monkeypatch):
    """⚠⚠ The early-return path shares the hazard, and was worse than unwarned.

    With no import graph, `get_dead_code_v2` falls back to call-graph-only mode,
    which never received `entry_point_patterns` and still does not use them — that
    mode has no file-level entry-point concept, so there is nothing for a path
    pattern to seed. Defensible. **Silently accepting a parameter and ignoring it is
    not**, and that is what shipped: the caller read an ordinary answer with no way
    to learn their patterns did nothing.

    Found by this file's own end-to-end test landing on the fallback by accident,
    which is the argument for testing through the tool rather than the helper.
    """
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / "store"))
    from jcodemunch_mcp import config as _config

    _config.load_config(storage_path=str(tmp_path / "store"))
    from jcodemunch_mcp.tools.get_dead_code_v2 import get_dead_code_v2
    from jcodemunch_mcp.tools.index_folder import index_folder

    project = tmp_path / "solo"
    project.mkdir()
    # One file, no imports anywhere -> no import graph -> call-graph-only mode.
    (project / "lib.py").write_text(
        "def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n", encoding="utf-8"
    )
    store = str(tmp_path / "store")
    repo = index_folder(
        path=str(project), use_ai_summaries=False, storage_path=store
    )["repo"]

    out = get_dead_code_v2(
        repo=repo, entry_point_patterns=["lib.{py,ts}"], storage_path=store
    )
    assert out.get("_meta", {}).get("mode") == "call_graph_only", out.get("_meta")
    assert out.get("entry_point_patterns_ignored") is True, out
    warning = out.get("entry_point_patterns_warning", "")
    assert "were not applied" in warning, warning


def test_v2_call_graph_only_is_silent_when_no_patterns_were_passed(tmp_path, monkeypatch):
    """The control: a caller who passed nothing must not be told anything."""
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / "store"))
    from jcodemunch_mcp import config as _config

    _config.load_config(storage_path=str(tmp_path / "store"))
    from jcodemunch_mcp.tools.get_dead_code_v2 import get_dead_code_v2
    from jcodemunch_mcp.tools.index_folder import index_folder

    project = tmp_path / "solo2"
    project.mkdir()
    (project / "lib.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    store = str(tmp_path / "store")
    repo = index_folder(
        path=str(project), use_ai_summaries=False, storage_path=store
    )["repo"]

    out = get_dead_code_v2(repo=repo, storage_path=store)
    assert "entry_point_patterns_ignored" not in out
    assert "entry_point_patterns_warning" not in out


def test_both_tools_share_one_definition_of_unmatched():
    """`get_dead_code_v2` imports the helper rather than reimplementing it.

    Same rule as #436: two definitions of what a pattern means is the original
    defect in a new costume.
    """
    from jcodemunch_mcp.tools import find_dead_code, get_dead_code_v2

    assert (
        get_dead_code_v2.unmatched_patterns is find_dead_code.unmatched_patterns
    )


def test_v2_warning_is_no_longer_gated_on_entry_point_count():
    """⚠⚠ The v2 message was correct and almost never fired.

    It sits behind `if entry_point_count == 0`, so a repo with any ordinary
    `main.py` made the count non-zero and the caller heard nothing. A correct
    warning behind the wrong gate reads as 'no problem found'. This asserts the
    ungated branch exists outside that block.
    """
    import inspect

    from jcodemunch_mcp.tools import get_dead_code_v2

    src = inspect.getsource(get_dead_code_v2)
    gated = src.index("if entry_point_count == 0:")
    ungated = src.index('result["entry_point_patterns_warning"]')
    assert ungated > gated, "the new warning must not live inside the gated block"
    # And it must not be indented under that `if` — same column as the gate itself.
    line = src[:ungated].rsplit("\n", 1)[-1]
    assert len(line) - len(line.lstrip()) <= 8, (
        "warning appears nested more deeply than the gate; check it is unconditional"
    )
