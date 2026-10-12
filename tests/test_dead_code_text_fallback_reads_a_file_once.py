"""`get_dead_code_v2`'s text fallback does one file's work once (LEDGER L-150).

When an index holds no call reference, `get_dead_code_v2` decides "has this
function a caller?" from text. The loop ran once per function, and each pass
split the symbol's whole file into lines, joined every line outside the symbol
back into one string and ran a word-boundary search over it. The file's text
was cached; the split, the join and the search were not, so one file cost time
quadratic in its functions (the ledger row carries the timings).

These tests count work. They do not time anything (Standing lesson 10-09):

- the number of times a file's text is split does not grow with its functions;
- the characters handed to a word-boundary search, and to the tokeniser, grow
  no faster than the file;
- the bytecode instructions the two modules execute grow in step with the
  input, which sees a per-symbol Python loop over a file's lines or over a
  name's matches whatever it calls;
- the answer is the one the old loop gave, over name shapes a token index
  could get wrong (`add/2`, `ns::f`, `$x`, `save!`, an operator, a name inside
  a longer word, a span past the end of the file).

`_reference_callers` is the loop as it was on `main` at `2e9af12a`, kept here
as the oracle. It is quadratic on purpose and runs on small inputs only.

What the counters do not see: work done inside one C call that is neither a
`splitlines`, a `_word_match` nor the tokeniser: a `str.find` per symbol over
the whole text, a direct `re.search` over lines joined again per symbol, a
`map` over every line. Three such rewrites pass this file (review, round two).
"""

from __future__ import annotations

import os
import random
import re
import sys
from types import SimpleNamespace

import pytest

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.tools import _call_graph
from jcodemunch_mcp.tools import get_dead_code_v2 as v2
from jcodemunch_mcp.tools.index_folder import index_folder


# ---------------------------------------------------------------------------
# counting
# ---------------------------------------------------------------------------


class _Counted(str):
    """A file's text that counts how often it is split into lines."""

    splits = 0

    def splitlines(self, *args, **kwargs):
        type(self).splits += 1
        return str.splitlines(self, *args, **kwargs)


class _Work:
    """Characters handed to a word-boundary search, summed."""

    def __init__(self) -> None:
        self.chars = 0
        self.calls = 0

    def word_match(self, content: str, name: str) -> bool:
        self.chars += len(content)
        self.calls += 1
        return bool(re.search(_call_graph.word_pattern(name), content))

    def word_starts(self, content: str, name: str) -> list[int]:
        self.chars += len(content)
        self.calls += 1
        return [m.start() for m in re.finditer(_call_graph.word_pattern(name), content)]


class _CountedWords:
    """The tokeniser, counting the characters it is handed."""

    def __init__(self, work: _Work, real) -> None:
        self.work, self.real = work, real

    def findall(self, text):
        self.work.tokenised += len(text)
        return self.real.findall(text)

    def finditer(self, text):
        # L-157's word pairs tokenise this way; counted like any other pass.
        self.work.tokenised += len(text)
        return self.real.finditer(text)


def _watch(monkeypatch) -> _Work:
    w = _Work()
    w.tokenised = 0
    monkeypatch.setattr(_call_graph, "_word_match", w.word_match)
    monkeypatch.setattr(v2, "_word_match", w.word_match)
    monkeypatch.setattr(_call_graph, "_word_starts", w.word_starts, raising=False)
    # `raising=False`: on a tree without the tokeniser the other counters still read.
    real = getattr(_call_graph, "_WORD_RE", re.compile(r"\w+"))
    monkeypatch.setattr(_call_graph, "_WORD_RE", _CountedWords(w, real), raising=False)
    _Counted.splits = 0
    return w


