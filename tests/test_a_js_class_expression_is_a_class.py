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
  export, so `default`; a NAMED default export keeps its own name, as
  `export default class Named {}` does). Its methods and fields hang off it, as they do off
  a class declaration. Parenthesised and TS `as`/`satisfies`/`!` wrappers
  are seen through.
- A class expression NOTHING binds (`new (class {...})()`, `return class
  {...}`, an argument, an object-literal value) has no name to borrow, so no
  class symbol, and its methods keep exactly what `main` published: bare at
  module level, qualified under the enclosing function inside one (#781
  still withholds its fields). ⚠⚠ Withholding the methods was the first
  ruling and review reversed it: the TypeScript mixin (`return class extends
  Base { stampNow() {} }`) then made `search_symbols("stampNow")` answer a
  confident ABSENT for a method that exists and `main` found. A false absence
  claim is worse than lexical nesting, which is what the issue's "not
  qualified under the function" asked to remove.
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
def test_an_unbound_class_expression_inside_a_function_keeps_its_lexical_methods(language, filename):
    """Review reversed the first ruling: the method stays findable as `f.k`,
    lexically nested under the function that produces the class, exactly as
    `main` published it; the field stays withheld (#781)."""
    rows = _rows("function f() { return class { w = 1; k() {} }; }\n", language, filename)
    assert rows == {"f": ("function", None), "f.k": ("method", "f")}


@pytest.mark.parametrize("language,filename", _LANGS)
def test_a_mixin_method_stays_findable(language, filename):
    """The shape that reversed the ruling: withheld, `stampNow` read as a
    confident absence to `search_symbols`."""
    src = (
        "export function Timestamped(Base) {\n"
        "  return class extends Base {\n    stampNow() { return 1; }\n  };\n}\n"
        "export const Activatable = (Base) => class extends Base {\n  activate() {}\n};\n"
    )
    names = {s.name for s in parse_file(src, filename, language)}
    assert {"stampNow", "activate"} <= names, names


@pytest.mark.parametrize("language,filename", _LANGS)
def test_a_bound_class_expression_inside_a_function_is_owned_by_the_function(language, filename):
    rows = _rows("function f() { const L = class { k() {} }; return L; }\n", language, filename)
    assert rows["f.L"] == ("class", "f"), rows
    assert rows["f.L.k"] == ("method", "f.L"), rows


@pytest.mark.parametrize("language,filename", _LANGS)
def test_an_unbound_class_expression_keeps_what_main_published(language, filename):
    """No class symbol and no field; the methods bare, as before."""
    rows = _rows("new (class { a = 1; b() {} })();\nuse(class { c() {} });\n", language, filename)
    assert rows == {"b": ("method", None), "c": ("method", None)}


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


@pytest.mark.parametrize("language,filename", _LANGS)
@pytest.mark.parametrize("source", [
    "module.exports = class UserService extends Base { find() {} };\n",
    "export default (class UserService { find() {} });\n",
])
def test_a_named_default_export_keeps_its_own_name(language, filename, source):
    """Review round 2: `default` dropped the name, so `UserService` read as
    absent, where `export default class Named {}` has always been `Named`."""
    rows = _rows(source, language, filename)
    assert rows == {"UserService": ("class", None), "UserService.find": ("method", "UserService")}


def test_a_named_ts_export_equals_keeps_its_own_name():
    rows = _rows("export = class UserService { find() {} };\n", "typescript", "a.ts")
    assert rows == {"UserService": ("class", None), "UserService.find": ("method", "UserService")}


@pytest.mark.parametrize("language,filename", _LANGS)
def test_a_destructuring_declarator_keeps_the_binding_main_published(language, filename):
    """Review round 2: the binding channel dropped `X` because the value is a
    class, while the walk emits no class for a non-identifier name."""
    ids = [s.id.rsplit("::", 1)[1] for s in parse_file("const {X} = class { s() {} };\n", filename, language)]
    assert ids == ["X#constant", "s#method"], ids


@pytest.mark.parametrize("language,filename", _LANGS)
@pytest.mark.parametrize("source", [
    "const C = class { m() {} };\n",
    "let C = class { m() {} };\n",
    "export const C = class { m() {} };\n",
    "const A = 1, C = class { m() {} };\n",
])
def test_a_bound_class_is_never_also_a_binding(language, filename, source):
    """The full list, never a dict keyed by name: `C#constant` beside
    `C#class` would collapse into one key (a set cannot count)."""
    ids = [s.id.rsplit("::", 1)[1] for s in parse_file(source, filename, language)]
    assert ids.count("C#class") == 1 and not any(i.startswith("C#") and i != "C#class" for i in ids), ids


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
