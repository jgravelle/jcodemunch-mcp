"""The parse budget bounds a file's PYTHON-side time, in the parsing thread (LEDGER L-116).

L-114 put the tree-sitter limit inside `parse_file`. What was left, the time
spent in Python walking the finished tree, was bounded by a thread wait
(`parse_file_budgeted`, 1.108.182), and that wait had four defects: one route
never asked it; it was wall-clock, so a file was charged for time another
thread held the GIL; its abandoned worker kept running and could write to the
parse cache for a file the caller was told is skipped; and it counted
characters where the parse limit counts bytes.

The wait is gone. Building a `Symbol` is a checkpoint: inside a `parse_file`
call it reads the file's deadline (the parsing thread's own time) and raises
`ParseBudgetExceeded` once the deadline has passed. Every extractor builds
symbols, so every route and every language has the limit by construction, and
no second thread exists to abandon.

The Python side is isolated with a clock the test controls: the deadline
"passes" the moment the file's C parse returns, so nothing here depends on a
fixture being slow on a particular machine.
"""
from __future__ import annotations

import threading

import pytest

from jcodemunch_mcp.parser import extractor, parse_budget
from jcodemunch_mcp.parser.symbols import Symbol
from jcodemunch_mcp.tools import _indexing_pipeline
from jcodemunch_mcp.tools._indexing_pipeline import ParseBudgetExceeded, parse_file_budgeted
from jcodemunch_mcp.tools.index_folder import index_folder

# A clock check happens once in this many checkpoints at most (the module's
# own constant is read below, so the bound here cannot drift from it).
N_FUNCTIONS = 600


def _python_source(n: int = N_FUNCTIONS) -> str:
    return "".join(f"def f{i}(a):\n    return a + {i}\n\n" for i in range(n))


def _vue_source(n: int = N_FUNCTIONS) -> str:
    script = "".join(f"function f{i}(a) {{ return a + {i} }}\n" for i in range(n))
    return "<template><div/></template>\n<script>\n" + script + "</script>\n"


def _sql_source(n: int = N_FUNCTIONS) -> str:
    return "".join(f"CREATE TABLE t{i} (id INT, name TEXT);\n" for i in range(n))


@pytest.fixture
def deadline_passes_after_the_c_parse(monkeypatch):
    """The file's deadline passes when its (last) tree-sitter parse returns.

    Everything after that point is Python-side work, which is what this file
    is about. `built` counts the symbols constructed afterwards.
    """
    monkeypatch.setenv("JCODEMUNCH_PARSE_BUDGET_SECONDS", "5")
    state = {"late": False}
    real_parse = parse_budget._BudgetedParser.parse
    real_clock = parse_budget._clock

    def parse(self, *args, **kwargs):
        tree = real_parse(self, *args, **kwargs)
        state["late"] = True
        return tree

    monkeypatch.setattr(parse_budget._BudgetedParser, "parse", parse)
    monkeypatch.setattr(parse_budget, "_clock", lambda: real_clock() + (1000.0 if state["late"] else 0.0))
    return state


@pytest.fixture
def deadline_already_passed(monkeypatch):
    """For a language that makes no tree-sitter parse: the deadline passes at once."""
    monkeypatch.setenv("JCODEMUNCH_PARSE_BUDGET_SECONDS", "5")
    real_clock = parse_budget._clock
    armed = {"n": 0}

    def clock():
        armed["n"] += 1
        return real_clock() + (0.0 if armed["n"] == 1 else 1000.0)  # the first read sets the deadline

    monkeypatch.setattr(parse_budget, "_clock", clock)


@pytest.fixture
def symbols_built(monkeypatch):
    built = []
    real_post_init = Symbol.__post_init__

    def post_init(self):
        built.append(self.name)
        return real_post_init(self)

    monkeypatch.setattr(Symbol, "__post_init__", post_init)
    return built


# --- parse_file ---------------------------------------------------------------


@pytest.mark.parametrize(
    "source, filename, language",
    [
        (_python_source(), "a.py", "python"),        # the generic spec walker
        (_vue_source(), "a.vue", "vue"),              # a dedicated parser with a nested parse_file
        (_sql_source(), "a.sql", "sql"),              # a dedicated parser, statement by statement
    ],
    ids=["python", "vue", "sql"],
)
def test_a_walk_past_the_deadline_is_stopped_and_the_file_named(
    source, filename, language, deadline_passes_after_the_c_parse, symbols_built
):
    with pytest.raises(ParseBudgetExceeded) as exc:
        extractor.parse_file(source, filename, language)

    assert "5s budget" in str(exc.value) and "JCODEMUNCH_PARSE_BUDGET_SECONDS" in str(exc.value)
    assert len(symbols_built) <= parse_budget.CHECK_EVERY + 1, (
        f"{len(symbols_built)} symbols were built after the deadline; the walk was not stopped"
    )
    assert parse_budget.active() is None


def test_a_walk_that_builds_no_symbol_is_stopped_too(deadline_passes_after_the_c_parse, symbols_built):
    """A tree of statements and no definitions: nothing is built, so only the
    generic walker's own checkpoint can stop it."""
    source = "".join(f"print({i})\n" for i in range(2000))

    with pytest.raises(ParseBudgetExceeded):
        extractor.parse_file(source, "calls.py", "python")

    assert symbols_built == []


