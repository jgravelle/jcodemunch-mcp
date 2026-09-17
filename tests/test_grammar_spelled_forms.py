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
gap, and reviewing this inventory found nine across six languages, every one
confirmed by running the product rather than by reading the scan (see
`_CONFIRMED_GAPS`).

⚠⚠ **The scan reaches 54 languages, and the first draft reached 22.** 35 specs
declare no `symbol_node_types` and still parse with a compiled grammar, matching
node types against literals written inline in `_parse_<lang>_symbols`. Calling
those "regex-parsed" and skipping them exempted `solidity` (10 such literals),
`nim` (10), `graphql` (9) and `vue` (8) -- and a Solidity form the grammar
spells and that inline list omits IS #698. Found in review; the exemption was
wrong for all 35, and the ARCHAEOLOGY row stated it as fact.
"""

import ast
import functools
import inspect
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
# ⚠ Six entries are not declarations at all (`python/with_item`,
# `bash/case_item`, `swift/capture_list_item`, `swift/tuple_type_item`,
# `rust/attribute_item`, `rust/inner_attribute_item`). They are left in rather
# than special-cased: narrowing the suffix set to exclude them is a rule written
# against six spellings, and the baseline's job is to be reviewed, not to be
# free of rows a reviewer dismisses in a second.
_DECLARATION_SUFFIXES = ("_declaration", "_definition", "_item", "_spec")

# Property A's one standing exception. Same rule as #712's `_KNOWN_GAPS`: the
# entry names the issue, and `test_a_known_ghost_is_still_a_ghost` fails when
# the gap closes, so an excuse cannot outlive the defect it excuses.
_KNOWN_GHOSTS = {
    "haskell": ({"type_synon"}, "#722: the grammar spells it `type_synomym`"),
}

# The gaps this inventory found on review, each CONFIRMED by parsing a snippet
# through `parse_file` and watching the symbol not appear -- never by reading
# the scan, and never by reading the extractor source.
#
# ⚠⚠ A LIST per language, not one entry. The first draft keyed one node type
# per language and silently dropped two of Swift's three confirmed gaps, which
# is the container's shape deciding what gets recorded. Found in review.
#
# ⚠ `kotlin/property_declaration` is here although `extractor.py` HAS a
# `property_declaration` branch: the branch exists and `val name` still yields
# no symbol, in a class body and at top level. Reading the source says covered;
# running the product says otherwise, and the product is the authority.
_CONFIRMED_GAPS = {
    "go": [("var_spec", "a package-level `var Client = 1` yields no symbol")],
    "kotlin": [("property_declaration", "`val name` / `var count` yield nothing, in a class or at top level")],
    "swift": [
        ("protocol_function_declaration", "a protocol's method requirements are absent"),
        ("protocol_property_declaration", "a protocol's property requirements are absent"),
        ("subscript_declaration", "a subscript yields no symbol"),
    ],
    "scala": [("given_definition", "a Scala 3 `given` yields no symbol")],
    "java": [("field_declaration", "a plain instance field yields no symbol; `static final` does, via constant_patterns")],
    "solidity": [
        ("constructor_definition", "a Solidity `constructor(...)` yields no symbol"),
        ("error_declaration", "`error Unauthorized(address)` yields nothing: the extractor "
                              "matches `error_definition` and the grammar emits "
                              "`error_declaration` -- #722's shape, in a second language"),
    ],
}

# ⚠⚠ Confirmed by the product and NOT expressible in the inventory, so recorded
# here rather than silently dropped. Julia's extractor matches
# `short_function_definition`, which its grammar does not emit, so `f(x) = x + 1`
# yields no symbol while `function g(y) ... end` does. It is a GHOST (a literal
# with no node type behind it), not an unnamed form, so it cannot appear in an
# inventory built from what the grammar emits. Property A would catch it, and
# property A deliberately does not run over the inline half -- see
# `test_the_ghost_check_does_not_run_over_the_inline_half` for why.
_INLINE_GHOSTS_FOUND = {
    "julia": ("short_function_definition", "`f(x) = x + 1` yields no symbol"),
    "solidity": ("error_definition", "the grammar emits `error_declaration`"),
}


@functools.lru_cache(maxsize=None)
def _grammar_kinds(language):
    """Every NAMED node kind the compiled grammar can emit, or None."""
    try:
        parser = get_parser(language)
    except Exception:
        # 13 languages in the registry have no grammar in this pack and are
        # indexed for text search only. Absent is not a failure here.
        return None
    lang = parser.language
    return frozenset(
        lang.node_kind_for_id(i)
        for i in range(lang.node_kind_count)
        if lang.node_kind_is_named(i) and lang.node_kind_for_id(i)
    )


@functools.lru_cache(maxsize=1)
def _extractor_functions():
    """`_parse_<lang>_symbols` by name, parsed ONCE.

    ⚠ `extractor.py` is large and `ast.parse` over it is not free. The first
    draft parsed it per language per call, and `_checkable_languages()` is
    called by every test in this file -- the suite went from under a second to
    over two minutes. Cached here rather than at the call sites, so a new
    caller inherits it.
    """
    from jcodemunch_mcp.parser import extractor

    module = ast.parse(inspect.getsource(extractor))
    return {
        node.name: node
        for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef)
    }


def _harvested_node_types(language, kinds):
    """Node types a hand-written `_parse_<lang>_symbols` matches on, by AST.

    ⚠⚠ 35 of the 79 specs declare NO `symbol_node_types` and still parse with a
    compiled tree-sitter grammar: their extractor function calls `get_parser`
    and compares `node.type` against string literals written inline. The first
    draft of this file called those specs "regex-parsed" and skipped them, which
    exempted `solidity` (10 literals), `nim` (10), `graphql` (9) and `vue` (8)
    from the very check they need -- a Solidity form the grammar spells and that
    inline list omits IS #698, and the guard would have said nothing. Found in
    review; the claim was wrong for every one of the 35.

    ⚠ Harvesting literals is weaker than reading a declared map and the weakness
    has a DIRECTION. A literal that is a field name rather than a node type
    (`"name"`, `"body"`) can coincide with a real node kind and inflate what
    this reports as recognised, which SHRINKS the gap. So this can under-report
    a gap and cannot invent one -- the safe direction for a scan whose output a
    human reviews.
    """
    node = _extractor_functions().get(f"_parse_{language}_symbols")
    if node is None or "get_parser" not in ast.unparse(node):
        return frozenset()
    literals = {
        n.value for n in ast.walk(node)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }
    return frozenset(literals & kinds)


@functools.lru_cache(maxsize=1)
def _checkable_languages():
    """Every language whose recognised node types can be compared to a grammar.

    Two sources, and the inventory records which:

    * `spec` -- the language declares `symbol_node_types` (22 specs);
    * `inline` -- the language declares none and its extractor function matches
      node types against literals in its body (32 more).

    A language with no grammar in this pack is skipped: 13 of them are indexed
    for text search only, by design, and absent is not a failure.
    """
    out = {}
    for language, spec in sorted(LANGUAGE_REGISTRY.items()):
        kinds = _grammar_kinds(language)
        if kinds is None:
            continue
        declared = set(getattr(spec, "symbol_node_types", None) or {})
        if declared:
            out[language] = (declared, kinds, "spec")
            continue
        harvested = _harvested_node_types(language, kinds)
        if harvested:
            out[language] = (harvested, kinds, "inline")
    return out


def _spec_declaring_languages():
    """The `spec` half alone -- the only half property A can speak about.

    ⚠ Property A asks whether a DECLARED node type exists in the grammar. A
    harvested literal is not a declaration: it is any string in a function
    body, so a literal absent from the grammar is usually a field name or a
    kind string, not a typo. Running property A over the inline half would
    report every one of them as a ghost.
    """
    return {k: (v[0], v[1]) for k, v in _checkable_languages().items() if v[2] == "spec"}


def _unnamed_declaration_forms(declared, kinds):
    return sorted(
        k for k in kinds - declared
        if k.endswith(_DECLARATION_SUFFIXES) and not k.startswith("_")
    )


def _current_inventory():
    out = {}
    for language, (recognised, kinds, _source) in _checkable_languages().items():
        gap = _unnamed_declaration_forms(recognised, kinds)
        if gap:
            out[language] = gap
    return out


def _inventory_sources():
    """Which half each language's recognised set came from: `spec` or `inline`."""
    return {lang: src for lang, (_r, _k, src) in _checkable_languages().items()}


