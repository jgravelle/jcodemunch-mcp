"""A `lang="tsx"` script in a Vue or Svelte file is read as TSX (LEDGER L-39).

Both hand walks picked their grammar with `lang if lang != "tsx" else
"typescript"`, so JSX was a syntax ERROR in a TSX script and error recovery
dropped or re-nested the declarations around it:
`function g(){return <b/>;} function f(){class K{ k(){} }}` published only the
component and a stray `K#class`, where the same script without JSX publishes
`g` and `f`. The #861 class gate and its binding test were made robust to the
mismatch; the walk that publishes the functions was not.

The property: JSX must not change WHAT a script declares, so a TSX script
publishes the ids the same script publishes with each JSX expression replaced
by a plain one.
"""

from __future__ import annotations

import pytest

import jcodemunch_mcp.config as config
from jcodemunch_mcp.parser.extractor import parse_file

#: (TSX body, the same body with every JSX expression replaced by a literal)
BODIES = [
    (
        "function g(){return <b/>;}\nfunction f(){class K{ k(){} }}\n",
        "function g(){return 1;}\nfunction f(){class K{ k(){} }}\n",
    ),
    (
        "const view = <div onClick={() => 1}>{2}</div>;\nfunction after(){ return 3 }\n",
        "const view = 0;\nfunction after(){ return 3 }\n",
    ),
    (
        "export function Row(p: { n: number }) { return <tr><td>{p.n}</td></tr> }\n"
        "export class Svc { run(): number { return 1 } }\n",
        "export function Row(p: { n: number }) { return 0 }\n"
        "export class Svc { run(): number { return 1 } }\n",
    ),
]

FRAMES = {
    "vue": ("Comp.vue", '<script lang="{lang}">\n{body}</script>\n<template><div/></template>\n'),
    # `<script setup>` takes a different dispatch in the Vue walk (review).
    "vue-setup": ("Comp.vue", '<script setup lang="{lang}">\n{body}</script>\n<template><div/></template>\n'),
    "svelte": ("Comp.svelte", '<script lang="{lang}">\n{body}</script>\n<div/>\n'),
}


@pytest.fixture(autouse=True)
def _languages_on(monkeypatch):
    # This box's config can disable languages; the gate is read through
    # config's globals, so patch it there.
    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def _ids(frame_name: str, body: str, lang: str) -> list[str]:
    filename, frame = FRAMES[frame_name]
    language = frame_name.split("-", 1)[0]
    return sorted(s.id for s in parse_file(frame.format(lang=lang, body=body), filename, language))


@pytest.mark.parametrize("language", sorted(FRAMES))
@pytest.mark.parametrize("tsx,plain", BODIES, ids=["function-with-jsx", "jsx-initializer", "component-and-class"])
def test_jsx_does_not_change_what_a_tsx_script_declares(language, tsx, plain):
    expected = _ids(language, plain, "ts")
    assert len(expected) > 1, "the plain script published nothing; the comparison would be vacuous"

    assert _ids(language, tsx, "tsx") == expected


@pytest.mark.parametrize("language", sorted(FRAMES))
def test_the_reported_script_publishes_g_and_f_and_no_stray_class(language):
    filename, _ = FRAMES[language]
    ids = _ids(language, BODIES[0][0], "tsx")

    assert f"{filename}::g#function" in ids
    assert f"{filename}::f#function" in ids
    assert f"{filename}::K#class" not in ids
