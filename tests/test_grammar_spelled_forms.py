"""#724: a declaration form the grammar spells and no spec names.

The complement of `tests/test_language_spec_maps_agree.py` (#712). That file
asks whether a node type a spec DECLARES can be named; it reads only what the
spec already lists, so a form the spec omits entirely is invisible to it by
construction. This file asks the other half: what does the GRAMMAR spell that
the spec never mentions?

Four issues were one property wearing four costumes -- #698 (TypeScript
`abstract_class_declaration`), #713 (Java records, compact constructors and
annotation types), #712's TS/TSX half (`generator_function_declaration`) and
#722 (Haskell `type_synon`). Every one was found by a human or an external
benchmark.

⚠⚠ **The source is the COMPILED GRAMMAR's symbol table, not `node-types.json`.**
#724 proposed the latter and it does not exist here: `tree_sitter_language_pack`
ships `__init__.py`, `bindings/` and `py.typed` and no grammar metadata at all.
`Language.node_kind_count` / `node_kind_for_id` / `node_kind_is_named` is the
vocabulary the parser will actually emit, it cannot go stale against the binary
it comes from, and it needs no fixture -- which also sidesteps #699's trap,
that a fixture written from our own spec can only confirm our own spec.

⚠ **Two properties, and only the first is exact.** A node type a spec declares
that the grammar never emits is a typo with no defensible reading (property A,
one hit today: #722). A `*_declaration` the grammar emits and the spec omits is
USUALLY correct -- `parameter_declaration`, `local_variable_declaration`,
`accessor_declaration` and `catch_declaration` are all rightly absent -- so
property B is a frozen inventory rather than a ratchet on zero. The suffix is a
naming convention, never a claim that the form is a symbol, and no threshold
over it separates the real gaps from the correct omissions.

⚠⚠ **What property B does and does not buy.** It does NOT adjudicate an
omission that has been there all along: #698's `abstract_class_declaration` was
in the TypeScript grammar from the day the spec was written, so a
growth-gated baseline would have recorded it as unnamed on day one and said
nothing. What it buys is that the set is WRITTEN DOWN and reviewable, and that
it cannot grow in silence -- a grammar upgrade that adds a form, or a new
language nobody classified, fails here. The review is what finds a standing
gap, and reviewing this inventory for the first time found five, every one
confirmed by running the product rather than by reading the scan (see
`_CONFIRMED_GAPS`).
"""

import json
import pathlib

import pytest

from jcodemunch_mcp.parser.grammar_pack import get_parser
from jcodemunch_mcp.parser.languages import LANGUAGE_REGISTRY


BASELINE = pathlib.Path(__file__).parent / "fixtures" / "grammar_declaration_inventory.json"

# A form is declaration-SHAPED when the grammar's own name says so. Four
# suffixes, because three grammars in the set use `_item` (rust) or `_spec`
# (go) for the same concept and stopping at `_declaration` would read those
# two languages as clean when they are the opposite.
_DECLARATION_SUFFIXES = ("_declaration", "_definition", "_item", "_spec")

# Property A's one standing exception. Same rule as #712's `_KNOWN_GAPS`: the
# entry names the issue, and `test_a_known_gap_is_still_a_gap` fails when the
# gap closes, so an excuse cannot outlive the defect it excuses.
_KNOWN_GHOSTS = {
    "haskell": ({"type_synon"}, "#722: the grammar spells it `type_synomym`"),
}

# The gaps this inventory found on its first review, each CONFIRMED by parsing
# a snippet through `parse_file` rather than by reading the scan. They stay in
# the baseline (they are the current, wrong, behaviour) and each names the
# issue that will close it. ⚠ This is documentation, not an excuse list: the
# baseline already carries these node types, and this map exists so a reader
# knows which baseline entries are known-wrong versus deliberately omitted.
_CONFIRMED_GAPS = {
    "go": ("var_spec", "a package-level `var Client = 1` yields no symbol"),
    "kotlin": ("property_declaration", "`val name` / `var count` yield nothing"),
    "swift": ("protocol_function_declaration", "protocol requirements are absent"),
    "scala": ("given_definition", "a Scala 3 `given` yields no symbol"),
    "java": ("field_declaration", "a plain instance field yields no symbol"),
}


def _grammar_kinds(language):
    """Every NAMED node kind the compiled grammar can emit, or None."""
    try:
        parser = get_parser(language)
    except Exception:
        # 13 languages in the registry have no grammar in this pack and are
        # indexed for text search only. Absent is not a failure here.
        return None
    lang = parser.language
    return {
        lang.node_kind_for_id(i)
        for i in range(lang.node_kind_count)
        if lang.node_kind_is_named(i) and lang.node_kind_for_id(i)
    }