class _Ops:
    """Bytecode instructions executed inside the two modules, counted.

    A Python loop costs instructions whatever it calls, so a per-symbol pass
    over a file's lines or over a name's matches shows here even when it
    calls nothing the other counters watch.
    """

    FILES = {os.path.normcase(os.path.abspath(m.__file__)) for m in (_call_graph, v2)}

    def __enter__(self):
        self.count = 0
        self._previous = sys.gettrace()
        sys.settrace(self._enter_frame)
        return self

    def __exit__(self, *exc):
        sys.settrace(self._previous)

    def _enter_frame(self, frame, event, arg):
        if os.path.normcase(os.path.abspath(frame.f_code.co_filename)) not in self.FILES:
            return None
        frame.f_trace_opcodes = True
        frame.f_trace_lines = False
        return self._in_frame

    def _in_frame(self, frame, event, arg):
        if event == "opcode":
            self.count += 1
        return self._in_frame


def _settled(measure) -> int:
    """Run *measure* until two runs in a row count the same; return that count.

    From Python 3.12 the first traced run of a code object undercounts (0 on
    3.12, about two fifths on 3.13 and 3.14) while the interpreter instruments
    it, so a single reading depends on which test ran before. The workloads
    here are deterministic, so two equal readings are the count.
    """
    previous = measure()
    for _ in range(5):
        current = measure()
        if current == previous:
            return current
        previous = current
    raise AssertionError(f"the instruction count did not settle: last two readings {previous}, {current}")


@pytest.fixture
def work(monkeypatch):
    """Count every word-boundary search either module makes."""
    return _watch(monkeypatch)


class _Store:
    def __init__(self, files: dict[str, str]) -> None:
        self.files = files
        self.reads: dict[str, int] = {}

    def get_file_content(self, owner, name, path):
        self.reads[path] = self.reads.get(path, 0) + 1
        text = self.files.get(path)
        return None if text is None else _Counted(text)


def _sym(sid, name, file, line, end_line, kind="function"):
    return {"id": sid, "name": name, "file": file, "line": line, "end_line": end_line, "kind": kind}


def _reference_callers(index, store, owner, name, rev) -> set[str]:
    """The fallback loop as it was before L-150. The oracle."""
    callee_has_caller: set[str] = set()
    _file_cache: dict[str, str] = {}
    for sym in index.symbols:
        if sym.get("kind") not in ("function", "method"):
            continue
        sym_file = sym.get("file", "")
        sym_name = sym.get("name", "")
        if not sym_name or not sym_file:
            continue
        sym_line = sym.get("line", 0)
        sym_end_line = sym.get("end_line", sym_line)
        if sym_file not in _file_cache:
            _file_cache[sym_file] = store.get_file_content(owner, name, sym_file) or ""
        own_content = _file_cache[sym_file]
        if own_content and sym_line:
            lines = own_content.splitlines()
            start_idx = max(0, sym_line - 1)
            end_idx = min(len(lines), sym_end_line)
            outside = "\n".join(lines[:start_idx] + lines[end_idx:])
            if outside and re.search(_call_graph.word_pattern(sym_name), outside):
                callee_has_caller.add(sym["id"])
                continue
        for importer_file in rev.get(sym_file, []):
            if importer_file not in _file_cache:
                _file_cache[importer_file] = store.get_file_content(owner, name, importer_file) or ""
            content = _file_cache[importer_file]
            if content and re.search(_call_graph.word_pattern(sym_name), content):
                callee_has_caller.add(sym["id"])
                break
    return callee_has_caller


def _fallback(symbols, files, rev=None):
    store = _Store(files)
    got = v2._text_fallback_callers(SimpleNamespace(symbols=symbols), store, "o", "n", rev or {})
    return got, store


def _both(symbols, files, rev=None):
    got, _ = _fallback(symbols, files, rev)
    want = _reference_callers(SimpleNamespace(symbols=symbols), _Store(files), "o", "n", rev or {})
    return got, want


# ---------------------------------------------------------------------------
# the entry point: the reported shape, through index_folder and the tool
# ---------------------------------------------------------------------------


