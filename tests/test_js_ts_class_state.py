"""#781: a JavaScript, TypeScript or TSX class field yielded no symbol.

`tally = 0;` in a class body was absent, so a class read as methods-only, and a
React class component's arrow-function handlers (`onDone = () => {}`) were
absent with it. The grammars spell the member `field_definition` (JS) and
`public_field_definition` (TS, TSX), and no channel named either.

The rule, per the owner's 2026-09-19 ruling that class state is indexed:

- a field whose value is a function is a `method`, the way a module-level
  `const f = () => {}` is already a `function`;
- a TypeScript `readonly` field is a `constant`, because the language says so;
- anything else is a `field`. JavaScript has no immutable field, so no JS
  member is a `constant` by this channel.

A computed key (`['k'] = 1`) is not a name a reader can look up and adds nothing.
"""

from collections import Counter

import pytest

from jcodemunch_mcp.parser.extractor import parse_file

_ALL = [("javascript", "a.js"), ("typescript", "a.ts"), ("tsx", "a.tsx")]
_TYPED = [("typescript", "a.ts"), ("tsx", "a.tsx")]


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    """Answer the parser, not the developer's config file (#411)."""
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def _rows(source, language, filename):
    symbols = parse_file(source, filename, language)
    by_id = {s.id: s for s in symbols}
    return [
        (s.kind, s.qualified_name, by_id[s.parent].name if s.parent in by_id else s.parent)
        for s in symbols
    ]


@pytest.mark.parametrize("language, filename", _ALL)
def test_the_reported_javascript_class_has_its_state(language, filename):
    source = "class Audit {\n  tally = 0;\n  runIt() { return 1; }\n  get view() { return 2; }\n}\n"
    assert _rows(source, language, filename) == [
        ("class", "Audit", None),
        ("field", "Audit.tally", "Audit"),
        ("method", "Audit.runIt", "Audit"),
        ("method", "Audit.view", "Audit"),
    ]


@pytest.mark.parametrize("language, filename", _TYPED)
def test_the_reported_typescript_class_has_its_state(language, filename):
    source = (
        "class Audit {\n  readonly limit: number = 3;\n  tally: number = 0;\n"
        "  runIt(): number { return 1; }\n  get view(): number { return 2; }\n}\n"
    )
    assert _rows(source, language, filename) == [
        ("class", "Audit", None),
        ("constant", "Audit.limit", "Audit"),
        ("field", "Audit.tally", "Audit"),
        ("method", "Audit.runIt", "Audit"),
        ("method", "Audit.view", "Audit"),
    ]


@pytest.mark.parametrize("label, member, expected", [
    ("static", "static COUNT = 1;", ("field", "COUNT")),
    ("private", "#secret = 2;", ("field", "#secret")),
    ("static private", "static #hidden = 3;", ("field", "#hidden")),
    ("no initialiser", "bare;", ("field", "bare")),
    ("arrow function", "onDone = () => {};", ("method", "onDone")),
    ("function expression", "handler = function () {};", ("method", "handler")),
    ("async arrow", "load = async () => {};", ("method", "load")),
])
def test_every_javascript_field_shape(label, member, expected):
    symbols = parse_file(f"class C {{\n  {member}\n}}\n", "a.js", "javascript")
    assert [(s.kind, s.name) for s in symbols] == [("class", "C"), expected], label


@pytest.mark.parametrize("language, filename", _TYPED)
@pytest.mark.parametrize("label, member, expected", [
    ("readonly", "readonly limit: number = 3;", ("constant", "limit")),
    ("static readonly", "static readonly MAX = 10;", ("constant", "MAX")),
    ("private optional", "private secret?: string;", ("field", "secret")),
    ("declare", "declare ambient: number;", ("field", "ambient")),
    ("definite", "sure!: number;", ("field", "sure")),
    ("typed arrow", "onDone = (): void => {};", ("method", "onDone")),
    # A function value wins over `readonly`, the way a module-level
    # `const f = () => {}` is a `function` and not a `constant`.
    ("readonly arrow", "readonly cb = () => {};", ("method", "cb")),
])
def test_every_typescript_field_shape(language, filename, label, member, expected):
    symbols = parse_file(f"class C {{\n  {member}\n}}\n", filename, language)
    assert [(s.kind, s.name) for s in symbols] == [("class", "C"), expected], label


@pytest.mark.parametrize("language, filename", _TYPED)
def test_an_abstract_field_is_state_of_the_abstract_class(language, filename):
    source = "abstract class Base {\n  abstract shape: string;\n  abstract area(): number;\n}\n"
    assert _rows(source, language, filename) == [
        ("class", "Base", None),
        ("field", "Base.shape", "Base"),
        ("method", "Base.area", "Base"),
    ]


