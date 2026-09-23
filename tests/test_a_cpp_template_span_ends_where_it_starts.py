"""A C++ template's span takes BOTH halves from the template wrapper (#827).

`_extract_symbol` widened the START of a templated C++ symbol to the nearest
`template_declaration` and took the END from the inner node, so a templated
class or struct recorded `template<typename T>\\nclass Foo { ... }` and stopped
before its own trailing `;`, which belongs to the wrapper. `content_hash` was
computed over the fragment and `end_line` disagreed with the bytes whenever
the `;` sat on a later line. #817 introduced and fixed the identical shape in
Go (`_go_binding_span_node` widens both halves) and scoped the fix to Go on
purpose; this is the C++ decision, made on its own measurement.

⚠ Decided: the terminator is INCLUDED, because the two halves of one span
come from one node -- the rule #817 wrote -- and a declaration's bytes end
at its terminator everywhere else in this extractor (a Go `var T = 4`, a C
`typedef`, a C++ `using` alias all record theirs). What moves: every
templated C++/Arduino class and struct's `byte_length`, `content_hash` and,
for the brace-newline form, `end_line`. Named in the CHANGELOG and under
`PARSER_GENERATION`.

⚠ A templated FUNCTION, ALIAS, function DECLARATION and a member function
template inside a class are byte-identical before and after: their inner node
already ends where the wrapper does, and the control rows pin that nothing
else moved. A member struct template moves like a file-scope one.

⚠ An UNTEMPLATED class (`class Bar { ... };`) still records to its `}`: that
`;` is a sibling of `class_specifier` under the file, not part of any node
the symbol is built from, in C and C++ alike. A different mechanism, left
alone here deliberately (moving every C-family class in every index does not
belong inside a fix about templates); the control row pins it so the
templated `;` is not read as a promise about the untemplated one.

Every shape runs in C++ and Arduino, whose specs are copies.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.parser.extractor import compute_content_hash, parse_file


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    """Answer the parser, not the developer's config file (#411)."""
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


_LANGUAGES = [pytest.param("cpp", "a.cpp", id="cpp"), pytest.param("arduino", "a.ino", id="arduino")]


def _one(source: str, language: str, filename: str, name: str):
    found = [s for s in parse_file(source, filename, language) if s.name == name]
    assert len(found) == 1, found
    return found[0]


def _recorded(source: str, sym) -> bytes:
    return source.encode()[sym.byte_offset : sym.byte_offset + sym.byte_length]


_REPORTED = "template<typename T>\nclass Foo { public: int n; };\n"


@pytest.mark.parametrize("language,filename", _LANGUAGES)
def test_the_reported_class_records_its_terminator(language, filename):
    """The issue's 51-byte source: the span is the whole declaration, 50
    bytes, ending in `;` -- not the 49 that stopped at the brace."""
    sym = _one(_REPORTED, language, filename, "Foo")
    assert _recorded(_REPORTED, sym) == b"template<typename T>\nclass Foo { public: int n; };"
    assert (sym.byte_offset, sym.byte_length) == (0, 50)


@pytest.mark.parametrize("language,filename", _LANGUAGES)
def test_a_templated_struct_records_its_terminator(language, filename):
    source = "template<typename T>\nstruct S { T v; };\n"
    sym = _one(source, language, filename, "S")
    assert _recorded(source, sym) == b"template<typename T>\nstruct S { T v; };"


@pytest.mark.parametrize("language,filename", _LANGUAGES)
def test_the_content_hash_covers_the_recorded_bytes(language, filename):
    """The hash and the span must describe the same bytes: a hash over a
    fragment is self-consistent and still wrong."""
    sym = _one(_REPORTED, language, filename, "Foo")
    assert sym.content_hash == compute_content_hash(_recorded(_REPORTED, sym))
    assert sym.content_hash != compute_content_hash(_REPORTED.encode()[:49])


@pytest.mark.parametrize("language,filename", _LANGUAGES)
def test_end_line_follows_the_terminator_when_it_sits_on_its_own_line(language, filename):
    """The one form where the old span's `end_line` disagreed with its bytes."""
    source = "template<typename T>\nclass Foo {\n  int n;\n}\n;\n"
    sym = _one(source, language, filename, "Foo")
    assert _recorded(source, sym) == b"template<typename T>\nclass Foo {\n  int n;\n}\n;"
    assert (sym.line, sym.end_line) == (1, 5)


