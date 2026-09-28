"""A C++ class defined with a qualified name is its owner's member (LEDGER L-46).

The pimpl idiom declares a nested class and defines it outside:
`class Widget { class Impl; };` then `class Widget::Impl { void go(); };`.
The class was indexed under its last segment, `Impl#class`, with members
`Impl.go`, so the out-of-line body `void Widget::Impl::go() {}` (named
`Widget.Impl.go` since L-07) found no owner and shared no name with its own
declaration. A deeper qualifier was worse: `struct a::W::I` became
`W::I#type`, a name holding `::`.

The property: a class defined out of line with a qualified name publishes
exactly the ids, parents and kinds of the same class defined inline in its
owner, and its out-of-line member bodies are owned by it.
"""

from __future__ import annotations

import pytest

import jcodemunch_mcp.config as config
from jcodemunch_mcp.parser.extractor import parse_file

# (out of line, the same class written inline in its owner)
PAIRS = {
    "class": (
        "class Widget { class Impl; };\nclass Widget::Impl { void go(); int n; };\n",
        "class Widget { class Impl { void go(); int n; }; };\n",
    ),
    "struct": (
        "class Widget { struct Impl; };\nstruct Widget::Impl { void go(); };\n",
        "class Widget { struct Impl { void go(); }; };\n",
    ),
    "two-levels": (
        "class A { class B { class C; }; };\nclass A::B::C { void f(); };\n",
        "class A { class B { class C { void f(); }; }; };\n",
    ),
    "namespace-qualified": (
        "namespace a { class W { struct I; }; }\nstruct a::W::I { int x; void f(); };\n",
        "namespace a { class W { struct I { int x; void f(); }; }; }\n",
    ),
    "inside-namespace": (
        "namespace a {\nclass W { struct I; };\nstruct W::I { void f(); };\n}\n",
        "namespace a {\nclass W { struct I { void f(); }; };\n}\n",
    ),
    "union": (
        "class A { union U; };\nunion A::U { int i; float f; };\n",
        "class A { union U { int i; float f; }; };\n",
    ),
    "final-with-base": (
        "class A { class B; };\nclass A::B final : public Base { void f(); };\n",
        "class A { class B final : public Base { void f(); }; };\n",
    ),
    "specialisation": (
        "class A { template <class T> class B; };\ntemplate <> class A::B<int> { void f(); };\n",
        "class A { template <class T> class B; template <> class B<int> { void f(); }; };\n",
    ),
    "enum": (
        "class A { enum class E; void a(); };\nenum class A::E { X, Y };\n",
        "class A { enum class E { X, Y }; void a(); };\n",
    ),
    "export-macro": (
        "class API A { class B; };\nclass API A::B { void f(); };\n",
        "class API A { class B { void f(); }; };\n",
    ),
    "global-qualifier": (
        "class A { class B; };\nclass ::A::B { void f(); };\n",
        "class A { class B { void f(); }; };\n",
    ),
    "global-qualifier-in-namespace": (
        "namespace n { class A { class B; }; }\nclass ::n::A::B { int y; void f(); };\n",
        "namespace n { class A { class B { int y; void f(); }; }; }\n",
    ),
    # Two specialisations of one template in a namespace are named as the
    # inline form names them: `std.hash<A>` and `std.hash<B>` since L-54.
    "two-specialisations": (
        "template <> struct std::hash<A> { int h(); };\ntemplate <> struct std::hash<B> { int h(); };\n",
        "namespace std {\ntemplate <> struct hash<A> { int h(); };\ntemplate <> struct hash<B> { int h(); };\n}\n",
    ),
    "nested-in-definition": (
        "class W { class I; };\nclass W::I { class J { void j(); }; };\n",
        "class W { class I { class J { void j(); }; }; };\n",
    ),
}


@pytest.fixture(autouse=True)
def _languages_on(monkeypatch):
    # This box's config can disable languages; the gate is read through
    # config's globals, so patch it there.
    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def _rows(source: str, filename: str = "a.cpp", language: str = "cpp"):
    return sorted(
        (s.id.split("::", 1)[1], (s.parent or "").split("::", 1)[-1], s.kind)
        for s in parse_file(source, filename, language)
    )


