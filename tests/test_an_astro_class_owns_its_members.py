"""A class in an Astro frontmatter or `<script>` block owns its members (LEDGER L-37).

`_parse_astro_symbols` re-parses the frontmatter as TypeScript and each inline
`<script>` as JS/TS, then rewraps every symbol with `parent=component_symbol`.
The ids were right (`Comp.K.k`), but every member's PARENT was the component,
not its class: `K.k`, `A.m`, `outer.Inner.i`, `script1.S.s` all hung off
`Comp`. #861 fixed the same ownership for Vue and Svelte; the #861 review
found Astro still had it.

The rule is the one the plain parse already gives: a rewrapped symbol's parent
is its OWN parse's parent, rewrapped the same way, and the component only
when the plain parse gave none. The property test asks that authority for
every symbol instead of listing the spellings someone remembered.
"""

from __future__ import annotations

import pytest

import jcodemunch_mcp.config as config
from jcodemunch_mcp.parser.extractor import parse_file

FRONTMATTER = """class K { k() { return 1 } f = 2 }
abstract class A { abstract m(): void; n() {} }
const C = class { c() {} }
export default class { d() {} }
function outer() { class Inner { i() {} } }
"""
SCRIPT = "class S { s() {} }\n"
SOURCE = f"---\n{FRONTMATTER}---\n<div id=\"x\"></div>\n<script>\n{SCRIPT}</script>\n"


@pytest.fixture(autouse=True)
def _languages_on(monkeypatch):
    # This box's config can disable languages; the gate is read through
    # config's globals, so patch it there (the cli/policy.py trap).
    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def _by_id(symbols):
    return {s.id: s for s in symbols}


@pytest.mark.parametrize(
    "member,owner",
    [
        ("Comp.K.k#method", "Comp.K#class"),
        ("Comp.K.f#field", "Comp.K#class"),
        ("Comp.A.m#method", "Comp.A#class"),
        ("Comp.A.n#method", "Comp.A#class"),
        ("Comp.C.c#method", "Comp.C#class"),
        ("Comp.outer.Inner.i#method", "Comp.outer.Inner#class"),
        ("Comp.default.d#method", "Comp.default#class"),
        ("Comp.outer.Inner#class", "Comp.outer#function"),
        ("Comp.script1.S.s#method", "Comp.script1.S#class"),
    ],
)
def test_a_member_is_owned_by_its_class_not_the_component(member, owner):
    syms = _by_id(parse_file(SOURCE, "Comp.astro", "astro"))

    assert syms[f"Comp.astro::{member}"].parent == f"Comp.astro::{owner}"


@pytest.mark.parametrize("top", ["Comp.K#class", "Comp.A#class", "Comp.outer#function", "Comp.x#constant", "Comp.script1.S#class"])
def test_a_top_level_symbol_is_still_owned_by_the_component(top):
    syms = _by_id(parse_file(SOURCE, "Comp.astro", "astro"))

    assert syms[f"Comp.astro::{top}"].parent == "Comp.astro::Comp#class"


_RAZOR_SCRIPT = "class S { s() {} }\n"


@pytest.mark.parametrize(
    "block,language,prefix,container",
    [
        (FRONTMATTER, "typescript", "Comp", ("astro", "Comp.astro", "Comp")),
        (SCRIPT, "javascript", "Comp.script1", ("astro", "Comp.astro", "Comp")),
        (_RAZOR_SCRIPT, "javascript", "V", ("razor", "V.razor", "V")),
    ],
    ids=["astro-frontmatter", "astro-script", "razor-script"],
)
def test_every_rewrapped_parent_is_the_plain_parse_parent(block, language, prefix, container):
    """The authority is the block parsed on its own: its parent, qualified
    the same way, or the container when it has none."""
    lang, filename, root = container
    source = SOURCE if lang == "astro" else RAZOR
    plain = parse_file(block, f"plain.{'ts' if language == 'typescript' else 'js'}", language)
    plain_qn = {s.id: s.qualified_name for s in plain}
    outer = {s.qualified_name: s for s in parse_file(source, filename, lang)}
    assert plain, "the block parsed to nothing; the property would be vacuous"

    for sym in plain:
        rewrapped = outer[f"{prefix}.{sym.qualified_name}"]
        expected = f"{prefix}.{plain_qn[sym.parent]}" if sym.parent in plain_qn else root
        parent_qn = next(s.qualified_name for s in outer.values() if s.id == rewrapped.parent)
        assert parent_qn == expected, (sym.qualified_name, parent_qn, expected)


