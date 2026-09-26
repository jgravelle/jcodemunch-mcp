"""A C-family prototype list binds every name it declares (#852).

`_extract_symbol` returns one symbol per node and the name reader takes one
declarator, so `int f(int), g(int);` gave `f` and no `g`, in C, C++ and
Arduino alike. #823 fixed the same shape for `typedef int A, B;` with
`_typedef_extra_names`, gated on `type_definition`; a `declaration` with N
function declarators still yielded one symbol. #817's mechanism in its fourth
C-family spelling (#817 Go, #823 typedef, #837 JS/TS `let`, this).

⚠ Only a declarator that is itself a bare prototype binds a function: in
`int f(int), x;` the `x` is a variable and emits nothing, as a lone `int x;`
does. A declaration the grammar could not parse keeps its old answer (#850's
rule). Each extra name is the declaration's bytes under that name, #823's
recorded choice for a typedef list.
"""

import pytest

from jcodemunch_mcp.parser.extractor import parse_file

LANGS = [("c", "a.c"), ("cpp", "a.cpp"), ("arduino", "a.ino")]


def _ids(source: str, language: str, filename: str) -> list[str]:
    return sorted(s.id.split("::", 1)[1] for s in parse_file(source, filename, language))


@pytest.mark.parametrize("language, filename", LANGS)
@pytest.mark.parametrize("decl, ids", [
    ("int f(int), g(int);", ["f#function", "g#function"]),
    ("int f(int), g(int), h(int);", ["f#function", "g#function", "h#function"]),
    ("static int f(int), g(int);", ["f#function", "g#function"]),
    ("int x, f(int), g(int);", ["f#function", "g#function"]),
    ("void (*ga)(int), gb(int), gc(int);", ["gb#function", "gc#function"]),
    ("int f(int), x;", ["f#function"]),
])
def test_every_prototype_in_a_list_is_bound(language, filename, decl, ids):
    assert _ids(decl + "\n", language, filename) == ids


@pytest.mark.parametrize("language, filename, ids", [
    ("cpp", "a.cpp", ["f#function~1", "f#function~2"]),
    ("arduino", "a.ino", ["f#function~1", "f#function~2"]),
    ("c", "a.c", ["f#function"]),
])
def test_an_overload_pair_is_two_ordinal_twins(language, filename, ids):
    """C++ overloads are two declarations under one qualified name. C has no
    overloading, and #835 keeps one C prototype per name."""
    assert _ids("int f(int), f(double);\n", language, filename) == ids


@pytest.mark.parametrize("language, filename", LANGS)
def test_a_block_scope_prototype_list_binds_every_name(language, filename):
    ids = _ids("void h(void) { int f(int), g(int); }\n", language, filename)
    assert ids == ["f#function", "g#function", "h#function"]


@pytest.mark.parametrize("language, filename", [("cpp", "a.cpp"), ("arduino", "a.ino")])
def test_a_namespace_prototype_list_binds_every_name(language, filename):
    assert _ids("namespace N { int f(int), g(int); }\n", language, filename) == ["N.f#function", "N.g#function"]


@pytest.mark.parametrize("language, filename", LANGS)
def test_an_errored_list_keeps_its_old_answer(language, filename):
    assert _ids("int f(int), g(int) @;\n", language, filename) == ["f#function"]


@pytest.mark.parametrize("language, filename", LANGS)
def test_every_name_carries_the_declaration_s_bytes(language, filename):
    rows = {s.name: (s.line, s.byte_offset, s.byte_length) for s in parse_file("int f(int), g(int);\n", filename, language)}
    assert rows["f"] == rows["g"] == (1, 0, 19), rows


