"""The parse budget bounds a file's PYTHON-side time, in the parsing thread (LEDGER L-116).

L-114 put the tree-sitter limit inside `parse_file`. What was left, the time
spent in Python walking the finished tree, was bounded by a thread wait
(`parse_file_budgeted`, 1.108.182), and that wait had four defects: one route
never asked it; it was wall-clock, so a file was charged for time another
thread held the GIL; its abandoned worker kept running and could write to the
parse cache for a file the caller was told is skipped; and it counted
characters where the parse limit counts bytes.

The wait is gone and no second thread exists to abandon. The parsing thread
passes checkpoints: inside a `parse_file` call a checkpoint reads the file's
deadline (the thread's own time) once in `CHECK_EVERY` and raises
`ParseBudgetExceeded` once it has passed. The limit is cooperative, so it holds
only where a checkpoint is passed, and two review rounds each found a loop that
passed none. The rule this file holds is therefore over the source, not over a
list of walkers: every `for` and `while` statement in the modules a parse runs
through starts with a checkpoint, every function there that can call itself
passes one, and building a `Symbol` is one. The exemptions are named below.

The Python side is isolated with a clock the test controls: the deadline
"passes" the moment the file's C parse returns, so nothing here depends on a
fixture being slow on a particular machine.
"""
from __future__ import annotations

import ast
import os
import threading
import time
import traceback
from pathlib import Path

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
    # ⚠ Half a second past the 5 s budget, not a thousand: a checkpoint that
    # stopped a file only when it was far over would pass a larger jump.
    monkeypatch.setattr(parse_budget, "_clock", lambda: real_clock() + (5.5 if state["late"] else 0.0))
    return state


@pytest.fixture
def deadline_already_passed(monkeypatch):
    """For a language that makes no tree-sitter parse: the deadline passes at once."""
    monkeypatch.setenv("JCODEMUNCH_PARSE_BUDGET_SECONDS", "5")
    real_clock = parse_budget._clock
    armed = {"n": 0}

    def clock():
        armed["n"] += 1
        return real_clock() + (0.0 if armed["n"] == 1 else 5.5)  # the first read sets the deadline

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


def test_a_deadline_that_passes_late_in_the_walk_stops_it_there(monkeypatch, symbols_built):
    """The clock goes late after several clock reads, not before the first: a
    checkpoint that stopped reading the clock part-way would pass every case
    whose deadline is already gone at the first read (review round 1)."""
    monkeypatch.setenv("JCODEMUNCH_PARSE_BUDGET_SECONDS", "5")
    late_after = 5 * parse_budget.CHECK_EVERY
    real_clock = parse_budget._clock
    monkeypatch.setattr(
        parse_budget, "_clock", lambda: real_clock() + (5.5 if len(symbols_built) >= late_after else 0.0)
    )

    with pytest.raises(ParseBudgetExceeded):
        extractor.parse_file(_python_source(), "a.py", "python")

    assert late_after <= len(symbols_built) <= late_after + parse_budget.CHECK_EVERY


# Each of these is parsed by a dedicated walker that builds no symbol for the
# input: a tree of calls or values and no definition. Review round 1 of L-116
# found seven such walkers with no checkpoint, running on past the deadline.
_SYMBOL_FREE = {
    "lua": ("a.lua", "f(1)\n"),
    "julia": ("a.jl", "f(1)\n"),
    "elixir": ("a.ex", "IO.puts(1)\n"),
    "xml": ("a.xml", "<a/>"),
    "hcl": ("a.tf", "x = [1]\n"),
    "objc": ("a.m", "void f(void){g(1);}\n"),
    "c": ("a.c", "int x[] = {1};\n"),
}


@pytest.mark.parametrize("language", sorted(_SYMBOL_FREE))
def test_a_dedicated_walker_is_stopped_on_a_tree_with_nothing_to_build(
    language, deadline_passes_after_the_c_parse
):
    filename, unit = _SYMBOL_FREE[language]
    source = unit * 2000
    if language == "xml":
        source = "<r>" + source + "</r>"

    with pytest.raises(ParseBudgetExceeded):
        extractor.parse_file(source, filename, language)


def _own_nodes(function):
    """The function's own body: not the bodies of functions defined inside it."""
    todo = list(ast.iter_child_nodes(function))
    while todo:
        node = todo.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            todo.extend(ast.iter_child_nodes(node))


def _passes_a_checkpoint(nodes) -> bool:
    return any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "checkpoint"
        for n in nodes
    )