def test_the_same_files_parse_in_full_when_the_deadline_holds(monkeypatch):
    monkeypatch.setenv("JCODEMUNCH_PARSE_BUDGET_SECONDS", "60")
    for source, filename, language in (
        (_python_source(), "a.py", "python"),
        (_vue_source(), "a.vue", "vue"),
        (_sql_source(), "a.sql", "sql"),
    ):
        assert len(extractor.parse_file(source, filename, language)) >= N_FUNCTIONS, language


def test_a_language_with_no_tree_sitter_parse_is_bounded_too(deadline_already_passed, symbols_built):
    """AutoHotkey is parsed by regex: no parser is loaded, so only the symbols can stop it."""
    source = "".join(f"Func{i}(a) {{\n    return a\n}}\n\n" for i in range(N_FUNCTIONS))
    with pytest.raises(ParseBudgetExceeded):
        extractor.parse_file(source, "a.ahk", "autohotkey")
    assert len(symbols_built) <= parse_budget.CHECK_EVERY + 1


def test_the_message_counts_bytes(deadline_passes_after_the_c_parse):
    source = '"""' + "é" * 10 + '"""\n' + _python_source()
    with pytest.raises(ParseBudgetExceeded) as exc:
        extractor.parse_file(source, "a.py", "python")
    assert f"({len(source.encode('utf-8')):,} bytes, python)" in str(exc.value)


def test_with_the_budget_off_nothing_is_stopped_and_no_clock_is_read(monkeypatch):
    monkeypatch.setenv("JCODEMUNCH_PARSE_BUDGET_SECONDS", "0")
    reads = []
    real_clock = parse_budget._clock
    monkeypatch.setattr(parse_budget, "_clock", lambda: reads.append(1) or real_clock())
    assert len(extractor.parse_file(_python_source(), "a.py", "python")) == N_FUNCTIONS
    assert reads == []


def test_a_symbol_built_outside_parse_file_reads_no_clock(monkeypatch):
    """Loading an index builds every stored symbol; that path must not pay for the budget."""
    monkeypatch.setenv("JCODEMUNCH_PARSE_BUDGET_SECONDS", "5")
    reads = []
    real_clock = parse_budget._clock
    monkeypatch.setattr(parse_budget, "_clock", lambda: reads.append(1) or real_clock())
    template = extractor.parse_file("def f():\n    return 1\n", "a.py", "python")[0]
    reads.clear()
    import dataclasses

    for _ in range(500):
        dataclasses.replace(template)
    assert reads == []


def test_a_parser_that_swallows_the_stop_still_has_its_file_named(deadline_passes_after_the_c_parse, monkeypatch):
    """Many dedicated parsers wrap their work in `except Exception`."""
    real = extractor._parse_file_within_budget

    def swallowing(*args, **kwargs):
        try:
            return real(*args, **kwargs)
        except Exception:
            return []

    monkeypatch.setattr(extractor, "_parse_file_within_budget", swallowing)
    with pytest.raises(ParseBudgetExceeded):
        extractor.parse_file(_python_source(), "a.py", "python")


def test_a_parser_that_swallows_the_stop_per_item_builds_nothing_more(deadline_already_passed):
    """`except Exception` around one item's work is common in the extractor. If
    only one checkpoint in `CHECK_EVERY` raised, such a loop would go on
    building 63 symbols in 64 at full cost, and the budget would bound nothing."""
    fields = dict(
        id="a.py::f#function", file="a.py", name="f", qualified_name="f",
        kind="function", language="python", signature="def f()",
    )
    built = 0
    with parse_budget.armed("python", 10) as scope:
        for _ in range(20 * parse_budget.CHECK_EVERY):
            try:
                Symbol(**fields)
                built += 1
            except Exception:
                pass
        assert scope.cancelled
    assert built < parse_budget.CHECK_EVERY, f"{built} symbols were built after the file was stopped"


def test_the_clock_is_not_read_at_every_checkpoint(monkeypatch):
    """A checkpoint is on the extractor's hottest paths (cost: the PR's evidence)."""
    monkeypatch.setenv("JCODEMUNCH_PARSE_BUDGET_SECONDS", "60")
    reads = []
    real_clock = parse_budget._clock
    monkeypatch.setattr(parse_budget, "_clock", lambda: reads.append(1) or real_clock())
    assert len(extractor.parse_file(_python_source(), "a.py", "python")) == N_FUNCTIONS
    # The walk passes a checkpoint per node and per symbol, several per function.
    assert len(reads) < N_FUNCTIONS, f"{len(reads)} clock reads for {N_FUNCTIONS} functions"


def test_another_threads_time_is_not_charged_to_the_walk(monkeypatch):
    """The wait this replaces was wall-clock: 10 of 60 fast files were named over
    budget beside another thread's parse (the L-116 row)."""
    monkeypatch.setenv("JCODEMUNCH_PARSE_BUDGET_SECONDS", "0.5")
    stop = threading.Event()

    def burn():
        while not stop.is_set():
            sum(range(2000))

    burner = threading.Thread(target=burn, daemon=True)
    burner.start()
    try:
        results = [len(extractor.parse_file(_python_source(200), "a.py", "python")) for _ in range(40)]
    finally:
        stop.set()
        burner.join()
    assert results == [200] * 40


