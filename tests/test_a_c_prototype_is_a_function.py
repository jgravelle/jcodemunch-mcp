"""A C function prototype is a `function`, as the same bytes are in C++ (#835).

`int f(int);` in a `.c` file yielded nothing, in every shape, while the
identical bytes in a `.cpp` file yielded `function f`. `C_SPEC` had no
`declaration` row; `CPP_SPEC` has carried one since #755, filtered through
`_is_cpp_function_declaration`. Three spec copies of one grammar shape and
the channel wired into two (#698's shape; #797/#825, #823 and #830 are the
same three copies). Reporter: @jgravelle.

Rulings:
- A file-scope prototype in C is a `function`, C EQUAL to C++ for the same
  bytes (kind, name, span).
- A prototype followed by its definition in the same C file is ONE `f`, the
  definition: C has no overloading, so name equality is exact and the
  prototype is a mention of the definition. C++ keeps its two, decided and
  not inherited: `int f(int); int f(double) {}` are two overloads under one
  qualified name, so dropping the prototype by name would drop a real
  declaration.
- `int a, b;` stays absent in both (no channel for a file-scope variable).
- `int (*fp)(int);` stays absent in C: the declarator binding the name is a
  pointer. C asks per declarator; C++'s subtree rule says `function fp`
  there and that is #850's.
- A block-scope prototype in C declares an external function and stays at
  file scope, as #833 ruled for C++.
- New symbols on unchanged C content, no id moves; `PARSER_GENERATION`
  names it.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.parser.extractor import parse_file


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def _rows(source: str, language: str, filename: str):
    return [
        (s.kind, s.qualified_name, s.byte_offset, s.byte_length)
        for s in parse_file(source, filename, language)
    ]


#: Every prototype shape the issue measured, plus the ones found beside it.
_PROTOTYPES = [
    pytest.param("int f(int);\n", "f", id="plain"),
    pytest.param("void g(struct S *p);\n", "g", id="struct-pointer-param"),
    pytest.param("void h(int *p);\n", "h", id="pointer-param"),
    pytest.param("struct S *make(void);\n", "make", id="struct-pointer-return"),
    pytest.param("int q(void);\n", "q", id="void-params"),
    pytest.param("void r(int a, int b);\n", "r", id="two-params"),
    pytest.param("void s(S_t *p);\n", "s", id="typedef-param"),
    pytest.param("extern int e(int);\n", "e", id="extern"),
    pytest.param("static void st(void);\n", "st", id="static"),
    pytest.param("static inline int si(int x);\n", "si", id="static-inline"),
    pytest.param("int old();\n", "old", id="k-and-r"),
    pytest.param("int *ar(void);\n", "ar", id="pointer-return"),
    pytest.param("int (*fr(void))[3];\n", "fr", id="pointer-to-array-return"),
]


@pytest.mark.parametrize("source, name", _PROTOTYPES)
def test_a_file_scope_prototype_is_a_function(source, name):
    assert _rows(source, "c", "a.c") == [("function", name, 0, len(source) - 1)]


@pytest.mark.parametrize("source, name", _PROTOTYPES)
def test_c_answers_what_cpp_answers_for_the_same_bytes(source, name):
    """The issue's own bar: equality, kind, name and span."""
    assert _rows(source, "c", "a.c") == _rows(source, "cpp", "a.cpp")


def test_a_prototype_followed_by_its_definition_is_one_function_in_c():
    """The definition is the symbol; the prototype is a mention of it."""
    source = "int f(int);\nint f(int a) { return a; }\n"
    assert _rows(source, "c", "a.c") == [("function", "f", 12, 26)]


def test_a_prototype_after_its_definition_is_still_one_function_in_c():
    source = "int f(int a) { return a; }\nint f(int);\n"
    assert _rows(source, "c", "a.c") == [("function", "f", 0, 26)]


def test_cpp_keeps_both_for_the_same_bytes_because_of_overloading():
    """Decided, not inherited: `int f(int); int f(double) {}` are two
    overloads under one qualified name in C++, so a by-name drop would lose
    a real declaration. The C++ twin is the overload problem, not #835's."""
    source = "int f(int);\nint f(int a) { return a; }\n"
    assert _rows(source, "cpp", "a.cpp") == [("function", "f", 0, 11), ("function", "f", 12, 26)]


def test_a_prototype_whose_definition_is_elsewhere_is_kept_in_c():
    """Only a definition in the SAME file absorbs the prototype."""
    source = "int f(int);\nint g(int a) { return a; }\n"
    assert _rows(source, "c", "a.c") == [("function", "f", 0, 11), ("function", "g", 12, 26)]


def test_a_file_scope_variable_is_not_swept_in():
    """Decided, not changed by accident: no channel for a file-scope variable."""
    source = "int a, b;\nstatic int c = 1;\nextern int d;\n"
    assert _rows(source, "c", "a.c") == []
    assert _rows(source, "cpp", "a.cpp") == []


def test_a_function_pointer_variable_is_not_a_function_in_c():
    """The declarator binding `fp` is a pointer, not a function. C++ answers
    `function fp` from a subtree rule; that is #850's, pinned here as the
    one deliberate inequality."""
    source = "int (*fp)(int);\n"
    assert _rows(source, "c", "a.c") == []
    assert _rows(source, "cpp", "a.cpp") == [("function", "fp", 0, 15)]


def test_a_block_scope_prototype_stays_at_file_scope_in_c():
    """As #833 ruled for C++: it declares an external function."""
    source = "void f(void) { int g(int); }\n"
    rows = [(s.kind, s.qualified_name, s.parent) for s in parse_file(source, "a.c", "c")]
    assert rows == [("function", "f", None), ("function", "g", None)]
    assert _rows(source, "c", "a.c") == _rows(source, "cpp", "a.cpp")


def test_a_multi_declarator_prototype_binds_what_cpp_binds():
    """Measured, pinned as found: both languages bind the FIRST name only
    (#817's mechanism; C++'s answer predates this change)."""
    source = "int f(int), g(int);\n"
    assert _rows(source, "c", "a.c") == _rows(source, "cpp", "a.cpp") == [("function", "f", 0, 19)]


def test_definitions_types_and_typedefs_are_unchanged_in_c():
    source = (
        "typedef int (*Cb)(int);\n"
        "struct S { int x; } sv;\n"
        "int top(void) { return 0; }\n"
    )
    assert _rows(source, "c", "a.c") == [
        ("type", "Cb", 0, 23),
        ("type", "S", 24, 19),
        ("field", "S.x", 35, 6),
        ("function", "top", 48, 27),
    ]
