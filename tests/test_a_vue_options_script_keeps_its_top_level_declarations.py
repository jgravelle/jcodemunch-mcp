"""A Vue Options API script keeps the declarations beside its options object (LEDGER L-36).

`_parse_vue_symbols` ran the composition walk only when the options walk
found nothing, so a `<script>` holding `export default { methods: {...} }`
lost every top-level function, binding and type declared beside the object:
`function helper() {}`, `const MAX = 5`, `const f = () => 1`, an
`interface`. Only classes survived, because since #861 they come from their
own emitter rather than from the dispatch.

The property: an Options script publishes the union of what its
declarations publish alone and what its options object publishes alone, by
id. And the other direction: the options object's own members are not
published twice, and a script with no options object is unchanged.
"""

from __future__ import annotations

import pytest

import jcodemunch_mcp.config as config
from jcodemunch_mcp.parser.extractor import parse_file

DECLARATIONS = {
    "function": "function helper() { return 1 }\n",
    "constant": "const MAX = 5\n",
    "variable": "let count = 0\n",
    "arrow": "const f = () => 1\n",
    "destructured": "const { a, b } = obj\n",
    "class": "class K { k() {} }\n",
    "mixed": "import x from 'y'\nfunction helper() {}\nconst MAX = 5\nconst f = () => 1\n",
}
TS_DECLARATIONS = {
    "interface": "interface Row { id: number }\n",
    "type": "type Id = string\n",
    "enum": "enum Mode { A, B }\n",
}
OPTIONS = {
    "object": "export default {\n  props: ['p'],\n  data() { return { n: 1 } },\n  methods: { go() {} },\n  computed: { total() { return 1 } },\n}\n",
    "define-component": "export default defineComponent({\n  methods: { go() {} },\n})\n",
    "with-setup": "export default {\n  setup() { const inner = () => 1; return { inner } },\n  methods: { go() {} },\n}\n",
}


@pytest.fixture(autouse=True)
def _languages_on(monkeypatch):
    # This box's config can disable languages; the gate is read through
    # config's globals, so patch it there.
    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def _ids(body: str, lang_attr: str = "") -> list[str]:
    source = f"<script{lang_attr}>\n{body}</script>\n<template><div/></template>\n"
    return sorted(s.id for s in parse_file(source, "Comp.vue", "vue"))


def _cases():
    for d in sorted(DECLARATIONS):
        for o in sorted(OPTIONS):
            yield pytest.param(DECLARATIONS[d], OPTIONS[o], "", id=f"{d}+{o}")
    for d in sorted(TS_DECLARATIONS):
        yield pytest.param(
            TS_DECLARATIONS[d], OPTIONS["object"], ' lang="ts"', id=f"ts-{d}+object"
        )


@pytest.mark.parametrize("declarations,options,lang_attr", list(_cases()))
@pytest.mark.parametrize("order", ["declarations-first", "options-first"])
def test_an_options_script_publishes_its_declarations_and_its_options(
    declarations, options, lang_attr, order
):
    alone = set(_ids(declarations, lang_attr))
    opts = set(_ids(options, lang_attr))
    assert alone - {"Comp.vue::Comp#class"}, (
        "the declarations publish nothing alone; vacuous"
    )
    assert opts - {"Comp.vue::Comp#class"}, (
        "the options object publishes nothing alone; vacuous"
    )
    assert not (alone & opts) - {"Comp.vue::Comp#class"}, (
        "fixture names collide; ~N would renumber"
    )

    body = (
        declarations + options
        if order == "declarations-first"
        else options + declarations
    )
    # A sorted LIST, so a symbol published twice counts twice.
    assert _ids(body, lang_attr) == sorted(alone | opts)


@pytest.mark.parametrize("lang_attr", ["", ' lang="ts"'])
def test_a_define_component_call_publishes_the_options_a_plain_object_does(lang_attr):
    """LEDGER L-43, found fixing L-36: `export default defineComponent({...})`
    handed the CALL to the options reader, whose children are never `pair`s,
    so the methods, computed, props and data of every `defineComponent`
    script were dropped."""
    obj = OPTIONS["object"]
    wrapped = (
        obj.replace("export default {", "export default defineComponent({")
        .rstrip()
        .rstrip("}")
        + "})\n"
    )
    assert wrapped != obj
    plain = _ids(obj, lang_attr)
    assert "Comp.vue::go#method" in plain
    assert _ids(wrapped, lang_attr) == plain


def test_a_declaration_named_like_an_options_member_is_numbered_beside_it():
    """ID MOVE, disclosed. `export default { props: ['p'] }` beside a top-level
    `const props = 1` published only the options `props#constant` (the
    declaration was dropped); both walks run now, so the two are numbered."""
    body = "const props = 1\nexport default { props: ['p'], methods: { go() {} } }\n"
    ids = _ids(body)
    assert "Comp.vue::props#constant~1" in ids
    assert "Comp.vue::props#constant~2" in ids
    assert "Comp.vue::props#constant" not in ids


