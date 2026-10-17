"""The module-level sweep searches a file's residue once (LEDGER L-152, finding 1).

`get_dead_code_v2` on an index that holds call references sweeps what those
references cannot see: code at module level. For a name that is a plain
identifier the sweep reads a token set built once per file. For any other
name (`valid?`, `save!`, `do-thing`, `add/2`, `ns::f`) it ran a word-boundary
search over the file's whole module-level residue, once per symbol and caller
file. In a language where most function names are not plain identifiers
(Clojure, Ruby predicates) that is every symbol against the whole residue.

These tests count work and never time it (Standing lesson 10-09):

- the characters handed to a word-boundary search grow no faster than the
  residue;
- the bytecode instructions the two modules execute grow in step with the
  symbols;
- the answer is the old sweep's, plain names included (their path is not
  changed and must not move).

`_reference_sweep` is the sweep as it was on `main` at `f2ec2db8`, the oracle.

What the counters do not see: work inside one C call that is neither a
`_word_match`, a `_word_starts` nor the tokeniser. A source scan of the sweep
closes the nearest case (the old search with `re` inlined).

The remainder, stated by `test_names_made_of_common_words_cost_one_scan_each`:
N distinct names whose every word is on most residue lines cost N scans of the
residue. That is what the old sweep cost for every name, and never more.
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

NL = chr(10)


# ---------------------------------------------------------------------------
# counting
# ---------------------------------------------------------------------------


class _Work:
    def __init__(self) -> None:
        self.chars = 0
        self.calls = 0
        self.tokenised = 0
        self.longest = 0
        self.listings = 0

    def word_match(self, content: str, name: str) -> bool:
        self.chars += len(content)
        self.calls += 1
        self.longest = max(self.longest, len(content))
        return bool(re.search(_call_graph.word_pattern(name), content))

    def word_starts(self, content: str, name: str) -> list[int]:
        """The search that lists every match: it cannot stop at the first."""
        self.chars += len(content)
        self.calls += 1
        self.listings += 1
        self.longest = max(self.longest, len(content))
        return [m.start() for m in re.finditer(_call_graph.word_pattern(name), content)]


class _CountedWords:
    def __init__(self, work: _Work, real) -> None:
        self.work, self.real = work, real

    def findall(self, text):
        self.work.tokenised += len(text)
        return self.real.findall(text)


@pytest.fixture
def work(monkeypatch):
    w = _Work()
    monkeypatch.setattr(_call_graph, "_word_match", w.word_match)
    monkeypatch.setattr(v2, "_word_match", w.word_match)
    monkeypatch.setattr(_call_graph, "_word_starts", w.word_starts, raising=False)
    # `raising=False`: on a tree without the tokeniser the other counter still reads.
    real = getattr(_call_graph, "_WORD_RE", re.compile(r"\w+"))
    monkeypatch.setattr(_call_graph, "_WORD_RE", _CountedWords(w, real), raising=False)
    return w


class _Ops:
    """Bytecode instructions executed inside the two modules, counted."""

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

    From Python 3.12 the first traced run of a code object undercounts, so a
    single reading depends on which test ran before.
    """
    previous = measure()
    for _ in range(5):
        current = measure()
        if current == previous:
            return current
        previous = current
    raise AssertionError(f"the instruction count did not settle: last two readings {previous}, {current}")


class _Store:
    def __init__(self, files: dict[str, str]) -> None:
        self.files = files
        self.reads: dict[str, int] = {}

    def get_file_content(self, owner, name, path):
        self.reads[path] = self.reads.get(path, 0) + 1
        return self.files.get(path)


def _sym(sid, name, file, line, end_line, kind="function"):
    return {"id": sid, "name": name, "file": file, "line": line, "end_line": end_line, "kind": kind}


_IDENT_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")