def test_no_symbol_id_moves():
    """Only `parent` changes; every id is the one main already published."""
    ids = sorted(s.id for s in parse_file(SOURCE, "Comp.astro", "astro"))

    assert ids == sorted(
        f"Comp.astro::{q}"
        for q in [
            "Comp#class", "Comp.K#class", "Comp.K.k#method", "Comp.K.f#field",
            "Comp.A#class", "Comp.A.m#method", "Comp.A.n#method",
            "Comp.C#class", "Comp.C.c#method", "Comp.outer#function",
            "Comp.outer.Inner#class", "Comp.outer.Inner.i#method", "Comp.x#constant",
            "Comp.script1.S#class", "Comp.script1.S.s#method",
            *EXTRA_IDS,
        ]
    )


#: The anonymous default export, as main publishes it.
EXTRA_IDS = ["Comp.default#class", "Comp.default.d#method"]


# Razor has the same rewrap (a JS `<script>` and a C# `@code` block, each
# rewrapped with `parent=view_symbol`), so the rule is shared, not per parser.
RAZOR = """@page "/x"
<div id="a"></div>
<script>
class S { s() {} }
</script>
@code {
    private int count = 0;
    void Inc() { count++; }
    class Helper { public void Go() {} }
}
"""


@pytest.mark.parametrize(
    "member,owner",
    [
        ("V.S.s#method", "V.S#class"),
        ("V.Helper.Go#method", "V.Helper#class"),
        # Direct `@code` members belong to the shim class, which is not
        # published; the view stands in for it.
        ("V.Inc#method", "V#class"),
        ("V.count#field", "V#class"),
        ("V.Helper#class", "V#class"),
        ("V.S#class", "V#class"),
    ],
)
def test_a_razor_member_is_owned_by_its_class(member, owner):
    syms = _by_id(parse_file(RAZOR, "V.razor", "razor"))

    assert syms[f"V.razor::{member}"].parent == f"V.razor::{owner}"


@pytest.mark.parametrize(
    "source,filename,language,owners",
    [
        (
            "---\nif (a) { class K { k() {} } } else { class K { j() {} } }\n---\n<p></p>\n",
            "C.astro",
            "astro",
            {"C.K.k#method": "C.K#class~1", "C.K.j#method": "C.K#class~2"},
        ),
        (
            "<script>\nclass Helper { go() {} }\n</script>\n@code {\n    class Helper { public void Go() {} }\n}\n",
            "V.razor",
            "razor",
            {"V.Helper.go#method": "V.Helper#class~1", "V.Helper.Go#method": "V.Helper#class~2"},
        ),
    ],
    ids=["astro-if-else-twins", "razor-script-and-code-twins"],
)
def test_same_named_twins_each_own_their_own_members(source, filename, language, owners):
    """Two classes with one name get `~1`/`~2`, and each keeps its members.
    The rewrap drops the block's own `~N`; `parse_file`'s outer pass renumbers
    and repoints by containment, and this pins that the two together hold
    (review: nothing pinned the dependency)."""
    syms = {s.id: s for s in parse_file(source, filename, language)}

    for member, owner in owners.items():
        match = [s for i, s in syms.items() if i.split("::", 1)[1].split("~")[0] == member]
        assert len(match) == 1, (member, sorted(syms))
        assert match[0].parent == f"{filename}::{owner}", (member, match[0].parent)
