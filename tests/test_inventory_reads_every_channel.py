"""#757: the inventory's recognised set read one channel of four.

`tests/test_grammar_spelled_forms.py` records, per language, the
declaration-shaped node types a grammar emits that the language's recognised
set omits. `_checkable_languages` derived that set from **`symbol_node_types`
alone**, and a spec has four extraction channels — `symbol_node_types`,
`constant_patterns`, `field_patterns` (#735) and `variable_patterns` (#741) —
so three of them were invisible.

⚠⚠ **The tell is the DIRECTION: closing a gap could make the count go UP.**
#735 fixed Java fields through `field_patterns`, so `java.field_declaration`
stayed listed although every Java field is now indexed -- that one is on `main`.
⚠ The second was measured on #743/#744's branch (PR #761, merged since), which
moves `php.property_declaration` out of `symbol_node_types` into the same
channel: the inventory GREW there **in the change that fixes it**. A file that
exists to name unindexed forms was naming indexed ones, and the fix for one
made it worse. ⚠ The two figures that measurement carried are not restated,
because the base moves with every parallel fix.

⚠⚠ **Why it was left alone twice, and what makes widening safe now.**
"Declared in a channel" is not "extracted by it", and #735 is the proof:
`java.field_declaration` sat in `constant_patterns` for years while every
ordinary field was dropped, because that channel required `static final`. A set
unioned over the four channels by DECLARATION would have called the form
recognised and hidden the widest gap #724 found.

So the union ships with the missing half: every form the union newly recognises
carries a sample here, and the sample must prove the CHANNEL extracts that
form — by deletion, not by appearance. A form that stops extracting returns to
the inventory instead of hiding in it.
"""

import pytest

from jcodemunch_mcp.parser.extractor import parse_file
from jcodemunch_mcp.parser.languages import LANGUAGE_REGISTRY

#: The channels a form can reach the index through, besides `symbol_node_types`.
#:
#: ⚠⚠ **IMPORTED, never restated.** A second copy of the channel list can be
#: widened on one side only: add a channel to the recognised set and not here,
#: and the union suppresses inventory rows while this file demands no sample --
#: an unwitnessed widening, which is the one thing this file exists to prevent.
#: The roster itself is gated next door by
#: `test_no_spec_field_is_an_unclassified_channel`, so a fifth field of
#: `LanguageSpec` fails by name instead of silently re-creating #757.
#:
#: ⚠ `variable_patterns` is read with `getattr` because it arrives with #741;
#: `_PENDING_CHANNELS` there records that, and fails once it merges.
from tests.test_grammar_spelled_forms import _EXTRACTION_CHANNELS as _OTHER_CHANNELS

