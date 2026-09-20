"""#712: a node type declared in one spec map and absent from its pair is dropped.

`LanguageSpec.symbol_node_types` says which tree-sitter nodes become symbols.
`LanguageSpec.name_fields` says which field carries each one's name.
`_extract_name` returns ``None`` for any node type missing from the second map
(`extractor.py:1081-1082`) unless an earlier language special case handles it,
and a symbol with no name is dropped -- so a node type listed in the first map
alone is advertised as supported and silently never appears.

⚠⚠ This is NOT #698 repeated. #698 was a node type missing from the spec
entirely, and its lesson ("list the node type") is satisfied here: the
JavaScript generator IS listed. An inventory check of `symbol_node_types`
passes while the symbol is still lost. The two maps are a contract and nothing
asserted they agree.

The ratchet below found a second defect on its first run -- Haskell's
`name_fields` was empty, so the whole language extracted nothing (#722, fixed
since). That is what a property test is for, and it is why the reported list was not the list.
"""

import ast
import inspect

import pytest

from jcodemunch_mcp.parser.extractor import parse_file
from jcodemunch_mcp.parser.languages import LANGUAGE_REGISTRY


# A node type may legitimately be absent from `name_fields` when `_extract_name`
# resolves its name BEFORE consulting that map. Each entry names the language
# and the reason, and `test_every_exception_has_a_real_special_case` proves the
# code path exists -- an entry cannot be used to silence a gap that has no
# handler behind it.
#
# ⚠ `haskell` was never here. Its five node types had no handler and no name
# field, so the language extracted nothing; that was #722, tracked in
# `_KNOWN_GAPS` until it was fixed rather than excused with an entry.
_RESOLVED_BEFORE_NAME_FIELDS = {
    "csharp": {
        "field_declaration": "walks variable_declaration -> variable_declarator",
        "event_field_declaration": "same walk as field_declaration",
        # #714. These three have no identifier to borrow: the grammar gives an
        # operator TOKEN, a conversion direction plus a target type, and
        # nothing at all for an indexer. The name is BUILT in the csharp
        # branch, which is what an entry here is required to point at.
        "operator_declaration": "csharp branch builds `operator +`",
        "conversion_operator_declaration": "csharp branch builds `explicit operator string`",
        "indexer_declaration": "csharp branch builds `this[]`",
    },
    "swift": {
        # #733. Both HAVE a `name` field and on both it points at the wrong
        # thing -- a `pattern` whose text carries the binding keyword
        # (`var value`), and the subscript's RETURN type. The swift branch
        # resolves the first and BUILDS the second, #714's remedy for a form
        # with no identifier to borrow.
        "protocol_property_declaration": "swift branch descends the pattern to its identifier",
        "subscript_declaration": "swift branch builds `subscript`",
    },
    "python": {"type_alias_statement": "explicit branch for the 3.12 type statement"},
    "dart": {
        "type_alias": "explicit branch keyed on the language",
        # ⚠ These two are keyed on the NODE TYPE with no language guard, their
        # Dart origin recorded only in a comment (`extractor.py:1009,1017`).
        # The first draft of this list said "resolved through the enclosing
        # declaration", which is not a thing that happens; the stricter guard
        # rejected it.
        "method_signature": "generic branch on the node type (no language guard)",
        "mixin_declaration": "generic branch on the node type (no language guard)",
    },
    "gleam": {
        "type_definition": "explicit branch",
        "type_alias": "explicit branch",
    },
    "kotlin": {
        # #732. Two levels down (`variable_declaration > simple_identifier`),
        # so `name_fields` cannot reach it; the branch also DECLINES when
        # `kotlin_property_is_constant` says the constant channel owns the
        # declaration, which is what stops `const val` being emitted twice.
        "property_declaration": "explicit kotlin branch, declines to the constant channel",
        "class_declaration": "blanket kotlin branch",
        "function_declaration": "blanket kotlin branch",
        "object_declaration": "blanket kotlin branch",
        "type_alias": "blanket kotlin branch",
    },
}

# language: (node types, issue) -- a gap that is TRACKED, not tolerated.
# EMPTY since #722 (Haskell's five unnamed node types) was fixed; the guard
# loops inside the test, so an empty register passes rather than skipping.
_KNOWN_GAPS: dict[str, tuple[set[str], str]] = {}