def _reported_repo(tmp_path, n):
    src = tmp_path / f"src{n}"
    src.mkdir()
    store = tmp_path / f"store{n}"
    store.mkdir()
    (src / "a.py").write_text("X = 1\n", encoding="utf-8")
    body = "import a\n" + "".join(f"def fn_{i}():\n    pass\n\n\n" for i in range(n)) + "fn_0()\n"
    (src / "big.py").write_text(body, encoding="utf-8")
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert r["success"] is True
    return r["repo"], str(store), len(body)


def _run_counted(tmp_path, monkeypatch, n):
    repo, store, size = _reported_repo(tmp_path, n)
    real = IndexStore.get_file_content

    def counted(self, owner, name, path, *a, **k):
        text = real(self, owner, name, path, *a, **k)
        return _Counted(text) if isinstance(text, str) and path.endswith("big.py") else text

    monkeypatch.setattr(IndexStore, "get_file_content", counted)
    w = _watch(monkeypatch)
    result = v2.get_dead_code_v2(repo=repo, min_confidence=0.0, max_results=0, storage_path=store)
    assert "error" not in result, result
    splits, chars = _Counted.splits, w.chars

    def traced():
        with _Ops() as ops:
            v2.get_dead_code_v2(repo=repo, min_confidence=0.0, max_results=0, storage_path=store)
        return ops.count

    w.count_ops = lambda: _settled(traced)
    return result, splits, chars, size, w


def test_the_reported_shape_takes_the_text_fallback(tmp_path):
    """Guard on the fixture: with a call reference in it this file tests nothing."""
    repo, store, _ = _reported_repo(tmp_path, 5)
    owner, name = repo.split("/", 1)
    index = IndexStore(base_path=store).load_index(owner, name)
    assert not index.get_callers_by_name()


def test_splitting_the_file_does_not_grow_with_its_functions(tmp_path, monkeypatch):
    _, small, _, _, _ = _run_counted(tmp_path, monkeypatch, 60)
    _, large, _, _, _ = _run_counted(tmp_path, monkeypatch, 240)
    assert large == small, f"splits: {small} at 60 functions, {large} at 240"
    assert large <= 4, f"the file was split {large} times"


def test_searched_characters_grow_no_faster_than_the_file(tmp_path, monkeypatch):
    _, _, small, small_size, _ = _run_counted(tmp_path, monkeypatch, 60)
    _, _, large, large_size, w = _run_counted(tmp_path, monkeypatch, 240)
    # Four times the functions is a little over four times the bytes. The old
    # loop searched the whole file once per function: sixteen times the work.
    assert large <= 5 * max(small, small_size), (small, large, small_size, large_size)
    assert large <= 4 * large_size, f"{large} characters searched in a {large_size}-byte file"
    assert w.tokenised <= 4 * large_size, f"{w.tokenised} characters tokenised in a {large_size}-byte file"


def test_the_tools_instructions_grow_in_step_with_the_functions(tmp_path, monkeypatch):
    """Four times the functions: about four times the instructions, not sixteen."""
    small = _run_counted(tmp_path, monkeypatch, 60)[-1].count_ops()
    large = _run_counted(tmp_path, monkeypatch, 240)[-1].count_ops()
    assert small > 1000, small
    assert large <= 6 * small, f"{small} instructions at 60 functions, {large} at 240"


def test_the_entry_point_still_finds_the_module_level_call(tmp_path):
    repo, store, _ = _reported_repo(tmp_path, 12)
    result = v2.get_dead_code_v2(repo=repo, min_confidence=0.0, max_results=0, storage_path=store)
    rows = {r["name"]: r for r in result["dead_symbols"]}
    assert "no_callers" not in str(rows.get("fn_0", {}).get("signals", "")), rows.get("fn_0")
    assert "no_callers" in str(rows["fn_1"]["signals"]), rows["fn_1"]


# ---------------------------------------------------------------------------
# the fallback itself: work
# ---------------------------------------------------------------------------


