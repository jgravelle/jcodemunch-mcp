"""A C++ out-of-class member definition is a member of its class (LEDGER L-07).

`class A { int run(); };` then `int A::run() { ... }` published the
declaration as `A.run#method` and the BODY as a bare `run#function` with no
owner: `_extract_cpp_name` kept only the last segment of the declarator's
`qualified_identifier`, so the scope was discarded and the body could not be
found as `A.run`. Pascal (#844) and Objective-C index a body as a method of
its class under the shared qualified name.

The property: a definition whose declarator is qualified (`A::run`,
`ns::A::f`, `B<T>::g`, `O::I::h`, `A::~A`, `V::operator+`) is named by its
full scope, joined to any enclosing namespace, and is
- a `method` owned by the class when the class is in the file;
- a `method` with no owner when the class is not (a `.cpp` beside its `.h`);
- a `function` when the scope is a namespace the file declares.
"""

from __future__ import annotations

import pytest

import jcodemunch_mcp.config as config
from jcodemunch_mcp.parser.extractor import parse_file

# name -> (source, body line, expected qualified name, kind, owner qualified name or None)
CASES = {
    "same-file": (
        "class A { int run(); };\nint A::run() { return 1; }\n",
        2,
        "A.run",
        "method",
        "A",
    ),
    "struct": ("struct S { void f(); };\nvoid S::f() {}\n", 2, "S.f", "method", "S"),
    "header-elsewhere": (
        '#include "a.h"\nint A::run() { return 1; }\n',
        2,
        "A.run",
        "method",
        None,
    ),
    "namespace-class": (
        "namespace ns { class A { void f(); }; }\nvoid ns::A::f() {}\n",
        2,
        "ns.A.f",
        "method",
        "ns.A",
    ),
    "in-namespace-block": (
        "namespace ns {\nclass A { void f(); };\nvoid A::f() {}\n}\n",
        3,
        "ns.A.f",
        "method",
        "ns.A",
    ),
    "constructor": ("class A { A(); };\nA::A() {}\n", 2, "A.A", "method", "A"),
    "destructor": ("class A { ~A(); };\nA::~A() {}\n", 2, "A.~A", "method", "A"),
    "operator": (
        "struct V { V operator+(const V&) const; };\nV V::operator+(const V& o) const { return o; }\n",
        2,
        "V.operator+",
        "method",
        "V",
    ),
    "template": (
        "template <typename T> struct B { void g(); };\ntemplate <typename T> void B<T>::g() {}\n",
        2,
        "B.g",
        "method",
        "B",
    ),
    "nested": (
        "struct O { struct I { void h(); }; };\nvoid O::I::h() {}\n",
        2,
        "O.I.h",
        "method",
        "O.I",
    ),
    "pointer-return": (
        "struct P { int* get(); };\nint* P::get() { return 0; }\n",
        2,
        "P.get",
        "method",
        "P",
    ),
    "namespace-function": (
        "namespace ns { void f(); }\nvoid ns::f() {}\n",
        2,
        "ns.f",
        "function",
        None,
    ),
    "namespace-by-member": (
        "namespace ns { void g(); }\nvoid ns::f() {}\n",
        2,
        "ns.f",
        "function",
        None,
    ),
    # C++17 nested-namespace definitions are two scopes (review of L-07).
    "nested-namespace-function": (
        "namespace a::b { void g(); }\nvoid a::b::f() {}\n",
        2,
        "a.b.f",
        "function",
        None,
    ),
    "nested-namespace-class": (
        "namespace a::b { struct A { void f(); }; }\nvoid a::b::A::f() {}\n",
        2,
        "a.b.A.f",
        "method",
        "a.b.A",
    ),
    "inside-nested-namespace": (
        "namespace a::b {\nstruct A { void f(); };\nvoid A::f() {}\n}\n",
        3,
        "a.b.A.f",
        "method",
        "a.b.A",
    ),
}
FRAMES = {"cpp": "a.cpp", "arduino": "a.ino"}


@pytest.fixture(autouse=True)
def _languages_on(monkeypatch):
    # This box's config can disable languages; the gate is read through
    # config's globals, so patch it there.
    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def _body(symbols, line):
    found = [s for s in symbols if s.line == line and s.kind in ("function", "method")]
    assert len(found) == 1, [(s.id, s.line) for s in symbols]
    return found[0]


@pytest.mark.parametrize("language", sorted(FRAMES))
@pytest.mark.parametrize("case", sorted(CASES))
def test_an_out_of_class_definition_is_named_by_its_scope(case, language):
    source, line, qualified, kind, owner = CASES[case]
    symbols = parse_file(source, FRAMES[language], language)
    body = _body(symbols, line)
    assert (body.qualified_name, body.kind) == (qualified, kind)
    assert body.name == qualified.rsplit(".", 1)[1]
    if owner is None:
        assert body.parent is None
    else:
        parent = next(s for s in symbols if s.id == body.parent)
        assert parent.qualified_name == owner
        assert parent.kind in ("class", "type")


