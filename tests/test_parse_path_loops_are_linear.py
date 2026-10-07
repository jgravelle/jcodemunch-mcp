"""Three loops on the parse path are linear in the file (LEDGER L-117).

Each one redid whole-file work per item:

- `_find_enclosing_symbol` rebuilt the list of symbol starts for every call
  site and then walked back over every symbol that did not hold it;
- the Razor brace scan started again for every `@code {` and ran to the end of
  the file when the block never closed;
- the dbt directive loop copied the file up to each directive, counted its
  newlines, searched it for a comment and split it into lines.

The measurements are in the ledger row.

Two instruments, because one cannot see everything. Loop iterations are counted
through `parse_budget.checkpoint()` (L-116's rule puts one at the top of every
loop), and that is blind to work done inside one C call: a slice, a `count`, a
regex search. So each fix is also raced against the code it replaced, which is
kept below as the oracle: the fix on a file twice as big must finish
before the old code finishes the small one. Both run on the same machine in the
same test, so no figure is typed here.

The oracles are also the equality check: the fix returns what the old code
returned on every generated input. One field is excepted: the old directive
loop's docstring was the defect of L-126, so that field is held to the rule.
"""

import ast
import bisect
import dataclasses
import pathlib
import random
import re
import sys
import time

import pytest

import jcodemunch_mcp.config as config_mod
from jcodemunch_mcp.parser import extractor, parse_budget, sql_preprocessor
from jcodemunch_mcp.parser.extractor import parse_file
from jcodemunch_mcp.parser.sql_preprocessor import DbtDirective
from jcodemunch_mcp.parser.symbols import Symbol


@pytest.fixture(autouse=True)
def _every_language_enabled(monkeypatch):
    # a developer config may disable a language; "0 symbols" would then pass a ratio
    monkeypatch.setattr(config_mod, "is_language_enabled", lambda *a, **k: True)


def _checkpoints(monkeypatch, content: str, filename: str, language: str) -> tuple[int, list]:
    count = 0
    real = parse_budget.checkpoint

    def counting():
        nonlocal count
        count += 1
        real()

    monkeypatch.setattr(parse_budget, "checkpoint", counting)
    try:
        symbols = parse_file(content, filename, language)
    finally:
        monkeypatch.setattr(parse_budget, "checkpoint", real)
    return count, symbols


def _seconds(fn) -> float:
    started = time.perf_counter()
    fn()
    return time.perf_counter() - started


def _assert_the_fix_outruns_the_old_code(old_small, new_big, what: str) -> None:
    """The fix on the file twice as big against the old code on the small one.

    The old code on the doubled file takes about four times its own small-file
    time, so a fix that is the old code again loses by about x4. The sizes are
    chosen so the real fix wins by a wide margin with coverage's C tracer on:
    the tracer slows the fix (under `src/`) and not the oracle (in this file),
    and that is what the 3.10 and 3.11 jobs run.
    """
    old = _seconds(old_small)
    new = min(_seconds(new_big) for _ in range(3))  # one stalled run does not decide it
    assert new < old, (
        f"{what}: the fix took {new:.4f} s on the file twice as big and the code it "
        f"replaced took {old:.4f} s on the small one; the old code on the doubled file reads about x4"
    )


# ---------------------------------------------------------------------------
# 1. Call sites to their enclosing symbol
# ---------------------------------------------------------------------------


def _old_find_enclosing_symbol(sorted_syms, byte_offset):
    if not sorted_syms:
        return None
    starts = [s[0] for s in sorted_syms]
    idx = bisect.bisect_right(starts, byte_offset) - 1
    while idx >= 0:
        start, end, _line, sym = sorted_syms[idx]
        if start <= byte_offset <= end:
            return sym
        idx -= 1
    return None


def _old_attribute_calls_to_symbols(symbols, calls):
    if not calls:
        return
    callable_syms = [
        (s.byte_offset, s.byte_offset + s.byte_length, s.line, s)
        for s in symbols
        if s.kind in ("function", "method") and s.byte_offset >= 0
    ]
    callable_syms.sort(key=lambda x: x[0])
    if not callable_syms:
        return
    for call_offset, called_name in calls:
        enclosing = _old_find_enclosing_symbol(callable_syms, call_offset)
        if enclosing and enclosing.name != called_name:
            if called_name not in enclosing.call_references:
                enclosing.call_references.append(called_name)


def _sym(i: int, kind: str, start: int, length: int) -> Symbol:
    return Symbol(
        id=f"f.py::s{i}#{kind}", file="f.py", name=f"s{i}", qualified_name=f"s{i}", kind=kind,
        language="python", signature="", line=i, byte_offset=start, byte_length=length,
    )