def _extractor_functions():
    tree = ast.parse(Path(extractor.__file__).read_text(encoding="utf-8"))
    return [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _recursion_without_a_checkpoint(functions) -> list[str]:
    """Names on a call cycle that no checkpoint breaks.

    Keyed by name: the extractor has dozens of nested `_walk`s, and a name is
    covered only when EVERY function of that name passes a checkpoint.
    """
    by_name: dict = {}
    for fn in functions:
        by_name.setdefault(fn.name, []).append(fn)
    covered = {name for name, fns in by_name.items() if all(_passes_a_checkpoint(_own_nodes(f)) for f in fns)}
    graph = {
        name: {
            n.func.id
            for f in fns
            for n in _own_nodes(f)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in by_name
        } - covered
        for name, fns in by_name.items()
        if name not in covered
    }

    def reaches_itself(start: str) -> bool:
        seen, todo = set(), list(graph[start])
        while todo:
            name = todo.pop()
            if name == start:
                return True
            if name not in seen:
                seen.add(name)
                todo.extend(graph.get(name, ()))
        return False

    return sorted(name for name in graph if reaches_itself(name))


def test_no_recursion_in_the_extractor_lacks_a_checkpoint():
    """The Python side is bounded only where a checkpoint is passed (it is
    cooperative), so the property is over the file, not over a list of
    walkers: a function that can call itself, directly or through another,
    passes a checkpoint somewhere on the way round."""
    assert _recursion_without_a_checkpoint(_extractor_functions()) == []


_PARSER_DIR = Path(extractor.__file__).parent

# The modules a `parse_file` call runs Python loops in.
_ON_THE_PARSE_PATH = {
    "extractor.py", "astro_shared.py", "complexity.py", "racket_reader.py",
    "sql_preprocessor.py", "template_shared.py",
}
# Every other module of the package, and why its loops need no checkpoint. A
# new module must be put in one set or the other (the test below).
_OFF_THE_PARSE_PATH = {
    "__init__.py": "re-exports",
    "parse_budget.py": "the budget itself",
    "symbols.py": "the Symbol dataclass; its checkpoint is `__post_init__`",
    "grammar_pack.py": "loads a parser; no loop over a file's content",
    "parse_cache.py": "wraps `parse_file` from outside its deadline",
    "languages.py": "registry tables and path-suffix lookups; no loop over a file's content",
    "imports.py": "import extraction runs after `parse_file`, outside its deadline",
    "hierarchy.py": "builds the outline from finished symbols, outside `parse_file`",
    "fqn.py": "name translation for lookups, outside `parse_file`",
}
# Loops that pass no checkpoint of their own, each with the reason it is safe.
# (module, function, the loop's iterable as source text)
_LOOPS_WITHOUT_A_CHECKPOINT = {
    ("complexity.py", "_bracket_nesting_depth", "body"):
        "one pass over one symbol's characters, once per symbol from a loop that has its checkpoint",
    ("complexity.py", "_count_params", "params_str"):
        "one pass over one signature's characters",
    ("extractor.py", "_walk_tree", "(*node.children, *adopted) if adopted else node.children"):
        "every iteration enters `_walk_tree`, whose first statement is a checkpoint",
}


def _loops(module: str):
    tree = ast.parse((_PARSER_DIR / module).read_text(encoding="utf-8"))
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in _own_nodes(fn):
                if isinstance(node, (ast.For, ast.While, ast.AsyncFor)):
                    yield fn.name, node
    for node in tree.body:  # module-level loops belong to no function
        if isinstance(node, (ast.For, ast.While)):
            yield "<module>", node


def _starts_with_a_checkpoint(loop) -> bool:
    return _passes_a_checkpoint([loop.body[0].value]) if isinstance(loop.body[0], ast.Expr) else False


def test_every_loop_on_the_parse_path_starts_with_a_checkpoint():
    """Review round 2: `_find_enclosing_symbol` (a `for` per call site, each
    rescanning every symbol), a Razor brace scan (`while i < len(content)`) and
    a dbt directive loop each ran quadratic and unstopped, because the scans
    then held looked for recursion and `while stack:` only (timings: the L-116
    row). The rule is every loop statement."""
    missing, exempt_seen = [], set()
    for module in sorted(_ON_THE_PARSE_PATH):
        for function, loop in _loops(module):
            if _starts_with_a_checkpoint(loop):
                continue
            key = (module, function, ast.unparse(loop.iter if hasattr(loop, "iter") else loop.test))
            if key in _LOOPS_WITHOUT_A_CHECKPOINT:
                exempt_seen.add(key)
            else:
                missing.append(f"{module}:{loop.lineno} {function}")
    assert missing == [], "a loop on the parse path does not start with parse_budget.checkpoint()"
    assert exempt_seen == set(_LOOPS_WITHOUT_A_CHECKPOINT), "an exemption names a loop that is gone or has a checkpoint"
    # The third exemption rests on `_walk_tree` STARTING with a checkpoint.
    walkers = [fn for fn in _extractor_functions() if fn.name == "_walk_tree"]
    assert len(walkers) == 1
    body = walkers[0].body
    first = body[1] if isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) else body[0]
    assert isinstance(first, ast.Expr) and _passes_a_checkpoint([first.value]), (
        "`_walk_tree` no longer starts with a checkpoint, and its child loop is exempt because it did"
    )