@pytest.mark.parametrize("frame", [("a.cpp", "cpp"), ("a.h", "cpp"), ("a.ino", "arduino")], ids=["cpp", "header", "arduino"])
@pytest.mark.parametrize("case", sorted(PAIRS))
def test_an_out_of_line_class_publishes_what_the_inline_class_publishes(case, frame):
    out_of_line, inline = PAIRS[case]
    expected = _rows(inline, *frame)
    assert len(expected) >= 3, expected
    assert _rows(out_of_line, *frame) == expected


def test_the_pimpl_body_is_owned_by_the_nested_class():
    source = (
        "class Widget { class Impl; };\n"
        "class Widget::Impl { void go(); };\n"
        "void Widget::Impl::go() {}\n"
    )
    symbols = parse_file(source, "a.cpp", "cpp")
    impl = next(s for s in symbols if s.name == "Impl")
    assert impl.qualified_name == "Widget.Impl"
    assert impl.parent is not None and impl.parent.endswith("Widget#class")
    go = [s for s in symbols if s.name == "go"]
    assert len(go) == 2, [s.id for s in go]
    assert {s.qualified_name for s in go} == {"Widget.Impl.go"}
    assert all(s.parent == impl.id and s.kind == "method" for s in go), [(s.id, s.parent) for s in go]


@pytest.mark.parametrize(
    "source,qualified",
    [
        ("class Widget::Impl { void go(); };\n", "Widget.Impl"),
        ("namespace n { class A::B { void g(); }; }\n", "n.A.B"),
        ("template <class T> class Outer<T>::Inner { void h(); };\n", "Outer.Inner"),
    ],
    ids=["owner-in-another-file", "owner-in-another-file-in-namespace", "template-owner"],
)
def test_an_owner_outside_the_file_still_qualifies_the_class(source, qualified):
    """The owner is declared in a header the file includes: the class keeps
    its full name and has no parent, as an out-of-line method body does."""
    symbols = parse_file(source, "a.cpp", "cpp")
    cls = next(s for s in symbols if s.kind in ("class", "type"))
    assert (cls.qualified_name, cls.parent) == (qualified, None)
    members = [s for s in symbols if s is not cls]
    assert members and all(s.parent == cls.id for s in members), [(s.id, s.parent) for s in members]


def test_a_global_qualifier_names_no_scope():
    """`class ::Top` is a file-scope class: `::` alone names no owner."""
    assert _rows("class ::Top { void t(); };\n") == _rows("class Top { void t(); };\n")


def test_a_qualified_type_is_no_evidence_of_a_namespace():
    """leveldb's `db_impl.cc` defines `struct DBImpl::Writer` and then every
    `DBImpl::` body. L-07 reads a scope that a file-scope symbol is qualified
    under as a NAMESPACE, and the first draft of L-46 made the struct such a
    symbol, so every body after it became a `function` (L-46's corpus diff).
    The bodies stay methods of a class declared in another file."""
    body = "namespace leveldb {\nvoid DBImpl::Recover() {}\n}\n"
    with_type = "namespace leveldb {\nstruct DBImpl::Writer { int x; };\nvoid DBImpl::Recover() {}\n}\n"
    recover = [
        (s.kind, s.parent)
        for src in (body, with_type)
        for s in parse_file(src, "db_impl.cc", "cpp")
        if s.name == "Recover"
    ]
    assert recover == [("method", None), ("method", None)]


@pytest.mark.parametrize(
    "source",
    [
        "namespace n { struct W::I { int x; }; }\nvoid n::f() {}\n",
        "namespace a::b { struct W::I { int x; }; }\nvoid a::b::f() {}\n",
        # One type defined twice (`#ifdef` branches) in two scopes: the later
        # definition must not erase the earlier one's namespace (review).
        "namespace a { struct W::I { int x; }; }\n#ifdef X\nstruct a::W::I { int y; };\n#endif\nvoid a::f() {}\n",
    ],
    ids=["enclosing-namespace", "cxx17-enclosing-namespace", "defined-twice"],
)
def test_the_namespace_enclosing_a_qualified_type_is_still_evidence(source):
    """Review of L-46: the first fix dropped the whole qualified type from
    L-07's namespace evidence, so `n::f` went from `main`'s function to a
    parentless method. Only its QUALIFIER is no evidence; the namespace it
    is defined in is."""
    f = [(s.kind, s.parent) for s in parse_file(source, "a.cpp", "cpp") if s.name == "f"]
    assert f == [("function", None)]