def _many(n, name_of):
    lines, symbols = [], []
    for i in range(n):
        lines += [f"def {name_of(i)}:", "    pass", ""]
        symbols.append(_sym(f"s{i}", name_of(i), "big.x", 3 * i + 1, 3 * i + 2))
    return symbols, {"big.x": "\n".join(lines) + "\n"}


@pytest.mark.parametrize(
    "name_of",
    [
        lambda i: f"fn_{i}",
        lambda i: f"fn_{i}/2",
        lambda i: f"ns::fn_{i}",
        lambda i: f"$fn_{i}",
        lambda i: f"fn_{i}!",
    ],
    ids=["plain", "arity", "qualified", "sigil", "bang"],
)
def test_one_file_is_split_once_whatever_the_names_look_like(work, name_of):
    symbols, files = _many(200, name_of)
    _, store = _fallback(symbols, files)
    assert _Counted.splits <= 1, f"split {_Counted.splits} times for 200 symbols"
    assert store.reads == {"big.x": 1}
    size = len(files["big.x"])
    assert work.chars <= 4 * size, f"{work.chars} characters searched in a {size}-byte file"


def _ops_of(symbols, files, rev=None):
    def traced():
        with _Ops() as ops:
            v2._text_fallback_callers(SimpleNamespace(symbols=symbols), _Store(files), "o", "n", rev or {})
        return ops.count

    return _settled(traced)


def _overloads(n, name, call):
    lines, symbols = [], []
    for i in range(n):
        lines += [f"def {name}", f"  {call}", ""]
        symbols.append(_sym(f"s{i}", name, "ops.x", 3 * i + 1, 3 * i + 1))
    return symbols, {"ops.x": chr(10).join(lines)}


def _whole_file_spans(n):
    symbols, files = _overloads(n, "run", "run()")
    return [dict(s, line=1, end_line=10 * n) for s in symbols], files


def _with_importer(n):
    symbols, files = _many(n, lambda i: f"fn_{i}")
    files["user.x"] = "".join(f"fn_{i}()" + chr(10) for i in range(0, n, 2))
    return symbols, files, {"big.x": ["user.x"]}


SHAPES = {
    "plain": lambda n: _many(n, lambda i: f"fn_{i}"),
    "arity": lambda n: _many(n, lambda i: f"fn_{i}/2"),
    "qualified": lambda n: _many(n, lambda i: f"ns::fn_{i}"),
    "sigil": lambda n: _many(n, lambda i: f"$fn_{i}"),
    "bang": lambda n: _many(n, lambda i: f"fn_{i}!"),
    "spaced": lambda n: _many(n, lambda i: f"Run fn_{i}"),
    "overloads of a word": lambda n: _overloads(n, "run", "run()"),
    "overloads of an operator": lambda n: _overloads(n, "+", "a+b"),
    "overloads of a compound": lambda n: _overloads(n, "add/2", "x add/2 y"),
    "an importer": _with_importer,
    # Every match is inside every span, so a walk over the matches cannot stop early.
    "overloads whose span is the whole file": lambda n: _whole_file_spans(n),
}


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_instructions_grow_in_step_with_the_symbols(shape, work):
    """Four times the symbols: about four times the instructions, not sixteen.

    Sees a Python loop per symbol over the file's lines, or over the lines
    that name it (two hundred overloads each walking two hundred matches).
    """
    small = _ops_of(*SHAPES[shape](100))
    large = _ops_of(*SHAPES[shape](400))
    assert small > 1000, small
    assert large <= 6 * small, f"{shape}: {small} instructions at 100 symbols, {large} at 400"


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_tokenised_characters_stay_within_the_files(shape, work):
    symbols, files, *rest = SHAPES[shape](200)
    _fallback(symbols, files, *rest)
    size = sum(len(t) for t in files.values())
    # Each file's text once, and each distinct name once.
    names = sum(len(n) for n in {s['name'] for s in symbols})
    assert work.tokenised <= size + 2 * names, f"{shape}: {work.tokenised} tokenised, files hold {size}"


