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
kept below as the oracle: the fix on a file eight times bigger must finish
before the old code finishes the small one. Both run on the same machine in the
same test, so no figure is typed here.

The oracles are also the equality check: the fix returns what the old code
returned on every generated input.
"""

import bisect
import random
import re
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
    old = _seconds(old_small)
    new = _seconds(new_big)
    for _ in range(2):  # a loaded machine can slow one run; it does not slow the best of three
        if new < old or new > 4 * old:
            break
        new = min(new, _seconds(new_big))
    assert new < old, (
        f"{what}: the fix took {new:.4f} s on the file eight times bigger and the code it "
        f"replaced took {old:.4f} s on the small one; work that grows with the square reads about x64"
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


@pytest.mark.parametrize("build, n", [(_flat_symbols_then_calls, 1000), (_one_symbol_many_calls, 1500)])
def test_attributing_calls_outruns_the_old_scan(build, n):
    _assert_the_fix_outruns_the_old_code(
        lambda: _old_attribute_calls_to_symbols(*build(n)),
        lambda: extractor._attribute_calls_to_symbols(*build(8 * n)),
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


def _closed_blocks(n: int) -> str:
    return "@code { int a; }\n" * n


def _open_positions(content: str) -> list[int]:
    return [m.end() - 1 for m in re.finditer(r"@code \{", content)]


@pytest.mark.parametrize("build, n", [(_unclosed_blocks, 500), (_unclosed_blocks_in_one_string, 250)])
def test_the_brace_scan_outruns_the_old_scan(build, n):
    small, big = build(n), build(8 * n)
    _assert_the_fix_outruns_the_old_code(
        lambda: [_old_extract_razor_brace_block(small, p) for p in _open_positions(small)],
        lambda: _new_blocks(big, _open_positions(big)),
        build.__name__,
    )


@pytest.mark.parametrize("build", [_unclosed_blocks, _unclosed_blocks_in_one_string, _closed_blocks])
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


# every character `str.splitlines` breaks a line on, and every one `str.strip` removes
_LINE_BREAKS = ["\n", "\n", "\r\n", "\r", chr(0x0B), chr(0x0C), chr(0x1C), chr(0x1D), chr(0x1E), chr(0x85),
                chr(0x2028), chr(0x2029)]
_DBT_PIECES = _LINE_BREAKS + [
    " ", "\t", chr(0xA0), chr(0x1F), "x", "-- c", "--", "-", "{#", "#}", "{# d #}", "{#/** d", "* e", "*/#}",
    "{% macro m(a, b) %}", "{%- macro n -%}", "{% endmacro %}", "{%- endmacro -%}", "{% test t(x) %}",
    "{% endtest %}", "{% snapshot s %}", "{% endsnapshot %}", "{% block b %}", "{% endblock %}", "{%", "%}",
]


@pytest.mark.parametrize("keywords", [("macro", "test", "snapshot", "materialization"), ("macro", "block")])
def test_every_directive_reads_as_the_old_loop_read_it(keywords):
    rng = random.Random(117)
    seen = 0
    for _ in range(4000):
        text = "".join(rng.choice(_DBT_PIECES) for _ in range(rng.randint(0, 30))).encode("utf-8")
        expected = _old_extract_dbt_directives(text, keywords)
        assert sql_preprocessor.extract_dbt_directives(text, keywords) == expected, repr(text)
        seen += len(expected)
    assert seen > 2000, "the generator produced too few directives to compare"


def _open_comments_and_macros(n: int) -> bytes:
    return "".join(f"{{# open\n{{% macro m{i}() %}}\n" for i in range(n)).encode("utf-8")


def _macros_that_never_end(n: int) -> bytes:
    return "".join(f"-- about m{i}\n{{% macro m{i}(a) %}}\nselect {i}\n" for i in range(n)).encode("utf-8")


def _macros_on_one_line(n: int) -> bytes:
    return "".join(f"{{% macro m{i}() %}}{{% endmacro %}} " for i in range(n)).encode("utf-8")


@pytest.mark.parametrize(
    "build, n", [(_open_comments_and_macros, 150),(_macros_that_never_end, 1500), (_macros_on_one_line, 2500)]
)
def test_the_directive_loop_outruns_the_old_loop(build, n):
    small, big = build(n), build(8 * n)
    _assert_the_fix_outruns_the_old_code(
        lambda: _old_extract_dbt_directives(small),
        lambda: sql_preprocessor.extract_dbt_directives(big),
        build.__name__,
    )


def test_a_macro_keeps_its_line_its_end_and_its_comment():
    text = b"select 1\n\n-- builds the key\n-- from two columns\n{% macro key(a, b) %}\n  {{ a }}\n{% endmacro %}\n"
    (found,) = sql_preprocessor.extract_dbt_directives(text)
    assert (found.name, found.params, found.line, found.end_line) == ("key", "a, b", 5, 7)
    assert found.docstring == "builds the key\nfrom two columns"
    assert text[found.byte_offset:found.byte_offset + found.byte_length].endswith(b"{% endmacro %}")