def _declaring_languages():
    """Specs that declare node types AND have a grammar to check them against.

    ⚠ A spec with an EMPTY `symbol_node_types` is regex-parsed or
    custom-extracted (Erlang, Fortran, SQL, Razor -- CLAUDE.md "Custom
    Parsers"), so every declaration form in its grammar would read as a gap.
    The first draft of this scan omitted this filter and reported 422 gaps
    across 39 languages; the real figure is an order of magnitude smaller, and
    the difference was entirely languages that do not use the node-type path
    at all. A scan that cannot tell "omitted" from "not applicable" is
    measuring its own blind spot.
    """
    out = {}
    for language, spec in sorted(LANGUAGE_REGISTRY.items()):
        declared = set(getattr(spec, "symbol_node_types", None) or {})
        if not declared:
            continue
        kinds = _grammar_kinds(language)
        if kinds is None:
            continue
        out[language] = (declared, kinds)
    return out


def _unnamed_declaration_forms(declared, kinds):
    return sorted(
        k for k in kinds - declared
        if k.endswith(_DECLARATION_SUFFIXES) and not k.startswith("_")
    )


def _current_inventory():
    return {
        language: _unnamed_declaration_forms(declared, kinds)
        for language, (declared, kinds) in _declaring_languages().items()
        if _unnamed_declaration_forms(declared, kinds)
    }


# ---------------------------------------------------------------------------
# Property A: a declared node type the grammar never emits
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("language", sorted(_declaring_languages()))
def test_every_declared_node_type_is_one_the_grammar_emits(language):
    """A spec entry naming a node type no grammar produces is dead on arrival.

    This is the #722 shape: `type_synon` sat in `HASKELL_SPEC` while the
    grammar spells it `type_synomym` -- the grammar's own typo, which no
    amount of reading our spec could reveal. The entry matches nothing, so the
    form is never extracted and nothing anywhere says so.

    ⚠ Exact, with no judgement call: the grammar either emits the kind or it
    does not.
    """
    declared, kinds = _declaring_languages()[language]
    excused = _KNOWN_GHOSTS.get(language, (set(), ""))[0]

    ghosts = (declared - kinds) - excused

    assert not ghosts, (
        f"{language}: {sorted(ghosts)} declared in symbol_node_types but the "
        f"grammar never emits that node type -- the entry matches nothing and "
        f"the form is silently unextractable (#724). Check the grammar's own "
        f"spelling with Language.node_kind_for_id."
    )


def test_a_known_ghost_is_still_a_ghost():
    """An excuse cannot outlive the defect it excuses.

    Mirrors `test_a_known_gap_is_still_a_gap` in the #712 file. When #722 is
    fixed the entry stops being a ghost and this fails, naming the line to
    delete -- so the exception list cannot quietly become permanent.
    """
    for language, (ghosts, why) in _KNOWN_GHOSTS.items():
        declared, kinds = _declaring_languages()[language]
        still = ghosts & (declared - kinds)
        assert still == ghosts, (
            f"{language}: {sorted(ghosts - still)} is no longer a ghost "
            f"({why}). Remove it from _KNOWN_GHOSTS."
        )


# ---------------------------------------------------------------------------
# Property B: the inventory of what the grammar spells and the spec omits
# ---------------------------------------------------------------------------

def test_the_unnamed_declaration_inventory_matches_the_baseline():
    """The set is written down, and it cannot move without a human seeing it.

    ⚠⚠ Failing here is NOT "a bug was introduced". It means the set of
    declaration forms a grammar spells and our spec ignores has CHANGED --
    because a grammar was upgraded, a language was added, or a spec gained or
    lost an entry. Read the diff and decide, per node type, whether it is a
    symbol we should extract (then declare it in the spec) or correctly
    ignored (then update the baseline in the same commit that changed it).

    ⚠ Both directions fail. A SHRINKING inventory is a gap being closed and is
    good news, and it still has to update the baseline, because a baseline
    that silently absorbs shrinkage cannot tell a fix from a spec entry that
    was deleted by accident.
    """
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))["inventory"]
    current = _current_inventory()

    added = {lang: sorted(set(v) - set(baseline.get(lang, [])))
             for lang, v in current.items()
             if set(v) - set(baseline.get(lang, []))}
    removed = {lang: sorted(set(v) - set(current.get(lang, [])))
               for lang, v in baseline.items()
               if set(v) - set(current.get(lang, []))}

    assert not added and not removed, (
        f"the unnamed-declaration inventory moved (#724).\n"
        f"  newly unnamed (the grammar spells these and no spec names them): {added}\n"
        f"  no longer unnamed (declared, or the grammar stopped emitting them): {removed}\n"
        f"Decide per node type, then update {BASELINE.name} in the same commit."
    )


