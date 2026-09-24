"""A JS/TS class expression is a class, named by what binds it (#803).

`const C = class { x = 1; m() {} }` published `C` as a `constant` and `m` as
a method with NO owner, and the field `x` not at all: #781 withholds a field
with no class symbol to own it, because a member with no owner is #698's
defect. An anonymous `export default class { ... }` did the same, and a class
expression inside a function published `f.k`, qualified as if the FUNCTION
declared `k`.

Rulings:
- A class expression is a `class` symbol named by its BINDER, the way
  `const d = function inner() {}` is already `d`: the declarator's name
  (`const C = class Inner {}` is `C`), `default` for an anonymous
  `export default class`, and the property for `obj.P = class {}` /
  `exports.K = class {}` (`module.exports = class {}` is the CommonJS default
  export, so `default`). Its methods and fields hang off it, as they do off
  a class declaration. Parenthesised and TS `as`/`satisfies`/`!` wrappers
  are seen through.
- A class expression NOTHING binds (`new (class {...})()`, `return class
  {...}`, an argument, an object-literal value) has no name to borrow, so no
  class symbol, and its members are WITHHELD, as #781 already withholds its
  fields: a member with no owner, or one qualified under the enclosing
  function, is worse than the absence.
- A class expression in a class-field initializer (`static Inner = class
  {...}`) is unchanged: its members were already qualified under the field.
- `C#constant` / `E#variable` become `C#class` / `E#class`, and every member
  of a bound class expression moves under it; `PARSER_GENERATION` names it.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.parser.extractor import parse_file

_LANGS = [("javascript", "a.js"), ("typescript", "a.ts"), ("tsx", "a.tsx")]


def _rows(source: str, language: str, filename: str):
    symbols = parse_file(source, filename, language)
    by_id = {s.id: s for s in symbols}
    return {
        s.qualified_name: (s.kind, by_id[s.parent].qualified_name if s.parent in by_id else s.parent)
        for s in symbols
    }


@pytest.mark.parametrize("language,filename", _LANGS)
def test_a_const_bound_class_expression_is_a_class_owning_its_members(language, filename):
    rows = _rows("const C = class { x = 1; m() {} };\n", language, filename)
    assert rows == {
        "C": ("class", None),
        "C.x": ("field", "C"),
        "C.m": ("method", "C"),
    }


@pytest.mark.parametrize("language,filename", _LANGS)
def test_a_named_class_expression_takes_its_binders_name(language, filename):
    rows = _rows("const D = class Inner { y = 1; n() {} };\n", language, filename)
    assert rows == {"D": ("class", None), "D.y": ("field", "D"), "D.n": ("method", "D")}


@pytest.mark.parametrize("language,filename", _LANGS)
def test_an_anonymous_default_export_class_is_default(language, filename):
    rows = _rows("export default class { z = 1; run() {} }\n", language, filename)
    assert rows == {
        "default": ("class", None),
        "default.z": ("field", "default"),
        "default.run": ("method", "default"),
    }


@pytest.mark.parametrize("language,filename", _LANGS)
def test_a_class_expression_assigned_to_a_property_takes_the_property(language, filename):
    rows = _rows("obj.P = class { q = 1; p() {} };\nexports.K = class { k() {} };\n", language, filename)
    assert rows == {
        "P": ("class", None),
        "P.q": ("field", "P"),
        "P.p": ("method", "P"),
        "K": ("class", None),
        "K.k": ("method", "K"),
    }


@pytest.mark.parametrize("language,filename", _LANGS)
def test_module_exports_is_the_commonjs_default(language, filename):
    rows = _rows("module.exports = class { m() {} };\n", language, filename)
    assert rows == {"default": ("class", None), "default.m": ("method", "default")}


@pytest.mark.parametrize("language,filename", _LANGS)
def test_let_and_export_bindings_are_classes_too(language, filename):
    rows = _rows(
        "let E = class extends Base { e = 1; g() {} };\nexport const F = class { h() {} };\n",
        language,
        filename,
    )
    assert rows["E"] == ("class", None) and rows["E.g"] == ("method", "E") and rows["E.e"] == ("field", "E")
    assert rows["F"] == ("class", None) and rows["F.h"] == ("method", "F")


@pytest.mark.parametrize("language,filename", _LANGS)
def test_a_class_expression_inside_a_function_is_not_qualified_under_the_function(language, filename):
    """The issue's last assertion: `f.k` said the function declared `k`."""
    rows = _rows("function f() { return class { w = 1; k() {} }; }\n", language, filename)
    assert rows == {"f": ("function", None)}


@pytest.mark.parametrize("language,filename", _LANGS)
def test_a_bound_class_expression_inside_a_function_is_owned_by_the_function(language, filename):
    rows = _rows("function f() { const L = class { k() {} }; return L; }\n", language, filename)
    assert rows["f.L"] == ("class", "f"), rows
    assert rows["f.L.k"] == ("method", "f.L"), rows


@pytest.mark.parametrize("language,filename", _LANGS)
def test_an_unbound_class_expression_publishes_no_member(language, filename):
    rows = _rows("new (class { a = 1; b() {} })();\nuse(class { c() {} });\n", language, filename)
    assert rows == {}


@pytest.mark.parametrize("language,filename", _LANGS)
def test_a_class_field_initializer_is_unchanged(language, filename):
    rows = _rows("class Outer { static Inner = class { im() {} }; }\n", language, filename)
    assert rows["Outer.Inner.im"] == ("method", "Outer"), rows


def test_a_parenthesised_or_cast_class_expression_is_seen_through():
    rows = _rows(
        "const P = (class { p() {} });\nconst Q = class { q() {} } as unknown as X;\n"
        "const R = <T>(class { r() {} });\nconst S = class { s() {} }!;\n"
        "const U = class { u() {} } satisfies Ctor;\n",
        "typescript",
        "a.ts",
    )
    for name in "PQRSU":
        assert rows[name] == ("class", None), (name, rows)
        assert rows[f"{name}.{name.lower()}"] == ("method", name), (name, rows)


def test_ts_export_equals_is_the_commonjs_default():
    rows = _rows("export = class { m() {} };\n", "typescript", "a.ts")
    assert rows == {"default": ("class", None), "default.m": ("method", "default")}


def test_the_signature_is_the_header_never_the_body():
    """A class declaration's signature is its header (`class D extends
    Base`); a bound class expression's is its binder's statement up to the
    body, whitespace collapsed."""
    src = "export const C = class extends Base {\n  x = 1;\n  m() {\n    return 2;\n  }\n};\n"
    sigs = {s.qualified_name: s.signature for s in parse_file(src, "a.js", "javascript")}
    assert sigs["C"] == "export const C = class extends Base"


@pytest.mark.parametrize("language,filename", _LANGS)
def test_a_class_declaration_is_unchanged(language, filename):
    rows = _rows("class Decl { d = 1; dm() {} }\nexport default class Named { nn() {} }\n", language, filename)
    assert rows == {
        "Decl": ("class", None),
        "Decl.d": ("field", "Decl"),
        "Decl.dm": ("method", "Decl"),
        "Named": ("class", None),
        "Named.nn": ("method", "Named"),
    }
