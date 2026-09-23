"""A C++ type declared inside a function is owned by the function (#833, #798).

`int f(void) { struct S { int x; }; typedef int L; enum E { A }; }` published
`S`, `L` and `E` at file scope with no owner in C++ and Arduino, while C
qualified the same bytes under `f`. `search_symbols` for `S` found a type
that does not exist at file scope, and two functions each declaring a local
`S` collided into `~1`/`~2` twins. Inside a member function the same rule
qualified the local under the CLASS (`K.L`, #798), as if `K` declared it.

`_walk_tree`'s C++ branch moved `next_parent` only at a type container, so a
function body was not a scope. It is now: everything declared in a
C++/Arduino function body is qualified under the function and owned by it,
as in C and Python, never absent (#699 demotes locals in ranking and never
filters them). A function body counts as one class-scope level for `kind`,
because the only function DEFINITION a C++ function body can hold is a
method of a local class (C++ has no nested functions; a lambda is not a
symbol), so that method is `method` whether its class is named or anonymous.
A block-scope PROTOTYPE (`void inner(int);` inside a body) declares a
namespace-scope function and stays at file scope with no owner, as `main`
answered it (found in review).

Ids move for every function-local C++/Arduino type, field and method;
`PARSER_GENERATION` names it. Reporter: @jgravelle.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.parser.extractor import parse_file


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def _rows(source: str, filename: str, language: str):
    symbols = parse_file(source, filename, language)
    by_id = {s.id: s for s in symbols}
    return {
        s.qualified_name: (s.kind, by_id[s.parent].qualified_name if s.parent else None)
        for s in symbols
    }


_FREE = (
    "int f(void) {\n"
    "    struct S { int x; };\n"
    "    typedef int L;\n"
    "    enum E { A };\n"
    "    return 0;\n"
    "}\n"
)
_MEMBER = (
    "class K {\n"
    "    void m() {\n"
    "        struct L { int p; };\n"
    "        struct { void lm(); } x;\n"
    "    }\n"
    "    int q;\n"
    "};\n"
)
_CPP = [("cpp", "a.cpp"), ("arduino", "a.ino")]


@pytest.mark.parametrize("language,filename", _CPP)
def test_a_type_local_to_a_free_function_is_owned_by_it(language, filename):
    rows = _rows(_FREE, filename, language)
    assert rows["f.S"] == ("type", "f")
    assert "S" not in rows


@pytest.mark.parametrize("language,filename", _CPP)
def test_the_free_function_answers_every_row_and_nothing_else(language, filename):
    assert _rows(_FREE, filename, language) == {
        "f": ("function", None),
        "f.S": ("type", "f"),
        "f.S.x": ("field", "f.S"),
        "f.L": ("type", "f"),
        "f.E": ("type", "f"),
    }


@pytest.mark.parametrize("language,filename", _CPP)
def test_cpp_answers_the_same_rows_as_c_for_the_same_bytes(language, filename):
    """The issue's own bar: 'as in C'."""
    assert _rows(_FREE, filename, language) == _rows(_FREE, "a.c", "c")


@pytest.mark.parametrize("language,filename", _CPP)
def test_a_type_local_to_a_member_function_is_owned_by_the_function_not_the_class(language, filename):
    """#798: `K` does not declare `L`, and `K::L` names nothing."""
    rows = _rows(_MEMBER, filename, language)
    assert rows == {
        "K": ("class", None),
        "K.m": ("method", "K"),
        "K.m.L": ("type", "K.m"),
        "K.m.L.p": ("field", "K.m.L"),
        "K.m.lm": ("method", "K.m"),
        "K.q": ("field", "K"),
    }
    assert "K.L" not in rows and "K.lm" not in rows


@pytest.mark.parametrize("language,filename", _CPP)
def test_every_local_type_spelling_is_owned(language, filename):
    source = (
        "void g() {\n"
        "    class C { public: void cm() {} };\n"
        "    union U { int a; float b; };\n"
        "    using A = int;\n"
        "}\n"
    )
    rows = _rows(source, filename, language)
    assert rows["g.C"] == ("class", "g")
    assert rows["g.C.cm"] == ("method", "g.C")
    assert rows["g.U"] == ("type", "g")
    assert rows["g.U.a"] == ("field", "g.U")
    assert rows["g.A"] == ("type", "g")


@pytest.mark.parametrize("language,filename", _CPP)
def test_two_functions_each_declaring_a_local_of_the_same_name_do_not_collide(language, filename):
    source = "void a() { struct S { int x; }; }\nvoid b() { struct S { int y; }; }\n"
    symbols = parse_file(source, filename, language)
    names = sorted(s.qualified_name for s in symbols if s.name == "S")
    assert names == ["a.S", "b.S"]
    assert len({s.id for s in symbols if s.name == "S"}) == 2


@pytest.mark.parametrize("language,filename", _CPP)
def test_a_block_scope_prototype_stays_at_file_scope(language, filename):
    """Found in review: `void inner(int);` inside a body declares a
    namespace-scope function. The first draft made it `df.inner` of kind
    `method`, a wrong kind and owner where `main` was right."""
    rows = _rows("void df() {\n    void inner(int);\n    struct D { int d; };\n}\n", filename, language)
    assert rows["inner"] == ("function", None)
    assert rows["df.D"] == ("type", "df")
    assert "df.inner" not in rows


@pytest.mark.parametrize("language,filename", _CPP)
def test_file_scope_and_class_scope_are_unchanged(language, filename):
    source = "struct T { int v; void tm(); };\nint top() { return 0; }\nnamespace N { struct W {}; }\n"
    assert _rows(source, filename, language) == {
        "T": ("type", None),
        "T.v": ("field", "T"),
        "T.tm": ("method", "T"),
        "top": ("function", None),
        "N.W": ("type", None),
    }