# --- the routes, where a user stands ------------------------------------------


def _project(tmp_path, slow_text):
    project = tmp_path / "project"
    project.mkdir()
    (project / "slow_module.py").write_text(slow_text, encoding="utf-8")
    (project / "ordinary.py").write_text("def ordinary_symbol():\n    return 2\n", encoding="utf-8")
    return project


def _index(project, tmp_path, incremental):
    return index_folder(
        str(project),
        use_ai_summaries=False,
        storage_path=str(tmp_path / "store"),
        incremental=incremental,
        context_providers=False,
    )


def _named(result):
    return [w for w in (result.get("warnings") or []) if "slow_module.py" in w]


@pytest.fixture
def only_the_slow_module_is_late(monkeypatch):
    """`slow_module.py`'s deadline passes after its C parse; other files are untouched."""
    monkeypatch.setenv("JCODEMUNCH_PARSE_BUDGET_SECONDS", "5")
    real_clock = parse_budget._clock
    real_armed = parse_budget.armed
    late = threading.local()

    import contextlib

    @contextlib.contextmanager
    def armed(language, size):
        with real_armed(language, size) as scope:
            late.on = scope is not None and size > 10_000
            try:
                yield scope
            finally:
                late.on = False

    real_parse = parse_budget._BudgetedParser.parse

    def parse(self, *args, **kwargs):
        tree = real_parse(self, *args, **kwargs)
        if getattr(late, "on", False):
            late.passed = True
        return tree

    def clock():
        return real_clock() + (1000.0 if getattr(late, "on", False) and getattr(late, "passed", False) else 0.0)

    monkeypatch.setattr(parse_budget, "armed", armed)
    monkeypatch.setattr(parse_budget._BudgetedParser, "parse", parse)
    monkeypatch.setattr(parse_budget, "_clock", clock)
    return late


def test_a_first_index_names_the_file_whose_walk_ran_over(tmp_path, only_the_slow_module_is_late):
    """The first index never asked the old wait: L-115's Vue file was indexed
    after 14.91 s at a 2 s budget, with no warning."""
    result = _index(_project(tmp_path, _python_source(2000)), tmp_path, incremental=False)

    named = _named(result)
    assert named, f"no warning names the file: {result.get('warnings')}"
    assert "5s budget" in named[0] and " bytes, python)" in named[0]
    assert result.get("success") is True and result.get("symbol_count", 0) >= 1


def test_a_reindex_names_the_same_file_the_same_way(tmp_path, only_the_slow_module_is_late):
    project = _project(tmp_path, "def marker():\n    return 1\n")
    first = _index(project, tmp_path, incremental=True)
    assert first.get("success") is True and not _named(first)

    (project / "slow_module.py").write_text(_python_source(2000), encoding="utf-8")
    again = _index(project, tmp_path, incremental=True)

    named = _named(again)
    assert named, f"no warning names the file: {again.get('warnings')}"
    assert "5s budget" in named[0] and " bytes, python)" in named[0], "the two routes word the skip differently"


@pytest.mark.parametrize("incremental", [False, True])
def test_no_route_starts_a_thread_to_parse_a_large_file(tmp_path, monkeypatch, incremental):
    """The abandoned worker is gone: nothing keeps running after the index returns,
    and nothing can write to the parse cache for a file reported as skipped."""
    monkeypatch.setenv("JCODEMUNCH_PARSE_BUDGET_SECONDS", "5")
    big = _python_source(4000)
    assert len(big) > 131072, "the old wait armed at 128 KiB; the fixture must be over it"
    project = _project(tmp_path, "def marker():\n    return 1\n")
    _index(project, tmp_path, incremental=incremental)
    (project / "slow_module.py").write_text(big, encoding="utf-8")

    started = []
    real_start = threading.Thread.start

    def recording_start(self, *args, **kwargs):
        started.append(self.name)
        return real_start(self, *args, **kwargs)

    monkeypatch.setattr(threading.Thread, "start", recording_start)
    result = _index(project, tmp_path, incremental=incremental)
    monkeypatch.undo()

    assert result.get("success") is True
    # git subprocesses start reader threads; a parse worker ran `_target`.
    assert [name for name in started if "_target" in name] == [], started


def test_parse_file_budgeted_is_parse_file(monkeypatch):
    """The name stays for its three callers; it adds nothing and waits for nothing."""
    monkeypatch.setenv("JCODEMUNCH_PARSE_BUDGET_SECONDS", "5")
    calls = []
    monkeypatch.setattr(
        _indexing_pipeline, "parse_file", lambda content, path, language, repo=None: calls.append(threading.get_ident()) or ["sym"]
    )
    assert parse_file_budgeted("a" * 200_000, "big.js", "javascript") == ["sym"]
    assert calls == [threading.get_ident()], "the parse ran in another thread"