# ---------------------------------------------------------------------------
# Property A: a declared node type the grammar never emits
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("language", sorted(_spec_declaring_languages()))
def test_every_declared_node_type_is_one_the_grammar_emits(language):
    """A spec entry naming a node type no grammar produces is dead on arrival.

    This is the #722 shape: `type_synon` sat in `HASKELL_SPEC` while the
    grammar spells it `type_synomym` -- the grammar's own typo, which no
    amount of reading our spec could reveal. The entry matches nothing, so the
    form is never extracted and nothing anywhere says so.

    ⚠ Exact, with no judgement call: the grammar either emits the kind or it
    does not.
    """
    declared, kinds = _spec_declaring_languages()[language]
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
        declared, kinds = _spec_declaring_languages()[language]
        still = ghosts & (declared - kinds)
        assert still == ghosts, (
            f"{language}: {sorted(ghosts - still)} is no longer a ghost "
            f"({why}). Remove it from _KNOWN_GHOSTS."
        )


def test_the_ghost_check_does_not_run_over_the_inline_half():
    """Property A is scoped to the `spec` half, and the reason is measurable.

    A harvested literal is any string in a function body, so "absent from the
    grammar" does not mean "typo". Measured across the 32 inline languages, the
    declaration-shaped literals no grammar emits are:

    * `apex` and `solidity`: the bare string `"_declaration"`, which is an
      argument to `endswith`, not a node type;
    * `svelte` and `vue`: seven or eight JavaScript node types, correct for the
      script block's DELEGATED grammar and absent from the host grammar;
    * `julia` and `solidity`: two real ghosts (`_INLINE_GHOSTS_FOUND`).

    Two signals in six, and the four false positives are structural rather than
    fixable by a threshold -- an `endswith` argument and a delegated grammar are
    both legitimate. Gating on this would fail four languages forever, which is
    how a guard gets disabled. The real ones are recorded and filed instead.

    This test asserts the SCOPE so a later widening has to argue with it.
    """
    spec_half = set(_spec_declaring_languages())
    all_checkable = set(_checkable_languages())

    assert spec_half < all_checkable, "the inline half is missing from the scan"
    for language in ("solidity", "vue", "svelte", "julia"):
        assert language not in spec_half, (
            f"{language} is now spec-declaring; property A applies to it and "
            f"the scoping argument in this docstring needs re-measuring"
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

    A `_checkable_languages()` that returned {} -- a broken grammar pack, a
    registry that failed to import, an over-eager filter -- would make every
    case above pass while checking nothing. Measured on THIS tree, 2026-09-17:
    54 languages are checkable (22 through `symbol_node_types`, 32 through
    inline literals) and 34 of them have at least one unnamed declaration form.
    The floors sit below both with room to move; they exist to catch a scan
    that collapsed, not to pin a count.
    """
    reachable = _checkable_languages()
    assert len(reachable) >= 45, sorted(reachable)

    inventory = _current_inventory()
    assert len(inventory) >= 28, sorted(inventory)

    # And the scan must be looking at a real vocabulary, not an empty set.
    # ⚠ The floor is 10, not a round 20: `elisp` has exactly 20 named kinds and
    # a `> 20` written from a guess failed against a correct tree. A
    # non-vacuity floor has to clear the SMALLEST real member, which is a
    # measurement rather than a number that looks safe.
    for language, (_recognised, kinds, _source) in reachable.items():
        assert len(kinds) >= 10, (language, len(kinds))


@pytest.mark.parametrize("language,node_type", sorted(
    (lang, nt) for lang, entries in _CONFIRMED_GAPS.items() for nt, _why in entries
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


def test_the_baseline_gate_fires_on_a_planted_difference(tmp_path, monkeypatch):
    """⚠⚠ The gate that runs on every commit, proven against a real difference.

    The detection PREDICATE is proven by
    `test_the_scan_would_have_caught_each_reported_defect`. The comparison is a
    different piece of code: a defect in the `added`/`removed` set arithmetic,
    or a baseline whose `inventory` key were present but empty, would keep every
    other test in this file green while gating nothing -- and the red arm's only
    witness for this test was a `FileNotFoundError`, which proves the file is
    read, not that a difference fails. Found in review.

    [[a-ratchet-can-pass-against-the-defect-it-names]]: run it against the
    reintroduced defect, never only against the fixed tree.
    """
    real = json.loads(BASELINE.read_text(encoding="utf-8"))

    # 1. A form that left the inventory (a gap we closed, or a spec entry
    #    deleted by accident) must fail.
    language = sorted(real["inventory"])[0]
    shrunk = json.loads(json.dumps(real))
    shrunk["inventory"][language] = shrunk["inventory"][language] + ["planted_extra_declaration"]
    planted = tmp_path / "shrunk.json"
    planted.write_text(json.dumps(shrunk), encoding="utf-8")
    monkeypatch.setattr(BASELINE.__class__, "read_text",
                        lambda self, **kw: planted.read_text(**kw)
                        if self == BASELINE else pathlib.Path.read_text(self, **kw))
    with pytest.raises(AssertionError, match="no longer unnamed"):
        test_the_unnamed_declaration_inventory_matches_the_baseline()

    # 2. A newly unnamed form -- the #698 arrival shape -- must fail too.
    grown = json.loads(json.dumps(real))
    grown["inventory"][language] = grown["inventory"][language][1:]
    planted.write_text(json.dumps(grown), encoding="utf-8")
    with pytest.raises(AssertionError, match="newly unnamed"):
        test_the_unnamed_declaration_inventory_matches_the_baseline()


def test_the_inline_half_is_actually_covered():
    """The blind spot this file shipped with, asserted closed.

    35 specs declare no `symbol_node_types` and still parse with a compiled
    grammar. The first draft skipped them as "regex-parsed", which exempted
    solidity, nim, graphql and vue -- languages that hard-code 8 to 10
    declaration node types in a function body, where an omission is #698
    exactly. A regression to the old filter drops these languages from
    `_checkable_languages()` and fails here.
    """
    sources = _inventory_sources()
    inline = sorted(lang for lang, src in sources.items() if src == "inline")

    assert len(inline) >= 25, inline
    for language in ("solidity", "nim", "graphql", "vue"):
        assert sources.get(language) == "inline", (language, sources.get(language))


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
    declared, kinds = _spec_declaring_languages()[language]
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
    declared, kinds = _spec_declaring_languages()["java"]
    planted = declared | {"recrod_declaration"}

    ghosts = planted - kinds

    assert ghosts == {"recrod_declaration"}, ghosts