def _unpaired(spec) -> set[str]:
    declared = set(getattr(spec, "symbol_node_types", None) or {})
    named = set(getattr(spec, "name_fields", None) or {})
    return declared - named


@pytest.mark.parametrize("language", sorted(LANGUAGE_REGISTRY))
def test_every_declared_node_type_can_be_named(language):
    """The property: a node type we advertise must have a way to get its name."""
    spec = LANGUAGE_REGISTRY[language]
    excused = set(_RESOLVED_BEFORE_NAME_FIELDS.get(language, {}))
    known_gap = set(_KNOWN_GAPS.get(language, (set(), ""))[0])

    unpaired = _unpaired(spec) - excused - known_gap

    assert not unpaired, (
        f"{language}: {sorted(unpaired)} declared in symbol_node_types with no "
        f"name_fields entry and no special case. Each will extract as an unnamed "
        f"node and be dropped -- listed as supported, never emitted (#712)."
    )


def test_the_scan_reaches_every_spec():
    """Non-vacuity floor: the parametrization must actually cover the registry.

    A filter bug that emptied `LANGUAGE_REGISTRY`, or a `_unpaired` that always
    returned an empty set, would make every case above pass while checking
    nothing. Both are the failure mode this file exists to catch, one level up.
    """
    assert len(LANGUAGE_REGISTRY) >= 70, len(LANGUAGE_REGISTRY)
    declared = sum(len(getattr(s, "symbol_node_types", None) or {})
                   for s in LANGUAGE_REGISTRY.values())
    # Measured on THIS tree, 2026-09-16: 79 specs, 131 declared node types.
    # The floors sit below that with room to grow; they exist to catch a
    # registry that collapsed, not to pin a count.
    # ⚠ Two corrections, both in the test written to catch hand-typed numbers.
    # The first draft asserted `>= 200` from memory and failed on a correct
    # tree. The comment then said 129, measured BEFORE this PR added its own
    # two entries to the TS and TSX specs -- stale by two, in a comment whose
    # only job is to record a measurement.
    assert declared >= 120, declared


def test_the_predicate_sees_a_planted_gap():
    """`_unpaired` must fire on the defect, not merely return empty everywhere.

    A set difference that always came out empty would make the whole file green
    against any spec, which is the shape [[a-set-cannot-count]] names.
    """
    class _Spec:
        symbol_node_types = {"a_declaration": "function", "b_declaration": "class"}
        name_fields = {"b_declaration": "name"}

    assert _unpaired(_Spec()) == {"a_declaration"}


def _extract_name_branches() -> list[str]:
    """The source of each top-level `if` in `_extract_name`, one string each.

    The handlers are `if` branches rather than dispatch-table entries, so the
    check reads source -- but it reads each branch SEPARATELY, because a scan
    over the whole function lets one language be excused by another's text.
    """
    import ast
    import inspect
    import textwrap

    from jcodemunch_mcp.parser import extractor

    source = textwrap.dedent(inspect.getsource(extractor._extract_name))
    func = ast.parse(source).body[0]
    return [
        ast.get_source_segment(source, node) or ""
        for node in func.body
        if isinstance(node, ast.If)
    ]


_ALL_LANGUAGES = frozenset(LANGUAGE_REGISTRY)


def _handles(branch: str, language: str, node_type: str) -> bool:
    """Does this branch handle ``node_type`` FOR ``language``?

    Two ways to qualify, and the second is why a bare substring scan was not
    enough. A branch may key on the language (`spec.ts_language == "gleam"`) or
    on the node type alone -- `mixin_declaration` and `method_signature` are
    handled by node type with no language guard at all, their Dart origin
    recorded only in a comment. A node-type-only branch is GENERIC, so it
    qualifies for any language; a branch naming a DIFFERENT language does not.
    """
    if node_type not in branch:
        return False
    named = {lang for lang in _ALL_LANGUAGES if f'"{lang}"' in branch}
    return not named or language in named


