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

# ⚠ Every duration this file bounds is `time.thread_time()`, the test thread's
# own time: a wall-clock bound fails the fixed code on a starved machine
# (harness FINDINGS F-33's instrument defect).

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
    started = time.thread_time()
    try:
        tree = parser.parse(_slow_python().encode("utf-8"))
    except ValueError:
        tree = None
    elapsed = time.thread_time() - started
    assert tree is None, "the parse finished under a 0.1 s timeout; the fixture is not slow here"
    assert elapsed < 3.0, f"a 0.1 s timeout took {elapsed:.2f} s to fire"


# --- parse_file --------------------------------------------------------------


def test_parse_file_stops_an_over_budget_parse(budget):
    started = time.thread_time()
    with pytest.raises(ParseBudgetExceeded) as exc:
        extractor.parse_file(_slow_python(), "slow.py", "python")
    elapsed = time.thread_time() - started

    assert elapsed < 3.0, f"the parse ran {elapsed:.2f} s against a {budget} s budget"
    message = str(exc.value)
    assert f"{budget:g}s budget" in message
    assert "JCODEMUNCH_PARSE_BUDGET_SECONDS" in message


def test_the_budget_does_not_depend_on_file_size(budget):
    """The old wait armed at 128 KiB; L-113's slow shape exists well under that."""
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


def test_every_parser_a_file_loads_carries_its_deadline(budget):
    """A file's second parser (a Vue script, a C header's re-parse) is bound like its first."""
    from jcodemunch_mcp.parser import parse_budget

    with parse_budget.armed("vue", 10) as scope:
        first = grammar_pack.get_parser("vue")
        second = grammar_pack.get_parser("typescript")
        third = grammar_pack.get_parser("typescript")
    for parser in (first, second, third):
        assert type(parser) is parse_budget._BudgetedParser and parser._scope is scope


def test_an_over_budget_embedded_script_is_stopped(monkeypatch):
    """The slow parse is the Vue file's SECOND one; the first (the template) is instant."""
    monkeypatch.setenv("JCODEMUNCH_PARSE_BUDGET_SECONDS", "0.05")
    script = "".join(f"export function f{i}(a: number): number {{ return a + {i}; }}\n" for i in range(30000))
    vue = '<template><div/></template>\n<script setup lang="ts">\n' + script + "</script>\n"
    with pytest.raises(ParseBudgetExceeded):
        extractor.parse_file(vue, "a.vue", "vue")


def test_a_cancel_is_recognised_at_a_budget_smaller_than_a_clock_tick(monkeypatch):
    """Review round 1: the first draft asked a clock whether the parse had been
    stopped, and Windows' 15.6 ms tick answered no for a cancel near the end of
    its deadline. The file then came back empty and unnamed."""
    monkeypatch.setenv("JCODEMUNCH_PARSE_BUDGET_SECONDS", "0.02")
    source = _slow_python(1500)
    outcomes = []
    for _ in range(25):
        try:
            outcomes.append("returned %d" % len(extractor.parse_file(source, "slow.py", "python")))
        except ParseBudgetExceeded:
            outcomes.append("budget")
        except Exception as exc:  # noqa: BLE001 - the outcome is the assertion
            outcomes.append(type(exc).__name__)
    assert outcomes == ["budget"] * 25, outcomes


class _FakeParser:
    """Stands in for tree-sitter's parser under `_BudgetedParser`."""

    def __init__(self, outcome, setter_raises=None):
        self._outcome = outcome
        self._setter_raises = setter_raises
        self.timeouts = []
        self.parsed = 0
        self.resets = 0

    def reset(self):
        self.resets += 1

    @property
    def timeout_micros(self):
        return self.timeouts[-1] if self.timeouts else 0

    @timeout_micros.setter
    def timeout_micros(self, value):
        if self._setter_raises is not None:
            raise self._setter_raises
        self.timeouts.append(value)

    def parse(self, *args, **kwargs):
        self.parsed += 1
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return self._outcome


def _bound(fake, seconds=5.0):
    from jcodemunch_mcp.parser import parse_budget

    scope = parse_budget._Scope(seconds, "python", 10)
    return parse_budget._BudgetedParser(fake, scope), scope


def test_a_parse_error_that_is_not_the_stop_keeps_its_own_class():
    parser, scope = _bound(_FakeParser(ValueError("bad grammar")))
    with pytest.raises(ValueError, match="bad grammar"):
        parser.parse(b"x")
    assert scope.cancelled is False


def test_the_stop_error_is_the_budget_however_early_it_arrives():
    parser, scope = _bound(_FakeParser(ValueError("Parsing failed")))
    with pytest.raises(ParseBudgetExceeded):
        parser.parse(b"x")
    assert scope.cancelled is True


