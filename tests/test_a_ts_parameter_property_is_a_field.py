"""A TypeScript constructor parameter property is a member of its class (#802).

`constructor(public injected: number, private readonly other: string) {}`
declares and assigns two members of the class, and yielded no symbol. It is
the idiomatic way Angular and NestJS declare injected dependencies
(`constructor(private readonly service: FooService) {}`), so for those
codebases it is most of a class's state.

Rulings:
- A constructor parameter carrying an accessibility modifier, `readonly` or
  `override` is a member; a parameter with none is an ordinary parameter.
- Kind by #781's rule: `readonly` is a `constant`, otherwise a `field`.
- ⚠⚠ The owner is the CLASS, never the constructor: `Audit.injected`, parent
  `Audit`. `_walk_tree`'s parent there is the constructor method.
- A parameter of any other method or function is never a member, modifier or
  not (TypeScript rejects one there; the grammar still parses it).
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.parser.extractor import parse_file

_TYPED = [("typescript", "a.ts"), ("tsx", "a.tsx")]


def _ids(source: str, language: str, filename: str):
    symbols = parse_file(source, filename, language)
    by_id = {s.id: s for s in symbols}
    return [
        (s.id.rsplit("::", 1)[1], by_id[s.parent].qualified_name if s.parent in by_id else s.parent)
        for s in symbols
    ]


@pytest.mark.parametrize("language,filename", _TYPED)
def test_the_reported_class_has_its_parameter_properties(language, filename):
    src = "class Audit {\n  constructor(public injected: number, private readonly other: string, plain: number) {}\n}\n"
    assert _ids(src, language, filename) == [
        ("Audit#class", None),
        ("Audit.constructor#method", "Audit"),
        ("Audit.injected#field", "Audit"),
        ("Audit.other#constant", "Audit"),
    ]


@pytest.mark.parametrize("language,filename", _TYPED)
@pytest.mark.parametrize("label,param,expected", [
    ("public", "public a: number", "C.a#field"),
    ("private", "private a: number", "C.a#field"),
    ("protected", "protected a: number", "C.a#field"),
    ("readonly", "readonly a: number", "C.a#constant"),
    ("private readonly", "private readonly a: number", "C.a#constant"),
    ("optional", "private a?: number", "C.a#field"),
    ("default value", "protected a = 1", "C.a#field"),
    ("override", "override a: number", "C.a#field"),
    ("override readonly", "public override readonly a: number", "C.a#constant"),
    ("decorated (NestJS)", "@Inject(TOKEN) private readonly a: Svc", "C.a#constant"),
])
def test_every_parameter_property_shape(language, filename, label, param, expected):
    ids = [i for i, _ in _ids(f"class C {{\n  constructor({param}) {{}}\n}}\n", language, filename)]
    assert ids == ["C#class", "C.constructor#method", expected], label


@pytest.mark.parametrize("language,filename", _TYPED)
def test_a_plain_constructor_parameter_is_not_a_member(language, filename):
    """An ABSENCE assertion: no modifier, no member."""
    ids = [i for i, _ in _ids("class C {\n  constructor(plain: number, opt?: string, d = 1) {}\n}\n", language, filename)]
    assert ids == ["C#class", "C.constructor#method"]


@pytest.mark.parametrize("language,filename", _TYPED)
@pytest.mark.parametrize("source", [
    "class C {\n  m(private a: number) {}\n}\n",
    "class C {\n  static create(public a: number) {}\n}\n",
    # A static method NAMED constructor is not the constructor (review round 1).
    "class C {\n  static constructor(private a: number) {}\n}\n",
    "function f(private a: number) {}\n",
    "const o = { constructor(private a: number) {} };\n",
    "const g = (private a: number) => 1;\n",
])
def test_a_modified_parameter_outside_a_class_constructor_is_not_a_member(language, filename, source):
    """An ABSENCE assertion. TypeScript rejects each of these; the grammar
    parses them, so the channel must ask where the parameter is."""
    names = {s.name for s in parse_file(source, filename, language)}
    assert "a" not in names, names


@pytest.mark.parametrize("language,filename", _TYPED)
def test_a_modified_parameter_nested_in_a_constructor_body_is_not_a_member(language, filename):
    """An ABSENCE assertion. Inside the constructor body the walk's parent is
    still the constructor, so only the parameter's own node can tell an arrow's
    or an object method's parameter from the constructor's."""
    src = (
        "class C {\n  constructor() {\n    use({ m(private a: number) {} });\n"
        "    use({ constructor(private c: number) {} });\n"
        "    use((private b: number) => b);\n    use(function (private d: number) {});\n  }\n}\n"
    )
    ids = [i for i, _ in _ids(src, language, filename)]
    assert not {i.split("#")[0].rsplit(".", 1)[-1] for i in ids} & {"a", "b", "c", "d"}, ids


@pytest.mark.parametrize("language,filename", _TYPED)
def test_the_owner_is_the_class_in_every_class_form(language, filename):
    src = (
        "abstract class Abs {\n  constructor(protected readonly repo: Repo) { super(); }\n}\n"
        "export class Exp {\n  constructor(private svc: Svc) {}\n}\n"
        "export const Bound = class {\n  constructor(public b: number) {}\n};\n"
        "function outer() {\n  class Inner {\n    constructor(private i: number) {}\n  }\n}\n"
    )
    rows = dict(_ids(src, language, filename))
    assert rows["Abs.repo#constant"] == "Abs"
    assert rows["Exp.svc#field"] == "Exp"
    assert rows["Bound.b#field"] == "Bound"
    assert rows["outer.Inner.i#field"] == "outer.Inner"


@pytest.mark.parametrize("language,filename", _TYPED)
@pytest.mark.parametrize("source", [
    # A class expression in a field initializer has no class symbol; its
    # constructor is `Outer.Inner.constructor` parented to `Outer`.
    "class Outer {\n  static Inner = class {\n    constructor(private a: number) {}\n  };\n}\n",
    # An unbound class expression (the mixin) has no class symbol (#803).
    "function M(B) {\n  return class extends B {\n    constructor(private a: number) { super(); }\n  };\n}\n",
    "use(class {\n  constructor(private a: number) {}\n});\n",
])
def test_a_member_of_a_class_with_no_symbol_is_withheld(language, filename, source):
    """An ABSENCE assertion: a member with no class to own it is #698's
    defect, and attributing it to an enclosing class is worse."""
    names = {s.name for s in parse_file(source, filename, language)}
    assert "a" not in names, names


@pytest.mark.parametrize("language,filename", _TYPED)
def test_a_class_declared_inside_a_constructor_owns_its_own_members(language, filename):
    src = "class P {\n  constructor(private a: number) {\n    class Z { constructor(public z: number) {} }\n  }\n}\n"
    rows = dict(_ids(src, language, filename))
    assert rows["P.a#field"] == "P"
    assert rows["P.constructor.Z.z#field"] == "P.constructor.Z"


@pytest.mark.parametrize("language,filename", _TYPED)
def test_a_multiline_constructor_with_comments_and_an_overload(language, filename):
    src = (
        "class Q {\n  constructor(a: string);\n  constructor(\n    // the service\n"
        "    private readonly svc: Svc, /* c */ public x = 1,\n  ) {}\n}\n"
    )
    rows = dict(_ids(src, language, filename))
    assert rows["Q.svc#constant"] == "Q" and rows["Q.x#field"] == "Q"


@pytest.mark.parametrize("language,filename", _TYPED)
def test_a_parameter_property_sits_beside_declared_fields(language, filename):
    src = "class C {\n  declared = 1;\n  constructor(private injected: Svc) {}\n  m() {}\n}\n"
    assert _ids(src, language, filename) == [
        ("C#class", None),
        ("C.declared#field", "C"),
        ("C.constructor#method", "C"),
        ("C.injected#field", "C"),
        ("C.m#method", "C"),
    ]


@pytest.mark.parametrize("language,filename", _TYPED)
def test_a_static_member_sharing_the_name_is_disclosed_not_lost(language, filename):
    """TypeScript lets a static member share an instance member's name. Both
    are published; the static one's id takes an ordinal, as the CHANGELOG
    discloses (review round 1)."""
    src = "class C {\n  static a = 1;\n  constructor(public a: number) {}\n}\n"
    ids = [i for i, _ in _ids(src, language, filename)]
    assert ids == ["C#class", "C.a#field~1", "C.constructor#method", "C.a#field~2"], ids


def test_javascript_is_unchanged():
    """JavaScript has no parameter properties; a plain constructor stays as it was."""
    ids = [i for i, _ in _ids("class C {\n  constructor(a, b = 1) {}\n}\n", "javascript", "a.js")]
    assert ids == ["C#class", "C.constructor#method"]