def _random_symbols(rng: random.Random) -> list[tuple[int, str, int, int]]:
    specs = []
    for i in range(rng.randint(0, 14)):
        kind = rng.choice(["function", "function", "method", "class", "constant"])
        # nested, overlapping, equal starts, empty spans and an unknown offset all occur
        specs.append((i, kind, rng.randint(-1, 40), rng.randint(0, 30)))
    return specs


def test_every_call_goes_to_the_symbol_the_old_scan_chose():
    rng = random.Random(117)
    for _ in range(3000):
        specs = _random_symbols(rng)
        calls = [(rng.randint(-2, 75), f"s{rng.randint(0, 16)}") for _ in range(rng.randint(0, 25))]
        expected = [_sym(*spec) for spec in specs]
        actual = [_sym(*spec) for spec in specs]
        _old_attribute_calls_to_symbols(expected, calls)
        extractor._attribute_calls_to_symbols(actual, calls)
        assert [s.call_references for s in actual] == [s.call_references for s in expected], (specs, calls)


def test_a_call_inside_two_symbols_goes_to_the_inner_one():
    outer, inner, after = _sym(0, "function", 0, 100), _sym(1, "function", 10, 20), _sym(2, "function", 200, 10)
    extractor._attribute_calls_to_symbols(
        [outer, inner, after], [(15, "a"), (50, "b"), (150, "c"), (205, "d"), (5, "e")]
    )
    assert inner.call_references == ["a"]
    assert outer.call_references == ["b", "e"]
    assert after.call_references == ["d"]


def _flat_symbols_then_calls(n: int) -> tuple[list[Symbol], list[tuple[int, str]]]:
    symbols = [_sym(i, "function", i * 10, 8) for i in range(n)]
    calls = [(n * 10 + i, f"g{i}") for i in range(2 * n)]  # after every function, inside none
    return symbols, calls


def _one_symbol_many_calls(n: int) -> tuple[list[Symbol], list[tuple[int, str]]]:
    return [_sym(0, "function", 0, 10 * n)], [(i, f"g{i}") for i in range(4 * n)]


@pytest.mark.parametrize("build, n", [(_flat_symbols_then_calls, 1000), (_one_symbol_many_calls, 4500)])
def test_attributing_calls_outruns_the_old_scan(build, n):
    _assert_the_fix_outruns_the_old_code(
        lambda: _old_attribute_calls_to_symbols(*build(n)),
        lambda: extractor._attribute_calls_to_symbols(*build(2 * n)),
        build.__name__,
    )


def _python_file(n: int) -> str:
    functions = "".join(f"def f{i}(a):\n    return a\n\n" for i in range(n))
    return functions + "".join(f"f{i % n}({i})\n" for i in range(3 * n))


def test_twice_the_python_file_is_about_twice_the_loop_iterations(monkeypatch):
    small_n, big_n = 150, 300
    small, small_syms = _checkpoints(monkeypatch, _python_file(small_n), "calls.py", "python")
    big, big_syms = _checkpoints(monkeypatch, _python_file(big_n), "calls.py", "python")
    assert len([s for s in small_syms if s.kind == "function"]) == small_n
    assert len([s for s in big_syms if s.kind == "function"]) == big_n
    assert big / small < 2.6, f"{small} loop iterations at {small_n} functions, {big} at {big_n} (x{big / small:.2f})"


# ---------------------------------------------------------------------------
# 2. The Razor `@code {` block
# ---------------------------------------------------------------------------


def _old_extract_razor_brace_block(content, brace_pos):
    if brace_pos < 0 or brace_pos >= len(content) or content[brace_pos] != "{":
        return None
    depth = 0
    i = brace_pos
    in_string = False
    string_quote = ""
    verbatim_string = False
    in_line_comment = False
    in_block_comment = False
    while i < len(content):
        ch = content[i]
        nxt = content[i + 1] if i + 1 < len(content) else ""
        if in_line_comment:
            if ch == "\n":
                in_line_comment = False
            i += 1
            continue
        if in_block_comment:
            if ch == "*" and nxt == "/":
                in_block_comment = False
                i += 2
                continue
            i += 1
            continue
        if in_string:
            if verbatim_string:
                if ch == '"' and nxt == '"':
                    i += 2
                    continue
                if ch == '"':
                    in_string = False
                    verbatim_string = False
            else:
                if ch == "\\":
                    i += 2
                    continue
                if ch == string_quote:
                    in_string = False
            i += 1
            continue
        if ch == "/" and nxt == "/":
            in_line_comment = True
            i += 2
            continue
        if ch == "/" and nxt == "*":
            in_block_comment = True
            i += 2
            continue
        if ch == "@" and nxt == '"':
            in_string = True
            string_quote = '"'
            verbatim_string = True
            i += 2
            continue
        if ch in ("'", '"'):
            in_string = True
            string_quote = ch
            verbatim_string = False
            i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return brace_pos + 1, i
        i += 1
    return None


