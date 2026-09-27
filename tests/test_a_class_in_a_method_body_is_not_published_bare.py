"""A class inside a method or generator body is never published bare (LEDGER L-38).

The Vue and Svelte hand walks stop at a function declaration, an arrow or a
function expression (`skip_recurse`), but walked INTO an object method
(`setup() {}`, `*gen() {}`, `get g() {}`, `async load() {}`) and a generator
function, reached the class inside and published it as `K#class` owned by the
component. The same text in a `.js` file names it `setup.K`: the method owns
it. #861 already listed the node types the generic walk treats as owners
(`_CLASS_GATE_OWNERS`); the hand walks' stop list was a second, shorter copy.

The property: every class a Vue or Svelte script publishes is a class the same
text publishes in a `.js` file, under the same qualified name.
"""

from __future__ import annotations

import pytest

import jcodemunch_mcp.config as config
from jcodemunch_mcp.parser.extractor import parse_file

CASES = {
    "object-method": "export default { setup() { class K { k() {} } return {} } }\n",
    "options-method": "export default { methods: { go() { class K { k() {} } } } }\n",
    "generator-method": "export default { *gen() { class K { k() {} } } }\n",
    "getter": "export default { get g() { class K { k() {} } return 1 } }\n",
    "async-method": "export default { async load() { class K { k() {} } } }\n",
    "nested-object-method": "const api = { inner: { run() { class K { k() {} } } } }\n",
    "generator-function": "function* gen() { class K { k() {} } }\n",
    # Already right, and must stay so: the `.js` file publishes these bare.
    "function-expression": "const f = function () { class K { k() {} } }\n",
    "generator-expression": "const g = function* () { class K { k() {} } }\n",
    "arrow": "const f = () => { class K { k() {} } }\n",
    "class-method": "class Outer { m() { class K { k() {} } } }\n",
    "top-level": "class K { k() {} }\n",
}
FRAMES = {
    "vue": ("Comp.vue", "<script>\n{b}</script>\n<template><div/></template>\n"),
    "vue-setup": ("Comp.vue", "<script setup>\n{b}</script>\n<template><div/></template>\n"),
    "svelte": ("Comp.svelte", "<script>\n{b}</script>\n<div/>\n"),
}


@pytest.fixture(autouse=True)
def _languages_on(monkeypatch):
    # This box's config can disable languages; the gate is read through
    # config's globals, so patch it there.
    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def _classes(source: str, filename: str, language: str) -> set[str]:
    return {s.qualified_name for s in parse_file(source, filename, language) if s.kind == "class"}


@pytest.mark.parametrize("frame", sorted(FRAMES))
@pytest.mark.parametrize("case", sorted(CASES))
def test_every_published_class_is_one_the_js_file_publishes(frame, case):
    body = CASES[case]
    filename, template = FRAMES[frame]
    component = filename.split(".")[0]
    js = _classes(body, "a.js", "javascript")
    published = _classes(template.format(b=body), filename, frame.split("-")[0]) - {component}

    assert published <= js, (published - js, js)


@pytest.mark.parametrize("frame", sorted(FRAMES))
@pytest.mark.parametrize(
    "case", ["top-level", "arrow", "function-expression", "generator-expression", "class-method"]
)
def test_a_class_the_js_file_publishes_bare_is_still_published(frame, case):
    """The other direction, so the property cannot pass by publishing nothing."""
    filename, template = FRAMES[frame]
    published = _classes(template.format(b=CASES[case]), filename, frame.split("-")[0])

    assert published & _classes(CASES[case], "a.js", "javascript"), published


def test_the_hand_walks_stop_where_the_generic_walk_gives_an_owner():
    """One list, not two: the hand walks' stop set contains the gate's owners."""
    from jcodemunch_mcp.parser import extractor

    assert extractor._CLASS_GATE_OWNERS <= extractor._HAND_WALK_STOP_TYPES


def test_the_stop_set_names_node_types_the_grammar_actually_produces():
    """`function` alone is the keyword leaf in the bundled grammars; a
    function expression is `function_expression` and a generator expression
    `generator_function`. A stop set naming only the old spelling stops
    nothing (review)."""
    from jcodemunch_mcp.parser import extractor
    from jcodemunch_mcp.parser.grammar_pack import get_parser

    # ⚠ The node type is READ from the grammar, never filtered through a
    # list of names: a list lets a renamed node drop out and the test pass
    # with the walk recursing into it (review round 2, the round-1 defect).
    source = b"const a = function () {}; const b = function* () {}; const c = () => {};\n"
    for grammar in ("javascript", "typescript", "tsx"):
        values = []
        stack = [get_parser(grammar).parse(source).root_node]
        while stack:
            node = stack.pop()
            if node.type == "variable_declarator":
                value = node.child_by_field_name("value")
                assert value is not None, (grammar, node)
                values.append(value.type)
            stack.extend(node.children)

        assert len(values) == 3, (grammar, values)
        missing = set(values) - extractor._HAND_WALK_STOP_TYPES
        assert not missing, (grammar, missing)


#: A helper declared in a function or method body is never a component member.
#: For a method or `function*` declaration the `.js` file agrees (`setup.inc`);
#: for an arrow or a function/generator EXPRESSION the `.js` file publishes the
#: helper bare, and the walks have always hidden it there by design ("not into
#: function bodies, to avoid inner helpers"), for arrows since before L-38.
HELPERS = {
    "options-setup": "export default { setup() { function inc() {} return { inc } } }\n",
    "store-method": "const store = { add(x) { function h() {} return x } }\n",
    "function-expression": "const f = function () { function inner() {} }\n",
    "generator-expression": "const g = function* () { function inner() {} }\n",
    "arrow": "const a = () => { function inner() {} }\n",
    "generator-function": "function* gen() { function inner() {} }\n",
}


@pytest.mark.parametrize("frame", sorted(FRAMES))
@pytest.mark.parametrize("case", sorted(HELPERS))
def test_a_helper_in_a_function_body_is_not_a_component_member(frame, case):
    filename, template = FRAMES[frame]
    names = {
        s.name
        for s in parse_file(template.format(b=HELPERS[case]), filename, frame.split("-")[0])
    }

    assert not names & {"inc", "h", "inner"}, names