@pytest.mark.parametrize("language, filename", _ALL)
def test_a_function_field_never_renames_the_real_method_it_shadows(language, filename):
    """Review measured this on NestJS: `use = (...) => {}` beside `use() {}`
    turned the published `use#method` into `use#method~1`. A class's real
    method keeps the id it had; the field that shadows it is a `field`."""
    source = "class A {\n  use() {}\n  use = () => {};\n  other = () => {};\n}\n"
    symbols = parse_file(source, filename, language)
    assert [(s.kind, s.name, s.id.rsplit("::", 1)[1]) for s in symbols] == [
        ("class", "A", "A#class"),
        ("method", "use", "A.use#method"),
        ("field", "use", "A.use#field"),
        ("method", "other", "A.other#method"),
    ]


@pytest.mark.parametrize("language, filename", _ALL)
@pytest.mark.parametrize("source", [
    "new (class { x = 1; })();\n",
    "use(class { z = 1; });\n",
    "function f() { return class { w = 1; }; }\n",
    "class M { meth() { return class { inM = 1; }; } }\n",
])
def test_a_field_with_no_class_symbol_to_own_it_is_not_published(language, filename, source):
    """An ABSENCE assertion. A class EXPRESSION nothing binds has no symbol,
    so its field came out bare (`x`, parent None) or owned by whatever
    function enclosed it (`f.w`, `M.meth.inM`). A member with no owner is
    #698's defect.

    ⚠ Since #803 a BOUND class expression (`const C = class {}`, `export
    default class {}`) is a class symbol that owns its fields
    (`tests/test_a_js_class_expression_is_a_class.py`), so the two bound
    samples this listed became unbound ones (Practice 9: they pinned the gap)."""
    assert [s for s in parse_file(source, filename, language) if s.kind in ("field", "constant")
            and s.name in ("x", "z", "w", "inM")] == []


@pytest.mark.parametrize("language, filename", _ALL)
def test_a_string_or_numeric_key_adds_nothing(language, filename):
    """Stated with the computed key: neither is an identifier a reader types."""
    symbols = parse_file("class C {\n  'quoted' = 1;\n  0 = 2;\n}\n", filename, language)
    assert [(s.kind, s.name) for s in symbols] == [("class", "C")]


@pytest.mark.parametrize("language, filename", _ALL)
def test_a_computed_key_adds_nothing(language, filename):
    """An absence assertion: `['k']` is an expression, not a name."""
    symbols = parse_file("class C {\n  ['k'] = 1;\n  [Symbol.iterator] = null;\n}\n", filename, language)
    assert [(s.kind, s.name) for s in symbols] == [("class", "C")]


@pytest.mark.parametrize("language, filename", _ALL)
def test_what_a_field_holds_is_not_attributed_to_the_class(language, filename):
    """#571's guard, which this channel must not undo: the inside of an
    initialiser belongs to the field, never to the class beside it."""
    source = (
        "class Host {\n"
        "  onDone = () => { const inner = 1; function helper() {} };\n"
        "  table = { real() {} };\n"
        "  real() {}\n"
        "}\n"
    )
    symbols = parse_file(source, filename, language)
    owned_by_host = sorted(s.name for s in symbols if s.qualified_name == f"Host.{s.name}")
    assert owned_by_host == ["onDone", "real", "table"]
    assert Counter(s.name for s in symbols)["real"] <= 2
    assert all("~" not in s.id for s in symbols)


@pytest.mark.parametrize("language, filename", _ALL)
def test_a_method_is_a_method_once_and_nothing_is_emitted_twice(language, filename):
    source = "class C {\n  x = 1;\n  m() {}\n  static s() {}\n  get g() { return 1; }\n}\n"
    symbols = parse_file(source, filename, language)
    assert Counter(s.name for s in symbols) == Counter(["C", "x", "m", "s", "g"])
    assert {s.name: s.kind for s in symbols}["x"] == "field"


@pytest.mark.parametrize("language, filename", _ALL)
def test_a_binding_in_a_method_or_a_static_block_is_never_class_state(language, filename):
    source = "class C {\n  m() { let local = 1; this.attr = 2; }\n  static { this.z = 1; let sb = 2; }\n}\n"
    assert [s.name for s in parse_file(source, filename, language)] == ["C", "m"]


def test_module_level_bindings_and_object_literals_are_untouched():
    source = "const MAX = 1;\nlet count = 0;\nconst obj = { key: 1, fn() {} };\n"
    before = [(s.kind, s.name) for s in parse_file(source, "a.js", "javascript")]
    assert ("constant", "MAX") in before and ("field", "key") not in before


def test_the_three_specs_carry_the_channel():
    from jcodemunch_mcp.parser.languages import LANGUAGE_REGISTRY

    assert LANGUAGE_REGISTRY["javascript"].field_patterns == ["field_definition"]
    assert LANGUAGE_REGISTRY["typescript"].field_patterns == ["public_field_definition"]
    assert LANGUAGE_REGISTRY["tsx"].field_patterns == ["public_field_definition"]