def test_every_body_of_a_class_declared_elsewhere_is_a_method():
    """A `.cpp` beside its `.h`: the first body is a parentless method, and it
    must not read as evidence that `DBImpl` is a namespace, which turned every
    later body into a function (leveldb's `db_impl.cc`, found in the corpus
    diff of the first draft)."""
    source = (
        '#include "db_impl.h"\nnamespace leveldb {\n'
        "Status DBImpl::Recover() { return Status(); }\n"
        "void DBImpl::MaybeIgnoreError(Status* s) const {}\n"
        "DBImpl::~DBImpl() {}\n}\n"
    )
    bodies = [
        s
        for s in parse_file(source, "db_impl.cc", "cpp")
        if s.kind in ("function", "method")
    ]
    assert sorted((s.qualified_name, s.kind, s.parent) for s in bodies) == [
        ("leveldb.DBImpl.MaybeIgnoreError", "method", None),
        ("leveldb.DBImpl.Recover", "method", None),
        ("leveldb.DBImpl.~DBImpl", "method", None),
    ]


@pytest.mark.parametrize(
    "source,qualified,kind",
    [
        ("namespace a {\nvoid a::f() {}\n}\n", "a.f", "function"),
        (
            "namespace testing { namespace internal { class M { void g(); }; } }\n"
            "namespace testing {\nvoid testing::internal::M::g() {}\n}\n",
            "testing.internal.M.g",
            "method",
        ),
        ("namespace a { namespace b {\nvoid a::b::f() {}\n} }\n", "a.b.f", "function"),
    ],
    ids=["same-namespace", "gtest-shape", "two-levels"],
)
def test_a_scope_naming_an_enclosing_namespace_is_that_namespace(
    source, qualified, kind
):
    """C++ looks the first segment up from the innermost enclosing scope
    outward, so inside `namespace testing`, `testing::internal::M::g` is
    `testing.internal.M.g`, never `testing.testing...` (the draft's corpus diff
    found gtest's `testing.testing.internal.MatcherBase`)."""
    name = qualified.rsplit(".", 1)[1]
    named = {
        (s.qualified_name, s.kind)
        for s in parse_file(source, "a.cpp", "cpp")
        if s.name == name
    }
    # The declaration (gtest shape) and the body share one qualified name.
    assert named == {(qualified, kind)}


@pytest.mark.parametrize(
    "source",
    [
        "namespace a::b { struct A { int x; }; }\n",
        "namespace a { namespace b { struct A { int x; }; } }\n",
    ],
    ids=["cpp17", "nested-blocks"],
)
def test_both_spellings_of_a_nested_namespace_name_its_members_alike(source):
    """ID MOVE, disclosed: `namespace a::b` named its members `a::b.A`; it is
    `a.b.A` now, as the two-block spelling always was."""
    pairs = sorted(
        (s.qualified_name, s.kind) for s in parse_file(source, "a.cpp", "cpp")
    )
    assert pairs == [("a.b.A", "type"), ("a.b.A.x", "field")]


def test_a_body_and_its_declaration_are_numbered_like_a_pascal_body():
    """ID MOVE, disclosed: the declaration and the body share a qualified name
    and kind, so both are numbered, as Pascal's are since #844."""
    symbols = parse_file(CASES["same-file"][0], "a.cpp", "cpp")
    ids = sorted(s.id for s in symbols if s.name == "run")
    assert ids == ["a.cpp::A.run#method~1", "a.cpp::A.run#method~2"]


def test_a_type_declared_in_a_body_is_owned_by_the_body():
    """#833: a function body is a scope. The body's new name must reach what
    it declares, so a local struct is owned by `A.run`, not a stale id."""
    source = "class A { void run(); };\nvoid A::run() {\n  struct L { int x; };\n}\n"
    symbols = parse_file(source, "a.cpp", "cpp")
    body = _body(symbols, 2)
    local = next(s for s in symbols if s.name == "L")
    assert local.parent == body.id
    assert local.qualified_name == "A.run.L"


@pytest.mark.parametrize(
    "source",
    [
        "void f() {}\n",
        "void ::f() {}\n",
        "namespace ns { void f() {} }\n",
        "class A { void run() {} };\n",
    ],
    ids=["free", "global-qualifier", "in-namespace", "inline-member"],
)
def test_an_unqualified_definition_is_unchanged(source):
    """The other direction: no qualified declarator, no change."""
    symbols = parse_file(source, "a.cpp", "cpp")
    assert all("::" not in s.name for s in symbols)
    fn = [s for s in symbols if s.kind in ("function", "method")]
    assert fn
    for s in fn:
        if "class A" in source:
            assert (s.qualified_name, s.kind) == ("A.run", "method")
        elif "namespace" in source:
            assert (s.qualified_name, s.kind) == ("ns.f", "function")
        else:
            assert (s.qualified_name, s.kind, s.parent) == ("f", "function", None)
