"""A C++ class declared behind an export macro is a class (LEDGER L-45).

`class LEVELDB_EXPORT Status { bool ok() const; };` is how most exported C++
libraries declare their API. The grammar cannot know `LEVELDB_EXPORT` is a
macro, so it reads `class LEVELDB_EXPORT` as a RETURN TYPE, `Status` as a
declarator and the class body as a statement block: the class was indexed as
`Status#function` and its members lost their owner. In a large header, error
recovery then filed unrelated declarations under the bogus function.

The property: a class, struct or union written with one or more macro tokens
between the keyword and its name publishes exactly what the same text without
the macro publishes, by id and parent, with its line and signature taken from
the ORIGINAL text. And the other direction: a real function whose return type
is an elaborated `class X` is still a function, and a clean class is
unchanged.
"""

from __future__ import annotations

import pytest

import jcodemunch_mcp.config as config
from jcodemunch_mcp.parser.extractor import parse_file

BODIES = {
    "class": "class {m}Status {{\n public:\n  bool ok() const;\n  int code() const {{ return c_; }}\n private:\n  int c_;\n}};\n",
    "struct": "struct {m}Options {{\n  int size;\n  void Reset();\n}};\n",
    "base-class": "class {m}Iter : public Base {{\n public:\n  void Next();\n}};\n",
    "final": "class {m}Leaf final {{\n  void f();\n}};\n",
    "in-namespace": "namespace leveldb {{\nclass {m}DB {{\n public:\n  virtual ~DB();\n  virtual void Put();\n}};\n}}\n",
    "two-macros": "class {m}{m}Twice {{\n  void f();\n}};\n",
    # The misparse gives these a non-identifier declarator (review round 2):
    "qualified-base": "class {m}Err : public std::runtime_error {{\n  void f();\n}};\n",
    "global-qualified-base": "class {m}Err : public ::std::runtime_error {{\n  void f();\n}};\n",
    "namespaced-base": "class {m}Impl : public ns::Base {{\n  void f();\n}};\n",
    "template-base": "class {m}Impl : public Base<int> {{\n  void f();\n}};\n",
    "specialisation": "template <> class {m}Matcher<int> : public Base {{\n  void f();\n}};\n",
    "then-other-class": "class {m}A {{\n  void a();\n}};\nclass B {{\n  void b();\n}};\n",
}
MACRO = "LEVELDB_EXPORT "
FRAMES = {"cpp": "a.cpp", "header": "a.h", "arduino": "a.ino"}


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


def _language(frame: str) -> str:
    return "arduino" if frame == "arduino" else "cpp"


@pytest.mark.parametrize("frame", sorted(FRAMES))
@pytest.mark.parametrize("case", sorted(BODIES))
def test_a_macro_before_the_name_changes_nothing(case, frame):
    plain = BODIES[case].format(m="")
    macro = BODIES[case].format(m=MACRO)
    expected = _rows(plain, FRAMES[frame], _language(frame))
    assert any(k in ("class", "type") for _, _, k in expected), expected
    assert _rows(macro, FRAMES[frame], _language(frame)) == expected


def test_line_and_signature_come_from_the_original_text():
    source = "// header\nclass LEVELDB_EXPORT Status {\n  bool ok() const;\n};\n"
    symbols = {s.name: s for s in parse_file(source, "a.cpp", "cpp")}
    status = symbols["Status"]
    assert status.kind in ("class", "type")
    assert status.line == 2
    assert "LEVELDB_EXPORT" in status.signature
    assert symbols["ok"].parent == status.id


@pytest.mark.parametrize(
    "source,name,kind",
    [
        ("class X make() { return X(); }\n", "make", "function"),
        ("struct S *next(struct S *s) { return s; }\n", "next", "function"),
        ("class X { int y; };\n", "X", "class"),
    ],
    ids=["elaborated-return-type", "pointer-return", "clean-class"],
)
def test_a_real_function_or_a_clean_class_is_unchanged(source, name, kind):
    """The other direction: a definition whose declarator is a function is a
    function, even when its return type is written `class X`."""
    found = [
        (s.name, s.kind) for s in parse_file(source, "a.cpp", "cpp") if s.name == name
    ]
    assert found == [(name, kind)]


@pytest.mark.parametrize(
    "source,filename,language,names",
    [
        ("struct ALIGN(16) V { float x; };\n", "a.cpp", "cpp", {"V"}),
        ("struct ALIGN(16) V { float x; };\n", "a.c", "c", {"V"}),
        ("class API(x) D {\n  void d();\n};\n", "a.cpp", "cpp", {"d"}),
        ("class DLL_EXPORT(x) D { void d(); };\n", "a.cpp", "cpp", {"d"}),
    ],
    ids=["align-cpp", "align-c", "api-x", "dll-export-x"],
)
def test_a_macro_with_arguments_is_left_as_parsed(source, filename, language, names):
    """Review of L-45: a macro that TAKES ARGUMENTS gives a parenthesized
    declarator, and blanking only its name left `struct (16) V {`, a cast that
    published NOTHING. It keeps `main`'s parse, whose names these are."""
    published = {s.name for s in parse_file(source, filename, language)}
    assert names <= published, published
