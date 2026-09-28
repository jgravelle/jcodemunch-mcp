"""A C++ template specialisation keeps its arguments in its id (LEDGER L-54).

`template <> struct hash<A> {}` and `hash<B>` were both named `hash`, so the
specialisations of one template in a file were `hash#type~1` and `~2`, ids
that depend on source order, and none could be found as `hash<A>`. jjg ruled
on 2026-09-28 that the arguments stay in the id.

The property: a class, struct or union specialisation is named with its
arguments, spelled without whitespace, wherever it is written (file scope,
a namespace, a class body, out of line); the primary template keeps its
bare name; and an out-of-line member body finds the specialisation it
belongs to, or the primary when its scope is the primary's parameters.
"""

from __future__ import annotations

import pytest

import jcodemunch_mcp.config as config
from jcodemunch_mcp.parser.extractor import parse_file


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


@pytest.mark.parametrize(
    "source,expected",
    [
        (
            "template <class T> struct hash;\n"
            "template <> struct hash<A> { int h(); };\n"
            "template <> struct hash<B> { int h(); };\n",
            [
                ("hash<A>#type", "", "type"),
                ("hash<A>.h#method", "hash<A>#type", "method"),
                ("hash<B>#type", "", "type"),
                ("hash<B>.h#method", "hash<B>#type", "method"),
            ],
        ),
        (
            "namespace std {\ntemplate <> struct hash<A> { int h(); };\n}\n",
            [("std.hash<A>#type", "", "type"), ("std.hash<A>.h#method", "std.hash<A>#type", "method")],
        ),
        (
            "class A {\n  template <class T> class B;\n  template <> class B<int> { void f(); };\n};\n",
            [
                ("A#class", "", "class"),
                ("A.B<int>#class", "A#class", "class"),
                ("A.B<int>.f#method", "A.B<int>#class", "method"),
            ],
        ),
        (
            "template <> struct hash< std::pair<int,  int> > { };\n",
            [("hash<std::pair<int,int>>#type", "", "type")],
        ),
        (
            "template <class T> struct B { void f(); };\n"
            "template <class T> struct B<T*> { void f(); };\n",
            [
                ("B#type", "", "type"),
                ("B.f#method", "B#type", "method"),
                ("B<T*>#type", "", "type"),
                ("B<T*>.f#method", "B<T*>#type", "method"),
            ],
        ),
        (
            "template <> union U<int> { int i; float f; };\n",
            [("U<int>#type", "", "type"), ("U<int>.f#field", "U<int>#type", "field"), ("U<int>.i#field", "U<int>#type", "field")],
        ),
    ],
    ids=["two-specialisations", "in-namespace", "in-class", "spacing-normalised", "partial", "union"],
)
def test_a_specialisation_is_named_with_its_arguments(source, expected):
    assert _rows(source) == expected


@pytest.mark.parametrize(
    "out_of_line,inline",
    [
        (
            "template <> struct std::hash<A> { int h(); };\ntemplate <> struct std::hash<B> { int h(); };\n",
            "namespace std {\ntemplate <> struct hash<A> { int h(); };\ntemplate <> struct hash<B> { int h(); };\n}\n",
        ),
        (
            "class A { template <class T> class B; };\ntemplate <> class A::B<int> { void f(); };\n",
            "class A { template <class T> class B; template <> class B<int> { void f(); }; };\n",
        ),
    ],
    ids=["namespace-qualified", "class-qualified"],
)
def test_an_out_of_line_specialisation_is_named_as_the_inline_one(out_of_line, inline):
    assert _rows(out_of_line) == _rows(inline)


def test_out_of_line_bodies_find_their_own_specialisation():
    """`B<T*>::f` belongs to the partial specialisation `B<T*>`; `B<T>::f`
    names the primary, whose scope is its own parameters."""
    source = (
        "template <class T> struct B { void f(); };\n"
        "template <class T> struct B<T*> { void f(); };\n"
        "template <class T> void B<T>::f() {}\n"
        "template <class T> void B<T*>::f() {}\n"
        "template <> struct B<int> { void g(); };\n"
        "void B<int>::g() {}\n"
    )
    bodies = {
        (s.qualified_name, (s.parent or "").split("::", 1)[-1])
        for s in parse_file(source, "a.cpp", "cpp")
        if s.kind == "method"
    }
    assert bodies == {
        ("B.f", "B#type"),
        ("B<T*>.f", "B<T*>#type"),
        ("B<int>.g", "B<int>#type"),
    }


@pytest.mark.parametrize(
    "source,expected",
    [
        pytest.param(
            "size_t hash<A>::h() const { return 0; }\nsize_t hash<B>::h() const { return 1; }\n",
            {("hash<A>.h", None), ("hash<B>.h", None)},
            id="class-in-another-file",
        ),
        pytest.param(
            "template <class T> struct O { template <class U> struct I; };\n"
            "template <> template <> struct O<int>::I<char> { void g(); };\n"
            "void O<int>::I<char>::g() {}\n",
            {("O<int>.I<char>.g", "O<int>.I<char>#type")},
            id="nested-specialisation",
        ),
        pytest.param(
            "template <class T> struct B { void f(); };\n"
            "template <class T> struct B<T*> { void f(); };\n"
            "template <class U> void B<U*>::f() {}\n",
            {("B.f", "B#type"), ("B<T*>.f", "B<T*>#type"), ("B<U*>.f", None)},
            id="unmatched-spelling-is-not-the-primary",
        ),
        pytest.param(
            "template <class... Ts> struct V { void f(); };\ntemplate <class... Ts> void V<Ts...>::f() {}\n",
            {("V.f", "V#type")},
            id="variadic-primary",
        ),
        pytest.param(
            "template <class T, int N> struct Arr { void f(); };\ntemplate <class T, int N> void Arr<T, N>::f() {}\n",
            {("Arr.f", "Arr#type")},
            id="two-parameter-primary",
        ),
    ],
)
def test_a_body_owner_is_resolved_segment_by_segment(source, expected):
    """Review of L-54: a body in a file without its class keeps the
    arguments (never `hash.h~N`); each segment of `O<int>::I<char>` resolves
    on its own; and a spelling matching no specialisation is NOT guessed onto
    the primary -- only a scope whose arguments are the enclosing template's
    own parameters names the primary."""
    methods = {
        (s.qualified_name, (s.parent or "").split("::", 1)[-1] or None)
        for s in parse_file(source, "a.cpp", "cpp")
        if s.kind in ("method", "function")
    }
    assert methods == expected