def _reference_sweep(index, store, owner, name, rev, callee_has_caller) -> None:
    """The sweep as it was before L-152. The oracle."""
    symbols_by_file: dict[str, list[dict]] = {}
    for s in index.symbols:
        if s.get("file"):
            symbols_by_file.setdefault(s["file"], []).append(s)
    names_cache: dict[str, set[str]] = {}
    text_cache: dict[str, str] = {}

    def residue_text(file_path):
        if file_path in text_cache:
            return text_cache[file_path]
        content = store.get_file_content(owner, name, file_path) or ""
        if content:
            lines = content.splitlines()
            covered = bytearray(len(lines))
            for s in symbols_by_file.get(file_path, ()):
                line = s.get("line") or 0
                if line <= 0:
                    continue
                start = max(0, line - 1)
                end = min(len(lines), s.get("end_line") or line)
                if end > start:
                    covered[start:end] = b"\x01" * (end - start)
            content = "\n".join(ln for i, ln in enumerate(lines) if not covered[i])
        text_cache[file_path] = content
        return content

    def residue_names(file_path):
        if file_path not in names_cache:
            names_cache[file_path] = set(_IDENT_RE.findall(residue_text(file_path)))
        return names_cache[file_path]

    for sym in index.symbols:
        if sym.get("kind") not in ("function", "method"):
            continue
        sid = sym.get("id", "")
        if not sid or sid in callee_has_caller:
            continue
        sym_file = sym.get("file", "")
        sym_name = sym.get("name", "")
        if not sym_name or not sym_file:
            continue
        plain = _IDENT_RE.fullmatch(sym_name) is not None
        for caller_file in (sym_file, *rev.get(sym_file, ())):
            if plain:
                hit = sym_name in residue_names(caller_file)
            else:
                residue = residue_text(caller_file)
                hit = bool(residue) and bool(re.search(_call_graph.word_pattern(sym_name), residue))
            if hit:
                callee_has_caller.add(sid)
                break


def _sweep(symbols, files, rev=None, already=()):
    store = _Store(files)
    got = set(already)
    v2._sweep_module_level_callers(SimpleNamespace(symbols=symbols), store, "o", "n", rev or {}, got)
    return got, store


def _both(symbols, files, rev=None, already=()):
    got, _ = _sweep(symbols, files, rev, already)
    want = set(already)
    _reference_sweep(SimpleNamespace(symbols=symbols), _Store(files), "o", "n", rev or {}, want)
    return got, want


# ---------------------------------------------------------------------------
# the entry point: a Clojure file through index_folder and the tool
# ---------------------------------------------------------------------------


def _clojure_repo(tmp_path, n):
    src = tmp_path / f"src{n}"
    src.mkdir()
    store = tmp_path / f"store{n}"
    store.mkdir()
    # Two Python files give the index an import edge and one call reference,
    # which is what sends the tool down the path that sweeps.
    (src / "util.py").write_text("def b():\n    return 1\n", encoding="utf-8")
    (src / "py.py").write_text("from util import b\n\ndef a():\n    return b()\n", encoding="utf-8")
    body = (
        "(ns big)\n"
        + "".join(f"(defn do-thing-{i} [x]\n  x)\n\n" for i in range(n))
        + "".join(f"(println {i})\n" for i in range(n))
        + "(do-thing-0 1)\n"
    )
    (src / "big.clj").write_text(body, encoding="utf-8")
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert r["success"] is True
    return r["repo"], str(store), len(body)


def test_the_fixture_takes_the_sweep_with_names_that_are_not_identifiers(tmp_path):
    """Guard on the fixture: a plain name or an empty call index tests nothing here."""
    repo, store, _ = _clojure_repo(tmp_path, 5)
    owner, name = repo.split("/", 1)
    index = IndexStore(base_path=store).load_index(owner, name)
    assert index.get_callers_by_name()
    names = [s["name"] for s in index.symbols if s["file"] == "big.clj" and s["kind"] == "function"]
    assert names == [f"do-thing-{i}" for i in range(5)]
    assert not any(_IDENT_RE.fullmatch(n) for n in names)
    out = v2.get_dead_code_v2(repo=repo, min_confidence=0.0, max_results=0, storage_path=store)
    assert out.get("_meta", {}).get("mode") != "call_graph_only", out.get("_meta")