#: `(filename, source, name, kind)` per form the union newly recognises.
#:
#: ⚠⚠ Each row is the evidence for ONE widening. A form with no row is not
#: widened — `test_every_widened_form_has_a_sample` fails by name — because the
#: whole hazard here is a declaration that names a node type nothing extracts.
_CHANNEL_SAMPLES: dict[tuple[str, str], tuple[str, str, str, str]] = {
    ("go", "const_declaration"): (
        "a.go", "package p\n\nconst Probe = 1\n", "Probe", "constant",
    ),
    ("go", "var_declaration"): (
        "a.go", "package p\n\nvar Probe = 1\n",
        "Probe", "variable",
    ),
    ("java", "field_declaration"): (
        "A.java", "class A {\n  private int probe;\n}\n", "probe", "field",
    ),
    # #786. ⚠ A NAMED field: a tuple struct's members carry no identifier in
    # either the grammar or `syn`, so a `struct A(u8);` sample would prove the
    # channel does nothing. ⚠ The node type is shared with an enum variant's
    # body, which the channel refuses on the holder's owner.
    ("rust", "field_declaration"): (
        "a.rs", "struct A {\n    probe: i32,\n}\n", "probe", "field",
    ),
    # #781. A class field cannot be written outside a class, so `A` is extracted
    # too, as with the PHP property below; `probe`/`field` is what the form
    # CONTRIBUTES. One row per spec: the three are copies (#698).
    ("javascript", "field_definition"): (
        "a.js", "class A {\n  probe = 1;\n}\n", "probe", "field",
    ),
    ("typescript", "public_field_definition"): (
        "a.ts", "class A {\n  probe: number = 1;\n}\n", "probe", "field",
    ),
    ("tsx", "public_field_definition"): (
        "a.tsx", "class A {\n  probe: number = 1;\n}\n", "probe", "field",
    ),
    ("javascript", "lexical_declaration"): (
        "a.js", "const PROBE = 1;\n", "PROBE", "constant",
    ),
    ("javascript", "variable_declaration"): (
        "a.js", "var probe = 1;\n",
        "probe", "variable",
    ),
    ("php", "const_declaration"): (
        "a.php", "<?php\nconst PROBE = 1;\n", "PROBE", "constant",
    ),
    # The one sample that needs a wrapper: a PHP property cannot be written
    # outside a class, so `A` is extracted too. The deletion check is what
    # keeps the row honest -- `probe`/`property` is what the form CONTRIBUTES.
    ("php", "property_declaration"): (
        "a.php", "<?php\nclass A {\n    public int $probe = 1;\n}\n",
        "probe", "property",
    ),
    ("rust", "const_item"): (
        "a.rs", "const PROBE: u8 = 1;\n", "PROBE", "constant",
    ),
    ("rust", "static_item"): (
        "a.rs", "static PROBE: u8 = 1;\n", "PROBE", "constant",
    ),
    ("tsx", "lexical_declaration"): (
        "a.tsx", "const PROBE = 1;\n", "PROBE", "constant",
    ),
    ("tsx", "variable_declaration"): (
        "a.tsx", "var probe = 1;\n",
        "probe", "variable",
    ),
    ("typescript", "lexical_declaration"): (
        "a.ts", "const PROBE = 1;\n", "PROBE", "constant",
    ),
    ("typescript", "variable_declaration"): (
        "a.ts", "var probe = 1;\n",
        "probe", "variable",
    ),
}


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    """⚠ Patch `jcodemunch_mcp.config`, never the parser module (the
    `cli/policy.py` trap — `parse_file`'s import of the gate is function-local).
    """
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def _widened_forms() -> dict[tuple[str, str], str]:
    """Every `(language, node_type)` whose INVENTORY ROW the union suppresses.

    ⚠⚠ **Scoped to what the union actually changes, and the scope is the
    reason.** A channel declares plenty of node types that could never appear
    in the inventory -- `python.assignment`, `c.preproc_def`,
    `bash.declaration_command` -- because the inventory only lists forms whose
    kind ends in a declaration suffix AND that the grammar emits. Demanding a
    sample for those would be asking for evidence about a row that does not
    exist, and the pile of rows would then be the thing nobody maintains.

    ⚠ The predicate and the grammar kinds are IMPORTED from the inventory's own
    module rather than restated here. A second copy of "what counts as a
    declaration form" is how the two notions drift, which is the defect class
    this file is in the middle of fixing.
    """
    from tests.test_grammar_spelled_forms import _DECLARATION_SUFFIXES, _grammar_kinds

    out = {}
    for language, spec in sorted(LANGUAGE_REGISTRY.items()):
        declared = set(getattr(spec, "symbol_node_types", None) or {})
        kinds = _grammar_kinds(language)
        if kinds is None:
            continue
        for channel in _OTHER_CHANNELS:
            for node_type in getattr(spec, channel, None) or []:
                if node_type in declared:
                    continue
                if node_type.endswith(_DECLARATION_SUFFIXES) and node_type in kinds:
                    out[(language, node_type)] = channel
    return out


def _pairs(language: str, node_type: str) -> set[tuple[str, str]]:
    filename, source, _name, _kind = _CHANNEL_SAMPLES[(language, node_type)]
    return {(s.name, s.kind) for s in parse_file(source, filename, language)}