_BACKSLASH = chr(92)
_RAZOR_PIECES = [
    "{", "{", "}", "}", '"', "'", _BACKSLASH, _BACKSLASH + '"', '@"', '""', "//", "/*", "*/", "/", "*",
    "@", "\n", " ", "x", "@code {", "@code {",
]


def _new_blocks(content: str, positions) -> list:
    blocks = extractor._RazorBraceBlocks(content)
    return [blocks.block(p) for p in positions]


def test_every_brace_closes_where_the_old_scan_closed_it():
    rng = random.Random(117)
    for _ in range(4000):
        content = "".join(rng.choice(_RAZOR_PIECES) for _ in range(rng.randint(0, 40)))
        positions = list(range(-1, len(content) + 2))
        rng.shuffle(positions)  # a later block may be asked for before an earlier one
        expected = [_old_extract_razor_brace_block(content, p) for p in positions]
        assert _new_blocks(content, positions) == expected, repr(content)


def test_a_brace_in_a_string_or_a_comment_does_not_close_the_block():
    content = '@code { var a = "}"; // }\n /* } */ var b = @"} "" }"; var c = \'}\'; { } }'
    open_at = content.index("{")
    assert _new_blocks(content, [open_at]) == [(open_at + 1, len(content) - 1)]


def _unclosed_blocks(n: int) -> str:
    return "@code {\n" * n


def _unclosed_blocks_in_one_string(n: int) -> str:
    # every `@code {` opens its own string at the escaped quote and none of them closes
    return '"' + ("@code { " + _BACKSLASH + '" ') * n + "x" * (8 * n)


def _unclosed_blocks_each_in_a_block_comment(n: int) -> str:
    # no scan meets another: each block is in code at its own brace, then in its own comment
    return "@code {/*\n" * n


def _unclosed_blocks_each_in_a_line_comment(n: int) -> str:
    return "@code {// " * n


def _closed_blocks(n: int) -> str:
    return "@code { int a; }\n" * n


def _open_positions(content: str) -> list[int]:
    return [m.end() - 1 for m in re.finditer(r"@code \{", content)]


@pytest.mark.parametrize(
    "build, n",
    [
        (_unclosed_blocks, 500),
        (_unclosed_blocks_in_one_string, 250),
        (_unclosed_blocks_each_in_a_block_comment, 400),
        (_unclosed_blocks_each_in_a_line_comment, 400),
    ],
)
def test_the_brace_scan_outruns_the_old_scan(build, n):
    small, big = build(n), build(2 * n)
    _assert_the_fix_outruns_the_old_code(
        lambda: [_old_extract_razor_brace_block(small, p) for p in _open_positions(small)],
        lambda: _new_blocks(big, _open_positions(big)),
        build.__name__,
    )


def _characters_searched(monkeypatch, content: str) -> int:
    """How many characters the brace scan's searches pass over, for every block of `content`.

    A search is one C call, so neither a checkpoint count nor a race against
    the slow old scan sees one that runs to the end of the file per block.
    This counts the span of each `find` and each regex search directly.
    """
    searched = 0

    def spanned(start: int, stop: int) -> None:
        nonlocal searched
        searched += (stop if stop >= 0 else len(content)) - start

    class Counted(str):
        def find(self, sub, start=0):  # type: ignore[override]
            stop = super().find(sub, start)
            spanned(start, stop)
            return stop

    class CountedPattern:
        def __init__(self, pattern):
            self._pattern = pattern

        def search(self, text, start=0):
            found = self._pattern.search(text, start)
            spanned(start, found.start() if found else -1)
            return found

    monkeypatch.setattr(extractor, "_RAZOR_CODE_STOP_RE", CountedPattern(extractor._RAZOR_CODE_STOP_RE))
    monkeypatch.setattr(
        extractor, "_RAZOR_STRING_STOP_RE",
        {state: CountedPattern(pattern) for state, pattern in extractor._RAZOR_STRING_STOP_RE.items()},
    )
    blocks = extractor._RazorBraceBlocks(Counted(content))
    for position in _open_positions(content):
        blocks.block(position)
    monkeypatch.undo()
    return searched


