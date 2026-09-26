"""A C++ variable with a function-shaped declarator or initializer is not a
function (#850).

Two mechanisms, one gate. `_is_cpp_function_declaration` asked whether a
function declarator appeared ANYWHERE under a `declaration`:

- the walk descended into an initializer's value, and the Arduino grammar
  spells a lambda's parameter list `abstract_function_declarator`, so on
  Arduino any declaration initialised with a lambda (`auto l = [](int a)
  {...};`, `int v = apply([](int a) {...});`) was a function, at any scope;
- at block scope the subtree rule made a local function-pointer variable
  (`int (*fp)(int);`) a function, and #833's block-scope-prototype exemption
  then published it at file scope with no owner.

⚠ A block-scope variable emits nothing whatever its shape, as a local `int x`
does. A block-scope PROTOTYPE still declares a namespace-scope function
(#833). A FILE-scope `int (*gfp)(int);` stays a `function`: #755 recorded
that re-grading it would trade a wrong kind for an absence, because a C++
file-scope variable has no channel.
"""

import pytest

from jcodemunch_mcp.parser.extractor import parse_file

LANGS = [("cpp", "a.cpp"), ("arduino", "a.ino")]


def _rows(source: str, language: str, filename: str) -> set[tuple[str, str]]:
    return {(s.qualified_name, s.kind) for s in parse_file(source, filename, language)}


@pytest.mark.parametrize("language, filename", LANGS)
def test_the_reported_locals_are_not_functions(language, filename):
    source = "void vf() { int (*fp)(int); }\nvoid lf() { auto l = [](int a) { return a; }; }\n"
    assert _rows(source, language, filename) == {("vf", "function"), ("lf", "function")}


@pytest.mark.parametrize("language, filename", LANGS)
@pytest.mark.parametrize("local", [
    "int (*fp)(int);",
    "int (*fp)(int) = nullptr;",
    "int (*fps[3])(int);",
    "int (&r)(int) = g;",
    "int (*(*pp)(int))(int);",
    "auto l = [](int a) { return a; };",
    "auto l = [&](int a) -> int { return a; };",
    "int v = apply([](int a) { return a; });",
    "std::function<int(int)> sf = [](int a) { return a; };",
])
def test_a_function_shaped_local_variable_emits_nothing(language, filename, local):
    source = f"int g(int);\nvoid f() {{ {local} }}\n"
    assert _rows(source, language, filename) == {("g", "function"), ("f", "function")}


@pytest.mark.parametrize("language, filename", LANGS)
@pytest.mark.parametrize("initialised", [
    "auto gl = [](int a) { return a; };",
    "int gv = apply([](int a) { return a; });",
    "std::function<int(int)> gf = [](int a) { return a; };",
])
def test_a_file_scope_variable_initialised_with_a_lambda_is_not_a_function(language, filename, initialised):
    """The Arduino grammar's lambda parameter list was counted as a function
    declarator; a plain file-scope variable emits nothing in C++."""
    assert _rows(initialised + "\n", language, filename) == set()


@pytest.mark.parametrize("language, filename", LANGS)
def test_a_block_scope_prototype_is_still_a_function(language, filename):
    source = "void h() { int proto(int); int (*make(int))(int); }\n"
    assert _rows(source, language, filename) == {
        ("h", "function"), ("proto", "function"), ("make", "function"),
    }


@pytest.mark.parametrize("language, filename", LANGS)
@pytest.mark.parametrize("decl", ["void helper(int), (*hp)(int);", "void (*hp)(int), helper(int);"])
def test_a_block_scope_prototype_beside_a_pointer_is_kept(language, filename, decl):
    """Either order: the gate must ask every declarator, not the first."""
    source = f"void h() {{ {decl} }}\n"
    assert _rows(source, language, filename) == {("h", "function"), ("helper", "function")}


@pytest.mark.parametrize("language, filename", LANGS)
def test_a_file_scope_function_pointer_keeps_755s_answer(language, filename):
    """Unchanged on purpose: the file-scope rule is #755's, not this issue's."""
    assert ("gfp", "function") in _rows("int (*gfp)(int);\n", language, filename)


@pytest.mark.parametrize("decl", ["void helper(int), (*hp)(int);", "void (*hp)(int), helper(int);"])
def test_c_asks_every_declarator_too(decl):
    """C shares the gate (#835) and read only the first declarator."""
    source = f"void h(void) {{ {decl} }}\n"
    assert _rows(source, "c", "a.c") == {("h", "function"), ("helper", "function")}