def test_the_baseline_covers_every_language_the_scan_can_reach():
    """Non-vacuity floor: the scan must actually reach the specs it claims to.

    A `_declaring_languages()` that returned {} -- a broken grammar pack, a
    registry that failed to import, an over-eager filter -- would make every
    case above pass while checking nothing. Measured on THIS tree, 2026-09-17:
    22 specs declare node types and have a grammar, and 19 of them have at
    least one unnamed declaration form. The floors sit below both with room to
    move; they exist to catch a scan that collapsed, not to pin a count.
    """
    reachable = _declaring_languages()
    assert len(reachable) >= 18, sorted(reachable)

    inventory = _current_inventory()
    assert len(inventory) >= 15, sorted(inventory)

    # And the scan must be looking at a real vocabulary, not an empty set.
    for language, (_declared, kinds) in reachable.items():
        assert len(kinds) > 20, (language, len(kinds))


@pytest.mark.parametrize("language,node_type", sorted(
    (lang, nt) for lang, (nt, _why) in _CONFIRMED_GAPS.items()
))
def test_a_confirmed_gap_is_in_the_inventory(language, node_type):
    """The five gaps this instrument found on its first review are real.

    ⚠ Each was confirmed by running a snippet through `parse_file` and
    watching the symbol not appear, never by reading the scan -- the
    [[a-fixture-that-cannot-express-the-reported-shape-cannot-fail-on-it]]
    rule. When one is fixed, the node type leaves the inventory and this test
    names the `_CONFIRMED_GAPS` line to delete, the same way the ghost list
    cannot outlive its defect.
    """
    inventory = _current_inventory()

    assert node_type in inventory.get(language, []), (
        f"{language}/{node_type} is recorded as a confirmed gap but is no "
        f"longer in the inventory -- if it was fixed, remove the "
        f"_CONFIRMED_GAPS entry; if the scan stopped seeing it, the scan "
        f"is broken."
    )


# ---------------------------------------------------------------------------
# Non-vacuity: the detector must FIRE on the defects it was written for
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("language,node_type", [
    # #698, the reported case: TS spelled it, neither spec named it.
    ("typescript", "abstract_class_declaration"),
    # #712's TS/TSX half.
    ("typescript", "generator_function_declaration"),
    # #713, three of its four forms.
    ("java", "record_declaration"),
    ("java", "compact_constructor_declaration"),
    ("java", "annotation_type_declaration"),
    # #714, whose names are built rather than borrowed.
    ("csharp", "operator_declaration"),
    ("csharp", "indexer_declaration"),
])
def test_the_scan_would_have_caught_each_reported_defect(language, node_type):
    """⚠⚠ The pass that makes this file worth having.

    All four reported defects are FIXED on main, so the detector cannot be
    proven against them live: it reports clean, which is indistinguishable
    from a detector that reports clean about everything
    [[a-ratchet-can-pass-against-the-defect-it-names]]. This removes the spec
    entry -- reproducing the tree as it was when the defect shipped -- and
    asserts the node type appears in the inventory as newly unnamed.

    A scan that could not see these is a scan that would not have prevented
    any of the four issues it was written for.
    """
    declared, kinds = _declaring_languages()[language]
    assert node_type in declared, (
        f"{language}/{node_type} is not declared, so this case is not "
        f"reproducing a fixed defect any more"
    )
    assert node_type in kinds, f"{language} grammar does not emit {node_type}"

    # The tree as it was before the fix: the grammar unchanged, the spec silent.
    as_shipped = declared - {node_type}

    unnamed = _unnamed_declaration_forms(as_shipped, kinds)

    assert node_type in unnamed, (
        f"{language}/{node_type} was NOT reported as an unnamed declaration "
        f"form with the spec entry removed -- the scan would have stayed "
        f"silent through the issue it exists to prevent"
    )


def test_the_ghost_check_fires_on_a_planted_typo():
    """Non-vacuity for property A, over the same shape #722 really had.

    A misspelling that no grammar emits must be reported. Without this, a
    `_grammar_kinds` that returned every possible string -- or a declared set
    that was empty -- would keep the property-A cases green forever.
    """
    declared, kinds = _declaring_languages()["java"]
    planted = declared | {"recrod_declaration"}

    ghosts = planted - kinds

    assert ghosts == {"recrod_declaration"}, ghosts