@pytest.mark.parametrize("opener", ["/*\n", "// "], ids=["block-comment", "line-comment"])
def test_twice_the_unclosed_blocks_is_about_twice_the_characters_searched(monkeypatch, opener):
    """Each block opens a comment that nothing closes, so no scan meets an
    earlier one before its search for the end; without the kept search every
    block searches to the end of the file. (A string is no such shape: the
    next block's own quote closes it.)"""
    small_n, big_n = 200, 400
    small = _characters_searched(monkeypatch, ("@code {" + opener) * small_n)
    big = _characters_searched(monkeypatch, ("@code {" + opener) * big_n)
    assert small > 0
    assert big / small < 2.6, f"{small} characters searched at {small_n} blocks, {big} at {big_n} (x{big / small:.2f})"


def _commented_out_blocks_then_pairs(n: int) -> str:
    # each block is unclosed and is followed, on its own scan, by every `{}` pair of the file
    return "// @code {\n" * n + "{}" * n


def _line_events(fn, module) -> int:
    """How many line events `fn` raises inside `module`: one per line run and
    one per turn of a loop, a comprehension's included.

    A list built per item (a comprehension, a copy loop) passes no checkpoint
    and is too cheap to lose the race, and it is a line event per element all
    the same. Line events, not opcode events: 3.12 reports no opcode for the
    first traced call of a function.
    """
    filename = module.__file__
    count = 0

    def trace(frame, event, arg):
        nonlocal count
        if frame.f_code.co_filename != filename:
            return None
        if event == "line":
            count += 1
        return trace

    previous = sys.gettrace()  # coverage's tracer, when it is on
    sys.settrace(trace)
    try:
        fn()
    finally:
        sys.settrace(previous)
    return count


@pytest.mark.parametrize("build", [_flat_symbols_then_calls, _one_symbol_many_calls])
def test_twice_the_calls_is_about_twice_the_line_events(build):
    small_n, big_n = 150, 300
    small_args, big_args = build(small_n), build(big_n)
    small = _line_events(lambda: extractor._attribute_calls_to_symbols(*small_args), extractor)
    big = _line_events(lambda: extractor._attribute_calls_to_symbols(*big_args), extractor)
    assert small > 5 * small_n, f"{small} line events counted: the tracer did not see the sweep"
    assert big / small < 2.6, f"{build.__name__}: {small} line events at {small_n}, {big} at {big_n} (x{big / small:.2f})"


@pytest.mark.parametrize(
    "build", [_unclosed_blocks, _unclosed_blocks_in_one_string, _closed_blocks, _commented_out_blocks_then_pairs]
)
def test_twice_the_razor_file_is_about_twice_the_loop_iterations(monkeypatch, build):
    small_n, big_n = 100, 200
    small, _ = _checkpoints(monkeypatch, build(small_n), "page.razor", "razor")
    big, big_syms = _checkpoints(monkeypatch, build(big_n), "page.razor", "razor")
    if build is _closed_blocks:
        assert len([s for s in big_syms if s.name == "a"]) > 0, "the @code blocks were not parsed"
    assert big / small < 2.6, f"{build.__name__}: {small} loop iterations at {small_n} blocks, {big} at {big_n}"


# ---------------------------------------------------------------------------
# 3. The dbt and Jinja directive loop
# ---------------------------------------------------------------------------

_OLD_JINJA_COMMENT_RE = re.compile(r"\{#(.*?)#\}", re.DOTALL)


def _old_extract_preceding_docstring(sql_str, offset):
    preceding = sql_str[:offset].rstrip()
    if not preceding:
        return ""
    lines = []
    jinja_comment = _OLD_JINJA_COMMENT_RE.search(preceding)
    if jinja_comment and preceding.rstrip().endswith("#}"):
        comment_body = jinja_comment.group(1).strip()
        if comment_body.startswith("/**"):
            comment_body = comment_body[3:]
        if comment_body.endswith("*/"):
            comment_body = comment_body[:-2]
        cleaned_lines = []
        for line in comment_body.strip().splitlines():
            stripped = line.strip()
            if stripped.startswith("*"):
                stripped = stripped[1:].strip()
            cleaned_lines.append(stripped)
        return "\n".join(cleaned_lines).strip()
    for line in reversed(preceding.splitlines()):
        stripped = line.strip()
        if stripped.startswith("--"):
            lines.insert(0, stripped[2:].strip())
        elif not stripped:
            if lines:
                lines.insert(0, "")
        else:
            break
    return "\n".join(lines).strip()


