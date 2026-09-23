"""#823: a C or C++ `typedef int A, B;` indexed `A` and nothing else.

The declaration carries one declarator per name and `_extract_symbol` returns
`Optional[Symbol]`, so whichever node is in `symbol_node_types` yields ONE
symbol however many declarators the source wrote -- #817's mechanism one
language over, in two spec copies (#698). `B` was not mis-kinded or unowned;
it was absent.

⚠ The span decision, made rather than defaulted (the issue asked for one):
every name of a multi-declarator typedef records the DECLARATION's bytes. A C
declarator (`*PP`, `Arr[4]`) does not carry the base type that gives the name
its meaning, unlike a Go `type_spec` (`A int`), so it is not "the widest node
that addresses this name alone" in the sense #817 used; the declaration is
the smallest node that says what `B` is. Nothing joins to a typedef by byte
offset (the receiver pass that made #817's spans load-bearing is Go's), so N
names sharing one span costs a consumer nothing it can observe today, and
this file pins that it is deliberate.
"""

from collections import Counter

import pytest

from jcodemunch_mcp.parser.extractor import parse_file

_LANGUAGES = [("c", "a.c"), ("cpp", "a.cpp")]


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    """Answer the parser, not the developer's config file (#411)."""
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def _types(source, language, filename):
    return [
        (s.name, s.qualified_name, s.byte_offset, s.byte_length)
        for s in parse_file(source, filename, language)
        if s.kind == "type"
    ]


@pytest.mark.parametrize("language, filename", _LANGUAGES)
def test_the_reported_typedef_binds_both_names(language, filename):
    assert [t[0] for t in _types("typedef int A, B;\n", language, filename)] == ["A", "B"]


#: Every declarator shape a typedef list can carry; each row names what it binds.
_SHAPES = [
    pytest.param("typedef int A, B, C;\n", {"A", "B", "C"}, id="three-plain"),
    pytest.param("typedef int *P, **PP;\n", {"P", "PP"}, id="pointers"),
    pytest.param("typedef int Arr[4], Grid[2][3];\n", {"Arr", "Grid"}, id="arrays"),
    pytest.param("typedef struct { int q; } Pt, *PtRef;\n", {"Pt", "PtRef"}, id="anonymous-struct-value-and-pointer"),
    pytest.param("typedef struct Node { int v; } Node, *NodePtr;\n", {"Node", "NodePtr"}, id="named-struct-value-and-pointer"),
    pytest.param("typedef void (*Cb)(int), (*Cb2)(void);\n", {"Cb", "Cb2"}, id="function-pointers"),
    pytest.param("typedef int Plain, (*FnPtr)(void);\n", {"Plain", "FnPtr"}, id="mixed-plain-and-function-pointer"),
    pytest.param("typedef unsigned long ul, *ulp, ula[8];\n", {"ul", "ulp", "ula"}, id="mixed-three-shapes"),
]


@pytest.mark.parametrize("language, filename", _LANGUAGES)
@pytest.mark.parametrize("source, want", _SHAPES)
def test_every_declarator_of_a_typedef_binds_its_name(source, want, language, filename):
    names = [t[0] for t in _types(source, language, filename)]
    assert set(names) >= want, (language, sorted(want - set(names)))


@pytest.mark.parametrize("language, filename", _LANGUAGES)
def test_a_function_pointer_typedef_is_named_by_its_identifier(language, filename):
    """Measured on `main`: C named `typedef void (*Cb)(int);` as the literal
    `(*Cb)` while C++ named it `Cb` -- the C unwrap stopped at the
    parenthesized declarator. Same declarator walk, same answer."""
    assert [t[0] for t in _types("typedef void (*Cb)(int);\n", language, filename)] == ["Cb"]


@pytest.mark.parametrize("language, filename", _LANGUAGES)
def test_c_and_cpp_answer_the_same_names_for_the_same_bytes(language, filename):
    """Two spec copies, one declaration node (#698): whichever fix reaches one
    must reach the other, asserted as equality rather than trusted."""
    source = "typedef int A, *B, C[2];\ntypedef void (*F)(int), (*G)(void);\n"
    assert _types(source, "c", "a.c") == _types(source, "cpp", "a.cpp")


@pytest.mark.parametrize("language, filename", _LANGUAGES)
def test_every_name_is_answered_once_and_nothing_else_is(language, filename):
    """Both directions, with a Counter: a set cannot count (08-27)."""
    source = "typedef int A, B;\ntypedef int C;\n"
    answered = Counter(s.name for s in parse_file(source, filename, language))
    assert answered == Counter(["A", "B", "C"])


@pytest.mark.parametrize("language, filename", _LANGUAGES)
def test_a_single_declarator_typedef_records_the_bytes_it_always_did(language, filename):
    """The common case is byte-identical to `main`: `typedef int C;` is the
    whole declaration, 14 bytes from offset 0."""
    assert _types("typedef int C;\n", language, filename) == [("C", "C", 0, 14)]


@pytest.mark.parametrize("language, filename", _LANGUAGES)
def test_every_name_of_a_list_records_the_declaration_span(language, filename):
    """The decision in the module docstring, pinned: all N names share the
    declaration's bytes, so `get_symbol_source` on `B` returns what `B` is."""
    rows = _types("typedef int A, *B;\n", language, filename)
    assert rows == [("A", "A", 0, 18), ("B", "B", 0, 18)]


_LOCAL = "int f(void) {\n    typedef int L1, L2;\n    L1 a = 0; L2 b = 0; return a + b;\n}\n"


def test_a_c_typedef_list_inside_a_function_binds_under_the_function():
    """A local typedef is legal C; BOTH names are qualified under the function,
    not published as file-scope types."""
    rows = _types(_LOCAL, "c", "a.c")
    assert {r[1] for r in rows} == {"f.L1", "f.L2"}, rows


def test_a_cpp_typedef_list_inside_a_function_is_a_known_gap():
    """A TRACKED gap, never a tolerated one: this FAILS when #833 is fixed.

    Both names are bound (this fix's claim, asserted), but C++ publishes any
    function-local type at file scope with no owner -- `_walk_tree`'s C++
    branch parents only type containers, so a free function's body sees the
    function's own scope. Measured on `main` for struct, typedef and enum
    alike; #798 is the same rule seen from inside a class. Delete this test
    and widen the one above to both languages when #833 closes.
    """
    rows = _types(_LOCAL, "cpp", "a.cpp")
    assert {r[0] for r in rows} == {"L1", "L2"}, rows
    assert {r[1] for r in rows} == {"L1", "L2"}, (
        "C++ now qualifies a function-local typedef, so #833 is fixed -- "
        "delete this test and widen the C one to both languages"
    )


def test_the_separate_line_and_list_spellings_bind_the_same_names():
    """The report's own comparison: two lines already bound both; the list
    must bind the same set."""
    listed = {t[0] for t in _types("typedef int A, B;\n", "c", "a.c")}
    separate = {t[0] for t in _types("typedef int A;\ntypedef int B;\n", "c", "a.c")}
    assert listed == separate == {"A", "B"}