def test_a_parse_that_returns_no_tree_is_the_budget():
    parser, scope = _bound(_FakeParser(None))
    with pytest.raises(ParseBudgetExceeded):
        parser.parse(b"x")
    assert scope.cancelled is True


def test_a_host_that_makes_the_deprecation_an_error_still_gets_its_symbols():
    """`-W error` must not turn every file into "no symbols" inside a parser's `except Exception`."""
    fake = _FakeParser("tree", setter_raises=DeprecationWarning("Use the progress_callback in parse()"))
    parser, scope = _bound(fake)
    assert parser.parse(b"x") == "tree"
    assert scope.cancelled is False


def test_the_bound_parser_is_given_what_is_left_of_the_deadline():
    fake = _FakeParser("tree")
    parser, _ = _bound(fake, seconds=5.0)
    parser.parse(b"x")
    assert 4_000_000 < fake.timeouts[0] <= 5_000_000


def test_time_another_thread_spent_is_not_charged_to_the_file(budget):
    """Review round 1: on a wall clock, another thread's GIL-holding parse spent
    this file's deadline and an under-budget file was skipped as over budget.

    The other thread BURNS CPU here (round 2: a sleep cannot tell this thread's
    clock from the process's, and a process clock charges the file again)."""
    import threading

    from jcodemunch_mcp.parser import parse_budget

    stop = threading.Event()

    def burn():
        while not stop.is_set():
            sum(range(2000))

    burner = threading.Thread(target=burn, daemon=True)
    with parse_budget.armed("python", 10) as scope:
        burner.start()
        try:
            time.sleep(budget * 3)
        finally:
            stop.set()
            burner.join()
        assert scope.remaining() > budget * 0.5, "another thread's time was charged to this file's parse budget"


def test_a_parser_asked_after_the_deadline_is_refused_and_the_file_is_named():
    """A file's second parse (a C header's re-parse, a Vue script) can start after
    the deadline; it must not run, and `parse_file` must still raise for the file."""
    fake = _FakeParser("tree")
    parser, scope = _bound(fake)
    scope.deadline = scope.started - 1.0
    with pytest.raises(ParseBudgetExceeded):
        parser.parse(b"x")
    assert fake.parsed == 0, "a parse was started after its file's deadline"
    assert scope.cancelled is True, "the refusal was not recorded, so a parser that swallows it hides the file"


def test_a_later_parser_gets_what_is_left_not_the_whole_budget():
    fake = _FakeParser("tree")
    parser, scope = _bound(fake, seconds=5.0)
    scope.deadline -= 3.0
    parser.parse(b"x")
    assert 1_000_000 < fake.timeouts[0] <= 2_000_000


def test_a_remainder_under_a_microsecond_still_sets_a_timeout(monkeypatch):
    """`timeout_micros = 0` means no timeout at all."""
    from jcodemunch_mcp.parser import parse_budget

    monkeypatch.setattr(parse_budget, "_clock", lambda: 100.0)
    fake = _FakeParser("tree")
    parser, scope = _bound(fake)
    scope.deadline = 100.0 + 1e-8
    parser.parse(b"x")
    assert fake.timeouts == [1]


def test_a_stopped_parser_is_reset_before_it_is_let_go():
    fake = _FakeParser(ValueError("Parsing failed"))
    parser, _ = _bound(fake)
    with pytest.raises(ParseBudgetExceeded):
        parser.parse(b"x")
    assert fake.resets == 1 and fake.timeout_micros == 0


def test_a_stopped_tree_sitter_parser_parses_the_next_source_from_scratch(budget):
    """Review round 2: tree-sitter keeps a stopped parse to resume it, and the
    same object then returned the OLD source's tree for a new source."""
    from tree_sitter_language_pack import get_parser

    from jcodemunch_mcp.parser import parse_budget

    raw = get_parser("python")
    scope = parse_budget._Scope(budget, "python", 10)
    with pytest.raises(ParseBudgetExceeded):
        parse_budget._BudgetedParser(raw, scope).parse(_slow_python().encode("utf-8"))
    tree = raw.parse(b"def after():\n    return 1\n")
    assert not tree.root_node.has_error
    assert [child.type for child in tree.root_node.children] == ["function_definition"]


def test_the_warning_filter_is_installed_once_not_per_file(budget, monkeypatch):
    """Each `filterwarnings` call clears every module's warn-once registry."""
    import warnings

    from jcodemunch_mcp.parser import parse_budget

    calls = []
    real = warnings.filterwarnings
    monkeypatch.setattr(warnings, "filterwarnings", lambda *a, **k: (calls.append(a), real(*a, **k))[1])
    monkeypatch.setattr(parse_budget, "_filter_installed", False)
    for name in ("a.py", "b.py", "c.py"):
        extractor.parse_file("def f():\n    return 1\n", name, "python")
    assert len(calls) == 1


