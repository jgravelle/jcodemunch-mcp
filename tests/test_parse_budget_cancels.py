"""`JCODEMUNCH_PARSE_BUDGET_SECONDS` stops a slow tree-sitter parse (LEDGER L-114).

The budget was a thread wait around `parse_file`. tree-sitter's parse holds the
GIL for its whole duration, so the wait could not return early, and by the time
it returned the parse had usually finished: an over-budget file came back with
its symbols and no warning, after the full parse time. The full-index loop of
`index_folder` never asked the budget at all.

The limit now sits in the parser itself. `parse_file` opens a deadline,
`grammar_pack.get_parser` (the one loader every grammar call uses) hands back a
parser that carries what is left of it, and tree-sitter's own timeout ends the
parse from inside the C code.

These tests ask at three altitudes: the installed binding (does it still cancel
at all), `parse_file` (every route's common layer), and `index_folder` (where a
user stands). The slow input is L-113's shape, a run of comment lines after a
statement, which the Python grammar parses in quadratic time.
"""
from __future__ import annotations

import time

import pytest

from jcodemunch_mcp.parser import extractor, grammar_pack
from jcodemunch_mcp.tools._indexing_pipeline import ParseBudgetExceeded, _PARSE_WATCHDOG_MIN_BYTES
from jcodemunch_mcp.tools.index_folder import index_folder

BUDGET = 0.25

_PADDING = "# " + ("x" * 78) + "\n"


def _slow_python(lines: int = 5000) -> str:
    """A statement, then `lines` comment lines: seconds of parse (L-113)."""
    return "def marker_symbol():\n    return 1\n\n" + _PADDING * lines


def _slow_small_python() -> str:
    """The same shape under the old 128 KiB arming threshold."""
    text = "def marker_symbol():\n    return 1\n\n" + "#\n" * 15000
    assert len(text.encode("utf-8")) < _PARSE_WATCHDOG_MIN_BYTES
    return text


@pytest.fixture
def budget(monkeypatch):
    monkeypatch.setenv("JCODEMUNCH_PARSE_BUDGET_SECONDS", str(BUDGET))
    return BUDGET


def _index(project, tmp_path, incremental=False):
    return index_folder(
        str(project),
        use_ai_summaries=False,
        storage_path=str(tmp_path / "store"),
        incremental=incremental,
        context_providers=False,
    )


# --- the installed binding ---------------------------------------------------


def test_the_installed_tree_sitter_can_cancel_a_parse():
    """The fix rests on `Parser.timeout_micros`, which tree-sitter 0.25 deprecates.

    A binding that drops it leaves only the thread wait, which cannot stop a
    parse. This fails then, so the upgrade is a decision and not a silent
    disarming.
    """
    from tree_sitter_language_pack import get_parser

    parser = get_parser("python")
    assert hasattr(type(parser), "timeout_micros"), (
        "the installed tree-sitter has no Parser.timeout_micros; the parse budget "
        "cannot cancel a parse on it (parser/parse_budget.py)"
    )
    parser.timeout_micros = 100_000
    started = time.monotonic()
    try:
        tree = parser.parse(_slow_python().encode("utf-8"))
    except ValueError:
        tree = None
    elapsed = time.monotonic() - started
    assert tree is None, "the parse finished under a 0.1 s timeout; the fixture is not slow here"
    assert elapsed < 3.0, f"a 0.1 s timeout took {elapsed:.2f} s to fire"


# --- parse_file --------------------------------------------------------------


def test_parse_file_stops_an_over_budget_parse(budget):
    started = time.monotonic()
    with pytest.raises(ParseBudgetExceeded) as exc:
        extractor.parse_file(_slow_python(), "slow.py", "python")
    elapsed = time.monotonic() - started

    assert elapsed < 3.0, f"the parse ran {elapsed:.2f} s against a {budget} s budget"
    message = str(exc.value)
    assert f"{budget:g}s budget" in message
    assert "JCODEMUNCH_PARSE_BUDGET_SECONDS" in message


def test_the_budget_does_not_depend_on_file_size(budget):
    """L-113's 80 KB file took 11.6 s and sat under the size the wait armed at."""
    with pytest.raises(ParseBudgetExceeded):
        extractor.parse_file(_slow_small_python(), "small_slow.py", "python")


def test_a_file_under_budget_parses_as_it_did(monkeypatch):
    source = "class A:\n    def m(self):\n        return helper()\n\ndef helper():\n    return 1\n"

    def ids(symbols):
        return [(s.id, s.kind, s.line, s.end_line) for s in symbols]

    monkeypatch.setenv("JCODEMUNCH_PARSE_BUDGET_SECONDS", "0")
    unbudgeted = ids(extractor.parse_file(source, "a.py", "python"))
    monkeypatch.setenv("JCODEMUNCH_PARSE_BUDGET_SECONDS", "20")
    budgeted = ids(extractor.parse_file(source, "a.py", "python"))

    assert unbudgeted and budgeted == unbudgeted