def _pairs_without_the_channel(language: str, node_type: str) -> set[tuple[str, str]]:
    """The same sample, parsed with this node type removed from every channel.

    ⚠⚠ **The deletion is what makes a row mean something.** A sample must be
    legal source, so it can carry a wrapper the spec ALSO declares, and a check
    that asks only whether a symbol appears can be answered by the wrapper
    instead of the form — the hollow row review found in #745's guard. Asking
    what the form CONTRIBUTES cannot be answered by anything else.
    """
    import dataclasses

    import jcodemunch_mcp.parser.extractor as extractor

    spec = LANGUAGE_REGISTRY[language]
    stripped = {
        channel: [n for n in (getattr(spec, channel, None) or []) if n != node_type]
        for channel in _OTHER_CHANNELS
        if getattr(spec, channel, None) is not None
    }
    without = dataclasses.replace(spec, **stripped)
    filename, source, _name, _kind = _CHANNEL_SAMPLES[(language, node_type)]
    original = extractor.LANGUAGE_REGISTRY
    extractor.LANGUAGE_REGISTRY = {**LANGUAGE_REGISTRY, language: without}
    try:
        return {(s.name, s.kind) for s in parse_file(source, filename, language)}
    finally:
        extractor.LANGUAGE_REGISTRY = original


def test_every_widened_form_has_a_sample():
    """A form the union recognises with no proof is the defect, not the fix.

    ⚠⚠ This is the half that makes widening honest. Without it the union is a
    claim about four declarations lists, and `java.field_declaration` is the
    standing proof that a declaration can be dead for years — it sat in
    `constant_patterns` while every ordinary Java field was dropped (#735).
    """
    missing = sorted(
        f"{language}.{node_type} (via {channel})"
        for (language, node_type), channel in _widened_forms().items()
        if (language, node_type) not in _CHANNEL_SAMPLES
    )
    assert not missing, (
        f"{len(missing)} form(s) are recognised through a channel with no "
        f"sample proving the channel extracts them: {missing}. Add the sample, "
        f"or the inventory is trusting a declaration (#757)."
    )


def test_no_sample_describes_a_form_that_is_not_widened():
    """The other direction: a row here for a form the union does not recognise.

    ⚠ A stale row is harmless to the product and corrosive to the file — it
    reads as evidence for a widening that is not happening, which is how a
    table stops describing the thing it is named after.
    """
    widened = set(_widened_forms())
    stale = sorted(f"{lang}.{nt}" for (lang, nt) in _CHANNEL_SAMPLES if (lang, nt) not in widened)
    assert not stale, f"samples for forms no channel declares: {stale}"


@pytest.mark.parametrize(
    "language,node_type",
    sorted(_CHANNEL_SAMPLES),
    ids=[f"{lang}.{nt}" for lang, nt in sorted(_CHANNEL_SAMPLES)],
)
def test_a_widened_form_actually_extracts(language, node_type):
    """The property: a form is recognised because it EXTRACTS, not because a
    list names it.

    Two halves, the second by deletion:

    1. the sample yields the expected `(name, kind)`;
    2. it stops yielding it when the node type leaves every channel.
    """
    _filename, _source, name, kind = _CHANNEL_SAMPLES[(language, node_type)]
    with_form = _pairs(language, node_type)
    assert (name, kind) in with_form, (
        f"{language}.{node_type} is recognised through a channel and its sample "
        f"yields {sorted(with_form) or 'NOTHING'} -- the channel declaration is "
        f"dead, which is #735's Java case and exactly what the inventory must "
        f"keep reporting (#757)."
    )

    contributed = with_form - _pairs_without_the_channel(language, node_type)
    assert (name, kind) in contributed, (
        f"{language}.{node_type}'s sample still yields ({name!r}, {kind!r}) with "
        f"the node type removed from every channel, so this row proves nothing "
        f"about the form -- something else in the sample is supplying it."
    )


def test_no_inventory_row_is_a_form_some_channel_extracts():
    """⚠⚠ The property #757 is about, asserted over the frozen inventory.

    A row in the inventory says "this grammar spells a declaration form and
    nothing here recognises it". After the union, a form reachable through ANY
    channel is recognised — so a row that names one is a false positive, and
    before this change there were two (`java.field_declaration`, and
    `php.property_declaration` the moment #743 landed).
    """
    from tests.test_grammar_spelled_forms import _current_inventory

    widened = set(_widened_forms())
    offenders = sorted(
        f"{language}.{node_type}"
        for language, forms in _current_inventory().items()
        for node_type in forms
        if (language, node_type) in widened
    )
    assert not offenders, (
        f"the inventory lists {offenders} as unrecognised while a channel "
        f"declares them -- the recognised set is reading fewer channels than "
        f"the product extracts through (#757)."
    )