def _old_extract_dbt_directives(sql_bytes, directive_keywords=("macro", "test", "snapshot", "materialization")):
    sql_str = sql_bytes.decode("utf-8", errors="replace")
    directives = []
    for m in sql_preprocessor._directive_pattern(tuple(directive_keywords)).finditer(sql_str):
        directive = m.group("directive")
        start_offset = m.start()
        end_tag_re = re.compile(r"\{%-?\s*end" + re.escape(directive) + r"\s*-?%\}", re.DOTALL)
        end_match = end_tag_re.search(sql_str, m.end())
        end_offset = end_match.end() if end_match else m.end()
        directives.append(DbtDirective(
            directive=directive,
            name=m.group("name"),
            params=(m.group("params") or "").strip(),
            line=sql_str[:start_offset].count("\n") + 1,
            end_line=sql_str[:end_offset].count("\n") + 1,
            docstring=_old_extract_preceding_docstring(sql_str, start_offset),
            byte_offset=start_offset,
            byte_length=end_offset - start_offset,
        ))
    return directives


# Jinja's three delimiters, read left to right, each to its first closer (L-130)
# A raw or verbatim block is one token: nothing inside it is a delimiter.
_SLOW_JINJA_TOKEN_RE = re.compile(
    r"\{%[-+~]?\s*raw\s*[-+~]?%\}.*?\{%[-+~]?\s*endraw\s*[-+~]?%\}"
    r"|\{%[-+~]?\s*verbatim\s*[-+~]?%\}.*?\{%[-+~]?\s*endverbatim\s*[-+~]?%\}"
    r"|\{\{.*?\}\}|\{%.*?%\}|\{#(?P<body>.*?)#\}",
    re.DOTALL,
)


def _docstring_of_the_comment_just_above(sql_str, offset, marks=("-", "+")):
    """The rule, the slow way (L-126, L-129, L-130, L-131): the Jinja comment
    that ENDS where the text before the directive ends, if it is first on its
    line; else the `--` lines directly above."""
    preceding = sql_str[:offset].rstrip()
    comments = {m.end(): m for m in _SLOW_JINJA_TOKEN_RE.finditer(sql_str) if m.group(0).startswith("{#")}

    def first_on_its_line(pos):
        while True:
            before = sql_str[:pos]
            lines = before.splitlines(keepends=True)
            current = lines[-1] if lines and lines[-1].splitlines()[0] == lines[-1] else ""
            if not current.strip() or current.lstrip().startswith("--"):
                return True
            earlier = comments.get(len(before.rstrip()))  # a comment ends where the line's text ends
            if earlier is None:
                return False
            pos = earlier.start()

    comment = comments.get(len(preceding))
    if comment is not None and first_on_its_line(comment.start()):
        body = comment.group("body")
        # `{#- ... -#}`: the mark is the delimiter's, one per side, this dialect's only
        body = body[1:] if body.startswith(marks) else body
        body = body[:-1] if body.endswith(marks) else body
        # the old lookup, given this comment alone, is its cleaning step
        alone = "{#" + body + "#}"
        return _old_extract_preceding_docstring(alone, len(alone))
    lines = []
    for line in reversed(preceding.splitlines()):
        stripped = line.strip()
        if stripped.startswith("--"):
            lines.insert(0, stripped[2:].strip())
        elif not stripped:
            if lines:
                lines.insert(0, "")
        else:
            break
    return "\n".join(lines).strip()


# every character `str.splitlines` breaks a line on, and every one `str.strip` removes
_LINE_BREAKS = ["\n", "\n", "\r\n", "\r", chr(0x0B), chr(0x0C), chr(0x1C), chr(0x1D), chr(0x1E), chr(0x85),
                chr(0x2028), chr(0x2029)]
_DBT_PIECES = _LINE_BREAKS + [
    " ", "\t", chr(0xA0), chr(0x1F), "x", "-- c", "--", "-", "{#", "#}", "{# d #}", "{#- f -#}", "{#-", "-#}", "{#+", "~#}", "+", "~", "{{", "}}", "{{ '{#' }}", "{{ y }}", "{% raw %}", "{%- endraw -%}", "{% raw %}{{ {# {% endraw %}", "{#/** d", "* e", "*/#}",
    "{% macro m(a, b) %}", "{%- macro n -%}", "{% endmacro %}", "{%- endmacro -%}", "{% test t(x) %}",
    "{% endtest %}", "{% snapshot s %}", "{% endsnapshot %}", "{% block b %}", "{% endblock %}", "{%", "%}",
]