@pytest.mark.parametrize("language, filename", [("cpp", "a.cpp"), ("arduino", "a.ino")])
@pytest.mark.parametrize("scope", ["file", "block"])
@pytest.mark.parametrize("decl", [
    "JsonString a(s1), b(s2);",
    # Review round 1: a `*` or `&` wraps the function declarator, and the
    # guard read the wrapper's (absent) parameters.
    "char *a(buf), *b(buf2);",
    "Foo &a(x), &b(y);",
    "Foo a(x), *b(y);",
])
def test_a_constructor_call_pair_gains_no_name(language, filename, scope, decl):
    """`JsonString a(s1), b(s2);` parses exactly like a prototype list, and a
    parameter that is a lone type name cannot be told from an argument, so no
    extra name is bound (measured on real code: four such lines, no real
    prototype list). The first declarator's answer is LEDGER L-21's."""
    source = f"{decl}\n" if scope == "file" else f"void h(void) {{ {decl} }}\n"
    assert not any(i.startswith("b#") for i in _ids(source, language, filename))


@pytest.mark.parametrize("scope", ["file", "block"])
@pytest.mark.parametrize("param", ["Foo", "Foo *", "const Foo *", "struct S", "struct S *", "enum E", "FILE *"])
def test_c_has_no_constructor_call_so_every_prototype_binds(scope, param):
    """Review round 1: the ambiguity is C++'s; C has no constructor call."""
    decl = f"int f(int), g({param});"
    source = f"{decl}\n" if scope == "file" else f"void h(void) {{ {decl} }}\n"
    assert {"f#function", "g#function"} <= set(_ids(source, "c", "a.c"))


@pytest.mark.parametrize("language, filename", [("cpp", "a.cpp"), ("arduino", "a.ino")])
@pytest.mark.parametrize("param", ["Foo *", "const Foo&", "Foo&", "const Foo", "struct S", "struct S *", "enum E"])
def test_a_parameter_no_expression_can_hold_binds_in_cpp(language, filename, param):
    """An abstract pointer or reference, a qualifier and a tagged type cannot
    parse from an argument, so they are not ambiguous."""
    assert "g#function" in _ids(f"int f(int), g({param});\n", language, filename)


@pytest.mark.parametrize("language, filename", LANGS)
def test_unambiguous_parameters_bind(language, filename):
    source = "int f(int), g(struct S *p), k(const char *), m(void), n();\n"
    assert _ids(source, language, filename) == [
        "f#function", "g#function", "k#function", "m#function", "n#function",
    ]


@pytest.mark.parametrize("language, filename", [("cpp", "a.cpp"), ("arduino", "a.ino")])
def test_an_argument_that_looks_like_an_array_type_gains_no_name(language, filename):
    """Measured on fmt: `fmt::string_view lhs(inputs[i]), rhs(inputs[j]);`."""
    ids = _ids("void t() { fmt::string_view lhs(inputs[i]), rhs(inputs[j]); }\n", language, filename)
    assert not any(i.startswith("rhs#") for i in ids), ids


@pytest.mark.parametrize("decl", [
    "JsonString a(s1), b(s2);",
    "char *a(buf), *b(buf2);",
    "string_view a(inputs[i]), b(inputs[j]);",
])
@pytest.mark.parametrize("scope", ["file", "block"])
def test_a_header_walked_as_c_keeps_the_constructor_call_rule(decl, scope):
    """Review round 2: a `.h` with no C++ marker is walked by the C fallback,
    and a C++ header's constructor call must not come back through it."""
    body = f"{decl}\n" if scope == "file" else f"inline void h(void) {{ {decl} }}\n"
    source = '#include "x.h"\n' + body
    assert not any(i.startswith("b#") for i in _ids(source, "cpp", "a.h"))


@pytest.mark.parametrize("language, filename", [("cpp", "a.cpp"), ("arduino", "a.ino"), ("cpp", "a.h")])
@pytest.mark.parametrize("param", ["Foo (*)(int)", "Foo (&)[3]", "Foo[]", "Foo (int)"])
def test_a_nested_pointer_reference_or_empty_array_binds(language, filename, param):
    """Review round 2: an abstract array or function is argument-shaped only
    when nothing inside it is a pointer, a reference, an empty `[]` or a
    parameter list no argument can spell."""
    assert "g#function" in _ids(f"int f(int), g({param});\n", language, filename)
