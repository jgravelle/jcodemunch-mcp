"""A Zig `packed`/`extern` struct or union is the container its body is (#841).

`const P = packed struct { a: u8, pub fn f() void {} };` was a bare
`constant P` with no class, no `P.a` and no `P.f`, while the same body
spelled `struct` was a `class` with owned members. `_is_type_expr` asked
whether the expression's TEXT starts with `struct`, `enum` or `union`, and a
qualifier starts the text instead: a guard written against a spelling
(Standing lesson 09-01). The grammar spells every container
`ContainerDecl > ContainerDeclType > <keyword>` with the qualifier as a
sibling token, and the test asks that node now.

Rulings: a qualified container is the same kind as its unqualified form
(`class` for struct, `type` for enum, union and opaque) with its fields and
fns owned by it, asserted EQUAL to the unqualified form's rows for the same
body; `opaque {}` is a `type` with no members (the same node, decided rather
than left). Ids move (`constant` -> `class`/`type`) and the members are new
symbols; `PARSER_GENERATION` names it. Found by the #809/#811 reviewer;
reporter @jgravelle.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.parser.extractor import parse_file


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def _rows(source: str):
    symbols = parse_file(source, "a.zig", "zig")
    by_id = {s.id: s for s in symbols}
    return {
        s.qualified_name: (s.kind, by_id[s.parent].name if s.parent else None)
        for s in symbols
    }


_BODY = "{ a: u8, const LIMIT: u8 = 3, pub fn f(self: *@This()) u8 { return self.a; } }"


def test_the_reported_packed_struct_is_a_class_with_owned_members():
    rows = _rows("const P = packed struct { a: u8, pub fn f() void {} };\n")
    assert rows == {
        "P": ("class", None),
        "P.a": ("field", "P"),
        "P.f": ("method", "P"),
    }


@pytest.mark.parametrize("qualifier", ["packed", "extern"])
def test_a_qualified_struct_answers_what_the_unqualified_one_answers(qualifier):
    body = "{ a: u8, pub fn f() void {} }"
    assert _rows(f"const S = {qualifier} struct {body};\n") == _rows(f"const S = struct {body};\n")
    assert _rows(f"const S = struct {body};\n")["S"] == ("class", None)


def test_an_extern_union_answers_what_a_union_answers():
    body = "{ a: u8, b: u16 }"
    assert _rows(f"const U = extern union {body};\n") == _rows(f"const U = union {body};\n")
    assert _rows(f"const U = union {body};\n") == {
        "U": ("type", None), "U.a": ("field", "U"), "U.b": ("field", "U"),
    }


def test_a_packed_struct_with_a_backing_integer_is_a_class():
    rows = _rows("const P = packed struct(u16) { a: u8, b: u8 };\n")
    assert rows == {"P": ("class", None), "P.a": ("field", "P"), "P.b": ("field", "P")}


def test_an_opaque_container_is_a_type_with_no_members():
    """The same node the other containers use; decided, not left."""
    assert _rows("const O = opaque {};\n") == {"O": ("type", None)}


def test_a_nested_qualified_container_is_owned():
    source = "const Outer = struct {\n    const Inner = packed struct { q: u8 };\n};\n"
    rows = _rows(source)
    assert rows["Outer.Inner"] == ("class", "Outer")
    assert rows["Outer.Inner.q"] == ("field", "Inner")


def test_a_pub_qualified_container_is_a_class():
    assert _rows("pub const P = extern struct { a: u8 };\n") == {"P": ("class", None), "P.a": ("field", "P")}


def test_the_unqualified_spellings_are_unchanged():
    assert _rows("const E = enum(u8) { a, b };\n") == {"E": ("type", None)}
    assert _rows("const T = union(enum) { a: u8 };\n") == {"T": ("type", None), "T.a": ("field", "T")}
    assert _rows("const S = struct { a: u8 };\n") == {"S": ("class", None), "S.a": ("field", "S")}
    assert _rows("const N: u32 = 1;\nconst V = struct_like;\n") == {"N": ("constant", None), "V": ("constant", None)}


def test_the_kind_change_moves_the_id_and_nothing_else_about_the_name():
    """`constant P` -> `class P`: the id is keyed on the kind, so it moves;
    the name and qualified name do not."""
    symbols = parse_file("const P = packed struct { a: u8 };\n", "a.zig", "zig")
    p = next(s for s in symbols if s.name == "P")
    assert p.kind == "class" and p.qualified_name == "P" and p.id.endswith("#class")