def test_every_exception_has_a_real_special_case():
    """An excuse must have a handler behind it, and the handler must be ITS OWN.

    ⚠⚠ The first version of this test was itself an escape hatch and was caught
    in review by the pass this repo requires and I had not run: three bogus
    exemptions -- `dart/totally_made_up_node`, `kotlin/another_fake_node` and
    `python/mixin_declaration` (a node type only dart handles) -- were planted
    and it stayed green. Three holes: a `language in {"kotlin"}` clause skipped
    the node-type assertion entirely, an `or language in {"dart"}` did the same,
    and the surviving check was a bare substring over the WHOLE function, so any
    language could borrow another's branch text. Neither clause bought anything:
    every excused node type appears in its own language's branch.

    [[a-ratchet-can-pass-against-the-defect-it-names]] -- run a text-scanning
    ratchet against the reintroduced defect, never only against the fixed tree.
    `test_the_exception_guard_rejects_a_planted_excuse` is that pass, and it is
    part of this file now rather than something a reviewer has to think to do.
    """
    branches = _extract_name_branches()
    assert branches, "no top-level branches found; the parse of _extract_name failed"

    for language, entries in _RESOLVED_BEFORE_NAME_FIELDS.items():
        declared = set(LANGUAGE_REGISTRY[language].symbol_node_types or {})
        for node_type in entries:
            # An excuse for a node type the spec does not even declare is
            # meaningless, and it is what the planted `python/mixin_declaration`
            # case is: a real node type, handled elsewhere, excused for a
            # language that never declares it.
            assert node_type in declared, (
                f"{language}/{node_type} is excused from the name_fields rule but "
                f"{language} does not declare that node type at all"
            )
            assert any(_handles(b, language, node_type) for b in branches), (
                f"{language}/{node_type} is excused with no branch in _extract_name "
                f"that handles it -- either for {language} by name, or generically"
            )


@pytest.mark.parametrize(
    "language,node_type",
    [
        ("dart", "totally_made_up_node"),
        ("kotlin", "another_fake_node"),
        # A real node type, handled only by ANOTHER language's branch. This is
        # the one a whole-function substring scan cannot see.
        ("python", "mixin_declaration"),
    ],
)
def test_the_exception_guard_rejects_a_planted_excuse(language, node_type, monkeypatch):
    """The non-vacuity pass for the guard above: it must FAIL on a bogus entry."""
    planted = {k: dict(v) for k, v in _RESOLVED_BEFORE_NAME_FIELDS.items()}
    planted.setdefault(language, {})[node_type] = "planted by the test"
    monkeypatch.setitem(globals(), "_RESOLVED_BEFORE_NAME_FIELDS", planted)

    with pytest.raises(AssertionError):
        test_every_exception_has_a_real_special_case()


def test_a_javascript_generator_declaration_yields_its_symbol():
    """The reported case, asserted through the product rather than the maps."""
    symbols = parse_file(
        "function* gen(a) { yield a; }\nfunction plain(b) { return b; }\n",
        "x.js",
        "javascript",
    )
    names = {s.name for s in symbols}

    assert "plain" in names, "the control is missing; the fixture proves nothing"
    assert "gen" in names, (
        "a generator declaration is listed in JAVASCRIPT_SPEC.symbol_node_types "
        "and produced no symbol"
    )


def test_a_generator_carries_its_parameters_like_an_ordinary_function():
    """`param_fields` was missing the same entry, and nothing else would say so.

    Naming the symbol is half of it: a generator with no parameter list reads as
    a zero-argument function everywhere the signature is shown.
    """
    symbols = {s.name: s for s in parse_file(
        "function* gen(a, b) { yield a; }\nfunction plain(a, b) { return a; }\n",
        "x.js",
        "javascript",
    )}

    assert set(symbols) >= {"gen", "plain"}

    # ⚠ NOT equality with the plain function's signature: the first draft
    # asserted that and would have REQUIRED the `*` to be dropped, i.e. it
    # graded a correct signature (`function* gen(a, b)`) as wrong. The property
    # is that the parameter list survives, and that the form is still visible.
    assert "(a, b)" in symbols["gen"].signature, symbols["gen"].signature
    assert "*" in symbols["gen"].signature, (
        "the generator's signature no longer shows it is a generator"
    )
    assert "(a, b)" in symbols["plain"].signature, "control"


def test_an_async_generator_declaration_also_yields_its_symbol():
    """The other spelling of the same form, checked rather than assumed.

    `async function*` parses to the same node type, so this passes once the
    entry exists -- which is worth pinning, because if a future grammar splits
    it into its own node type this file is where that shows up.
    """
    names = {s.name for s in parse_file(
        "async function* agen(a) { yield a; }\n", "x.js", "javascript"
    )}

    assert "agen" in names