def test_a_file_that_ends_past_its_deadline_with_no_checkpoint_seeing_it_is_named(
    deadline_passes_after_the_c_parse, symbols_built
):
    """Review round 3: one slow call (a regex over the whole file) passes no
    checkpoint, and a small file passes fewer than `CHECK_EVERY` after it, so
    the clock was never read again and the file came back unnamed. One
    function here: a handful of checkpoints, then the end of the file."""
    with pytest.raises(ParseBudgetExceeded):
        extractor.parse_file("def f():\n    return 1\n", "a.py", "python")
    assert len(symbols_built) == 1, "the walk was stopped early; this case is about the end of the file"


def test_a_file_that_ends_past_its_deadline_having_built_nothing_is_named_too(
    deadline_passes_after_the_c_parse, symbols_built
):
    """Review round 4: the end-of-file read must not depend on the file having
    symbols; an empty result is exactly what a slow file with none returns."""
    with pytest.raises(ParseBudgetExceeded):
        extractor.parse_file("print(1)\n", "a.py", "python")
    assert symbols_built == []


def test_a_file_stopped_by_the_parser_is_stopped_at_the_next_checkpoint(monkeypatch):
    """tree-sitter's timer is wall-clock and the deadline here is the thread's
    own time, so a parser can stop a file whose deadline has NOT passed (a
    starved box). The file is stopped all the same: every later checkpoint
    raises, though the clock would say there is time left."""
    monkeypatch.setenv("JCODEMUNCH_PARSE_BUDGET_SECONDS", "60")
    with parse_budget.armed("python", 10) as scope:
        parse_budget.checkpoint()
        scope.stop()
        assert scope.remaining() > 0
        for _ in range(3):
            with pytest.raises(ParseBudgetExceeded):
                parse_budget.checkpoint()


def test_no_work_list_loop_in_the_extractor_lacks_a_checkpoint():
    """`while stack:` visits a whole tree. Round 1's narrower scan, kept: the
    exemption table above must never grow a work-list loop."""
    missing = [
        f"{fn.name}:{node.lineno}"
        for fn in _extractor_functions()
        for node in _own_nodes(fn)
        if isinstance(node, ast.While) and isinstance(node.test, ast.Name) and not _passes_a_checkpoint(ast.walk(node))
    ]
    assert missing == []


def test_every_module_of_the_parser_package_is_on_the_path_or_named_off_it():
    on_disk = {p.name for p in _PARSER_DIR.glob("*.py")}
    assert on_disk == _ON_THE_PARSE_PATH | set(_OFF_THE_PARSE_PATH)
    assert not _ON_THE_PARSE_PATH & set(_OFF_THE_PARSE_PATH)


# Review round 2's three inputs, small: each loop below builds no symbol while
# it runs and none of the three is a recursion.
_QUADRATIC_SHAPES = {
    "python-call-sites": (
        "calls.py", "python",
        "".join(f"def f{i}(): return {i}\n" for i in range(300)) + "".join(f"f{i % 300}()\n" for i in range(1500)),
    ),
    "razor-code-blocks": ("a.razor", "razor", "@code {\n" * 1500),
    "dbt-directives": ("m.sql", "sql", "{# \n{% macro m() %}\n" * 300),
}


@pytest.mark.parametrize("shape", sorted(_QUADRATIC_SHAPES))
def test_a_loop_that_builds_nothing_and_is_no_recursion_is_stopped(shape, monkeypatch):
    filename, language, source = _QUADRATIC_SHAPES[shape]
    monkeypatch.setenv("JCODEMUNCH_PARSE_BUDGET_SECONDS", "5")
    real_clock = parse_budget._clock
    reads = []

    def clock():
        reads.append(1)
        return real_clock() + (0.0 if len(reads) == 1 else 5.5)  # the first read sets the deadline

    monkeypatch.setattr(parse_budget, "_clock", clock)
    with pytest.raises(ParseBudgetExceeded):
        parse_file_budgeted(source, filename, language)
    assert len(reads) <= 3, f"the walk went on for {len(reads)} clock reads after its deadline"