@pytest.mark.parametrize("language,filename", _LANGUAGES)
def test_the_two_halves_of_every_template_span_come_from_one_node(language, filename):
    """The property #817 wrote, applied to C++: a span that starts at
    `template` ends at the wrapper's last byte, which is `;` for a type and
    `}` for a function body -- never at an inner node's end."""
    source = (
        "template<typename T>\nclass A { int n; };\n"
        "template<typename T>\nstruct B { T v; };\n"
        "template<typename T>\nT id(T v) { return v; }\n"
        "template<class T>\nusing Vec = std::vector<T>;\n"
        "template<class T>\nT id2(T v);\n"
    )
    for sym in parse_file(source, filename, language):
        rec = _recorded(source, sym)
        if rec.startswith(b"template"):
            assert rec.endswith((b";", b"}")), (sym.name, rec[-12:])


# --- what does NOT move, pinned byte for byte ------------------------------

@pytest.mark.parametrize(
    "source,name,length",
    [
        pytest.param("template<typename T>\nT id(T v) { return v; }\n", "id", 44, id="function-template"),
        pytest.param("template<class T>\nusing Vec = std::vector<T>;\n", "Vec", 45, id="alias-template"),
        pytest.param("template<typename T>\nT id(T v);\n", "id", 31, id="function-template-declaration"),
        pytest.param("class Bar { public: int n; };\n", "Bar", 28, id="untemplated-class-control"),
    ],
)
@pytest.mark.parametrize("language,filename", _LANGUAGES)
def test_a_span_whose_inner_node_already_ends_at_the_wrapper_is_unchanged(
    source, name, length, language, filename
):
    """Measured on `main` before the change; the control rows are what a
    reader checks first when every templated class's hash moves."""
    sym = _one(source, language, filename, name)
    assert sym.byte_offset == 0 and sym.byte_length == length, (sym.byte_offset, sym.byte_length)


@pytest.mark.parametrize("language,filename", _LANGUAGES)
def test_a_member_template_inside_a_class_follows_the_same_rule(language, filename):
    """A wrapper INSIDE a class body: the method's `}` already ends where its
    wrapper does and is unchanged; the nested struct's `;` did not (measured
    on `main`: 36 bytes, stopping at `}`) and records it now."""
    source = "class C {\n  template<class T> void m(T t) {}\n  template<class T> struct In { T v; };\n};\n"
    m = _one(source, language, filename, "m")
    inner = _one(source, language, filename, "In")
    assert _recorded(source, m) == b"template<class T> void m(T t) {}"
    assert _recorded(source, inner) == b"template<class T> struct In { T v; };"


@pytest.mark.parametrize(
    "source,name,recorded",
    [
        pytest.param(
            "template<> class Foo<int> { int n; };\n", "Foo", b"template<> class Foo<int> { int n; };",
            id="explicit-specialisation",
        ),
        pytest.param(
            "template<class T> struct S<T*> { T v; };\n", "S", b"template<class T> struct S<T*> { T v; };",
            id="partial-specialisation",
        ),
    ],
)
@pytest.mark.parametrize("language,filename", _LANGUAGES)
def test_a_specialisation_moves_with_the_rule(source, name, recorded, language, filename):
    """Found in review: a specialisation is a `template_declaration` wrapping
    a class too, so it gains its `;` by the same rule; pinned so a later spec
    edit cannot un-move one spelling in silence."""
    sym = _one(source, language, filename, name)
    assert _recorded(source, sym) == recorded


@pytest.mark.parametrize("language,filename", _LANGUAGES)
def test_a_nested_template_wrapper_is_unchanged(language, filename):
    """`_nearest_cpp_template_wrapper` walks to the OUTER wrapper; a member
    template of a class template ends at its body's `}` before and after
    (measured on `main`: 58 bytes)."""
    source = "template<class T>\ntemplate<class U>\nvoid Foo<T>::m(U u) {}\n"
    sym = _one(source, language, filename, "m")
    assert _recorded(source, sym) == b"template<class T>\ntemplate<class U>\nvoid Foo<T>::m(U u) {}"
    assert sym.byte_length == 58


def test_cpp_and_arduino_answer_the_same_bytes():
    """Two spec copies, one wrapper rule."""
    for source in (_REPORTED, "template<typename T>\nstruct S { T v; };\n"):
        cpp = [(s.name, s.byte_offset, s.byte_length, s.end_line) for s in parse_file(source, "a.cpp", "cpp")]
        ino = [(s.name, s.byte_offset, s.byte_length, s.end_line) for s in parse_file(source, "a.ino", "arduino")]
        assert cpp == ino