def test_typescript_generators_are_covered_too():
    """A DIFFERENT shape in the same family, found by running the fixture.

    ⚠ JavaScript lists the node type and cannot name it (#712's shape). TS and
    TSX did not list it at all, which is #698's shape -- and #698 was fixed in
    these two specs, for `abstract_class_declaration`, without anyone asking
    what else the grammar spells that they do not list. Both spellings of the
    same loss are pinned here so neither can come back alone.
    """
    for language, filename in (("typescript", "x.ts"), ("tsx", "x.tsx")):
        names = {s.name for s in parse_file(
            "function* gen(a: number) { yield a; }\n", filename, language
        )}
        assert "gen" in names, f"{language} dropped a generator declaration"


def test_a_known_gap_is_still_a_gap():
    """A tracked gap must FAIL once it is fixed, or it becomes an exemption.

    `_KNOWN_GAPS` excused Haskell's five node types while #722 was open, and is
    empty now that it is fixed. Nothing else would notice when a tracked issue closes: the entry would sit there permanently
    excusing a language that no longer needs it, which is how a gap becomes a
    hole. This fails the moment the gap is repaired, and the failure message
    says the remedy is to DELETE the entry, not to widen it.
    """
    for language, (node_types, why) in _KNOWN_GAPS.items():
        still_unpaired = _unpaired(LANGUAGE_REGISTRY[language])
        assert node_types <= still_unpaired, (
            f"{language}: {sorted(node_types - still_unpaired)} is no longer "
            f"unpaired, so this _KNOWN_GAPS entry is stale. If the gap is fixed "
            f"({why}), DELETE the entry -- do not adjust it to match."
        )


#: Every spec channel whose members are extracted by their OWN dispatcher, with
#: the kind it must produce and one sample per declaring language.
#:
#: ⚠⚠ ONE table, not one test per channel: `variable_patterns` (#741/#742) is
#: the second entry, and writing it as a copy of the field test would put the
#: #725 readership claim in two places that can drift. A channel added without
#: a row here fails `test_every_extraction_channel_is_covered` below.
_EXTRACTION_CHANNELS = {
    "field_patterns": (
        "field",
        {
            "java": ("A.java", "class A {\n  private int probe;\n}\n"),
            # ⚠⚠ The KIND is per LANGUAGE, not per channel, and #743 is why.
            # `field_patterns` answers "this declaration binds N names and is
            # not a symbol in its own right"; what those names ARE is the
            # language's own word. Java calls them fields, PHP calls them
            # properties, and PHP_SPEC has declared the `property` kind since
            # before #571. A table keyed on the channel ALONE would force one of
            # the two languages to lie about its own members to satisfy a test,
            # so a sample may carry its own kind as an optional third element.
            "php": ("a.php", "<?php\nclass A { public $probe = 1; }\n", "property"),
            # #755. One sample EACH: `arduino` carries its own copy of the C++
            # spec, and a fix applied to one spec reaches half the product.
            "cpp": ("a.cpp", "class A {\n  int probe;\n};\n"),
            "arduino": ("a.ino", "class A {\n  int probe;\n};\n"),
            # #781. Same rule, three more copies, and the two grammars spell
            # the form differently.
            "javascript": ("a.js", "class A {\n  probe = 1;\n}\n"),
            "typescript": ("a.ts", "class A {\n  probe: number = 1;\n}\n"),
            "tsx": ("a.tsx", "class A {\n  probe: number = 1;\n}\n"),
        },
    ),
    "variable_patterns": (
        "variable",
        {
            "javascript": ("a.js", "let probe = 1;\n"),
            "typescript": ("a.ts", "let probe = 1;\n"),
            "tsx": ("a.tsx", "let probe = 1;\n"),
            # ⚠ Go joins this channel on the same merge (#731). Its grammar
            # spells `const_declaration` and `var_declaration` separately, so
            # unlike JS/TS it needs no shared predicate -- asserted here so the
            # channel is proved for BOTH declarers, not just the first.
            "go": ("a.go", "package m\n\nvar probe = 1\n"),
        },
    ),
}