@pytest.mark.parametrize(
    "data",
    [
        "data() { return { n: 1 } }",
        "data: function () { return { n: 1 } }",
        "data: () => ({ n: 1 })",
    ],
    ids=["method-shorthand", "function-expression", "arrow"],
)
def test_every_spelling_of_data_is_published_once(data):
    """L-43, review round 1: only `data: () => ...` was read. The method
    shorthand is a `method_definition`, not a `pair`, and the bundled grammar
    spells `function () {}` as `function_expression`, not `function`."""
    ids = _ids(f"export default {{\n  {data},\n  methods: {{ go() {{}} }},\n}}\n")
    assert ids.count("Comp.vue::data#function") == 1, ids


@pytest.mark.parametrize(
    "export",
    [
        "export default { methods: { go() {} } } as any\n",
        "export default { methods: { go() {} } } satisfies Component\n",
        "export default ({ methods: { go() {} } })\n",
        "export default defineComponent({ methods: { go() {} } }) as any\n",
        "export default Vue.extend({ methods: { go() {} } })\n",
        # The L-43 residue: a non-null assertion, and a type assertion whose
        # expression is its LAST named child (`type_arguments` comes first).
        "export default defineComponent({ methods: { go() {} } })!\n",
        "export default <Component>{ methods: { go() {} } }\n",
        "export default <Component>defineComponent({ methods: { go() {} } })\n",
        "export default (<any>defineComponent({ methods: { go() {} } }))!\n",
        # A comment inside a wrapper is a named child too (review).
        "export default <Component>/* c */ { methods: { go() {} } }\n",
        "export default ( /* c */ { methods: { go() {} } })\n",
    ],
    ids=[
        "as",
        "satisfies",
        "parenthesized",
        "define-component-as",
        "vue-extend",
        "non-null",
        "type-assertion",
        "type-assertion-call",
        "nested",
        "type-assertion-comment",
        "parenthesized-comment",
    ],
)
def test_a_wrapped_options_object_publishes_what_the_plain_one_does(export):
    """L-43, review round 1: a wrapper is another spelling of the same default
    export. `Vue.extend({...})` is read like `defineComponent({...})`: the
    reader takes any call's object argument."""
    plain = _ids("export default { methods: { go() {} } }\n", ' lang="ts"')
    assert "Comp.vue::go#method" in plain
    assert _ids(export, ' lang="ts"') == plain


@pytest.mark.parametrize(
    "body,moved",
    [
        # The top-level function was the only `data`; the options `data` joins it.
        (
            "function data() { return {} }\nexport default { data: () => ({ n: 1 }), methods: { go() {} } }\n",
            "data#function",
        ),
        (
            "const data = () => ({})\nexport default { data: () => ({ n: 1 }), methods: { go() {} } }\n",
            "data#function",
        ),
        # `defineComponent` fell back to the composition walk, so the top-level
        # `props` was published alone; the options `props` joins it.
        (
            "const props = buildProps({})\nexport default defineComponent({ props: props })\n",
            "props#constant",
        ),
    ],
    ids=["data-function", "data-arrow", "define-component-props"],
)
def test_an_options_member_named_like_a_declaration_moves_it_to_a_numbered_id(
    body, moved
):
    """ID MOVE, disclosed (review round 1): an id published alone on main is
    numbered beside the options member that shares its name and kind."""
    ids = _ids(body)
    assert f"Comp.vue::{moved}~1" in ids and f"Comp.vue::{moved}~2" in ids
    assert f"Comp.vue::{moved}" not in ids


def test_an_options_object_member_is_not_published_by_the_composition_walk():
    """`setup()`'s body and the options object's methods are the options
    walk's to publish: `inner` is a local of `setup`, and `go` is a method,
    never also a function."""
    ids = _ids(OPTIONS["with-setup"])
    assert "Comp.vue::go#method" in ids
    assert not [i for i in ids if "inner" in i]
    assert not [i for i in ids if i.endswith("#function") and "go" in i]


def test_the_expression_wrappers_are_one_set():
    """ONE set of TS/JS expression wrappers, read by the class-expression
    binder and the Vue options reader. The options reader carried a second
    copy until the review of the L-43 residue; a wrapper added to one copy
    would have reached only half the parser (Standing lesson 08-19)."""
    import ast
    import inspect

    import jcodemunch_mcp.parser.extractor as extractor

    tree = ast.parse(inspect.getsource(extractor))
    sets = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Set)
        and any(
            isinstance(e, ast.Constant) and e.value == "non_null_expression"
            for e in node.elts
        )
    ]
    assert len(sets) == 1, (
        f"{len(sets)} literal sets name `non_null_expression`; share _JS_EXPRESSION_WRAPPERS"
    )