@pytest.mark.parametrize("keywords", [("macro", "test", "snapshot", "materialization"), ("macro", "block")])
def test_every_directive_reads_as_the_old_loop_read_it(keywords):
    rng = random.Random(117)
    seen = moved = held = 0
    for _ in range(4000):
        text = "".join(rng.choice(_DBT_PIECES) for _ in range(rng.randint(0, 30))).encode("utf-8")
        expected = _old_extract_dbt_directives(text, keywords)
        actual = sql_preprocessor.extract_dbt_directives(text, keywords)
        # Every field but the docstring is the old loop's. The old docstring
        # was the defect of L-126 (the first comment of the file for any
        # directive under a `#}`), so that field is held to the rule instead.
        assert [dataclasses.replace(d, docstring="") for d in actual] == [
            dataclasses.replace(d, docstring="") for d in expected
        ], repr(text)
        sql_str = text.decode("utf-8", errors="replace")
        assert [d.docstring for d in actual] == [
            _docstring_of_the_comment_just_above(sql_str, d.byte_offset) for d in expected
        ], repr(text)
        # Outside the defect's domain the old loop itself is still the oracle.
        for a, e in zip(actual, expected):
            if not sql_str[:e.byte_offset].rstrip().endswith("#}"):
                assert a.docstring == e.docstring, repr(text)
                held += 1
        seen += len(expected)
        moved += sum(1 for a, e in zip(actual, expected) if a.docstring != e.docstring)
    assert seen > 2000, "the generator produced too few directives to compare"
    assert moved > 50, "the generator produced too few directives under a second comment"
    assert held > 1000, "the generator produced too few directives with no comment end above them"


def _open_comments_and_macros(n: int) -> bytes:
    return "".join(f"{{# open\n{{% macro m{i}() %}}\n" for i in range(n)).encode("utf-8")


def _macros_that_never_end(n: int) -> bytes:
    return "".join(f"-- about m{i}\n{{% macro m{i}(a) %}}\nselect {i}\n" for i in range(n)).encode("utf-8")


def _macros_on_one_line(n: int) -> bytes:
    return "".join(f"{{% macro m{i}() %}}{{% endmacro %}} " for i in range(n)).encode("utf-8")


@pytest.mark.parametrize(
    "build, n", [(_open_comments_and_macros, 150), (_macros_that_never_end, 3000), (_macros_on_one_line, 6000)]
)
def test_the_directive_loop_outruns_the_old_loop(build, n):
    small, big = build(n), build(2 * n)
    _assert_the_fix_outruns_the_old_code(
        lambda: _old_extract_dbt_directives(small),
        lambda: sql_preprocessor.extract_dbt_directives(big),
        build.__name__,
    )