def test_an_importer_is_read_and_split_once(work):
    symbols, files = _many(150, lambda i: f"fn_{i}")
    files["user.x"] = "".join(f"fn_{i}()\n" for i in range(0, 150, 2))
    got, store = _fallback(symbols, files, {"big.x": ["user.x", "gone.x"]})
    assert got == {f"s{i}" for i in range(0, 150, 2)}
    assert store.reads["user.x"] == 1 and store.reads["gone.x"] == 1
    assert _Counted.splits <= 2
    assert work.chars <= 4 * (len(files["big.x"]) + len(files["user.x"]))


def test_a_name_declared_many_times_is_looked_up_once(work):
    """Overloads: two hundred functions named `+` in one file."""
    lines, symbols = [], []
    for i in range(200):
        lines += ["op + (a, b)", "  a+b", ""]
        symbols.append(_sym(f"s{i}", "+", "ops.x", 3 * i + 1, 3 * i + 1))
    files = {"ops.x": "\n".join(lines)}
    got, want = _both(symbols, files)
    assert got == want and len(got) == 200
    work.chars = 0
    _fallback(symbols, files)
    assert work.chars <= 4 * len(files["ops.x"])


# ---------------------------------------------------------------------------
# the fallback itself: the answer is the old loop's
# ---------------------------------------------------------------------------


