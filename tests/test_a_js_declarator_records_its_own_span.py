"""Each name in a multi-declarator JS/TS `let`/`const`/`var` records its own span (#837).

`let x = 1, y = 2;` gave `x` and `y` the whole statement: two symbols, one
set of bytes, so `get_symbol_source` on `x` returned `y`'s initializer too.
#826 fixed the same defect for Go under the rule *the widest node that
addresses this name alone*, written as ONE function so two channels could
not answer differently; JS has two channels (bindings, and the
`const f = () => ...` function expressions) and neither asked.

The rule here: the declaration (keyword included, `export` excluded as
before) when it holds ONE declarator, the `variable_declarator` when it
holds several; `signature` follows the span. A destructuring pattern is ONE
declarator however many names it binds, so `const { a, b } = o` keeps the
declaration's span for both, the rule and not an exception (Go's
`const D, E = 5, 6`). Java's `int a, b;` stays on its declaration (#823:
a Java declarator does not carry the type); a JS declarator carries the
initializer, which is what a reader opens.

Spans move for every name of a multi-declarator statement; the id does
not. `PARSER_GENERATION` names it. Reporter: @jgravelle.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.parser.extractor import parse_file

_LANGS = [("javascript", "a.js"), ("typescript", "a.ts"), ("tsx", "a.tsx")]


def _rows(source: str, language: str, filename: str):
    return [
        (s.kind, s.name, s.byte_offset, s.byte_length, s.signature)
        for s in parse_file(source, filename, language)
    ]


@pytest.mark.parametrize("language, filename", _LANGS)
def test_each_name_in_a_let_list_records_its_own_declarator(language, filename):
    assert _rows("let x = 1, y = 2;\n", language, filename) == [
        ("variable", "x", 4, 5, "x = 1"),
        ("variable", "y", 11, 5, "y = 2"),
    ]


@pytest.mark.parametrize("language, filename", _LANGS)
def test_each_name_in_a_const_list_records_its_own_declarator(language, filename):
    assert _rows("const c1 = 1, c2 = 2;\n", language, filename) == [
        ("constant", "c1", 6, 6, "c1 = 1"),
        ("constant", "c2", 14, 6, "c2 = 2"),
    ]


@pytest.mark.parametrize("language, filename", _LANGS)
def test_each_name_in_a_var_list_records_its_own_declarator(language, filename):
    """An uninitialised declarator is its bare name."""
    assert _rows("var v1 = 1, v2;\n", language, filename) == [
        ("variable", "v1", 4, 6, "v1 = 1"),
        ("variable", "v2", 12, 2, "v2"),
    ]


@pytest.mark.parametrize("language, filename", _LANGS)
def test_an_exported_list_records_each_declarator(language, filename):
    assert _rows("export const e1 = 1, e2 = 2;\n", language, filename) == [
        ("constant", "e1", 13, 6, "e1 = 1"),
        ("constant", "e2", 21, 6, "e2 = 2"),
    ]


@pytest.mark.parametrize("language, filename", _LANGS)
def test_a_single_declarator_records_the_bytes_it_always_did(language, filename):
    """Keyword included, byte-identical to before."""
    assert _rows("let s = 1;\n", language, filename) == [("variable", "s", 0, 10, "let s = 1;")]
    assert _rows("const k = 2;\n", language, filename) == [("constant", "k", 0, 12, "const k = 2;")]
    assert _rows("export const k = 2;\n", language, filename) == [("constant", "k", 7, 12, "const k = 2;")]


@pytest.mark.parametrize("language, filename", _LANGS)
def test_a_destructuring_pattern_is_one_declarator_shared_by_its_names(language, filename):
    """Measured before the rule was applied (#751's forms): one declarator,
    two names, so the declaration's span is the rule, as for Go's
    `const D, E = 5, 6`."""
    assert _rows("const { a, b } = o;\n", language, filename) == [
        ("constant", "a", 0, 19, "const { a, b } = o;"),
        ("constant", "b", 0, 19, "const { a, b } = o;"),
    ]
    assert _rows("const [p, q] = arr;\n", language, filename) == [
        ("constant", "p", 0, 19, "const [p, q] = arr;"),
        ("constant", "q", 0, 19, "const [p, q] = arr;"),
    ]


@pytest.mark.parametrize("language, filename", _LANGS)
def test_a_pattern_beside_a_plain_declarator_records_each_declarator(language, filename):
    assert _rows("const { a } = o, d = 3;\n", language, filename) == [
        ("constant", "a", 6, 9, "{ a } = o"),
        ("constant", "d", 17, 5, "d = 3"),
    ]


@pytest.mark.parametrize("language, filename", _LANGS)
def test_the_function_expression_channel_follows_the_same_rule(language, filename):
    """The same statement, the other channel; one rule for both."""
    assert _rows("const f = () => 1, g = function () {};\n", language, filename) == [
        ("function", "f", 6, 11, "f = () => 1"),
        ("function", "g", 19, 18, "g = function () {}"),
    ]
    assert _rows("const f = () => 1;\n", language, filename) == [
        ("function", "f", 0, 18, "const f = () => 1;"),
    ]


@pytest.mark.parametrize("language, filename", [("typescript", "a.ts"), ("tsx", "a.tsx")])
def test_a_typed_declarator_records_its_annotation(language, filename):
    assert _rows("let t1: number = 1, t2: string = 'a';\n", language, filename) == [
        ("variable", "t1", 4, 14, "t1: number = 1"),
        ("variable", "t2", 20, 16, "t2: string = 'a'"),
    ]


@pytest.mark.parametrize("language, filename", _LANGS)
def test_line_numbers_follow_the_declarator(language, filename):
    symbols = parse_file("let m1 = 1,\n    m2 = 2;\n", filename, language)
    assert [(s.name, s.line, s.end_line) for s in symbols] == [("m1", 1, 1), ("m2", 2, 2)]


@pytest.mark.parametrize("language, filename", _LANGS)
def test_no_two_symbols_from_one_statement_share_a_span(language, filename):
    """The PROPERTY the issue asks for, over every shape at once."""
    source = (
        "let x = 1, y = 2;\n"
        "const c1 = 1, c2 = 2;\n"
        "var v1 = 1, v2;\n"
        "export const e1 = 1, e2 = 2;\n"
        "const { a } = o, d = 3;\n"
        "const f = () => 1, g = function () {};\n"
    )
    symbols = parse_file(source, filename, language)
    spans = [(s.byte_offset, s.byte_length) for s in symbols]
    assert len(spans) == len(set(spans)), sorted((s.name, s.byte_offset, s.byte_length) for s in symbols)
    assert len(symbols) == 12


@pytest.mark.parametrize("language, filename", _LANGS)
def test_the_id_does_not_move(language, filename):
    """Only the span moves; the id is keyed on the qualified name."""
    multi = {s.name: s.id for s in parse_file("let x = 1, y = 2;\n", filename, language)}
    single = {s.name: s.id for s in parse_file("let x = 1;\nlet y = 2;\n", filename, language)}
    assert multi == single