def test_a_bad_budget_value_is_logged_once_not_per_file(monkeypatch, caplog):
    import logging

    from jcodemunch_mcp.parser import parse_budget

    monkeypatch.setenv("JCODEMUNCH_PARSE_BUDGET_SECONDS", "soon")
    monkeypatch.setattr(parse_budget, "_warned_bad_env", False)
    with caplog.at_level(logging.WARNING, logger=parse_budget.__name__):
        for name in ("a.py", "b.py", "c.py"):
            extractor.parse_file("def f():\n    return 1\n", name, "python")
    assert len([r for r in caplog.records if "non-numeric" in r.getMessage()]) == 1


def test_two_threads_parsing_at_once_each_have_their_own_deadline(budget):
    """Review round 3: with one deadline shared by every thread, a slow file in
    one thread left the fast files of the others empty and unnamed."""
    import threading

    slow, fast = _slow_python(), "def quick():\n    return 1\n"
    outcomes = []
    lock = threading.Lock()

    def work(tag):
        # Each thread mixes slow and fast files, so its own clock has moved on
        # by the time it parses a fast one; a deadline read off another
        # thread's clock then refuses the fast file.
        for round_no in range(2):
            for name, source in [("slow.py", slow)] + [("fast.py", fast)] * 10:
                try:
                    result = [s.name for s in extractor.parse_file(source, name, "python")]
                except ParseBudgetExceeded:
                    result = "budget"
                with lock:
                    outcomes.append((name, result))

    threads = [threading.Thread(target=work, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    slow_results = [r for n, r in outcomes if n == "slow.py"]
    assert slow_results == ["budget"] * 8, slow_results
    wrong = [(n, r) for n, r in outcomes if n == "fast.py" and r != ["quick"]]
    assert not wrong, f"{len(wrong)} of 80 fast parses were not served beside slow ones: {wrong[:3]}"


def test_a_standalone_grammars_parser_is_bound_too(budget):
    """F# brings its own wheel and takes `get_parser`'s other branch."""
    from jcodemunch_mcp.parser import parse_budget

    assert grammar_pack.STANDALONE_GRAMMARS, "no standalone grammar is registered; this guard is stale"
    for name in grammar_pack.STANDALONE_GRAMMARS:
        with parse_budget.armed(name, 10) as scope:
            parser = grammar_pack.get_parser(name)
        assert type(parser) is parse_budget._BudgetedParser and parser._scope is scope, name


def test_a_negative_budget_disables_the_limit(monkeypatch):
    monkeypatch.setenv("JCODEMUNCH_PARSE_BUDGET_SECONDS", "-1")
    symbols = extractor.parse_file("def f():\n    return 1\n", "a.py", "python")
    assert [s.name for s in symbols] == ["f"]


def test_a_binding_that_cannot_cancel_gets_its_own_parser_back_and_one_warning(budget, monkeypatch, caplog):
    """Wrapping a parser with no `timeout_micros` would raise AttributeError
    inside a dedicated parser's `except Exception` and lose every file."""
    import logging

    from jcodemunch_mcp.parser import parse_budget

    class _NoTimeout:
        def parse(self, source):
            return "tree"

    monkeypatch.setattr(parse_budget, "_warned_no_timeout", False)
    raw = _NoTimeout()
    with caplog.at_level(logging.WARNING, logger=parse_budget.__name__):
        with parse_budget.armed("python", 10):
            assert parse_budget.bind(raw) is raw
            assert parse_budget.bind(raw) is raw
    assert len([r for r in caplog.records if "timeout_micros" in r.getMessage()]) == 1


def test_a_stop_that_returns_no_tree_resets_the_parser_too():
    fake = _FakeParser(None)
    parser, _ = _bound(fake)
    with pytest.raises(ParseBudgetExceeded):
        parser.parse(b"x")
    assert fake.resets == 1 and fake.timeout_micros == 0


def test_a_parser_that_cannot_be_reset_still_names_the_file():
    class _NoReset(_FakeParser):
        def reset(self):
            raise RuntimeError("no reset in this binding")

    parser, scope = _bound(_NoReset(ValueError("Parsing failed")))
    with pytest.raises(ParseBudgetExceeded):
        parser.parse(b"x")
    assert scope.cancelled is True


def test_the_message_counts_bytes_not_characters(budget):
    source = '"""' + "\u00e9" * 10 + '"""\n' + _slow_python()
    assert len(source.encode("utf-8")) == len(source) + 10
    with pytest.raises(ParseBudgetExceeded) as exc:
        extractor.parse_file(source, "slow.py", "python")
    assert f"({len(source.encode('utf-8')):,} bytes, python)" in str(exc.value)


def test_text_in_an_unregistered_language_is_never_encoded():
    """`main` returned [] before touching the text; a lone surrogate must not raise now."""
    assert extractor.parse_file("\ud800", "a.zzz", "no-such-language") == []


def test_with_the_variable_unset_the_limit_is_on_at_its_default(monkeypatch):
    """Review round 4: every other test here sets the variable, so the shipped
    configuration could be switched off with this file green (#437's shape)."""
    from jcodemunch_mcp.parser import parse_budget

    monkeypatch.delenv("JCODEMUNCH_PARSE_BUDGET_SECONDS", raising=False)
    assert parse_budget.DEFAULT_PARSE_BUDGET_SECONDS == 20.0  # the documented default (CLAUDE.md Env Vars)
    assert parse_budget.budget_seconds() == parse_budget.DEFAULT_PARSE_BUDGET_SECONDS
    with parse_budget.armed("python", 10) as scope:
        assert scope is not None and scope.budget == parse_budget.DEFAULT_PARSE_BUDGET_SECONDS
        assert type(grammar_pack.get_parser("python")) is parse_budget._BudgetedParser


def test_a_value_that_is_not_a_number_keeps_the_default_limit(monkeypatch):
    """A typo must not switch the limit off."""
    from jcodemunch_mcp.parser import parse_budget

    monkeypatch.setenv("JCODEMUNCH_PARSE_BUDGET_SECONDS", "soon")
    with parse_budget.armed("python", 10) as scope:
        assert scope is not None and scope.budget == parse_budget.DEFAULT_PARSE_BUDGET_SECONDS


def test_a_stopped_file_is_named_even_when_another_part_of_it_parsed(monkeypatch):
    """Review round 4: an Astro file's over-budget frontmatter is stopped inside a
    nested call that swallows it, and the outer dispatch still returns a symbol
    for the template. A partial file returned unnamed is L-114's own symptom."""
    monkeypatch.setenv("JCODEMUNCH_PARSE_BUDGET_SECONDS", "0")
    small = "---\nexport function early(a: number): number { return a }\n---\n<div/>\n"
    assert extractor.parse_file(small, "a.astro", "astro"), "the fixture needs a part that parses"

    monkeypatch.setenv("JCODEMUNCH_PARSE_BUDGET_SECONDS", "0.05")
    script = "".join(f"export function f{i}(a: number): number {{ return a + {i}; }}\n" for i in range(30000))
    with pytest.raises(ParseBudgetExceeded):
        extractor.parse_file("---\n" + script + "---\n<div/>\n", "a.astro", "astro")


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

    started = time.thread_time()
    result = _index(project, tmp_path)
    elapsed = time.thread_time() - started

    named = _parse_warnings(result)
    assert named, f"no warning names the over-budget file: {result.get('warnings')}"
    assert f"{budget:g}s budget" in named[0]
    assert result.get("success") is True
    assert result.get("symbol_count", 0) >= 1, "the ordinary file's symbol was lost with the slow one"
    assert elapsed < 6.0, f"the index took {elapsed:.2f} s; the slow parse was not stopped"


def test_a_first_index_leaves_no_thread_behind(tmp_path, budget, monkeypatch):
    """Review round 2: the first index is the default route, and the wall-clock
    thread wait of the other routes (LEDGER L-116) charged a fast file for
    time another thread held the GIL. The first index parses in its own thread;
    the tree-sitter limit inside `parse_file` is the one it has."""
    import threading

    big_fast = _PADDING * 2000 + "def marker_symbol():\n    return 1\n"
    assert len(big_fast) >= _PARSE_WATCHDOG_MIN_BYTES
    project = _project(tmp_path, big_fast)

    started = []
    real_start = threading.Thread.start

    def recording_start(self, *args, **kwargs):
        started.append(self.name)
        return real_start(self, *args, **kwargs)

    monkeypatch.setattr(threading.Thread, "start", recording_start)
    result = _index(project, tmp_path)
    monkeypatch.undo()

    assert result.get("success") is True and result.get("symbol_count", 0) >= 2
    # git subprocesses start reader threads; the wait's worker runs `_target`.
    waits = [name for name in started if "_target" in name]
    assert waits == [], f"the first index started a parse wait thread: {started}"


def test_an_incremental_reindex_names_the_over_budget_file(tmp_path, budget):
    project = _project(tmp_path, "def marker_symbol():\n    return 1\n")
    first = _index(project, tmp_path, incremental=True)
    assert first.get("success") is True and not _parse_warnings(first)

    (project / "slow_module.py").write_text(_slow_python(), encoding="utf-8")
    again = _index(project, tmp_path, incremental=True)

    named = _parse_warnings(again)
    assert named, f"no warning names the over-budget file: {again.get('warnings')}"
    assert f"{budget:g}s budget" in named[0]