def _tool_work(tmp_path, monkeypatch, n):
    repo, store, size = _clojure_repo(tmp_path, n)
    w = _Work()
    monkeypatch.setattr(_call_graph, "_word_match", w.word_match)
    monkeypatch.setattr(v2, "_word_match", w.word_match)
    monkeypatch.setattr(_call_graph, "_word_starts", w.word_starts, raising=False)
    out = v2.get_dead_code_v2(repo=repo, min_confidence=0.0, max_results=0, storage_path=store)
    assert "error" not in out, out
    return w.chars, size, out


def test_searched_characters_grow_no_faster_than_the_file(tmp_path, monkeypatch):
    small, small_size, _ = _tool_work(tmp_path, monkeypatch, 60)
    large, large_size, _ = _tool_work(tmp_path, monkeypatch, 240)
    # Four times the functions. The old sweep searched the whole residue once
    # per function: sixteen times the characters.
    assert large <= 5 * max(small, small_size), (small, large, small_size, large_size)
    assert large <= 4 * large_size, f"{large} characters searched in a {large_size}-byte file"


def test_the_tool_still_finds_the_module_level_call(tmp_path):
    repo, store, _ = _clojure_repo(tmp_path, 12)
    out = v2.get_dead_code_v2(repo=repo, min_confidence=0.0, max_results=0, storage_path=store)
    rows = {r["name"]: r for r in out["dead_symbols"]}
    assert "no_callers" not in rows["do-thing-0"]["signals"], rows["do-thing-0"]
    assert "no_callers" in rows["do-thing-1"]["signals"], rows["do-thing-1"]


# ---------------------------------------------------------------------------
# the sweep itself: work
# ---------------------------------------------------------------------------


def _many(n, name_of, residue_of=lambda i: f"(println {i})"):
    """n two-line functions, then n module-level lines, then one call."""
    lines, symbols = [], []
    for i in range(n):
        lines += [f"def {name_of(i)}", "  body", ""]
        symbols.append(_sym(f"s{i}", name_of(i), "big.x", 3 * i + 1, 3 * i + 2))
    lines += [residue_of(i) for i in range(n)]
    lines.append(f"call {name_of(0)} now")
    return symbols, {"big.x": NL.join(lines) + NL}


def _with_importers(n):
    symbols, files = _many(n, lambda i: f"do-thing-{i}")
    files["user.x"] = "".join(f"use do-thing-{i} here" + NL for i in range(0, n, 2))
    files["other.x"] = "".join(f"nothing {i}" + NL for i in range(n))
    return symbols, files, {"big.x": ["other.x", "user.x"]}


SHAPES = {
    "hyphenated": lambda n: _many(n, lambda i: f"do-thing-{i}"),
    "predicate": lambda n: _many(n, lambda i: f"check_{i}?"),
    "bang": lambda n: _many(n, lambda i: f"save_{i}!"),
    "arity": lambda n: _many(n, lambda i: f"fn_{i}/2"),
    "qualified": lambda n: _many(n, lambda i: f"ns::fn_{i}"),
    "spaced": lambda n: _many(n, lambda i: f"Run fn_{i}"),
    "one common word": lambda n: _many(n, lambda i: f"thing-{i}", lambda i: "thing thing thing"),
    "overloads of an operator": lambda n: _many(n, lambda i: "+", lambda i: "a+b"),
    "overloads of a compound": lambda n: _many(n, lambda i: "add/2", lambda i: f"x{i}"),
    "plain": lambda n: _many(n, lambda i: f"fn_{i}"),
    "importers": _with_importers,
    "dotted": lambda n: _many(n, lambda i: f"ns.fn_{i}"),
    "backslashed": lambda n: _many(n, lambda i: f"App\\fn_{i}"),
    "unicode": lambda n: _many(n, lambda i: f"größe-{i}"),
    "plain and compound in one file": lambda n: _many(n, lambda i: f"fn_{i}" if i % 2 else f"do-thing-{i}"),
}


