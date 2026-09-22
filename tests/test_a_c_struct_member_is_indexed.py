"""#797 / #825: a C struct or union indexed no members at all.

C++ and Arduino got the data-member channel in #755; C's spec is a separate
copy (#698's shape) and was never given it, so `struct S { int x; };` indexed
as the bare name `S` in C while the identical bytes in a `.cpp` file indexed
`S` and `S.x`. A C struct is nothing but fields.

The declarator walk is `_extract_cpp_fields`, shared: the grammars spell a
member identically (`field_declaration`), so this asserts C against C++'s
answer for the SAME source rather than restating each shape by hand -- a
second table would be a second copy of the thing that drifted.
"""

from collections import Counter

import pytest

from jcodemunch_mcp.parser.extractor import parse_file


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    """Answer the parser, not the developer's config file (#411)."""
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def _rows(source: str, language: str, filename: str):
    symbols = parse_file(source, filename, language)
    by_id = {s.id: s for s in symbols}
    return [
        (s.kind, s.qualified_name, by_id[s.parent].name if s.parent else None)
        for s in symbols
    ]


_REPORTED = "struct S { int x; double y, *z; };\nunion U { int a; float b; };\n"


def test_the_reported_struct_and_union_index_their_members_owned():
    assert _rows(_REPORTED, "c", "a.c") == [
        ("type", "S", None),
        ("field", "S.x", "S"),
        ("field", "S.y", "S"),
        ("field", "S.z", "S"),
        ("type", "U", None),
        ("field", "U.a", "U"),
        ("field", "U.b", "U"),
    ]


#: Every declarator shape #755 pinned for C++, plus the two container forms C
#: has and the anonymous-typedef form. One source, parsed as C and as C++.
_SHAPES = [
    pytest.param("struct S { int plain; };\n", id="plain"),
    pytest.param("struct S { int *p, **pp; };\n", id="pointer"),
    pytest.param("struct S { int arr[4]; char grid[2][3]; };\n", id="array"),
    pytest.param("struct S { unsigned flag : 1; int wide : 12; };\n", id="bit-field"),
    pytest.param("struct S { int a, b, c; };\n", id="n-names"),
    pytest.param("struct S { void (*cb)(int); int (*table[3])(void); };\n", id="function-pointer-is-data"),
    pytest.param("struct S { const int limit; volatile int flag; };\n", id="qualified"),
    pytest.param("union U { int i; float f; char bytes[4]; };\n", id="union"),
    pytest.param("typedef struct { int t; } T;\n", id="typedef-anonymous-struct"),
    pytest.param("typedef union { int i; float f; } V;\n", id="typedef-anonymous-union"),
    pytest.param("struct Outer { struct Inner { int q; } in_; int after; };\n", id="nested-named"),
    pytest.param("struct Outer { struct { int q; } anon; };\n", id="nested-anonymous-under-a-member"),
    pytest.param("struct S { struct Other *link; };\n", id="pointer-to-another-struct"),
]


@pytest.mark.parametrize("source", _SHAPES)
def test_c_answers_what_cpp_answers_for_the_same_bytes(source):
    """The channel is shared; the answer must be too. C++'s side is #755's,
    pinned by `test_cpp_data_members.py`, so this need not restate it."""
    c = _rows(source, "c", "a.c")
    cpp = _rows(source, "cpp", "a.cpp")
    assert c == cpp
    assert any(kind == "field" for kind, _, _ in c), c


def test_every_declared_name_is_answered_once_and_nothing_else_is():
    """Both directions of the SET: a Counter, because a set cannot count."""
    source = "struct S { int x; double y, *z; int b : 3; void (*cb)(int); };\n"
    answered = Counter(s.name for s in parse_file(source, "a.c", "c"))
    assert answered == Counter(["S", "x", "y", "z", "b", "cb"])


def test_a_local_or_a_parameter_is_never_a_field():
    """A C local is a `declaration`, a different node; asserted, not trusted."""
    source = (
        "int f(int param) {\n"
        "    int local = param;\n"
        "    struct { int inner; } anon_local;\n"
        "    return local + anon_local.inner;\n"
        "}\n"
    )
    rows = _rows(source, "c", "a.c")
    assert [r for r in rows if r[0] == "field"] == []
    assert ("function", "f", None) in rows


def test_a_file_scope_object_of_an_anonymous_struct_publishes_no_fields():
    """Nothing owns the member (#755's allowlist, `_CPP_ANONYMOUS_TYPE_OWNERS`),
    so it is withheld rather than published bare -- C inherits the C++ rule
    rather than acquiring a different one silently (#825)."""
    rows = _rows("struct { int orphan; } global_obj;\n", "c", "a.c")
    assert [r for r in rows if r[0] == "field"] == []


def test_a_prototype_inside_a_struct_is_not_a_field():
    """C cannot declare a member function, but the grammar accepts the bytes
    and `_cpp_declarator_is_function` is asked per declarator either way."""
    rows = _rows("struct S { int x; int (*fp)(void); };\n", "c", "a.c")
    assert ("field", "S.fp", "S") in rows
    assert ("field", "S.x", "S") in rows


def test_the_nested_struct_keeps_the_qualified_name_it_had():
    """#797 said making the struct a container might move a nested struct's
    id from `In` to `Outer.In`; measured on `main`, it was `Outer.In` already,
    so this pins that no id moved."""
    rows = _rows("struct Outer { struct In { int q; } in_; };\n", "c", "a.c")
    assert ("type", "Outer.In", "Outer") in rows
    assert ("field", "Outer.In.q", "In") in rows
