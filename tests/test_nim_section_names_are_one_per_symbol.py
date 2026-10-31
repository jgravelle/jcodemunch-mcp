"""A Nim `var`/`let`/`const` binding publishes one symbol per name (LEDGER L-05).

`var x*, y: int` published ONE constant named `x*, y`: the section branch of
`_parse_nim_symbols` took the TEXT of the whole `symbol_declaration_list` and
stripped a trailing `*`, so neither `x` nor `y` was findable by name. The
object fields (#812) and the routines (#843) already read each
`symbol_declaration`'s `name` through `_declared_name`; the section branch was
the one reader still reading text.

⚠ A probe of the grammar found three more spellings the text read gets wrong:
a pragma rides inside the declaration (`var g* {.global.}: int` was named
`g* {.global.}`), a backticked name kept its backticks, and tuple unpacking
(`let (p, q) = t`) nests the declarations one level down, under
`tuple_deconstruct_declaration`, and was named `(p, q)`.
"""

import pytest

from jcodemunch_mcp.parser.extractor import parse_file


@pytest.fixture(autouse=True)
def _nim_enabled(monkeypatch):
    """This box's config disables nim, and a disabled language yields `[]`,
    indistinguishable from the defect."""
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def _names(source: str) -> list[str]:
    return sorted(
        s.name for s in parse_file(source, "a.nim", "nim") if s.kind == "constant"
    )


@pytest.mark.parametrize(
    "source, names",
    [
        ("var x*, y: int\n", ["x", "y"]),  # the reported case
        ("let a = 1\n", ["a"]),
        ("const c* = 2\n", ["c"]),
        ("var\n  m, n: int\n  o* = 3\n", ["m", "n", "o"]),
        ("var g* {.global.}: int\n", ["g"]),
        ("const `type`* = 2\n", ["type"]),
        ("let (p, q) = t\n", ["p", "q"]),
        ("let (r*, s) = t\n", ["r", "s"]),
        ("let (a, (b, c)) = t\n", ["a", "b", "c"]),  # nested unpacking
        ("let (_, z) = t\n", ["z"]),  # a blank binds no name
    ],
)
def test_each_bound_name_is_its_own_symbol(source, names):
    assert _names(source) == names


def test_a_name_from_a_list_is_found_by_its_own_name():
    syms = {s.name: s for s in parse_file("var x*, y: int\n", "a.nim", "nim")}
    assert syms["x"].qualified_name == "x"
    assert syms["y"].id != syms["x"].id
    assert syms["x"].signature.startswith("var ")


def test_the_parser_generation_moved_for_the_new_ids():
    """Ids move on unchanged content, so an existing index must re-parse.
    Generation 27 is also claimed by other unmerged branches: whichever merges
    later raises the constant and this assertion to the next number."""
    from jcodemunch_mcp.storage.index_store import PARSER_GENERATION

    assert PARSER_GENERATION >= 27


def test_each_symbol_spans_its_declaration_line():
    syms = {s.name: s for s in parse_file("var\n  m, n: int\n  o* = 3\n", "a.nim", "nim")}
    assert (syms["m"].line, syms["n"].line, syms["o"].line) == (2, 2, 3)
