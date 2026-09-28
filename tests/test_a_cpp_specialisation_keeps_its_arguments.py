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
        # gtest's FloatingPoint<float>::Max: under `template <>` it specialises
        # the PRIMARY's member, so the primary owns it (review of L-54).
        pytest.param(
            "template <class T> class FP { static T Max(); };\n"
            "template <>\ninline float FP<float>::Max() { return 1; }\n"
            "template <>\ninline double FP<double>::Max() { return 2; }\n",
            {("FP.Max", "FP#class"), ("FP<float>.Max", "FP#class"), ("FP<double>.Max", "FP#class")},
            id="member-specialisation",
        ),
        # The same property, two more spellings (review of L-54, round 3): a
        # plain last scope, and an OUTER `template <>` over a member template.
        pytest.param(
            "template <class T> struct O { struct I { void g(); }; };\n"
            "template <> void O<int>::I::g() {}\n",
            {("O.I.g", "O.I#type"), ("O<int>.I.g", "O.I#type")},
            id="member-specialisation-through-a-plain-scope",
        ),
        pytest.param(
            "template <class T> struct A { template <class U> void f(U); };\n"
            "template <> template <class U> void A<int>::f(U) {}\n",
            {("A.f", "A#type"), ("A<int>.f", "A#type")},
            id="member-template-specialisation",
        ),
        # Without `template <>` it is a member of a class specialisation
        # defined elsewhere, never of the primary.
        pytest.param(
            "template <class T> struct B { void g(); };\nvoid B<int>::g() {}\n",
            {("B.g", "B#type"), ("B<int>.g", None)},
            id="member-of-a-specialisation-elsewhere",
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


def test_a_name_the_grammar_cut_short_keeps_its_bare_name():
    """fmt's `use_format_as<T, bool_constant<...<T>>::value>>`: the grammar
    splits the `>>` wrongly and leaves the last `>` in an ERROR, so the node
    is one `>` short. The specialisation keeps its bare name rather than
    publish a truncated one (L-54's corpus diff); a comparison inside
    parentheses (`B2<(1>2)>`) is not a bracket and keeps its arguments."""
    cut = (
        "template <class T, class E> struct U;\n"
        "template <typename T>\n"
        "struct U<\n    T, bool_constant<std::is_arithmetic<R<T>>::value>>\n    : std::true_type {};\n"
    )
    assert [s.qualified_name for s in parse_file(cut, "a.cpp", "cpp")] == ["U"]
    paren = "template <bool B> struct B2;\ntemplate <> struct B2<(1>2)> { void f(); };\n"
    assert [s.qualified_name for s in parse_file(paren, "a.cpp", "cpp")] == ["B2<(1>2)>", "B2<(1>2)>.f"]


def test_a_member_class_specialised_under_template_is_owned_by_the_primary():
    """`template <> template <> struct O<int>::I<char>` specialises the
    primary `O`'s member class, so `O` owns it; its head's own specifier is
    not the enclosing `template <>` it is looked up in (review of L-54)."""
    source = (
        "template <class T> struct O { template <class U> struct I; };\n"
        "template <> template <> struct O<int>::I<char> { void g(); };\n"
    )
    rows = sorted(
        (s.qualified_name, (s.parent or "").split("::", 1)[-1])
        for s in parse_file(source, "a.cpp", "cpp")
    )
    assert rows == [("O", ""), ("O<int>.I<char>", "O#type"), ("O<int>.I<char>.g", "O<int>.I<char>#type")]
