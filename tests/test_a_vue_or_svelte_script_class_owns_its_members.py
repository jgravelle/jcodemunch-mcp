"""A class in a Vue or Svelte `<script>` owns its members, as in a `.ts` file (#861).

Both channels walked their script by hand and stopped at a class: a
declaration published its name and no member, and a class expression
(`const C = class {...}`) published a `constant` with no class (#803's shape in
a channel #803 never reached). The class and its members now come from the
generic JS/TS walk, so these tests compare against a `.ts`/`.js` file of the
same script rather than restating a member list.
"""

import pytest

import jcodemunch_mcp.config as config_module
from jcodemunch_mcp.parser.extractor import parse_file


@pytest.fixture(autouse=True)
def _all_languages(monkeypatch):
    monkeypatch.setattr(config_module, "is_language_enabled", lambda *a, **k: True)


CLASS_TS = "class Svc {\n  x = 1;\n  constructor(private a: number) {}\n  m() {}\n}\n"
CLASS_JS = "class Svc {\n  x = 1;\n  constructor(a) {}\n  m() {}\n}\n"
EXPRESSION = "const C = class { x = 1; m() {} };\nexport default { name: 'A' };\n"

CHANNELS = [("a.vue", "vue"), ("a.svelte", "svelte")]


def _ids(symbols):
    return {s.id.split("::")[1]: s.parent and s.parent.split("::")[1] for s in symbols}


def _script(body: str, attrs: str = "") -> str:
    return f"<template><div/></template>\n<script{attrs}>\n{body}</script>\n"


def _expected(body: str, lang: str) -> dict:
    """What a plain `.ts`/`.js` file of the same script publishes, re-rooted at
    the component (`a#class`), the only difference the channel adds."""
    ext = "ts" if lang == "typescript" else "js"
    got = _ids(parse_file(body, f"a.{ext}", lang))
    return {k: (v if v is not None else "a#class") for k, v in got.items()}


@pytest.mark.parametrize("filename,language", CHANNELS)
@pytest.mark.parametrize(
    "body,attrs,lang",
    [
        (CLASS_TS, ' lang="ts"', "typescript"),
        (CLASS_JS, "", "javascript"),
        (EXPRESSION, "", "javascript"),
        (EXPRESSION, ' lang="ts"', "typescript"),
    ],
    ids=["declaration-ts", "declaration-js", "expression-js", "expression-ts"],
)
def test_the_class_publishes_what_a_script_file_publishes(filename, language, body, attrs, lang):
    got = _ids(parse_file(_script(body, attrs), filename, language))
    expected = _expected(body, lang)

    assert any(k.endswith("#class") and k != "a#class" for k in expected)
    assert any(k.endswith("#method") for k in expected)
    for sid, parent in expected.items():
        assert got.get(sid, "MISSING") == parent, (sid, got)


@pytest.mark.parametrize("filename,language", CHANNELS)
def test_a_class_expression_is_not_also_a_constant(filename, language):
    got = _ids(parse_file(_script(EXPRESSION), filename, language))

    assert "C#class" in got
    assert "C#constant" not in got


def test_vue_script_setup_class_owns_its_members():
    got = _ids(parse_file(_script(CLASS_TS, ' setup lang="ts"'), "a.vue", "vue"))

    assert got["Svc.m#method"] == "Svc#class"
    assert got["Svc.a#field"] == "Svc#class"


@pytest.mark.parametrize("filename,language", CHANNELS)
def test_exported_and_multi_declarator_classes_own_their_members(filename, language):
    body = (
        "export class F { n() {} }\n"
        "export const E = class { e() {} };\n"
        "const A = class { a() {} }, B = class { b() {} };\n"
    )
    got = _ids(parse_file(_script(body), filename, language))

    for cls, member in (("F", "n"), ("E", "e"), ("A", "a"), ("B", "b")):
        assert got.get(f"{cls}#class") == "a#class", got
        assert got.get(f"{cls}.{member}#method") == f"{cls}#class", got


@pytest.mark.parametrize("filename,language", CHANNELS)
def test_member_lines_and_bytes_address_the_component_file(filename, language):
    source = _script("// é\n" + CLASS_TS, ' lang="ts"')
    data = source.encode("utf-8")
    lines = source.splitlines()

    members = [s for s in parse_file(source, filename, language) if s.qualified_name.startswith("Svc")]

    assert {s.name for s in members} >= {"Svc", "x", "m"}
    for s in members:
        text = data[s.byte_offset:s.byte_offset + s.byte_length].decode("utf-8")
        assert text.split("\n")[0].strip() in lines[s.line - 1], (s.id, text, lines[s.line - 1])
        assert s.file == filename
        assert s.language == language


@pytest.mark.parametrize("filename,language", CHANNELS)
def test_a_class_inside_a_function_is_still_not_published(filename, language):
    body = "function make() { return class Inner { i() {} }; }\n"
    got = _ids(parse_file(_script(body), filename, language))

    assert not any(k.startswith("Inner") for k in got), got
