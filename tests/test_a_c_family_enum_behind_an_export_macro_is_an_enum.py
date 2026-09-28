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
        ("enum Color c = RED;\n", set()),
        ("enum class API E;\n", set()),
    ],
    ids=["brace-initialised-variable", "initialised-variable", "opaque-declaration"],
)
def test_a_real_variable_or_an_opaque_declaration_is_unchanged(source, expected):
    """The other direction: a one-element brace initializer on a plain enum
    is a real variable, and an opaque declaration has no list to blank."""
    published = {(s.name, s.kind) for s in parse_file(source, "a.cpp", "cpp")}
    assert published == expected