def test_zero_disables_the_limit(monkeypatch):
    monkeypatch.setenv("JCODEMUNCH_PARSE_BUDGET_SECONDS", "0")
    symbols = extractor.parse_file(_slow_python(1500), "slow.py", "python")
    assert [s.name for s in symbols] == ["marker_symbol"]


def test_a_real_parse_failure_is_not_reported_as_a_timeout(budget, monkeypatch):
    """A grammar that fails to load keeps its own outcome: no symbols, no budget error."""

    def _no_grammar(name):
        raise LookupError("no such grammar")

    monkeypatch.setattr(extractor, "get_parser", _no_grammar)
    assert extractor.parse_file("def a():\n    return 1\n", "a.py", "python") == []


def test_a_parser_loaded_outside_parse_file_carries_no_limit(budget):
    """`search_ast` and other direct callers get the pack's own parser, unchanged."""
    from tree_sitter import Parser

    assert type(grammar_pack.get_parser("python")) is Parser


def test_an_embedded_script_shares_its_files_deadline(budget):
    """A Vue file's `<script>` is parsed by a nested `parse_file`; one file, one budget."""
    body = "function marker() { return 1 }\n" + "// " + "x" * 70 + "\n"
    vue = "<template><div/></template>\n<script>\n" + body + "</script>\n"
    symbols = extractor.parse_file(vue, "a.vue", "vue")
    assert "marker" in [s.name for s in symbols]

    from jcodemunch_mcp.parser import parse_budget

    assert parse_budget.active() is None, "a deadline outlived the parse_file call that opened it"


def test_a_nested_call_gets_the_open_deadline_not_a_new_one(budget):
    from jcodemunch_mcp.parser import parse_budget

    with parse_budget.armed("vue", 10) as outer:
        assert outer is not None and parse_budget.active() is outer
        with parse_budget.armed("javascript", 5) as inner:
            assert inner is None, "the nested call opened its own deadline"
            assert parse_budget.active() is outer
            assert grammar_pack.get_parser("javascript")._scope is outer
        assert parse_budget.active() is outer, "the nested call closed its file's deadline"
    assert parse_budget.active() is None


def test_every_parser_attribute_the_extractor_reads_is_on_the_bound_parser():
    """The bound parser carries named attributes only; a missing one would raise
    inside a dedicated parser's `except Exception` and read as a file with no symbols."""
    import re
    from pathlib import Path

    from jcodemunch_mcp.parser import parse_budget

    source = Path(extractor.__file__).read_text(encoding="utf-8")
    read = set(re.findall(r"\b[A-Za-z_]*parser\.([A-Za-z_]+)", source))
    assert "parse" in read, "the scan found no parser.parse call; its pattern is stale"
    missing = sorted(name for name in read if not hasattr(parse_budget._BudgetedParser, name))
    assert not missing, f"the extractor reads parser.{missing} and _BudgetedParser does not carry it"


def test_a_cancelled_parse_leaves_no_deadline_behind(budget):
    from jcodemunch_mcp.parser import parse_budget

    with pytest.raises(ParseBudgetExceeded):
        extractor.parse_file(_slow_python(), "slow.py", "python")
    assert parse_budget.active() is None
    assert [s.name for s in extractor.parse_file("def after():\n    return 1\n", "b.py", "python")] == ["after"]


def test_the_pipeline_and_the_parser_raise_the_same_class():
    """`except ParseBudgetExceeded` at either import path catches both."""
    from jcodemunch_mcp.parser import parse_budget

    assert ParseBudgetExceeded is parse_budget.ParseBudgetExceeded


# --- index_folder, where a user stands ---------------------------------------


def _project(tmp_path, slow_text):
    project = tmp_path / "project"
    project.mkdir()
    (project / "slow_module.py").write_text(slow_text, encoding="utf-8")
    (project / "ordinary.py").write_text("def ordinary_symbol():\n    return 2\n", encoding="utf-8")
    return project


def _parse_warnings(result):
    return [w for w in (result.get("warnings") or []) if "slow_module.py" in w]


def test_a_full_index_names_the_over_budget_file_and_keeps_the_rest(tmp_path, budget):
    project = _project(tmp_path, _slow_python())

    started = time.monotonic()
    result = _index(project, tmp_path)
    elapsed = time.monotonic() - started

    named = _parse_warnings(result)
    assert named, f"no warning names the over-budget file: {result.get('warnings')}"
    assert f"{budget:g}s budget" in named[0]
    assert result.get("success") is True
    assert result.get("symbol_count", 0) >= 1, "the ordinary file's symbol was lost with the slow one"
    assert elapsed < 6.0, f"the index took {elapsed:.2f} s; the slow parse was not stopped"


def test_an_incremental_reindex_names_the_over_budget_file(tmp_path, budget):
    project = _project(tmp_path, "def marker_symbol():\n    return 1\n")
    first = _index(project, tmp_path, incremental=True)
    assert first.get("success") is True and not _parse_warnings(first)

    (project / "slow_module.py").write_text(_slow_python(), encoding="utf-8")
    again = _index(project, tmp_path, incremental=True)

    named = _parse_warnings(again)
    assert named, f"no warning names the over-budget file: {again.get('warnings')}"
    assert f"{budget:g}s budget" in named[0]