def test_the_recursion_scan_sees_a_cycle_it_should():
    """The scan against the defect it names: self-recursion, a pair, and a
    nested function whose parent has the checkpoint."""
    tree = ast.parse(
        "def direct(n):\n    direct(n)\n"
        "def ping(n):\n    pong(n)\n"
        "def pong(n):\n    ping(n)\n"
        "def outer(n):\n    parse_budget.checkpoint()\n    def inner(m):\n        inner(m)\n    inner(n)\n"
        "def fine(n):\n    parse_budget.checkpoint()\n    fine(n)\n"
        "def leaf(n):\n    return n\n"
    )
    functions = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    assert _recursion_without_a_checkpoint(functions) == ["direct", "inner", "ping", "pong"]


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
    """A parse-cache hit rebuilds every stored symbol of a file outside any
    parse; that path must not read a clock."""
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
    # A sleep after the C parse stands in for waiting on the interpreter while
    # another thread runs: wall time passes, this thread's own time does not.
    # No race: a second real thread would make the outcome depend on the box.
    # ⚠ The file must fit its budget in the thread's OWN time on any runner: a
    # 0.3 s budget failed on a cold windows-latest job (PR #978), where loading
    # the grammar and parsing took longer than that. So the grammar is loaded
    # first, the file is small, and the budget is a second; the sleep is longer
    # than the budget, which is all the wall-clock mutant needs.
    source = _python_source(50)
    monkeypatch.setenv("JCODEMUNCH_PARSE_BUDGET_SECONDS", "0")
    assert len(extractor.parse_file(source, "warm.py", "python")) == 50
    monkeypatch.setenv("JCODEMUNCH_PARSE_BUDGET_SECONDS", "1")
    real_parse = parse_budget._BudgetedParser.parse

    def parse(self, *args, **kwargs):
        tree = real_parse(self, *args, **kwargs)
        time.sleep(1.3)
        return tree

    monkeypatch.setattr(parse_budget._BudgetedParser, "parse", parse)
    assert len(extractor.parse_file(source, "a.py", "python")) == 50


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
        return real_clock() + (5.5 if getattr(late, "on", False) and getattr(late, "passed", False) else 0.0)

    monkeypatch.setattr(parse_budget, "armed", armed)
    monkeypatch.setattr(parse_budget._BudgetedParser, "parse", parse)
    monkeypatch.setattr(parse_budget, "_clock", clock)
    return late


def test_a_first_index_names_the_file_whose_walk_ran_over(tmp_path, only_the_slow_module_is_late):
    """The first index never asked the old wait: on 1.108.329 it indexed a file
    whose walk ran far past the budget, with no warning (the L-116 row)."""
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
        started.append(traceback.extract_stack())
        return real_start(self, *args, **kwargs)

    parsed_on = []
    real_parse_file = extractor.parse_file

    def recording_parse_file(*args, **kwargs):
        parsed_on.append(threading.get_ident())
        return real_parse_file(*args, **kwargs)

    monkeypatch.setattr(threading.Thread, "start", recording_start)
    monkeypatch.setattr(extractor, "parse_file", recording_parse_file)
    result = _index(project, tmp_path, incremental=incremental)
    monkeypatch.undo()

    assert result.get("success") is True
    # The property, at the route: every file is parsed on the thread that
    # called `index_folder` (review round 2: a wait put back around
    # `parse_file_budgeted` from `_indexing_pipeline.py` passed the check below).
    assert parsed_on and set(parsed_on) == {threading.get_ident()}, "a file was parsed on another thread"
    # git subprocesses start reader threads, so "no thread at all" is not the
    # property. A thread started from anywhere under a file's parse is: found
    # by WHERE it was started, never by its name (review round 1 of L-116: a
    # name match passed with a thread started inside `parse_file`).
    parse_files = {"extractor.py", "parse_budget.py", "grammar_pack.py", "parse_cache.py"}
    under_a_parse = [
        [f"{os.path.basename(f.filename)}:{f.name}" for f in stack][-6:]
        for stack in started
        if any(os.path.basename(f.filename) in parse_files or f.name == "parse_file_budgeted" for f in stack)
    ]
    assert under_a_parse == []


def test_parse_file_budgeted_is_parse_file(monkeypatch):
    """The name stays for its three callers; it adds nothing and waits for nothing."""
    monkeypatch.setenv("JCODEMUNCH_PARSE_BUDGET_SECONDS", "5")
    calls = []
    monkeypatch.setattr(
        _indexing_pipeline, "parse_file", lambda content, path, language, repo=None: calls.append(threading.get_ident()) or ["sym"]
    )
    assert parse_file_budgeted("a" * 200_000, "big.js", "javascript") == ["sym"]
    assert calls == [threading.get_ident()], "the parse ran in another thread"
