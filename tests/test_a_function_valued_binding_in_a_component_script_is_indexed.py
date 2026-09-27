"""A function-valued binding in a Vue or Svelte script is a function (LEDGER L-42).

`const f = () => 1` and `const g = function () {}` publish `f#function` and
`g#function` from a `.js` file. In a Vue or Svelte script both hand walks
declined a declarator whose value is a function, and `arrow_function` stops
their recursion, so nothing published the binding. A Composition API
component's event handlers are exactly this shape.

The property: for a component-level function-valued binding, a Vue or Svelte
script publishes the same `(name, kind)` list the script's own language
publishes from a plain file, owned by the component. And the other direction:
a binding that is not function-valued keeps its binding kind, and a
destructured binding is not called a function.
"""

from __future__ import annotations

import pytest

import jcodemunch_mcp.config as config
from jcodemunch_mcp.parser.extractor import parse_file

BODIES = {
    "arrow": "const f = () => 1\n",
    "arrow-block": "const f = (a, b) => { return a + b }\n",
    "async-arrow": "const f = async () => { await x() }\n",
    "function-expression": "const g = function () { return 2 }\n",
    "named-function-expression": "const g = function inner () { return 2 }\n",
    "generator": "const gen = function* () { yield 1 }\n",
    "let": "let f = () => 1\n",
    "var": "var f = () => 1\n",
    "two-declarators": "const f = () => 1, g = function () {}\n",
    "beside-a-value": "const n = 1\nconst f = () => n\n",
    "beside-a-declaration": "function h() {}\nconst f = () => 1\n",
}
# frame -> (component file, template, reference file, reference language)
FRAMES = {
    "vue": (
        "Comp.vue",
        "<script>\n{b}</script>\n<template><div/></template>\n",
        "a.js",
        "javascript",
    ),
    "vue-setup": (
        "Comp.vue",
        "<script setup>\n{b}</script>\n<template><div/></template>\n",
        "a.js",
        "javascript",
    ),
    "vue-ts": (
        "Comp.vue",
        '<script lang="ts">\n{b}</script>\n<template><div/></template>\n',
        "a.ts",
        "typescript",
    ),
    "vue-tsx": (
        "Comp.vue",
        '<script lang="tsx">\n{b}</script>\n<template><div/></template>\n',
        "a.tsx",
        "tsx",
    ),
    "svelte": ("Comp.svelte", "<script>\n{b}</script>\n<div/>\n", "a.js", "javascript"),
    "svelte-ts": (
        "Comp.svelte",
        '<script lang="ts">\n{b}</script>\n<div/>\n',
        "a.ts",
        "typescript",
    ),
    "svelte-tsx": (
        "Comp.svelte",
        '<script lang="tsx">\n{b}</script>\n<div/>\n',
        "a.tsx",
        "tsx",
    ),
}


@pytest.fixture(autouse=True)
def _languages_on(monkeypatch):
    # This box's config can disable languages; the gate is read through
    # config's globals, so patch it there.
    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def _functions(symbols) -> list[tuple[str, str]]:
    # A sorted LIST, not a set: two same-named functions must count as two.
    return sorted((s.name, s.kind) for s in symbols if s.kind == "function")


@pytest.mark.parametrize("frame", sorted(FRAMES))
@pytest.mark.parametrize("case", sorted(BODIES))
def test_a_function_valued_binding_is_published_as_a_plain_file_does(frame, case):
    body = BODIES[case]
    filename, template, ref_file, ref_lang = FRAMES[frame]
    expected = _functions(parse_file(body, ref_file, ref_lang))
    assert expected, (
        f"{ref_file} published no function; the comparison would be vacuous"
    )

    language = filename.rsplit(".", 1)[1]
    symbols = parse_file(template.format(b=body), filename, language)
    assert _functions(symbols) == expected

    comp = next(s for s in symbols if s.kind == "class" and s.name == "Comp")
    for s in symbols:
        if s.kind == "function":
            assert s.parent == comp.id, f"{s.id} is not owned by the component"


def _function_ids(symbols) -> list[str]:
    return sorted(s.id.split("::", 1)[1] for s in symbols if s.kind == "function")


@pytest.mark.parametrize("frame", sorted(FRAMES))
def test_a_same_named_declaration_and_binding_are_numbered_as_a_plain_file_does(frame):
    """ID MOVE, disclosed (review round 1). `function h() {}` beside
    `var h = () => 1` was `h#function` alone; the new binding shares its name,
    so both are numbered, `h#function~1` and `~2`, exactly as a `.js` file
    numbers them. The `(name, kind)` comparison above cannot see a suffix."""
    body = "function h() {}\nconst h2 = 1\nvar h = () => 1\n"
    filename, template, ref_file, ref_lang = FRAMES[frame]
    expected = _function_ids(parse_file(body, ref_file, ref_lang))
    assert expected == ["h#function~1", "h#function~2"], expected

    language = filename.rsplit(".", 1)[1]
    assert (
        _function_ids(parse_file(template.format(b=body), filename, language))
        == expected
    )


@pytest.mark.parametrize("frame", sorted(FRAMES))
def test_a_value_binding_keeps_its_binding_kind(frame):
    """The other direction: only a FUNCTION value becomes a function. A value,
    a call result and a destructured binding keep the kinds they had."""
    body = "const n = 1\nlet m = make()\nconst { a, b } = obj\nconst f = () => 1\n"
    filename, template, _, _ = FRAMES[frame]
    language = filename.rsplit(".", 1)[1]
    pairs = sorted(
        (s.name, s.kind)
        for s in parse_file(template.format(b=body), filename, language)
    )
    assert ("n", "constant") in pairs
    assert ("m", "variable") in pairs
    assert ("a", "constant") in pairs and ("b", "constant") in pairs
    assert ("f", "function") in pairs
    for name in ("n", "m", "a", "b"):
        assert (name, "function") not in pairs
    assert ("f", "constant") not in pairs and ("f", "variable") not in pairs


def test_a_function_binding_inside_a_function_body_is_not_published():
    """The component walk stops at a function body (L-38), so a helper bound
    inside a handler is not a component-level function."""
    body = "const outer = () => {\n  const inner = () => 1\n  return inner()\n}\n"
    for filename, template, _, _ in FRAMES.values():
        language = filename.rsplit(".", 1)[1]
        names = {
            s.name for s in parse_file(template.format(b=body), filename, language)
        }
        assert "outer" in names
        assert "inner" not in names
