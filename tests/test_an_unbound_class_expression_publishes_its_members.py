"""A class expression bound to nothing still publishes its members (LEDGER L-40).

`new (class { m() {} })()`, `register(class { m() {} })`, `[class {...}]`,
`{ K: class {...} }` and `const [a] = class {...}` bind no name the generic
walk can give the class, so a `.js` file publishes the members bare
(`m#method`, no parent). `_EmbeddedScriptClasses` published class ROOTS and
their subtrees only, so in a Vue or Svelte script these members vanished.

The property: for a class-body member, a Vue or Svelte script publishes the
same `(name, kind)` set a `.js` file publishes. And the other direction: an
OBJECT-LITERAL method, which also has no parent in a `.js` file, is not swept
up by the rule, because it is not in a class body.
"""

from __future__ import annotations

import pytest

import jcodemunch_mcp.config as config
from jcodemunch_mcp.parser.extractor import parse_file

UNBOUND = {
    "new-iife": "new (class { m() {} })()\n",
    "argument": "register(class { m() {} })\n",
    "array-element": "const xs = [class { m() {} }]\n",
    "object-value": "const o = { K: class { m() {} } }\n",
    "destructured": "const [a] = class { m() {} }\n",
    "with-field": "register(class { x = 1; static y = 2; m() {} })\n",
    "two-same-named": "register(class { m() {} }, class { m() {} })\n",
}
FRAMES = {
    "vue": ("Comp.vue", "<script>\n{b}</script>\n<template><div/></template>\n"),
    "vue-setup": ("Comp.vue", "<script setup>\n{b}</script>\n<template><div/></template>\n"),
    # The member test reads the hand walk's tree, whose grammar follows `lang`
    # (review: a fixture with plain JS only cannot see a grammar mismatch).
    "vue-ts": ("Comp.vue", '<script lang="ts">\n{b}</script>\n<template><div/></template>\n'),
    "svelte": ("Comp.svelte", "<script>\n{b}</script>\n<div/>\n"),
    "svelte-ts": ("Comp.svelte", '<script lang="ts">\n{b}</script>\n<div/>\n'),
}
_MEMBER_KINDS = {"method", "field", "property"}


@pytest.fixture(autouse=True)
def _languages_on(monkeypatch):
    # This box's config can disable languages; the gate is read through
    # config's globals, so patch it there.
    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def _members(symbols) -> list[tuple[str, str]]:
    # A sorted LIST, not a set: two same-named members must count as two
    # (review; Standing lesson 08-27, a set cannot count).
    return sorted((s.name, s.kind) for s in symbols if s.kind in _MEMBER_KINDS)


@pytest.mark.parametrize("frame", sorted(FRAMES))
@pytest.mark.parametrize("case", sorted(UNBOUND))
def test_an_unbound_class_publishes_the_members_a_js_file_does(frame, case):
    body = UNBOUND[case]
    filename, template = FRAMES[frame]
    js = _members(parse_file(body, "a.js", "javascript"))
    assert js, "the .js file published no member; the comparison would be vacuous"

    published = [s for s in parse_file(template.format(b=body), filename, frame.split("-")[0])]

    assert _members(published) == js
    component = f"{filename}::{filename.split('.')[0]}#class"
    assert all(s.parent == component for s in published if s.kind in _MEMBER_KINDS)


@pytest.mark.parametrize("frame", sorted(FRAMES))
def test_an_object_literal_method_is_not_swept_up(frame):
    """Also parentless in a `.js` file, but not in a class body."""
    body = "const api = { run() { return 1 } }\nregister(class { m() {} })\n"
    filename, template = FRAMES[frame]
    names = {s.name for s in parse_file(template.format(b=body), filename, frame.split("-")[0])}

    assert "m" in names
    assert "run" not in names


@pytest.mark.parametrize("frame", sorted(FRAMES))
def test_a_bound_class_is_unchanged(frame):
    body = "const C = class { m() {} }\n"
    filename, template = FRAMES[frame]
    ids = {s.id for s in parse_file(template.format(b=body), filename, frame.split("-")[0])}

    assert {f"{filename}::C#class", f"{filename}::C.m#method"} <= ids
    assert f"{filename}::m#method" not in ids


@pytest.mark.parametrize(
    "language,filename,frame",
    [
        ("vue", "Comp.vue", '<script lang="tsx">\n{b}</script>\n<template><div/></template>\n'),
        ("svelte", "Comp.svelte", '<script lang="tsx">\n{b}</script>\n<div/>\n'),
    ],
    ids=["vue-tsx", "svelte-tsx"],
)
def test_a_tsx_class_with_jsx_in_its_body_publishes_its_members(language, filename, frame):
    """JSX inside the class body: read with the wrong grammar, error recovery
    hid the `class_body` and the members were dropped (review; the grammar
    matches since L-39)."""
    body = "register(class { m() { return <b/> } })\nregister(class { a = <i/>; n() {} })\n"
    tsx = _members(parse_file(body, "a.tsx", "tsx"))
    assert tsx, "the .tsx file published no member; the comparison would be vacuous"

    assert _members(parse_file(frame.format(b=body), filename, language)) == tsx
