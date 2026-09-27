"""A Vue component with a `<script>` and a `<script setup>` indexes both (LEDGER L-44).

Vue 3 pairs a plain `<script>` (for `name`, `inheritAttrs`, a named export)
with `<script setup>`, which holds the component's code. `_parse_vue_symbols`
stopped at the FIRST `script_element`, so whichever block came second was
never read: usually the `<script setup>`, i.e. the component itself.

The property: a two-script component publishes, by id, the union of what
each block publishes as the only script of the component, in either order
and whatever each block's `lang`; each symbol's line points into its own
block. And the other direction: a one-script component is unchanged.
"""

from __future__ import annotations

import pytest

import jcodemunch_mcp.config as config
from jcodemunch_mcp.parser.extractor import parse_file

PLAIN = {
    "options": "export default { inheritAttrs: false, methods: { go() {} } }\nconst A = 1\n",
    "named-export": "export const shared = 1\nexport function helper() {}\n",
    "empty": "\n",
}
SETUP = {
    "composition": "const B = ref(2)\nfunction onClick() {}\nconst f = () => 1\n",
    "types": "interface Row { id: number }\nconst rows = ref<Row[]>([])\n",
    "class": "class K { k() {} }\nconst k = new K()\n",
}
LANGS = {"js": "", "ts": ' lang="ts"'}


@pytest.fixture(autouse=True)
def _languages_on(monkeypatch):
    # This box's config can disable languages; the gate is read through
    # config's globals, so patch it there.
    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def _block(tag: str, body: str) -> str:
    return f"<{tag}>\n{body}</script>\n"


def _ids(*blocks: str) -> list[str]:
    source = "".join(blocks) + "<template><div/></template>\n"
    return sorted(s.id for s in parse_file(source, "Comp.vue", "vue"))


def _cases():
    for p in sorted(PLAIN):
        for s in sorted(SETUP):
            for pl, sl in (("js", "js"), ("ts", "ts"), ("js", "ts")):
                yield pytest.param(p, s, pl, sl, id=f"{p}+{s}-{pl}-{sl}")


@pytest.mark.parametrize("plain,setup,plain_lang,setup_lang", list(_cases()))
@pytest.mark.parametrize("order", ["script-first", "setup-first"])
def test_a_two_script_component_publishes_both_blocks(
    plain, setup, plain_lang, setup_lang, order
):
    a = _block(f"script{LANGS[plain_lang]}", PLAIN[plain])
    b = _block(f"script setup{LANGS[setup_lang]}", SETUP[setup])
    alone_a, alone_b = set(_ids(a)), set(_ids(b))
    assert alone_b - {"Comp.vue::Comp#class"}, (
        "the setup block publishes nothing alone; vacuous"
    )
    assert not (alone_a & alone_b) - {"Comp.vue::Comp#class"}, (
        "fixture names collide; ~N would renumber"
    )

    both = _ids(a, b) if order == "script-first" else _ids(b, a)
    # A sorted LIST, so a symbol published twice (or a second component
    # symbol) counts twice.
    assert both == sorted(alone_a | alone_b)


@pytest.mark.parametrize("order", ["script-first", "setup-first"])
def test_each_symbol_points_into_its_own_block(order):
    a = _block("script", "const A = 1\n\n\nfunction helper() {}\n")
    b = _block("script setup", "const B = 2\nfunction onClick() {}\n")
    source = (
        a + b if order == "script-first" else b + a
    ) + "<template><div/></template>\n"
    lines = source.split("\n")
    symbols = [s for s in parse_file(source, "Comp.vue", "vue") if s.kind != "class"]
    assert sorted(s.name for s in symbols) == ["A", "B", "helper", "onClick"]
    for s in symbols:
        assert s.name in lines[s.line - 1], (
            f"{s.id} line {s.line}: {lines[s.line - 1]!r}"
        )


def test_a_script_with_no_body_does_not_hide_the_other_block():
    """`<script src="./x.js"></script>` has no text to read; it used to end the
    parse with NOTHING published, not even the component."""
    ids = _ids(
        '<script src="./x.js"></script>\n', _block("script setup", "const B = 2\n")
    )
    assert "Comp.vue::Comp#class" in ids
    assert "Comp.vue::B#constant" in ids


@pytest.mark.parametrize("order", ["script-first", "setup-first"])
def test_a_name_declared_in_both_blocks_moves_to_a_numbered_id(order):
    """ID MOVE, disclosed. The block read first published `shared#constant`
    alone; the other block's `shared` joins it, so both are numbered."""
    a = _block("script", "export const shared = 1\n")
    b = _block("script setup", "const shared = ref(2)\n")
    ids = _ids(a, b) if order == "script-first" else _ids(b, a)
    assert "Comp.vue::shared#constant~1" in ids and "Comp.vue::shared#constant~2" in ids
    assert "Comp.vue::shared#constant" not in ids


@pytest.mark.parametrize("body", [PLAIN["options"], SETUP["composition"]])
def test_a_one_script_component_is_unchanged(body):
    """A single block publishes one component and no duplicate id. Only that:
    the unchanged ids of one-script components are shown by the corpus id
    diff in the PR (zero changes), not pinned here (review round 1)."""
    ids = _ids(_block("script", body))
    assert ids.count("Comp.vue::Comp#class") == 1
    assert len(ids) == len(set(ids))
