"""A C-family enum declared behind an export macro is an enum (LEDGER L-47).

`enum class API E { A, B };` published NOTHING: the grammar cannot know
`API` is a macro, so it reads `enum class API` as an elaborated type, `E` as
a variable and the enumerator list as a brace initializer. In a class body the
same head gave `S.E#field`, and the C grammar read `enum API E { A, B };` as
`E#function`. L-45 unmasked class, struct and union heads only.

The property: an enum written with macro tokens between its keyword and its
name publishes exactly what the same text without the macro publishes, by id,
parent and kind. The other direction: `enum Color c { RED };` is a real,
brace-initialised variable in C++ and stays as parsed, since nothing in the
text can tell it from a one-enumerator enum behind a macro.
"""

from __future__ import annotations

import pytest

import jcodemunch_mcp.config as config
from jcodemunch_mcp.parser.extractor import parse_file

BODIES = {
    "scoped": "enum class {m}E {{ A, B }};\n",
    "scoped-struct": "enum struct {m}E {{ A, B }};\n",
    "scoped-one": "enum class {m}E {{ A }};\n",
    "scoped-empty": "enum class {m}E {{}};\n",
    "scoped-base": "enum class {m}E : int {{ A, B }};\n",
    # A qualified base moves into the declarator slot (review of L-47):
    "scoped-qualified-base": "enum class {m}E : std::uint8_t {{ A, B }};\n",
    "plain-qualified-base": "enum {m}E : ns::T {{ A, B }};\n",
    "in-class-qualified-base": "struct S {{\n  enum class {m}E : std::uint8_t {{ A }};\n  void f();\n}};\n",
    "commented-list": "enum class {m}E {{ A, /* note */ B }};\n",
    "two-macros": "enum class {m}{m}E {{ A, B }};\n",
    "in-namespace": "namespace n {{\nenum class {m}E {{ A, B }};\n}}\n",
    "in-class": "struct S {{\n  enum class {m}E {{ A, B }};\n  void f();\n}};\n",
    "plain-two": "enum {m}E {{ A, B }};\n",
    "then-function": "enum class {m}E {{ A, B }};\nvoid g();\nint h() {{ return 0; }}\n",
}
MACRO = "API "
FRAMES = {"cpp": ("a.cpp", "cpp"), "header": ("a.h", "cpp"), "arduino": ("a.ino", "arduino")}


@pytest.fixture(autouse=True)
def _languages_on(monkeypatch):
    # This box's config can disable languages; the gate is read through
    # config's globals, so patch it there.
    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def _rows(source: str, filename: str, language: str):
    return sorted(
        (s.id.split("::", 1)[1], (s.parent or "").split("::", 1)[-1], s.kind)
        for s in parse_file(source, filename, language)
    )


@pytest.mark.parametrize("frame", sorted(FRAMES))
@pytest.mark.parametrize("case", sorted(BODIES))
def test_a_macro_before_the_enum_name_changes_nothing(case, frame):
    filename, language = FRAMES[frame]
    expected = _rows(BODIES[case].format(m=""), filename, language)
    assert any(i.endswith("E#type") for i, _, _ in expected), expected
    assert _rows(BODIES[case].format(m=MACRO), filename, language) == expected


@pytest.mark.parametrize("case", ["plain-two", "then-function"])
def test_a_c_enum_behind_a_macro_is_an_enum(case):
    """The C grammar reads the same head as a FUNCTION definition."""
    source = BODIES[case].replace("enum class", "enum").format(m=MACRO)
    plain = BODIES[case].replace("enum class", "enum").format(m="")
    expected = _rows(plain, "a.c", "c")
    assert any(i.endswith("E#type") for i, _, _ in expected), expected
    assert _rows(source, "a.c", "c") == expected


def test_line_and_signature_come_from_the_original_text():
    source = "// header\nenum class API E { A, B };\n"
    symbols = {s.name: s for s in parse_file(source, "a.cpp", "cpp")}
    assert symbols["E"].line == 2
    assert "API" in symbols["E"].signature


@pytest.mark.parametrize(
    "source,expected",
    [
        ("enum Color c { RED };\n", set()),
        ("enum Color c { RED /* default */ };\n", set()),
        ("enum Color c { RED // default\n};\n", set()),
        ("enum Color c = RED;\n", set()),
        ("enum class API E;\n", set()),
    ],
    ids=[
        "brace-initialised-variable",
        "brace-initialised-variable-block-comment",
        "brace-initialised-variable-line-comment",
        "initialised-variable",
        "opaque-declaration",
    ],
)
def test_a_real_variable_or_an_opaque_declaration_is_unchanged(source, expected):
    """The other direction: a one-element brace initializer on a plain enum
    is a real variable, and an opaque declaration has no list to blank."""
    published = {(s.name, s.kind) for s in parse_file(source, "a.cpp", "cpp")}
    assert published == expected


# Expected rows are what `main` publishes for the same text.
REAL_CODE = {
    "commented-brace-field": (
        "struct S {\n  enum Color c { RED /* default */ };\n  int x;\n};\n",
        "a.cpp",
        "cpp",
        [("S#type", "type"), ("S.c#field", "field"), ("S.x#field", "field")],
    ),
    "bit-field-with-a-cast-width": (
        "struct S {\n  enum Color c : std::uint8_t{ 3 };\n  int x;\n};\n",
        "a.cpp",
        "cpp",
        [("S#type", "type"), ("S.c#field", "field"), ("S.x#field", "field")],
    ),
    # An array takes any number of entries (review of L-47, round 2):
    "array-then-function": (
        "enum Color cs[2] { RED, GREEN };\nint after() { return 0; }\n",
        "a.cpp",
        "cpp",
        [("after#function", "function")],
    ),
    "arduino-array-then-setup": (
        "enum Mode modes[] { OFF, ON };\nvoid setup() {}\n",
        "a.ino",
        "arduino",
        [("setup#function", "function")],
    ),
    "array-field": (
        "struct S {\n  enum Color cs[2] { RED, GREEN };\n  int x;\n};\n",
        "a.cpp",
        "cpp",
        [("S#type", "type"), ("S.cs#field", "field"), ("S.x#field", "field")],
    ),
    # MOVES (disclosed): main gave the C misparse `cs#function`; the array is
    # a file-scope variable, which C++ does not index either (review, round 3).
    "c-array-then-function": (
        "enum Color cs[2] { RED, GREEN };\nint after() { return 0; }\n",
        "a.c",
        "c",
        [("after#function", "function")],
    ),
    "two-dimensional-array-field": (
        "struct S {\n  enum Color g[2][2] { {RED, GREEN}, {BLUE, RED} };\n};\n",
        "a.cpp",
        "cpp",
        [("S#type", "type"), ("S.g#field", "field")],
    ),
}


@pytest.mark.parametrize("case", sorted(REAL_CODE))
def test_real_enum_typed_code_is_unchanged(case):
    """A plain enum field with one braced value, a bit-field whose width is a
    functional cast, and a brace-initialised enum ARRAY are real code: each
    publishes exactly what main published, nothing dropped and nothing added."""
    source, filename, language, expected = REAL_CODE[case]
    rows = sorted((s.id.split("::", 1)[1], s.kind) for s in parse_file(source, filename, language))
    assert rows == expected