def _characters_read_by_the_directive_loop(monkeypatch, sql_bytes: bytes) -> int:
    """How many characters the directive loop copies or searches for one file.

    A slice, a `find` and a regex search are each one C call: no checkpoint is
    passed, and a copy of the file per directive is still far cheaper than the
    old loop, so it wins the race. This counts the length of every slice of
    the file and the span of every search over it.
    """
    read = 0

    def spanned(length: int) -> None:
        nonlocal read
        read += max(length, 0)

    class Counted(str):
        def __getitem__(self, key):
            got = super().__getitem__(key)
            spanned(len(got))
            return got

        def _bounded(self, bounds, stop=-1) -> None:
            start = bounds[0] if bounds else 0
            limit = bounds[1] if len(bounds) > 1 else len(self)
            spanned((stop if stop >= 0 else limit) - start)

        def find(self, sub, *bounds):  # type: ignore[override]
            stop = super().find(sub, *bounds)
            self._bounded(bounds, stop)
            return stop

        def count(self, sub, *bounds):  # type: ignore[override]
            self._bounded(bounds)
            return super().count(sub, *bounds)

        def splitlines(self, *args):  # type: ignore[override]
            spanned(len(self))
            return super().splitlines(*args)

        def rstrip(self, *args):  # type: ignore[override]
            spanned(len(self))
            return super().rstrip(*args)

    class CountedPattern:
        def __init__(self, pattern):
            self._pattern = pattern

        def search(self, text, *bounds):
            found = self._pattern.search(text, *bounds)
            start = bounds[0] if bounds else 0
            limit = bounds[1] if len(bounds) > 1 else len(text)
            spanned((found.start() if found else limit) - start)
            return found

        def match(self, text, *bounds):
            found = self._pattern.match(text, *bounds)
            spanned(found.end() - found.start() if found else 1)
            return found

        def finditer(self, text, *bounds):
            spanned(len(text) - (bounds[0] if bounds else 0))
            return self._pattern.finditer(text, *bounds)

        def findall(self, text, *bounds):
            spanned(len(text) - (bounds[0] if bounds else 0))
            return self._pattern.findall(text, *bounds)

    class CountedRe:
        """`re`, with every pattern it compiles and every search it runs counted."""

        def __getattr__(self, name):
            return getattr(re, name)

        def compile(self, pattern, flags=0):
            return CountedPattern(re.compile(pattern, flags))

        def search(self, pattern, text, flags=0):
            return CountedPattern(re.compile(pattern, flags)).search(text)

        def finditer(self, pattern, text, flags=0):
            return CountedPattern(re.compile(pattern, flags)).finditer(text)

        def findall(self, pattern, text, flags=0):
            return CountedPattern(re.compile(pattern, flags)).findall(text)

    class Source(bytes):
        def decode(self, *args, **kwargs):  # the loop's only way to the text
            return Counted(super().decode(*args, **kwargs))

    real_directive_pattern = sql_preprocessor._directive_pattern
    monkeypatch.setattr(sql_preprocessor, "re", CountedRe())
    monkeypatch.setattr(sql_preprocessor, "_directive_pattern", lambda kw: CountedPattern(real_directive_pattern(kw)))
    monkeypatch.setattr(sql_preprocessor, "_LINE_BREAK_RE", CountedPattern(sql_preprocessor._LINE_BREAK_RE))
    monkeypatch.setattr(sql_preprocessor, "_NON_SPACE_RE", CountedPattern(sql_preprocessor._NON_SPACE_RE))
    monkeypatch.setattr(sql_preprocessor, "_JINJA_OPENER_RE", CountedPattern(sql_preprocessor._JINJA_OPENER_RE))
    monkeypatch.setattr(sql_preprocessor, "_RAW_OPEN_RE", CountedPattern(sql_preprocessor._RAW_OPEN_RE))
    monkeypatch.setattr(
        sql_preprocessor, "_RAW_END_RE",
        {word: CountedPattern(pattern) for word, pattern in sql_preprocessor._RAW_END_RE.items()},
    )
    try:
        found = sql_preprocessor.extract_dbt_directives(Source(sql_bytes))
    finally:
        monkeypatch.undo()
    assert found == sql_preprocessor.extract_dbt_directives(sql_bytes), "counting changed what the loop read"
    return read


def _commented_macros(n: int) -> bytes:
    return "".join(f"{{# about m{i} #}}\n{{% macro m{i}() %}}\n{{% endmacro %}}\n" for i in range(n)).encode("utf-8")


def _comment_chains_above_macros(n: int) -> bytes:
    # the walk back to the line start steps over every comment before the last one
    return "".join("{# a #}  " * 6 + f"{{# about m{i} #}}\n{{% macro m{i}() %}}{{% endmacro %}}\n" for i in range(n)).encode("utf-8")


def _one_chain_of_comments(n: int) -> bytes:
    return ("{# a #} " * (8 * n) + "\n{% macro m() %}{% endmacro %}\n").encode("utf-8")


def _expressions_that_never_close(n: int) -> bytes:
    # no `}}` anywhere: one failed search, then `{{` is text
    return "".join(f"{{{{ open\n{{# about m{i} #}}\n{{% macro m{i}() %}}{{% endmacro %}}\n" for i in range(n)).encode("utf-8")


def _trailing_comments(n: int) -> bytes:
    return "".join(f"{{% macro m{i}() %}}{{{{ x }}}}{{% endmacro %}} {{# end of m{i} #}}\n" for i in range(n)).encode("utf-8")


def _raw_blocks_that_never_end(n: int) -> bytes:
    # no `{% endraw %}` anywhere: one failed search, then `raw` is an ordinary tag
    return "".join(f"{{% raw %}}\n{{# about m{i} #}}\n{{% macro m{i}() %}}{{% endmacro %}}\n" for i in range(n)).encode("utf-8")


def _raw_blocks(n: int) -> bytes:
    return "".join(
        f"{{% raw %}}{{{{ {{# {{% endraw %}}\n{{# about m{i} #}}\n{{% macro m{i}() %}}{{% endmacro %}}\n" for i in range(n)
    ).encode("utf-8")


_DIRECTIVE_SHAPES = [
    _raw_blocks_that_never_end, _raw_blocks,
    _open_comments_and_macros, _macros_that_never_end, _macros_on_one_line, _commented_macros,
    _comment_chains_above_macros, _one_chain_of_comments, _expressions_that_never_close, _trailing_comments,
]


