"""#755: a C++ (and Arduino) data member yielded no symbol.

The grammar spells a data member and a member function prototype with ONE node
type, `field_declaration`, told apart by a `function_declarator`. The spec
claimed the node type for functions, so everything the function path declined
-- every data member -- had no channel to fall to, and a struct that is nothing
but data indexed as an empty name. #735 in a second language family.

The two channels ask ONE predicate (`_is_cpp_function_declaration`), so a
declaration is a method or a field and never both.
"""

from collections import Counter

import pytest

from jcodemunch_mcp.parser.extractor import parse_file

_LANGUAGES = [("cpp", "a.cpp"), ("arduino", "a.ino")]


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    """Answer the parser, not the developer's config file (#411)."""
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def _rows(source, language, filename):
    symbols = parse_file(source, filename, language)
    by_id = {s.id: s for s in symbols}
    return [
        (s.kind, s.qualified_name, by_id[s.parent].name if s.parent else None)
        for s in symbols
    ]


@pytest.mark.parametrize("language, filename", _LANGUAGES)
def test_the_reported_member_is_a_field_owned_by_its_class(language, filename):
    assert _rows("class Holder { int probe; };\n", language, filename) == [
        ("class", "Holder", None),
        ("field", "Holder.probe", "Holder"),
    ]


@pytest.mark.parametrize("language, filename", _LANGUAGES)
def test_a_struct_of_nothing_but_data_is_not_an_empty_name(language, filename):
    rows = _rows("struct Point { int x; double y; };\n", language, filename)
    assert [r for r in rows if r[0] == "field"] == [
        ("field", "Point.x", "Point"),
        ("field", "Point.y", "Point"),
    ]


@pytest.mark.parametrize("language, filename", _LANGUAGES)
def test_one_declaration_binding_several_names_yields_each(language, filename):
    """`int a, b, *c;` is one node and three members, which is why this is the
    N-names channel and not a `symbol_node_types` entry."""
    rows = _rows("class H { int a, b, *c; };\n", language, filename)
    assert [q for k, q, _ in rows if k == "field"] == ["H.a", "H.b", "H.c"]


@pytest.mark.parametrize("label, member, name", [
    ("array", "int arr[3];", "arr"),
    ("two-dimensional array", "int grid[2][3];", "grid"),
    ("pointer", "int *p;", "p"),
    ("pointer to pointer", "char **argv;", "argv"),
    ("reference", "int &ref;", "ref"),
    ("default value", "int init = 0;", "init"),
    ("brace initialiser", "int brace{1};", "brace"),
    ("const", "const int limit = 3;", "limit"),
    ("static const", "static const int K = 1;", "K"),
    ("static", "static int s;", "s"),
    ("mutable", "mutable int m;", "m"),
    ("bit-field", "int bits : 3;", "bits"),
    ("template type", "std::vector<int> v;", "v"),
])
def test_every_declarator_shape_names_its_member(label, member, name):
    rows = _rows(f"class H {{\n  {member}\n}};\n", "cpp", "a.cpp")
    assert rows == [("class", "H", None), ("field", f"H.{name}", "H")], label


@pytest.mark.parametrize("language, filename", _LANGUAGES)
@pytest.mark.parametrize("member, name", [
    ("void proto();", "proto"),
    ("virtual void pv() = 0;", "pv"),
    ("int (*getfp())(int);", "getfp"),
    ("int operator+(int);", "operator+"),
])
def test_a_member_function_is_a_method_once_and_never_a_field(language, filename, member, name):
    """The over-emission direction, written first: the two forms share a node
    type, so a channel without the shared predicate emits a prototype twice."""
    symbols = parse_file(f"class H {{\n  {member}\n}};\n", filename, language)
    assert [(s.kind, s.name) for s in symbols] == [("class", "H"), ("method", name)]
    assert all("~" not in s.id for s in symbols)


def test_both_channels_ask_the_one_predicate():
    """A declaration is a method or a field, never both and never neither, on
    a class holding every shape at once. A Counter, because a set cannot count
    a member emitted twice."""
    source = (
        "class H {\npublic:\n  int plain;\n  int a, b;\n  void proto();\n"
        "  int (*getfp())(int);\n  static const int K = 1;\n  H();\n  ~H();\n"
        "  void inline_def() { int local; }\n};\n"
    )
    symbols = parse_file(source, "a.cpp", "cpp")
    assert Counter(s.name for s in symbols) == Counter(
        ["H", "plain", "a", "b", "proto", "getfp", "K", "H", "~H", "inline_def"]
    )
    kinds = {s.name: s.kind for s in symbols if s.name != "H"}
    assert {n for n, k in kinds.items() if k == "field"} == {"plain", "a", "b", "K"}


