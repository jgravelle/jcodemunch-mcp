"""A C-family type specifier without a body is a reference, not a declaration (#830).

tree-sitter-c and tree-sitter-cpp spell a definition (`struct S { ... }`), a
reference (`struct S` in a declarator, a parameter, a cast, a `sizeof`, a
typedef target) and a forward declaration (`struct S;`) with ONE node type per
keyword: `struct_specifier`, `union_specifier`, `enum_specifier` and, in C++,
`class_specifier`. `C_SPEC`, `CPP_SPEC` and `ARDUINO_SPEC` all list those node
types and nothing asked whether the node had a `body`, so every mention of a
type was published as a declaration of it: `struct S { struct Other *link; }`
declared a nested type `S.Other` the file never defines.

⚠ The forward declaration is a DECISION, not an accident: `struct S;` and
`class K;` yield no symbol. A forward declaration carries only the name; a
header that forward-declares forty classes would otherwise publish forty
memberless `class` symbols, each a second declaration beside the real one. The
function prototype precedent (`int f(int);` is a `function`) does not transfer,
because a prototype carries the signature a caller reads.

Every shape runs in C, C++ and Arduino: the three specs are copies and the
fix is one predicate at the walk site, so a language that regresses is a
copy that stopped sharing it.
"""

from __future__ import annotations

from collections import Counter

import pytest

from jcodemunch_mcp.parser.extractor import parse_file


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    """Answer the parser, not the developer's config file (#411)."""
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


_LANGUAGES = [
    pytest.param("c", "a.c", id="c"),
    pytest.param("cpp", "a.cpp", id="cpp"),
    pytest.param("arduino", "a.ino", id="arduino"),
]

_TYPE_KINDS = {"type", "class"}


def _types(source: str, language: str, filename: str) -> Counter:
    """Every type-shaped symbol by qualified name, counted."""
    symbols = parse_file(source, filename, language)
    return Counter(s.qualified_name for s in symbols if s.kind in _TYPE_KINDS)


# (source, the names the file MENTIONS but does not define)
_REFERENCES = [
    pytest.param("struct S { struct Other *link; };\n", {"Other"}, id="field-type"),
    pytest.param("typedef struct S S_t;\n", {"S"}, id="typedef-target"),
    pytest.param("void g(struct S *p);\n", {"S"}, id="parameter"),
    pytest.param("struct S *make(void);\n", {"S"}, id="return-type"),
    pytest.param("struct S g;\n", {"S"}, id="file-scope-variable"),
    pytest.param("void f(void) { struct S s; }\n", {"S"}, id="local-variable"),
    pytest.param("void f(void *p) { struct S *q = (struct S *)p; }\n", {"S"}, id="cast"),
    pytest.param("int n = sizeof(struct S);\n", {"S"}, id="sizeof"),
    pytest.param("union U v;\n", {"U"}, id="union-reference"),
    pytest.param("enum E e;\n", {"E"}, id="enum-reference"),
    pytest.param("struct S;\n", {"S"}, id="struct-forward-declaration"),
    pytest.param("union U;\n", {"U"}, id="union-forward-declaration"),
]


@pytest.mark.parametrize("source,mentioned", _REFERENCES)
@pytest.mark.parametrize("language,filename", _LANGUAGES)
def test_a_mention_of_a_type_declares_nothing(source, mentioned, language, filename):
    found = _types(source, language, filename)
    fabricated = {
        name
        for name in found
        if name.split(".")[-1] in mentioned
    }
    assert not fabricated, (
        f"{language}: {sorted(fabricated)} published as declarations of types "
        f"this file only mentions"
    )


@pytest.mark.parametrize("language,filename", _LANGUAGES)
def test_a_definition_followed_by_references_is_declared_once(language, filename):
    """The definition survives and it is the only `S`: the other three
    mentions (a return type, a parameter, a local) add nothing."""
    source = (
        "struct S { int x; };\n"
        "struct S *make(void);\n"
        "void take(struct S *p);\n"
        "void f(void) { struct S local; }\n"
    )
    assert _types(source, language, filename)["S"] == 1, language


@pytest.mark.parametrize("language,filename", _LANGUAGES)
def test_a_definition_with_a_body_is_still_declared(language, filename):
    """The predicate is HAS A BODY, so a definition of each keyword still
    yields exactly one symbol -- including an empty body, which is a body."""
    source = "struct S { int x; };\nunion U { int i; };\nenum E { A, B };\nstruct Empty {};\n"
    found = _types(source, language, filename)
    assert found["S"] == 1 and found["U"] == 1 and found["E"] == 1 and found["Empty"] == 1, (
        language,
        found,
    )


def test_a_cpp_class_forward_declaration_declares_nothing():
    """`class K;` is `class_specifier` without a body: the same rule, the C++
    spelling. Decided, not incidental (see the module docstring)."""
    for language, filename in (("cpp", "a.cpp"), ("arduino", "a.ino")):
        assert "K" not in _types("class K;\nclass K *p;\n", language, filename), language


def test_a_cpp_scoped_enum_forward_declaration_declares_nothing():
    for language, filename in (("cpp", "a.cpp"), ("arduino", "a.ino")):
        assert "E" not in _types("enum class E;\n", language, filename), language


@pytest.mark.parametrize("language,filename", _LANGUAGES)
def test_a_pointer_to_another_struct_leaves_the_member_and_drops_the_type(language, filename):
    """The reported case, whole: the field is right and the fabricated nested
    type is gone. Restores the row #797's equality table could not carry."""
    symbols = parse_file("struct S { struct Other *link; };\n", filename, language)
    rows = Counter((s.kind, s.qualified_name) for s in symbols)
    assert rows[("field", "S.link")] == 1, language
    assert rows == Counter({("type", "S"): 1, ("field", "S.link"): 1}), (language, rows)