@pytest.mark.parametrize("build", _DIRECTIVE_SHAPES)
def test_twice_the_directives_is_about_twice_the_loop_iterations(monkeypatch, build):
    counts = []
    for n in (200, 400):
        count = 0
        real = parse_budget.checkpoint

        def counting():
            nonlocal count
            count += 1
            real()

        monkeypatch.setattr(parse_budget, "checkpoint", counting)
        try:
            found = sql_preprocessor.extract_dbt_directives(build(n))
        finally:
            monkeypatch.setattr(parse_budget, "checkpoint", real)
        assert found, "the shape holds no directive"
        counts.append(count)
    small, big = counts
    assert small > 0
    assert big / small < 2.6, f"{build.__name__}: {small} loop iterations at 200, {big} at 400"


@pytest.mark.parametrize("build", _DIRECTIVE_SHAPES)
def test_twice_the_directives_is_about_twice_the_characters_read(monkeypatch, build):
    small_n, big_n = 200, 400
    small_text, big_text = build(small_n), build(big_n)
    small = _characters_read_by_the_directive_loop(monkeypatch, small_text)
    big = _characters_read_by_the_directive_loop(monkeypatch, big_text)
    assert small >= len(small_text), f"{small} characters counted in a file of {len(small_text)}: the count saw nothing"
    assert big / small < 2.6, f"{build.__name__}: {small} characters read at {small_n} directives, {big} at {big_n}"


# ---------------------------------------------------------------------------
# 4. The line of an offset
# ---------------------------------------------------------------------------

_PARSER_DIR = pathlib.Path(extractor.__file__).parent


def _newline_counts_up_to_an_offset(source: str) -> list[int]:
    """Lines of `x.count("\\n", a, b)` and `x[a:b].count("\\n")`: a count bounded by an offset."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "count"):
            continue
        if not (node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value in ("\n", b"\n")):
            continue
        if len(node.args) > 1 or isinstance(node.func.value, ast.Subscript):
            found.append(node.lineno)
    return found


def test_no_parser_counts_newlines_up_to_an_offset():
    """Five parsers numbered each symbol by counting newlines from the start of
    the file (Razor, Astro, VHDL, Verilog, COBOL): the whole file again per
    symbol. `_line_numbers` builds one table. A count of the whole text, with
    no bound, is one pass and is not what this looks for."""
    offenders = {
        path.name: lines
        for path in sorted(_PARSER_DIR.glob("*.py"))
        if (lines := _newline_counts_up_to_an_offset(path.read_text(encoding="utf-8")))
    }
    assert offenders == {}, "use extractor._line_numbers(text), built once per file"


def test_the_scan_for_a_bounded_newline_count_sees_both_spellings():
    source = (
        "def a(text, pos):\n    return text.count('\\n', 0, pos) + 1\n"
        "def b(text, pos):\n    return text[:pos].count('\\n') + 1\n"
        "def c(text):\n    return text.count('\\n') + 1\n"
        "def d(text, pos):\n    return text.count('x', 0, pos)\n"
    )
    assert _newline_counts_up_to_an_offset(source) == [2, 4]


def test_a_line_number_is_the_count_of_newlines_before_the_offset():
    rng = random.Random(117)
    for _ in range(300):
        text = "".join(rng.choice(["\n", "\n", "\r\n", "x", " ", "é"]) for _ in range(rng.randint(0, 60)))
        line_of = extractor._line_numbers(text)
        for offset in range(len(text) + 3):
            assert line_of(offset) == text.count("\n", 0, offset) + 1, (text, offset)


@pytest.mark.parametrize(
    "language, filename, build",
    [
        ("razor", "page.razor", lambda n: "<script>var a = 1;</script>\n" * n),
        ("vhdl", "top.vhd", lambda n: "".join(f"entity e{i} is\nend e{i};\n" for i in range(n))),
        ("verilog", "top.v", lambda n: "".join(f"module m{i}();\nendmodule\n" for i in range(n))),
    ],
)
def test_the_last_symbol_of_a_long_file_is_on_its_own_line(language, filename, build):
    n = 400
    symbols = parse_file(build(n), filename, language)
    lines = sorted(s.line for s in symbols if s.line > 1)
    assert len(lines) >= n - 1 and lines[-1] >= n, (len(symbols), lines[-3:])


def test_a_macro_keeps_its_line_its_end_and_its_comment():
    text = b"select 1\n\n-- builds the key\n-- from two columns\n{% macro key(a, b) %}\n  {{ a }}\n{% endmacro %}\n"
    (found,) = sql_preprocessor.extract_dbt_directives(text)
    assert (found.name, found.params, found.line, found.end_line) == ("key", "a, b", 5, 7)
    assert found.docstring == "builds the key\nfrom two columns"
    assert text[found.byte_offset:found.byte_offset + found.byte_length].endswith(b"{% endmacro %}")