CASES = {
    "called below its own body": (
        [_sym("a", "run", "f.x", 1, 2)],
        {"f.x": "def run():\n    pass\nrun()\n"},
    ),
    "named only on its own lines": (
        [_sym("a", "run", "f.x", 1, 2)],
        {"f.x": "def run():\n    run\nother()\n"},
    ),
    "a longer word is not the name": (
        [_sym("a", "run", "f.x", 1, 1)],
        {"f.x": "def run(): pass\nrunner()\nrerun()\nrun2()\n2run()\n"},
    ),
    "a leading digit glues to the name": (
        [_sym("a", "run", "f.x", 1, 1), _sym("b", "go", "f.x", 2, 2)],
        {"f.x": "def run(): pass\ndef go(): pass\n2run\n go \n"},
    ),
    "arity name, called": (
        [_sym("a", "add/2", "f.erl", 1, 1)],
        {"f.erl": "add(A, B) -> A + B.\n-export([add/2]).\n"},
    ),
    "arity name, a different arity elsewhere": (
        [_sym("a", "add/2", "f.erl", 1, 1)],
        {"f.erl": "add(A, B) -> A + B.\n-export([add/23, xadd/2]).\n"},
    ),
    "qualified name": (
        [_sym("a", "m::mul", "f.f90", 1, 1), _sym("b", "m::div", "f.f90", 2, 2)],
        {"f.f90": "function mul\nfunction div\ncall m::mul(1)\ncall m:: div(1)\n"},
    ),
    "a sigil glued to a word is not the name (L-154)": (
        [_sym("a", "$go", "f.php", 1, 1), _sym("b", "$no", "f.php", 2, 2)],
        {"f.php": "x\ny\nrun$go()\nrun $no()\n"},
    ),
    "a bang glued to a word is not the name (L-154)": (
        [_sym("a", "save!", "f.rb", 1, 1), _sym("b", "keep!", "f.rb", 2, 2)],
        {"f.rb": "x\ny\nsave!now\nkeep! now\n"},
    ),
    "an operator between words": (
        [_sym("a", "==", "f.x", 1, 1), _sym("b", "<=>", "f.x", 2, 2)],
        {"f.x": "x\ny\na==b\na <=> b\n"},
    ),
    "a name with no span is not searched in its own file": (
        [_sym("a", "run", "f.x", 0, 0)],
        {"f.x": "run()\nrun()\n", "u.x": "nothing\n"},
        {"f.x": ["u.x"]},
    ),
    "a name with no span is still searched in an importer": (
        [_sym("a", "run", "f.x", 0, 0)],
        {"f.x": "run()\n", "u.x": "run()\n"},
        {"f.x": ["u.x"]},
    ),
    "an end before the start removes nothing": (
        [_sym("a", "run", "f.x", 3, 1)],
        {"f.x": "x\ny\ndef run(): pass\n"},
    ),
    "a span past the end of the file": (
        [_sym("a", "run", "f.x", 2, 99), _sym("b", "go", "f.x", 50, 60)],
        {"f.x": "run\ndef run(): pass\ngo\n"},
    ),
    "a negative end counts from the end of the file": (
        [_sym("a", "run", "f.x", 1, -1), _sym("b", "go", "f.x", 1, -2), _sym("c", "up", "f.x", 2, -99)],
        {"f.x": "def run go\nx up\nrun\ngo\nz\n"},
    ),
    "a name holding a newline matches across two lines": (
        [_sym("a", "a\nb", "f.x", 1, 1), _sym("b", "c\nd", "f.x", 1, 1)],
        {"f.x": "x\na\r\nb\nc\nx\nd\n"},
    ),
    "a name holding a separator, in an importer": (
        [_sym("a", "a\x0cb", "f.x", 1, 1), _sym("b", "a\u2028b", "f.x", 1, 1),
         _sym("c", "a\rb", "f.x", 0, 0)],
        {"f.x": "x\n", "u.x": "a\x0cb a\nb\n"},
        {"f.x": ["u.x"]},
    ),
    "a name that is two words and a space": (
        [_sym("a", "Run Report", "f.al", 1, 1), _sym("b", "Run  Report", "f.al", 2, 2)],
        {"f.al": "x\ny\nRun Report;\nRun\nReport\n"},
    ),
    "crlf, form feed and a unicode separator": (
        [_sym("a", "run", "f.x", 1, 1), _sym("b", "go", "f.x", 2, 2)],
        {"f.x": "def run\r\ndef go\r\nx\x0crun go\r\n"},
    ),
    "unicode identifiers": (
        [_sym("a", "größe", "f.x", 1, 1), _sym("b", "名前", "f.x", 2, 2)],
        {"f.x": "def größe\ndef 名前\ngröße()\n名前s()\n"},
    ),
    "an empty file and a missing file": (
        [_sym("a", "run", "empty.x", 1, 1), _sym("b", "go", "gone.x", 1, 1)],
        {"empty.x": "", "user.x": "run go\n"},
        {"empty.x": ["user.x"], "gone.x": ["user.x"]},
    ),
    "classes and unnamed symbols are skipped": (
        [_sym("a", "Run", "f.x", 1, 1, kind="class"), _sym("b", "", "f.x", 2, 2), _sym("c", "go", "", 1, 1)],
        {"f.x": "Run\n\nRun go\n"},
    ),
    "found in the second importer": (
        [_sym("a", "run", "f.x", 1, 1)],
        {"f.x": "def run\n", "u1.x": "nothing\n", "u2.x": "run()\n"},
        {"f.x": ["u1.x", "u2.x"]},
    ),
    "the importer's own definition counts": (
        [_sym("a", "run", "f.x", 1, 1), _sym("b", "run", "g.x", 1, 1)],
        {"f.x": "def run\n", "g.x": "def run\n"},
        {"f.x": ["g.x"]},
    ),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_the_answer_is_the_old_loops(case):
    symbols, files, *rest = CASES[case]
    got, want = _both(symbols, files, *rest)
    assert got == want, case


EXPECTED = {
    "called below its own body": {"a"},
    "named only on its own lines": set(),
    "a longer word is not the name": set(),
    "a leading digit glues to the name": {"b"},
    "arity name, called": {"a"},
    "arity name, a different arity elsewhere": set(),
    "qualified name": {"a"},
    "a sigil glued to a word is not the name (L-154)": {"b"},
    "a bang glued to a word is not the name (L-154)": {"b"},
    "an operator between words": {"a", "b"},
    "a name with no span is not searched in its own file": set(),
    "a name with no span is still searched in an importer": {"a"},
    "an end before the start removes nothing": {"a"},
    "a span past the end of the file": {"a", "b"},
    "a negative end counts from the end of the file": {"b", "c"},
    "a name holding a newline matches across two lines": {"a"},
    "a name holding a separator, in an importer": {"a"},
    "a name that is two words and a space": {"a"},
    "crlf, form feed and a unicode separator": {"a", "b"},
    "unicode identifiers": {"a"},
    "an empty file and a missing file": {"a", "b"},
    "classes and unnamed symbols are skipped": set(),
    "found in the second importer": {"a"},
    "the importer's own definition counts": {"a"},
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_the_answer_is_pinned(case):
    """The oracle agreeing with the code is not enough if both are wrong."""
    symbols, files, *rest = CASES[case]
    got, _ = _fallback(symbols, files, *rest)
    assert got == EXPECTED[case], case


def test_every_case_has_a_pinned_answer():
    assert set(CASES) == set(EXPECTED)


def test_generated_files_get_the_old_loops_answer():
    """Six hundred seeded files of mixed name shapes, spans (negative ones too) and separators."""
    rng = random.Random(150)
    atoms = ["run", "go", "x", "run2", "add", "2", "ns", "f", "größe", "a_b", "$v", "k"]
    glue = ["", "/", "::", "!", "$", ".", " ", "==", "+", "-", "\n", "\x0c"]
    seps = ["\n", "\n", "\n", "\r\n", "\x0c", " "]
    hits = 0
    for _ in range(600):
        def name():
            return "".join(rng.choice(atoms) + rng.choice(glue) for _ in range(rng.randint(1, 3))).strip() or "run"

        names = [name() for _ in range(rng.randint(1, 6))]
        files = {}
        for path in ("f.x", "u.x"):
            n_lines = rng.randint(0, 14)
            text = ""
            for _ in range(n_lines):
                words = [rng.choice(names + atoms + glue) for _ in range(rng.randint(0, 5))]
                text += rng.choice(["", " "]).join(words) + rng.choice(seps)
            files[path] = text
        symbols = [
            _sym(f"s{i}", nm, "f.x", rng.randint(-4, 16), rng.randint(-8, 18))
            for i, nm in enumerate(names)
        ]
        rev = {"f.x": ["u.x"]} if rng.random() < 0.7 else {}
        got, want = _both(symbols, files, rev)
        assert got == want, (names, files, symbols, rev)
        hits += len(want)
    assert hits > 100, f"the generator found a caller only {hits} times"


def test_the_separator_class_is_what_splitlines_cuts_on():
    """`FileWords` refuses a name per line exactly when the name can span two."""
    for code in range(0x11000):
        if 0xD800 <= code < 0xE000:
            continue
        cut = len(("a" + chr(code) + "b").splitlines()) > 1
        assert _call_graph.FileWords.spans_lines(chr(code)) is cut, hex(code)


def test_a_name_that_spans_lines_is_refused_not_answered():
    words = _call_graph.FileWords("a" + chr(10) + "b" + chr(10))
    with pytest.raises(ValueError):
        words.lines_naming("a" + chr(10) + "b")
    assert words.names("a") and not words.names("c")


def test_lines_outside_a_span_are_the_slices_lines():
    """`names_outside` against the slice it stands for, every small file and bound."""
    for count in range(6):
        for hit in range(count + 1):
            lines = ["x"] * count
            if hit < count:
                lines[hit] = "run"
            words = _call_graph.FileWords(chr(10).join(lines))
            for start in range(-8, 9):
                for end in range(-8, 9):
                    want = "run" in lines[:start] + lines[end:]
                    assert words.names_outside("run", start, end) is want, (lines, start, end)