@pytest.mark.parametrize("channel", sorted(_EXTRACTION_CHANNELS))
def test_every_declared_extraction_channel_actually_yields_its_kind(channel):
    """A channel must be READ, which is the whole difference from #725.

    ⚠⚠ `type_patterns` and `return_type_fields` are declared by 19 and 14 of the
    79 specs respectively and read by NOTHING -- #725 -- and #735 added a third
    node-type list beside them, #741 and #731 a fourth. A list no channel
    consults is indistinguishable from the defect it was added to fix ("a
    parameter that is present and does nothing", 08-19), and the only thing
    separating a new list from the two dead ones is that something runs it.
    This asserts that through the product, per language: a spec declaring the
    list must produce at least one symbol of the channel's kind from the node
    type it names.

    ⚠ Keyed on the SPEC rather than on a hardcoded language list, so a
    language joining a channel inherits the check on arrival instead of
    joining unwatched -- which is also what kept the table honest when two
    branches added members to the same channel on the same day.
    """
    kind, samples = _EXTRACTION_CHANNELS[channel]

    declaring = {
        name: spec
        for name, spec in LANGUAGE_REGISTRY.items()
        if getattr(spec, channel, None)
    }
    assert declaring, f"no spec declares {channel}; delete the field or the channel"

    for language, spec in sorted(declaring.items()):
        sample = samples.get(language)
        assert sample is not None, (
            f"{language} declares {channel}={getattr(spec, channel)} with no "
            f"sample here, so nothing proves the channel runs for it. Add three "
            f"lines rather than trusting the declaration."
        )
        # ⚠ A sample may carry its own kind as a third element, overriding the
        # channel's; the table's PHP row says why (#743).
        filename, source, *override = sample
        expected = override[0] if override else kind
        kinds = {s.kind for s in parse_file(source, filename, language)}
        assert expected in kinds, (
            f"{language} declares {channel}={getattr(spec, channel)} and its "
            f"sample yields no {expected} -- the list is write-only, which is "
            f"#725 in another costume. Got kinds: {sorted(kinds)}"
        )


def _channels_the_walker_consults() -> set[str]:
    """Every `spec.<name>_patterns` the tree walk actually reads.

    ⚠⚠ **Asked of the PRODUCT, not of a naming convention.** The first version
    of this test collected every field ending in `_patterns`, which swept in
    `type_patterns` -- a list declared by 19 specs and read by NOTHING (#725),
    i.e. the very defect the table exists to detect, reported as a missing row.
    A guard keyed to a spelling instead of to the property is the #709 shape,
    and it failed here on its first run.

    `_walk_tree` is where a channel becomes real: a list nothing dispatches on
    is not a channel, however it is named.
    """
    import jcodemunch_mcp.parser.extractor as extractor

    tree = ast.parse(inspect.getsource(extractor._walk_tree))
    return {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "spec"
        and node.attr.endswith("_patterns")
    }


def test_every_extraction_channel_is_covered():
    """A channel the walker dispatches on, with no row above, is unwatched.

    ⚠ The readership claim is per CHANNEL, so a fifth node-type list arriving
    without a row here inherits exactly the #725 silence the table exists to
    end. `constant_patterns` is excluded because it predates the channel idea
    and the per-language extraction tests cover it directly.
    """
    consulted = _channels_the_walker_consults() - {"constant_patterns"}
    assert consulted, (
        "the walker consults no *_patterns channel, which means this table is "
        "describing something that no longer exists"
    )
    missing = sorted(consulted - set(_EXTRACTION_CHANNELS))
    assert not missing, (
        f"{missing} are dispatched on by _walk_tree and have no row in "
        f"_EXTRACTION_CHANNELS, so nothing proves they yield anything "
        f"(#725, #735, #731)."
    )


def test_the_table_describes_no_channel_the_walker_ignores():
    """The other direction: a row for a list nothing dispatches on.

    ⚠ That is #725 exactly -- a declared list read by nobody -- and a row here
    would read as proof that it IS read, which is worse than the silence.
    """
    consulted = _channels_the_walker_consults()
    stale = sorted(set(_EXTRACTION_CHANNELS) - consulted)
    assert not stale, (
        f"{stale} have rows here and _walk_tree dispatches on none of them: "
        f"either the channel was removed, or it was never wired in (#725)."
    )