def test_a_local_variable_is_never_a_field():
    """C++ spells a local `declaration`, a different node type, so no scope
    gate is needed. Asserted rather than trusted (#732 shipped the other case)."""
    source = "void f() { int local; int a, b; }\nint global_var;\n"
    assert [s.name for s in parse_file(source, "a.cpp", "cpp")] == ["f"]


def test_a_member_of_a_nested_type_is_owned_by_the_nested_type():
    source = "class Outer {\n  struct In { int q; } in;\n  int own;\n};\n"
    rows = _rows(source, "cpp", "a.cpp")
    assert ("field", "Outer.In.q", "In") in rows
    assert ("field", "Outer.in", "Outer") in rows
    assert ("field", "Outer.own", "Outer") in rows


def test_an_anonymous_unions_members_belong_to_the_enclosing_class():
    """That is the language's own rule: `h.u1` is how they are reached."""
    rows = _rows("class H {\n  union { int u1; float u2; };\n};\n", "cpp", "a.cpp")
    assert rows == [
        ("class", "H", None),
        ("field", "H.u1", "H"),
        ("field", "H.u2", "H"),
    ]


@pytest.mark.parametrize("member, name", [
    ("void (*fp)(int);", "fp"),
    ("int (*table[4])(void);", "table"),
    # The grammar ERRORs on `H::*` and still exposes the name beside the error.
    # Review found the first draft walked into the ERROR node and dropped both,
    # trading the old wrong kind (`method`) for an absence.
    ("void (H::*pmf)();", "pmf"),
    ("int (H::*pmd);", "pmd"),
])
def test_a_function_pointer_member_is_data(member, name):
    """`void (*fp)(int);` holds a `function_declarator`, and the NAME is bound
    by the pointer inside it. It was indexed as a `method` while data had no
    channel; the shared predicate asks which declarator binds the name now."""
    symbols = parse_file(f"class H {{\n  {member}\n}};\n", "a.cpp", "cpp")
    assert [(s.kind, s.name) for s in symbols] == [("class", "H"), ("field", name)]


@pytest.mark.parametrize("language, filename", _LANGUAGES)
@pytest.mark.parametrize("member, expected", [
    # The method channel names a declaration's FIRST declarator, so a function
    # in second position has no channel: absent, and never a field.
    ("int x, f();", [("field", "x")]),
    ("int g(), y;", [("method", "g"), ("field", "y")]),
])
def test_a_declaration_mixing_data_and_functions_is_asked_per_declarator(
    language, filename, member, expected
):
    """Review ran `int x, f();` and got `f` published as a FIELD while the
    docstring and the CHANGELOG both said it was absent: the node-level
    predicate read the first declarator and the name walk unwrapped the
    second's `function_declarator`. Each declarator answers for itself now."""
    symbols = parse_file(f"class H {{\n  {member}\n}};\n", filename, language)
    assert [(s.kind, s.name) for s in symbols if s.name != "H"] == expected


def test_a_named_anonymous_structs_members_belong_to_the_member_that_holds_them():
    """`h.inst.ax` is how it is reached. With NO declarator the members are the
    enclosing class's (the test above); with one, they are the declarator's."""
    source = "class H {\n  struct { int ax; } inst;\n  union { int u1; };\n};\n"
    rows = _rows(source, "cpp", "a.cpp")
    assert rows == [
        ("class", "H", None),
        ("field", "H.inst", "H"),
        ("field", "H.inst.ax", "inst"),
        ("field", "H.u1", "H"),
    ]


def test_a_function_returning_a_reference_or_pointer_is_a_method():
    """The other side of the same rule: the wrapper is OUTSIDE the
    `function_declarator`, so the name is still bound by the function."""
    source = "class H {\n  int &at(int);\n  char *name();\n};\n"
    symbols = parse_file(source, "a.cpp", "cpp")
    assert [(s.kind, s.name) for s in symbols] == [
        ("class", "H"), ("method", "at"), ("method", "name"),
    ]


def test_the_two_specs_carry_the_same_field_channel():
    """`arduino` carries its own copy of the spec, and a fix applied to one
    reaches half the product (#698)."""
    from jcodemunch_mcp.parser.languages import LANGUAGE_REGISTRY

    assert (
        LANGUAGE_REGISTRY["cpp"].field_patterns
        == LANGUAGE_REGISTRY["arduino"].field_patterns
        == ["field_declaration"]
    )