def _common_words(n):
    """n distinct two-word names over a small vocabulary; every residue line holds every word."""
    vocab = int(n ** 0.5) + 2
    pairs = [(a, b) for a in range(vocab) for b in range(vocab) if a != b][:n]
    assert len(pairs) == n
    lines, symbols = [], []
    for i, (a, b) in enumerate(pairs):
        lines += [f"def w{a}-w{b}", "  body"]
        symbols.append(_sym(f"s{i}", f"w{a}-w{b}", "big.x", 2 * i + 1, 2 * i + 2))
    every = " ".join(f"w{k}" for k in range(vocab))
    lines += [every] * max(1, n // vocab)
    return symbols, {"big.x": NL.join(lines) + NL}


def test_names_made_of_common_words_cost_one_scan_each(work):
    """The stated remainder: one scan of the residue per distinct name, never more.

    Every word of every name is on every residue line, so no word narrows the
    search. Asking each line costs more than one search of the text does.
    """
    symbols, files = _common_words(300)
    got, want = _both(symbols, files)
    assert got == want == set()
    work.chars = work.calls = 0
    _sweep(symbols, files)
    size = _residue_size(symbols, files)
    assert work.calls <= len(symbols), f"{work.calls} searches for {len(symbols)} names"
    assert work.chars <= len(symbols) * size, f"{work.chars} characters; one scan each is {len(symbols) * size}"
    assert work.listings == 0, "the sweep asks whether a caller exists; it must not list every match"


def _called_everywhere(n):
    """n two-word names, each called on every one of forty long residue lines."""
    symbols, _ = _common_words(n)
    names = [s["name"] for s in symbols]
    lines = []
    for nm in names:
        lines += [f"def {nm}", "  body"]
    lines += [" ".join(names)] * 40
    return symbols, {"big.x": NL.join(lines) + NL}


def test_a_name_called_on_every_line_is_found_at_the_first(work):
    """Existence stops at the first match (review, round two).

    Listing every line that names a name which is on every line cost up to
    sixty-four times the old sweep, which stopped at its first match.
    """
    symbols, files = _called_everywhere(200)
    got, want = _both(symbols, files)
    assert got == want == {s['id'] for s in symbols}
    work.chars = work.calls = work.listings = 0
    _sweep(symbols, files)
    assert work.listings == 0, f"{work.listings} names had every match listed"
    assert work.calls <= len(symbols), f"{work.calls} searches for {len(symbols)} names"


# ---------------------------------------------------------------------------
# the index: which lines are asked, and when the text is searched instead
# ---------------------------------------------------------------------------


def _index_of(lines):
    return _call_graph.FileWords(NL.join(lines)), sum(len(ln) for ln in lines) + len(lines)


def test_short_lines_that_hold_the_word_are_asked_and_the_long_text_is_not_searched(work):
    """Twenty short lines hold the words; four thousand characters elsewhere do not."""
    lines = ["w1 w2"] * 20 + ["z" * 200] * 20
    words, chars = _index_of(lines)
    assert words.lines_naming("w1-w2") == [] and not words.names("w2-w1")
    assert work.longest <= 5, f"a {work.longest}-character search in a {chars}-character text"


def test_a_word_on_every_line_is_not_asked_line_by_line(work):
    lines = ["w1 w2 w3"] * 400
    words, chars = _index_of(lines)
    assert words.lines_naming("w1-w2") == [] and not words.names("w2-w3")
    assert work.calls == 2, f"{work.calls} searches for two names"


@pytest.mark.parametrize("filler, searched_whole", [(40, True), (200, False)])
def test_the_route_is_chosen_by_cost_in_characters(work, filler, searched_whole):
    """Ten 100-character lines hold the word: asking them costs their 1,000 characters
    and `_ASK_COST` each. With 40 characters of other text that is more than
    the text holds, so the text is searched; with 200 it is less, so the lines are.
    """
    lines = ["w1 " + "y" * 97] * 10 + ["z" * filler]
    words, chars = _index_of(lines)
    asking = 10 * 100 + 10 * _call_graph.FileWords._ASK_COST
    assert (asking > chars) is searched_whole, (asking, chars)
    assert words.lines_naming("w1-w1") == []
    assert (work.longest > 100) is searched_whole, (work.longest, chars)
    # The substring test spares a line that cannot match: nothing is searched
    # at all when the lines are asked and none holds the name.
    assert work.calls == (1 if searched_whole else 0), work.calls


def test_existence_stops_at_the_first_line_that_names_it(work):
    lines = ["call do-it now " + "y" * 60] * 200 + ["z" * 40000]
    words, _ = _index_of(lines)
    assert words.names("do-it") is True
    assert work.calls == 1 and work.listings == 0, (work.calls, work.listings)
    work.calls = 0
    assert words.names("do-it") is True and work.calls == 0, "the answer is kept"
    assert words.lines_naming("do-it") == list(range(200))
    assert words.names("do-it") is True


def test_a_listing_answers_the_existence_question_after_it(work):
    words, _ = _index_of(["a do-it b", "c", "do-it"])
    assert words.lines_naming("do-it") == [0, 2]
    work.calls = 0
    assert words.names("do-it") is True and work.calls == 0
    assert words.lines_naming("no-such") == [] and words.names("no-such") is False


def test_one_line_named_twice_is_listed_once():
    words, _ = _index_of(["w1-w2 w1-w2 w1 w2"] * 50)
    assert words.lines_naming("w1-w2") == list(range(50))
    assert words.lines_naming("+") == []
    plus, _ = _index_of(["a+b c+d"] * 3)
    assert plus.lines_naming("+") == [0, 1, 2]


def test_the_sweep_holds_one_whole_text_search_and_it_is_the_spanning_names():
    """The old search with `re` inlined passes every counter; this reads the source."""
    import inspect

    source = inspect.getsource(v2._sweep_module_level_callers)
    assert source.count("_word_match(") == 1, "a second whole-residue search is back"
    arm = source.index("FileWords.spans_lines(sym_name)")
    assert arm < source.index("_word_match(") < source.index("words = _residue_words(caller_file)")
    for banned in ("re.search", "re.finditer", "re.compile", "_word_starts", ".count(", ".find(", ".index("):
        assert banned not in source, f"`{banned}` in the sweep: a per-symbol pass over the residue text"


def _residue_size(symbols, files):
    """Characters outside every symbol's span, over all files."""
    total = 0
    for path, text in files.items():
        lines = text.splitlines()
        covered = set()
        for s in symbols:
            if s["file"] == path:
                covered.update(range(s["line"] - 1, s["end_line"]))
        total += sum(len(ln) + 1 for i, ln in enumerate(lines) if i not in covered)
    return total


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_searched_characters_stay_within_the_residue(shape, work):
    symbols, files, *rest = SHAPES[shape](200)
    _sweep(symbols, files, *rest)
    size = _residue_size(symbols, files)
    assert work.chars <= 4 * size, f"{shape}: {work.chars} characters searched, the residue holds {size}"


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_each_file_is_read_once(shape, work):
    symbols, files, *rest = SHAPES[shape](50)
    _, store = _sweep(symbols, files, *rest)
    assert set(store.reads.values()) == {1}, store.reads


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_instructions_grow_in_step_with_the_symbols(shape):
    """Four times the symbols: about four times the instructions, not sixteen."""

    def ops_at(n):
        symbols, files, *rest = SHAPES[shape](n)

        def traced():
            with _Ops() as ops:
                v2._sweep_module_level_callers(
                    SimpleNamespace(symbols=symbols), _Store(files), "o", "n", (rest[0] if rest else {}), set()
                )
            return ops.count

        return _settled(traced)

    small, large = ops_at(100), ops_at(400)
    assert small > 1000, small
    assert large <= 6 * small, f"{shape}: {small} instructions at 100 symbols, {large} at 400"


# ---------------------------------------------------------------------------
# the sweep itself: the answer is the old sweep's
# ---------------------------------------------------------------------------


CASES = {
    "a hyphenated name called at module level": (
        [_sym("a", "do-it", "f.clj", 1, 2), _sym("b", "do-not", "f.clj", 3, 4)],
        {"f.clj": "(defn do-it []\n 1)\n(defn do-not []\n 2)\n(do-it)\n"},
    ),
    "a name inside another symbol's body is not residue": (
        [_sym("a", "do-it", "f.clj", 1, 1), _sym("b", "run-all", "f.clj", 2, 3)],
        {"f.clj": "(defn do-it [] 1)\n(defn run-all []\n (do-it))\n(println 1)\n"},
    ),
    "a longer hyphenated name is not the name": (
        [_sym("a", "do-it", "f.clj", 1, 1)],
        {"f.clj": "(defn do-it [] 1)\n(xdo-it)\n(do-it2)\n"},
    ),
    "a hyphen continues the name in the old search too": (
        [_sym("a", "do-it", "f.clj", 1, 1), _sym("b", "go-on", "f.clj", 2, 2)],
        {"f.clj": "(defn do-it [] 1)\n(defn go-on [] 1)\n(do-it-now)\n(pre-go-on)\n"},
    ),
    "a predicate glued to a word is not the name (L-154)": (
        [_sym("a", "ok?", "f.rb", 1, 1), _sym("b", "on?", "f.rb", 2, 2)],
        {"f.rb": "def ok?; end\ndef on?; end\nok?x\non?\n"},
    ),
    "an arity name": (
        [_sym("a", "add/2", "f.erl", 1, 1), _sym("b", "sub/2", "f.erl", 2, 2)],
        {"f.erl": "add(A, B) -> A.\nsub(A, B) -> A.\n-export([add/2, sub/23]).\n"},
    ),
    "found in an importer's residue": (
        [_sym("a", "do-it", "f.clj", 1, 1), _sym("b", "main", "u.clj", 1, 2)],
        {"f.clj": "(defn do-it [] 1)\n", "u.clj": "(defn main []\n (x))\n(do-it)\n"},
        {"f.clj": ["u.clj"]},
    ),
    "inside an importer's symbol is not residue": (
        [_sym("a", "do-it", "f.clj", 1, 1), _sym("b", "main", "u.clj", 1, 2)],
        {"f.clj": "(defn do-it [] 1)\n", "u.clj": "(defn main []\n (do-it))\n(println)\n"},
        {"f.clj": ["u.clj"]},
    ),
    "found in the second importer": (
        [_sym("a", "do-it", "f.clj", 1, 1)],
        {"f.clj": "(defn do-it [] 1)\n", "u1.clj": "nothing\n", "u2.clj": "(do-it)\n"},
        {"f.clj": ["u1.clj", "gone.clj", "u2.clj"]},
    ),
    "an operator between words": (
        [_sym("a", "==", "f.x", 1, 1), _sym("b", "<=>", "f.x", 2, 2)],
        {"f.x": "def ==\ndef <=>\na==b\na <=> b\n"},
    ),
    "a symbol with no span stays in the residue": (
        [_sym("a", "do-it", "f.clj", 0, 0), _sym("b", "go-on", "f.clj", 2, 2)],
        {"f.clj": "(defn do-it [] 1)\n(defn go-on [] 1)\n"},
    ),
    "an end before the start covers nothing": (
        [_sym("a", "do-it", "f.clj", 2, 1)],
        {"f.clj": "x\n(defn do-it [] 1)\n"},
    ),
    "a span past the end of the file": (
        [_sym("a", "do-it", "f.clj", 2, 99), _sym("b", "go-on", "f.clj", 50, 60)],
        {"f.clj": "(do-it)\n(defn do-it [] 1)\n(go-on)\n"},
    ),
    "a symbol that already has a caller is not searched": (
        [_sym("a", "do-it", "f.clj", 1, 1), _sym("b", "go-on", "f.clj", 2, 2)],
        {"f.clj": "(defn do-it [] 1)\n(defn go-on [] 1)\n"},
        {},
        {"a"},
    ),
    "classes, unnamed and fileless symbols are skipped": (
        [_sym("a", "Do-It", "f.clj", 1, 1, kind="class"), _sym("b", "", "f.clj", 2, 2),
         _sym("c", "go-on", "", 1, 1), _sym("", "do-it", "f.clj", 3, 3)],
        {"f.clj": "x\ny\nz\nDo-It go-on do-it\n"},
    ),
    "an empty file and a missing file": (
        [_sym("a", "do-it", "empty.clj", 1, 1), _sym("b", "go-on", "gone.clj", 1, 1)],
        {"empty.clj": "", "user.clj": "do-it go-on\n"},
        {"empty.clj": ["user.clj"], "gone.clj": ["user.clj"]},
    ),
    "a name holding a newline matches across two residue lines": (
        [_sym("a", "a\nb", "f.x", 1, 1), _sym("b", "c\nd", "f.x", 1, 1), _sym("c", "e\nf", "f.x", 1, 1)],
        {"f.x": "x\na\nb\nc\nq\nd\ne\n"},
    ),
    "a newline name spans a removed symbol": (
        [_sym("a", "e\nf", "f.x", 2, 2), _sym("b", "mid", "f.x", 2, 2)],
        {"f.x": "e\nmid\nf\n"},
    ),
    "crlf, form feed and a unicode separator": (
        [_sym("a", "do-it", "f.x", 1, 1), _sym("b", "go-on", "f.x", 2, 2)],
        {"f.x": "def do-it\r\ndef go-on\r\nx\x0cdo-it go-on\r\n"},
    ),
    "a plain name keeps the token rule": (
        [_sym("a", "run", "f.x", 1, 1), _sym("b", "go", "f.x", 2, 2), _sym("c", "$v", "f.x", 3, 3)],
        {"f.x": "def run\ndef go\ndef $v\n2run\ngo-on\nx = $v\n"},
    ),
    "case is kept": (
        [_sym("a", "Do-It", "f.clj", 1, 1), _sym("b", "go-on", "f.clj", 2, 2)],
        {"f.clj": "(defn Do-It [] 1)\n(defn go-on [] 1)\n(do-it)\n(Go-On)\n(GO-ON)\n"},
    ),
    "a name is not trimmed": (
        [_sym("a", "do-it ", "f.clj", 1, 1), _sym("b", " go-on", "f.clj", 2, 2), _sym("c", "be-it ", "f.clj", 3, 3)],
        {"f.clj": "x\ny\nz\n(do-it)\n(go-on)\nbe-it now\n"},
    ),
    "a name every word of which is on every line": (
        [_sym("a", "w1-w2", "f.x", 1, 1), _sym("b", "w2-w1", "f.x", 2, 2)],
        {"f.x": "d\nd\n" + "w1 w2\n" * 40 + "x w2-w1 y\n" + "w2 w1\n" * 40},
    ),
    "unicode in a compound name": (
        [_sym("a", "größe-x", "f.x", 1, 1), _sym("b", "名前-y", "f.x", 2, 2)],
        {"f.x": "def größe-x\ndef 名前-y\ngröße-x\n名前-ys\n"},
    ),
}

EXPECTED = {
    "a hyphenated name called at module level": {"a"},
    "a name inside another symbol's body is not residue": set(),
    "a longer hyphenated name is not the name": set(),
    "a hyphen continues the name in the old search too": {"a", "b"},
    "a predicate glued to a word is not the name (L-154)": {"b"},
    "an arity name": {"a"},
    "found in an importer's residue": {"a"},
    "inside an importer's symbol is not residue": set(),
    "found in the second importer": {"a"},
    "an operator between words": {"a", "b"},
    "a symbol with no span stays in the residue": {"a"},
    "an end before the start covers nothing": {"a"},
    "a span past the end of the file": {"a"},
    "a symbol that already has a caller is not searched": {"a"},
    "classes, unnamed and fileless symbols are skipped": set(),
    "an empty file and a missing file": {"a", "b"},
    "a name holding a newline matches across two residue lines": {"a"},
    "a newline name spans a removed symbol": {"a"},
    "crlf, form feed and a unicode separator": {"a", "b"},
    "a plain name keeps the token rule": {"a", "b", "c"},
    "case is kept": set(),
    "a name is not trimmed": {"c"},
    "a name every word of which is on every line": {"b"},
    "unicode in a compound name": {"a"},
}


def _case(case):
    symbols, files, *rest = CASES[case]
    rev = rest[0] if rest else {}
    already = rest[1] if len(rest) > 1 else ()
    return symbols, files, rev, already


@pytest.mark.parametrize("case", sorted(CASES))
def test_the_answer_is_the_old_sweeps(case):
    got, want = _both(*_case(case))
    assert got == want, case


@pytest.mark.parametrize("case", sorted(CASES))
def test_the_answer_is_pinned(case):
    """The oracle agreeing with the code is not enough if both are wrong."""
    got, _ = _sweep(*_case(case))
    assert got == EXPECTED[case], case


def test_every_case_has_a_pinned_answer():
    assert set(CASES) == set(EXPECTED)


def test_generated_files_get_the_old_sweeps_answer():
    """Six hundred seeded repositories of mixed name shapes, spans and separators."""
    rng = random.Random(152)
    atoms = ["run", "go", "x", "run2", "add", "2", "ns", "f", "größe", "a_b", "$v", "k"]
    glue = ["", "/", "::", "!", "?", "$", ".", " ", "==", "+", "-", "\n", "\x0c"]
    seps = ["\n", "\n", "\n", "\r\n", "\x0c", " "]
    hits = compound_hits = 0
    for _ in range(600):
        def name():
            return "".join(rng.choice(atoms) + rng.choice(glue) for _ in range(rng.randint(1, 3))).strip() or "run"

        names = [name() for _ in range(rng.randint(1, 6))]
        files = {}
        for path in ("f.x", "u.x"):
            text = ""
            for _ in range(rng.randint(0, 14)):
                words = [rng.choice(names + atoms + glue) for _ in range(rng.randint(0, 5))]
                text += rng.choice(["", " "]).join(words) + rng.choice(seps)
            files[path] = text
        symbols = [
            _sym(f"s{i}", nm, rng.choice(["f.x", "f.x", "u.x"]), rng.randint(-2, 16), rng.randint(-4, 18))
            for i, nm in enumerate(names)
        ]
        rev = {"f.x": ["u.x"]} if rng.random() < 0.7 else {}
        already = {"s0"} if rng.random() < 0.2 else set()
        got, want = _both(symbols, files, rev, already)
        assert got == want, (names, files, symbols, rev, already)
        found = want - already
        hits += len(found)
        compound_hits += sum(1 for s in symbols if s["id"] in found and not _IDENT_RE.fullmatch(s["name"]))
    assert hits > 100, f"the generator found a caller only {hits} times"
    assert compound_hits > 40, f"only {compound_hits} of them through a name that is not an identifier"
