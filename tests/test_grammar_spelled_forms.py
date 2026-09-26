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

⚠⚠ **The scan reaches 54 languages, and the first draft reached 22.** Of the 44
registry entries that have a grammar and declare no `symbol_node_types`, **34
parse with that compiled grammar** and match node types against literals written
inline in `_parse_<lang>_symbols` -- `solidity`, `nim`, `graphql` and `vue`
among them. Skipping all of them as "regex-parsed" exempted exactly the
languages where an omission IS #698. Found in review.

⚠ The old label was not wrong for everyone, and saying "wrong for all of them"
replaced one imprecise claim with another: **7 are genuinely regex-parsed**
(`asm`, `astro`, `cobol`, `racket`, `verilog`, `vhdl`, `yaml` -- their extractor
never calls `get_parser`) and **3 have no extractor function at all** (`html`,
`r`, `twig`). For those ten the exemption was correct.

⚠ **Two of the 34 leave the scan and it is not an oversight to fix silently:**
`elixir` and `nix` match node types in module-level helpers (`_walk_elixir`,
`_walk_nix_bindings`) rather than inside `_parse_<lang>_symbols`, so the AST
walk finds no literals and `_checkable_languages()` drops them. 32 reach the
inline half. `test_the_inline_half_names_what_it_cannot_reach` pins that count
so a third language cannot join them unnoticed.
"""

import ast
import dataclasses
import functools
import re
import inspect
import json
import pathlib

import pytest

from jcodemunch_mcp.parser.grammar_pack import get_parser
from jcodemunch_mcp.parser.languages import LANGUAGE_REGISTRY, LanguageSpec


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

# Property A's standing exceptions, EMPTY since #722 (Haskell's `type_synon`,
# which the grammar spells `type_synomym`) was fixed. Same rule as #712's
# `_KNOWN_GAPS`: an entry names the issue, and
# `test_a_known_ghost_is_still_a_ghost` fails when the gap closes, so an excuse
# cannot outlive the defect it excuses. It loops inside the test, so an empty
# register passes rather than skipping.
_KNOWN_GHOSTS: dict[str, tuple[set[str], str]] = {}

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
# ⚠ `kotlin/property_declaration` was here and is GONE, closed by #732. That is
# the intended lifecycle: `test_a_confirmed_gap_is_in_the_inventory` went red
# the moment the fix landed and named this line, so the record could not
# outlive the defect it records.
# ⚠ `go/var_spec` and `scala/given_definition` were here and are GONE, closed by
# #731 and #734. ⚠⚠ Note the ASYMMETRY between the two removals, because it is
# the kind of thing that reads as an oversight later. `given_definition` leaves
# the INVENTORY as well: the Scala spec now names it, so the row disappears and
# `test_a_confirmed_gap_is_in_the_inventory` went red and named the line.
# `var_spec` stays IN the inventory and only loses its gap entry, because the
# fix declares `var_declaration` -- the node a reader opens, and the one that
# wraps every spec of a grouped block -- while `var_spec` itself is still named
# by no channel. The row remains true as written ("no channel names this form");
# what stopped being true is the gap entry's claim that the form yields nothing.
# ⚠ And `swift`'s three requirement forms were the last entries, closed by
# #733. The dict is EMPTY, which is the intended end state and not a missing
# import: every gap review confirmed through `parse_file` has been fixed.
# `test_a_confirmed_gap_is_in_the_inventory` is vacuous while it stays empty,
# and becomes the lifecycle guard again the moment review records the next one.
_CONFIRMED_GAPS: dict[str, list[tuple[str, str]]] = {}

# ⚠⚠ Confirmed by the product and NOT expressible in the inventory, so recorded
# here rather than silently dropped. Julia's extractor matches
# `short_function_definition`, which its grammar does not emit, so `f(x) = x + 1`
# yields no symbol while `function g(y) ... end` does. It is a GHOST (a literal
# with no node type behind it), not an unnamed form, so it cannot appear in an
# inventory built from what the grammar emits. Property A would catch it, and
# property A deliberately does not run over the inline half -- see
# `test_the_ghost_check_does_not_run_over_the_inline_half` for why.
#: Ghosts found by the inline-half measurement and NOT YET FIXED.
#:
#: ⚠⚠ Both original entries left on 2026-09-17: julia's
#: `short_function_definition` (#738) and solidity's `error_definition` (#737)
#: are fixed, so an entry claiming they are ghosts would now be a false record.
#: A third literal, julia's `mutable_struct_definition`, was never recorded here
#: and was correct not to be: the grammar has no such kind but spells
#: `mutable struct X` as an ordinary `struct_definition`, which the extractor
#: already matched, so it cost nothing. It was deleted as dead code in the same
#: change, with `test_a_mutable_struct_still_extracts` proving the removal safe.
#:
#: ⚠ Empty is the correct state, not a broken table --
#: `test_the_ghost_check_does_not_run_over_the_inline_half` still pins the scope,
#: and `test_no_inline_language_recognises_node_types_outside_its_parse_function`
#: still measures whether the harvest can invent a gap.
_INLINE_GHOSTS_FOUND: dict[str, tuple[str, str]] = {}


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

    ⚠⚠ Many specs declare NO `symbol_node_types` and still parse with a compiled
    tree-sitter grammar: their extractor function calls `get_parser` and
    compares `node.type` against string literals written inline. The first draft
    of this file called all of those "regex-parsed" and skipped them, which
    exempted `solidity`, `nim`, `graphql` and `vue` from the very check they
    need -- a Solidity form the grammar spells and that inline list omits IS
    #698, and the guard would have said nothing.

    ⚠ The counts are asserted by `test_the_inline_half_names_what_it_cannot_reach`
    and stated nowhere else. An earlier draft typed a per-language literal count
    into this docstring, over three different bases, none of which reconciled --
    in the file whose whole subject is a hand-written list drifting from what
    the grammar says.

    ⚠ Harvesting literals is weaker than reading a declared map, in BOTH
    directions, and only one of them is safe by construction:

    * a literal that is a field name rather than a node type (`"name"`,
      `"body"`) can coincide with a real node kind and inflate `recognised`,
      which SHRINKS the reported gap -- safe, it can only hide a finding;
    * a node type matched OUTSIDE this function -- in a module-level helper the
      `ast.walk` never reaches -- shrinks `recognised` and INFLATES the gap.
      `elixir` and `nix` prove the mechanism exists; they escape only because
      their harvest is empty and `_checkable_languages()` drops them.

    ⚠⚠ So "this cannot invent a gap" is TRUE ON THIS TREE and is not true by
    construction. Measured 2026-09-16: no inline language matches node types
    both inside and outside its parse function
    (`test_no_inline_language_recognises_node_types_outside_its_parse_function`
    is that measurement, kept as an assertion rather than a sentence, because
    the claim is what a reviewer will rely on).
    """
    node = _extractor_functions().get(f"_parse_{language}_symbols")
    if node is None or "get_parser" not in ast.unparse(node):
        return frozenset()
    literals = {
        n.value for n in ast.walk(node)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }
    return frozenset(literals & kinds)


#: Every channel a spec can extract a node type through.
#:
#: ⚠⚠ **FOUR, and this function read ONE for its whole life (#757).** A form
#: fixed through `field_patterns` or `constant_patterns` stayed listed as
#: unrecognised, so #735 left `java.field_declaration` in the inventory and
#: #743 made the count GROW in the change that fixed PHP properties -- a file
#: that names unindexed forms was naming indexed ones, and closing a gap could
#: make it worse.
#:
#: ⚠⚠ **"Declared in a channel" is NOT "extracted by it", which is why this
#: union ships with evidence.** `java.field_declaration` sat in
#: `constant_patterns` for years while every ordinary field was dropped,
#: because that channel required `static final`; a union by declaration alone
#: would have called the form recognised and hidden the widest gap #724 found.
#: `tests/test_inventory_reads_every_channel.py` owes a sample for every form
#: this widening suppresses a row for, and proves the channel extracts it BY
#: DELETION -- so a form that stops extracting returns to the inventory instead
#: of hiding in it.
#:
#: ⚠ Every name here is read through `getattr`, so this does not depend on the
#: order two branches merge in; `variable_patterns` (#741) arrived that way and
#: `_PENDING_CHANNELS` is empty again.
_EXTRACTION_CHANNELS = (
    "constant_patterns",
    "field_patterns",
    "variable_patterns",
)


def _spec_recognised(spec) -> set[str]:
    """Every node type this spec can extract, across all four channels."""
    recognised = set(getattr(spec, "symbol_node_types", None) or {})
    for channel in _EXTRACTION_CHANNELS:
        recognised |= set(getattr(spec, channel, None) or [])
    return recognised

#: ⚠ EMPTY, and that is the expected end state. `variable_patterns` was the
#: one reviewed entry (#741 / PR #753); it is a field of `LanguageSpec` since
#: that branch merged, so the exemption was dropped rather than left to rot.
_PENDING_CHANNELS: dict[str, str] = {}

#: Fields whose VALUES are node-type collections, classified by what reads them.
#:
#: ⚠⚠ **ONE predicate over the SHAPE, because the first two versions of this
#: gate were keyed to a spelling and review broke both.** Round two's version
#: had no rule at all, so a new channel could be classified non-channel and
#: every guard stayed green. Round three's was keyed to `list[str]` -- and the
#: canonical channel, `symbol_node_types`, is a `dict[str, str]`, which is the
#: natural shape for any channel carrying a kind, so a dict-shaped fifth channel
#: walked straight through the rule written to stop exactly that. Same recurrence
#: as #709, which was re-keyed FOUR times in six rounds; what held there was one
#: shared predicate plus pinned cases, so that is what this is.
#:
#: The predicate is the annotation being a node-type collection (`list[str]` or
#: `dict[str, str]`), and every field matching it owes ONE of four
#: classifications:
#:
#: * the first channel, `symbol_node_types`, unioned in by `_spec_recognised`;
#: * a member of `_EXTRACTION_CHANNELS` (or `_PENDING_CHANNELS`);
#: * `_UNREAD_NON_CHANNEL_FIELDS` -- nothing reads it, and that is SCANNED;
#: * `_READ_FOR_SOMETHING_ELSE` -- read, but not to decide what is extracted,
#:   with the purpose stated and the reading file pinned.
#:
#: A field matching the predicate and none of the four fails by name. There is
#: no fifth bucket, which is the point: the bucket is where an excuse would go.

#: Read by NOTHING, which is why a field here is not a channel -- and the claim
#: is asserted, not quoted.
#:
#: ⚠⚠ **EMPTY since #725, and the other test now keeps it that way.**
#: `type_patterns`, `return_type_fields` and `param_fields` sat here, filled
#: in by every spec and read by nothing in `src/`; #725 deleted them from
#: `LanguageSpec`. `tests/test_every_language_spec_field_has_a_reader.py`
#: fails on ANY field no consumer reads, so an entry arriving here is a field
#: that test already refuses. The bucket stays so the classification below
#: keeps its four arms and names the decision if one is ever wanted again.
_UNREAD_NON_CHANNEL_FIELDS: dict[str, str] = {}

#: Read, but for something other than deciding what gets extracted: the purpose
#: and the file that does the reading, so an excuse cannot outlive its reason.
_READ_FOR_SOMETHING_ELSE = {
    "container_node_types": (
        "ownership: parent_is_container promotes a function to a method, it "
        "does not turn a node into a symbol",
        "src/jcodemunch_mcp/parser/extractor.py",
    ),
    "name_fields": (
        "naming: how to NAME a node type the spec already declares",
        "src/jcodemunch_mcp/parser/extractor.py",
    ),
}

#: Fields that are not node-type collections at all, with what they are.
_SCALAR_SPEC_FIELDS = frozenset(
    {
        "ts_language",              # the grammar's name
        "docstring_strategy",       # a strategy name
        "decorator_node_type",      # one node type, attached to a symbol, never one itself
        "decorator_from_children",  # a bool
    }
)

#: The union every field of `LanguageSpec` must fall into. Kept for the roster
#: test, which is what fails when a field arrives that nothing here classifies.
_NON_CHANNEL_SPEC_FIELDS = frozenset(
    {"symbol_node_types"}
    | set(_UNREAD_NON_CHANNEL_FIELDS)
    | set(_READ_FOR_SOMETHING_ELSE)
    | _SCALAR_SPEC_FIELDS
)

#: The SCALAR annotations. Everything else is treated as a node-type collection.
#:
#: ⚠⚠ **INVERTED, and the direction is the whole point.** Listing the collection
#: spellings makes the rule fail OPEN: `tuple[str, ...]`, `frozenset[str]`,
#: `dict[str, list[str]]`, a bare `list` or `list[str] | None` all fall out of
#: it, and a channel spelled any of those is classified away with no scan. That
#: is the third version of one hole -- round two had no rule, round three keyed
#: it to `list[str]`, and each fix was a narrower version of the same
#: fail-open shape (#709: re-keyed four times in six rounds).
#:
#: Pinning the three scalar spellings this dataclass uses inverts it: an
#: unrecognised annotation lands in the collection rule and owes a
#: classification, so a new SPELLING fails closed and a new scalar KIND (say
#: `int`) fails loudly rather than being waved through. Text comparison stops
#: being load-bearing for coverage -- it only has to recognise three spellings,
#: and being wrong about one of those is the safe direction.
#:
#: ⚠ Both string and object forms are covered by normalising the text, because
#: a dataclass annotation reaches `dataclasses.fields` as a string under
#: `from __future__ import annotations` and as the object without it.
_SCALAR_SPEC_ANNOTATIONS = (
    "str",
    "Optional[str]",
    "bool",
)

def _annotation_text(annotation) -> str:
    """One normalised spelling for an annotation, however it arrives.

    ⚠⚠ Three forms reach `dataclasses.fields`, and the first version of this
    handled ONE. Without `from __future__ import annotations` a plain class
    arrives as the class object and stringifies as `<class 'str'>`, a `typing`
    construct as `typing.Optional[str]`, and a builtin generic as `list[str]` --
    so a normaliser written against the third reads the first two as unknown.
    Under the INVERTED rule that direction is safe (unknown means "treat it as a
    collection", so it fails loudly) and it was still wrong: every scalar field
    was reported as a collection at once, which is how it was found.
    """
    if isinstance(annotation, str):
        text = annotation
    elif isinstance(annotation, type):
        text = annotation.__name__
    else:
        text = str(annotation)
    return text.replace(" ", "").replace("typing.", "")


def _spec_field_names() -> set[str]:
    return {f.name for f in dataclasses.fields(LanguageSpec)}


def _node_type_collection_fields() -> set[str]:
    """Fields of `LanguageSpec` whose values are collections of node types.

    ⚠⚠ Defined by EXCLUSION, so an unrecognised annotation is a collection and
    owes a classification. Keying it to the collection spellings instead let a
    dict-shaped channel through (round three), and before that there was no rule
    at all (round two) -- the same fail-open shape, twice, narrower each time.
    """
    return {
        f.name
        for f in dataclasses.fields(LanguageSpec)
        if _annotation_text(f.type) not in _SCALAR_SPEC_ANNOTATIONS
    }


def _src_files():
    root = pathlib.Path(__file__).resolve().parent.parent / "src"
    return sorted(root.rglob("*.py"))


def _reads_of(field_name: str) -> list[str]:
    """Every file under `src/` that READS a spec's `field_name`.

    Three shapes, found by AST rather than by text:

    * `spec.field_name` -- an attribute read, the likely one;
    * `getattr(anything, "field_name")` -- quote style irrelevant, which a text
      scan got wrong: it built a double-quoted needle and missed
      `getattr(spec, 'type_patterns')`;
    * `anything["field_name"]` -- a subscript read, the `asdict(spec)` route.

    ⚠ Keyword CONSTRUCTION is deliberately NOT a read: every spec in
    `languages.py` passes `type_patterns=[...]`, and counting that would make
    every field look consumed, so the scan would assert nothing. An
    `ast.keyword` is never an `Attribute`, `getattr` call or `Subscript`, so
    this falls out of the shapes rather than needing an exclusion.

    ⚠ Co-located by construction: a text scan asked whether `getattr` and the
    name both appeared ANYWHERE in the same file, which is over-inclusive on
    unrelated files and under-inclusive on quote style at once.
    """
    hits = []
    for path in _src_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:  # pragma: no cover - a file we cannot parse is not a read
            continue
        for node in ast.walk(tree):
            read = False
            if isinstance(node, ast.Attribute) and node.attr == field_name:
                read = True
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and node.args
                and isinstance(node.args[1] if len(node.args) > 1 else None, ast.Constant)
                and node.args[1].value == field_name
            ):
                read = True
            elif (
                isinstance(node, ast.Subscript)
                and isinstance(node.slice, ast.Constant)
                and node.slice.value == field_name
            ):
                read = True
            if read:
                hits.append("src/" + path.as_posix().split("/src/", 1)[-1])
                break
    return sorted(set(hits))


def test_every_node_type_collection_field_is_classified():
    """⚠⚠ The gate for #757 recurring one field over, and the ONE place the
    lazy answer is refused.

    Every field whose value is a collection of node types owes a
    classification: a channel, unread (scanned), or read for something else
    with the purpose and the reader named. There is no bucket for "not a
    channel" on its own, because that is where an excuse goes -- and a wrong
    answer here is invisible in the product: the inventory simply starts
    reporting forms the extractor handles, which reads like a gap nobody has
    got to yet.
    """
    classified = (
        {"symbol_node_types"}
        | set(_EXTRACTION_CHANNELS)
        | set(_PENDING_CHANNELS)
        | set(_UNREAD_NON_CHANNEL_FIELDS)
        | set(_READ_FOR_SOMETHING_ELSE)
    )
    unclassified = sorted(_node_type_collection_fields() - classified)
    assert not unclassified, (
        f"{unclassified} hold collections of node types and nothing says what "
        f"reads them. Pick one: an extraction channel (_EXTRACTION_CHANNELS, "
        f"plus a sample per widened form in "
        f"tests/test_inventory_reads_every_channel.py); unread "
        f"(_UNREAD_NON_CHANNEL_FIELDS, which is scanned across src/); or read "
        f"for something other than extraction (_READ_FOR_SOMETHING_ELSE, with "
        f"the purpose and the file that reads it). A field classified only as "
        f"'not a channel' is the escape hatch this refuses (#757)."
    )


def test_a_field_classified_unread_is_still_unread():
    """The measurement inside the gate, asserted instead of transcribed.

    ⚠⚠ This is the trip-wire: when a fix wires an unread field into the
    extractor it becomes an extraction channel, and the recognised set will not
    know. This fails on that commit and names the decision.

    ⚠ A loop, not a parametrize: the ledger is empty since #725, and an empty
    parametrize SKIPS, which spends the skip budget to assert nothing.
    """
    for field_name in sorted(_UNREAD_NON_CHANNEL_FIELDS):
        readers = _reads_of(field_name)
        assert not readers, (
            f"{field_name} is classified a non-channel because nothing reads it "
            f"({_UNREAD_NON_CHANNEL_FIELDS[field_name]}), and {readers} read it "
            f"now. If it feeds extraction it is a CHANNEL: add it to "
            f"_EXTRACTION_CHANNELS, and a sample per newly recognised form in "
            f"tests/test_inventory_reads_every_channel.py. If it feeds something "
            f"else, move it to _READ_FOR_SOMETHING_ELSE with the purpose and this "
            f"file named (#757)."
        )


@pytest.mark.parametrize("field_name", sorted(_READ_FOR_SOMETHING_ELSE))
def test_a_field_read_for_something_else_is_still_read_there(field_name):
    """The positive control for the scan, and the other direction of the excuse.

    ⚠ If the read disappears the field is UNREAD and belongs in the scanned
    set; if it moves, the pinned file is wrong and the next reader is misled.
    Either way the classification as written has stopped being true.

    ⚠⚠ These arms are what make the unread arms mean something: they share one
    `_reads_of`, so a scan stubbed to find nothing fails HERE while the unread
    tests stay green, and a scan that finds everything fails there while this
    stays green. Neither half can be vacuous while the other passes.
    """
    purpose, reader = _READ_FOR_SOMETHING_ELSE[field_name]
    readers = _reads_of(field_name)
    assert reader in readers, (
        f"{field_name} is excused from being a channel because {reader} reads "
        f"it for {purpose}, and that read is gone -- readers now {readers}. "
        f"Re-classify it: unread fields go in _UNREAD_NON_CHANNEL_FIELDS "
        f"(#757)."
    )


def test_no_classification_names_a_field_that_is_gone():
    """A classification for a field that no longer exists.

    ⚠ Harmless to the product and corrosive to the gate: it reads as a decision
    someone made about a real field, so the next reader trusts the list is
    complete. The sample table next door is gated in both directions for the
    same reason.
    """
    named = (
        set(_UNREAD_NON_CHANNEL_FIELDS)
        | set(_READ_FOR_SOMETHING_ELSE)
        | _SCALAR_SPEC_FIELDS
        | {"symbol_node_types"}
    )
    stale = sorted(named - _spec_field_names())
    assert not stale, (
        f"{stale} are classified as fields of LanguageSpec and are not fields "
        f"of it -- renamed or removed (#757)."
    )


def test_every_extraction_channel_is_a_real_spec_field():
    """A channel name that is not a field reads as an absent one, silently.

    ⚠⚠ Both tuples go through `getattr(spec, channel, None) or []`, which is
    exactly right for a field that has not merged yet and exactly wrong for a
    TYPO -- the two are indistinguishable at the call site, and the typo
    version quietly recognises nothing for the life of the file.
    """
    unknown = sorted(
        set(_EXTRACTION_CHANNELS) - _spec_field_names() - set(_PENDING_CHANNELS)
    )
    assert not unknown, (
        f"{unknown} are read as extraction channels and are not fields of "
        f"LanguageSpec -- either a typo, or a pending arrival that belongs in "
        f"_PENDING_CHANNELS with its branch named (#757)."
    )


def test_a_pending_channel_is_still_pending():
    """The `getattr` excuse dies when the field arrives.

    ⚠ Same shape as `_KNOWN_GAPS` next door: an exemption that outlives its
    reason stops being an exemption and becomes a hole.
    """
    arrived = sorted(name for name in _PENDING_CHANNELS if name in _spec_field_names())
    assert not arrived, (
        f"{arrived} are fields of LanguageSpec now, so the branch adding them "
        f"has merged: drop the _PENDING_CHANNELS entry (#757)."
    )


def test_a_pending_channel_is_not_a_typo_of_an_existing_field():
    """⚠⚠ The hole `_PENDING_CHANNELS` would otherwise keep open.

    `test_every_extraction_channel_is_a_real_spec_field` SUBTRACTS the pending
    set, and `test_a_pending_channel_is_still_pending` can only fire when a name
    becomes a real field -- which a MISSPELLED name never does. So
    `feild_patterns` would sit there green forever, contributing nothing, which
    is the exact silence the pending block says it exists to end.

    ⚠ Measured on this roster: the three shapes a typo takes are all refused --
    `feild_patterns` 0.929, `field_paterns` 0.963, `field_pattern` 0.963 -- and
    plausible new names pass with margin (`variable_patterns` 0.71,
    `method_patterns` 0.759, `enum_patterns` 0.769). One boundary case is
    refused and is legitimate: `container_patterns` scores 0.857 against
    `constant_patterns`. Kept, because the refusal is loud, names the pair, and
    `test_the_pending_set_is_exactly_what_review_saw` already forces any new
    entry to be argued for in the same commit.
    """
    import difflib

    fields = _spec_field_names()
    near = {}
    for pending in _PENDING_CHANNELS:
        for existing in fields:
            # ⚠ An EXACT match is not a typo, it is an arrival, and
            # `test_a_pending_channel_is_still_pending` owns that case. Without
            # this, planting the arrival fires BOTH tests and neither verdict
            # means what it says -- found by planting it.
            if pending == existing:
                continue
            if difflib.SequenceMatcher(None, pending, existing).ratio() >= 0.85:
                near.setdefault(pending, []).append(existing)
    assert not near, (
        f"{near} -- each pending channel is one small edit from a field that "
        f"already exists, which is a typo, not an arrival. A typo'd channel is "
        f"read as an absent field forever and nothing else would say so (#757)."
    )


def test_the_pending_set_is_exactly_what_review_saw():
    """A pending entry is a REVIEWED exemption, not a place to put a name.

    ⚠ Pinned by content, so adding one fails here and has to be argued for --
    the `_KNOWN_GAPS` treatment. Removing the last one when #741 merges is the
    expected direction, and this line is the reminder.
    """
    assert set(_PENDING_CHANNELS) == set(), (
        f"_PENDING_CHANNELS is {sorted(_PENDING_CHANNELS)}; it is empty since "
        f"#741 / PR #753 merged and variable_patterns became a real field. A "
        f"new entry needs the branch that adds the field named, and a `getattr` "
        f"read is not evidence the field will ever exist (#757)."
    )


def test_every_scalar_field_has_a_pinned_scalar_annotation():
    """The inversion's own guard: a field named a scalar must be one.

    ⚠ `_SCALAR_SPEC_FIELDS` is prose, `_SCALAR_SPEC_ANNOTATIONS` is the rule.
    If a field in the first grows a collection annotation, the collection rule
    already catches it -- this fails FIRST and says which of the two lists is
    now wrong, which is the difference between a diagnosis and a puzzle.
    """
    annotations = {
        f.name: _annotation_text(f.type) for f in dataclasses.fields(LanguageSpec)
    }
    wrong = {
        name: annotations[name]
        for name in sorted(_SCALAR_SPEC_FIELDS)
        if annotations.get(name) not in _SCALAR_SPEC_ANNOTATIONS
    }
    assert not wrong, (
        f"{wrong} are listed as scalar fields and are not annotated with a "
        f"pinned scalar spelling {_SCALAR_SPEC_ANNOTATIONS}. Either the "
        f"annotation changed -- in which case classify the field per the "
        f"collection rule -- or a new scalar spelling needs adding to "
        f"_SCALAR_SPEC_ANNOTATIONS deliberately (#757)."
    )


#: Where spec FIELDS are read. Scoped, and the scope is narrower than the claim
#: it is tempting to make: four modules outside the parser reference
#: `LanguageSpec` or `LANGUAGE_REGISTRY` (`config.py`, `server.py`,
#: `cli/hooks/_common.py`, `tools/search_ast.py`), so "the parser is the only
#: consumer" would be false. What is true today is that every read of a spec's
#: FIELDS lives in `parser/extractor.py` and `parser/imports.py`, both inside
#: this scope.
#:
#: ⚠ A computed read over a spec field in one of those four modules escapes this
#: guard. Widening it to them fails on three unrelated `server.py` sites
#: (`getattr(logging, level_name, ...)` and friends), so the honest closure is to
#: key on the OBJECT rather than the directory -- flag
#: `getattr(<name bound to a spec>, <computed>)` anywhere in `src/`. Not done:
#: it needs binding analysis to be worth more than the scope, and the two
#: modules that actually read fields are covered. Recorded so the next reader
#: knows the limit rather than inferring a stronger claim from the constant.
_SPEC_READING_PACKAGE = "src/jcodemunch_mcp/parser"


def _dynamic_attribute_reads() -> list[str]:
    """`getattr(x, <not a literal>)` sites in the package that reads specs."""
    sites = []
    for path in _src_files():
        posix = "src/" + path.as_posix().split("/src/", 1)[-1]
        if not posix.startswith(_SPEC_READING_PACKAGE):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) > 1
                and not isinstance(node.args[1], ast.Constant)
            ):
                sites.append(f"{posix}:{node.lineno}")
    return sorted(set(sites))


def test_no_dynamic_attribute_read_hides_a_spec_field():
    """⚠⚠ An UNKNOWN read must not be reported as an absence.

    `_reads_of` matches a literal attribute, `getattr` with a constant name and
    a constant subscript. It cannot see `getattr(spec, name)` where `name` is a
    variable -- and **that is exactly how this file reads the channels**:
    `_spec_recognised` loops over `_EXTRACTION_CHANNELS` and calls
    `getattr(spec, channel, None)`. If the parser ever adopts that style over
    spec fields, the scan reports "unread" for a field read on every call, and
    `test_a_field_classified_unread_is_still_unread` would then CERTIFY the
    classification it exists to refuse.

    So a dynamic read in the package that consumes specs is UNKNOWN, and UNKNOWN
    fails loudly instead of counting as absence -- the same rule the product
    applies to `has_any()` and to every tri-state probe in this tree. Measured
    when written: six such calls exist under `src/`, none in the parser and none
    over a spec (`logging`, `sys`, `self._mod`, two over a partial index row).
    """
    sites = _dynamic_attribute_reads()
    assert not sites, (
        f"{sites} read an attribute by a computed name inside "
        f"{_SPEC_READING_PACKAGE}, so the unread scan can no longer tell a "
        f"field nothing reads from one read dynamically. Either read the field "
        f"by name, or make the unread classification prove itself another way "
        f"-- an UNKNOWN read must not be published as an absence (#757)."
    )


def test_no_spec_field_is_unaccounted_for():
    """⚠⚠ The roster, pinned: a new field of `LanguageSpec` fails by name.

    The collection rule above decides what a node-type collection owes. This is
    the outer ring -- every field, collection-shaped or not, must be accounted
    for somewhere, so a new field cannot arrive unexamined and a new SCALAR
    cannot be mistaken for a reviewed one.
    """
    accounted = (
        {"symbol_node_types"}
        | set(_EXTRACTION_CHANNELS)
        | set(_PENDING_CHANNELS)
        | set(_UNREAD_NON_CHANNEL_FIELDS)
        | set(_READ_FOR_SOMETHING_ELSE)
        | _SCALAR_SPEC_FIELDS
    )
    unaccounted = sorted(_spec_field_names() - accounted)
    assert not unaccounted, (
        f"LanguageSpec gained {unaccounted}, which nothing here accounts for. "
        f"If it holds node types, classify it per the collection rule. If it is "
        f"a scalar, add it to _SCALAR_SPEC_FIELDS with what it is -- and note "
        f"that this is not an exit: the collection rule keys on the ANNOTATION, "
        f"so a collection named as a scalar still owes a classification (#757)."
    )


@functools.lru_cache(maxsize=1)
def _checkable_languages():
    """Every language whose recognised node types can be compared to a grammar.

    Two sources, and the inventory records which:

    * `spec` -- the language declares node types in any extraction channel,
      `symbol_node_types` or one of `_EXTRACTION_CHANNELS` (#757; 22 specs
      declare the first, and `_spec_recognised` is what the arm tests);
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
        declared = _spec_recognised(spec)
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

    spec = LANGUAGE_REGISTRY[language]
    where = {
        node_type: sorted(
            channel
            for channel in ("symbol_node_types", *_EXTRACTION_CHANNELS)
            if node_type in (getattr(spec, channel, None) or ())
        )
        for node_type in sorted(ghosts)
    }

    assert not ghosts, (
        f"{language}: {where} -- declared in the channel(s) named, and the "
        f"grammar never emits that node type, so the entry matches nothing and "
        f"the form is silently unextractable (#724). Check the grammar's own "
        f"spelling with Language.node_kind_for_id. ⚠ The channel is "
        f"REPORTED rather than assumed: since #757 this property covers all "
        f"four, and naming symbol_node_types unconditionally sent a reader to "
        f"the wrong list."
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

    * `apex` and `solidity`: the bare string `"_declaration"`, an argument to
      `str.replace` in a signature f-string (`node.type.replace("_declaration",
      "")`), not a node type;
    * `svelte` and `vue`: seven or eight JavaScript node types, correct for the
      script block's DELEGATED grammar and absent from the host grammar;
    * `julia` and `solidity`: two real ghosts, BOTH FIXED on 2026-09-17 (#737,
      #738), so `_INLINE_GHOSTS_FOUND` is empty rather than broken.

    Two signals in six, and the four false positives are structural rather than
    fixable by a threshold -- an `endswith` argument and a delegated grammar are
    both legitimate. Gating on this would fail four languages forever, which is
    how a guard gets disabled. The real ones are recorded and filed instead.

    ⚠ A third literal turned up in julia while #738 was being fixed and was
    right to be absent from that measurement: `mutable_struct_definition` is a
    kind the grammar does not emit, but it spells `mutable struct X` as an
    ordinary `struct_definition` which the extractor already matched, so the
    dead alternative cost nothing. Deleted as dead code, not filed as a defect.

    This test asserts the SCOPE so a later widening has to argue with it.
    """
    spec_half = set(_spec_declaring_languages())
    all_checkable = set(_checkable_languages())

    assert spec_half < all_checkable, "the inline half is missing from the scan"

    # ⚠ Assert the SOURCE property A is parametrized over, not just that the
    # four languages are outside the spec half: the first version asserted the
    # precondition and would have stayed green if the parametrize at the top of
    # property A were widened to `_checkable_languages()`, which is the change
    # this test claims to guard against.
    cases = set(test_every_declared_node_type_is_one_the_grammar_emits.pytestmark[0].args[1])
    assert cases == spec_half, sorted(cases ^ spec_half)

    for language in ("solidity", "vue", "svelte", "julia"):
        assert language not in cases, (
            f"{language} is now in property A's cases; the four structural "
            f"false positives in this docstring need re-measuring first"
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
    case above pass while checking nothing. Measured on THIS tree, 2026-09-16:
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
    # ⚠ The floor is 10 because the SMALLEST real vocabulary is `json` at 12
    # (then toml 19, elisp 20, groovy 20). A `> 20` written from a guess failed
    # against a correct tree, and its first correction named elisp -- the
    # third-smallest, which happened to be the one that failed. A non-vacuity
    # floor has to clear the smallest MEMBER, not the one that caught you.
    for language, (_recognised, kinds, _source) in reachable.items():
        assert len(kinds) >= 10, (language, len(kinds))


def test_a_confirmed_gap_is_in_the_inventory():
    """Every gap this instrument found on review is real, and none outlives its fix.

    ⚠ Each was confirmed by running a snippet through `parse_file` and
    watching the symbol not appear, never by reading the scan -- the
    [[a-fixture-that-cannot-express-the-reported-shape-cannot-fail-on-it]]
    rule. When one is fixed, the node type leaves the inventory and this test
    names the `_CONFIRMED_GAPS` line to delete, the same way the ghost list
    cannot outlive its defect.

    ⚠⚠ NOT parametrized, deliberately. `_CONFIRMED_GAPS` is empty now that
    #733 closed the last three entries, and a parametrize over an empty set
    SKIPS -- a green-looking row that asserts nothing, and one unit of the
    `ci.skips_windows` ceiling spent on it. Iterating inside the test reports
    every stale entry in one message instead of one id per entry, which is the
    better failure anyway: the fix is always "delete these lines".
    """
    inventory = _current_inventory()

    stale = sorted(
        f"{language}/{node_type}"
        for language, entries in _CONFIRMED_GAPS.items()
        for node_type, _why in entries
        if node_type not in inventory.get(language, [])
    )
    assert not stale, (
        f"{stale} are recorded as confirmed gaps but are no longer in the "
        f"inventory -- if they were fixed, remove the _CONFIRMED_GAPS "
        f"entries; if the scan stopped seeing them, the scan is broken."
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
    # ⚠⚠ Match the PLANTED node type, never the direction's wording. The gate
    # builds one message carrying both "newly unnamed" and "no longer unnamed"
    # on every failure, so matching either phrase proves only that something
    # raised -- the first version of this test asserted both directions and
    # tested one of them twice. That is the defect this test exists to prevent,
    # reproduced inside it [[a-ratchet-can-pass-against-the-defect-it-names]].
    with pytest.raises(AssertionError, match=r"no longer unnamed.*planted_extra_declaration"):
        test_the_unnamed_declaration_inventory_matches_the_baseline()

    # 2. A newly unnamed form -- the #698 arrival shape -- must fail too.
    grown = json.loads(json.dumps(real))
    dropped = grown["inventory"][language][0]
    grown["inventory"][language] = grown["inventory"][language][1:]
    planted.write_text(json.dumps(grown), encoding="utf-8")
    with pytest.raises(AssertionError, match=rf"newly unnamed.*{re.escape(dropped)}"):
        test_the_unnamed_declaration_inventory_matches_the_baseline()


@functools.lru_cache(maxsize=1)
def _top_level_functions():
    """Module-level functions of `extractor.py`, by name.

    ⚠⚠ `ast.walk` is WRONG for this and the difference is not cosmetic: 22
    helper names are defined 27 times as NESTED closures inside other
    functions, so a name-keyed walk collapses them and resolves a call to
    whichever copy it saw last. Module body only. A nested closure is already
    inside the harvest's own walk, so it needs no resolution here.
    """
    import ast as _ast

    from jcodemunch_mcp.parser import extractor

    module = _ast.parse(inspect.getsource(extractor))
    # ⚠ `AsyncFunctionDef` too: `extractor.py` has none today (147 top-level
    # functions, 0 async), and an `async def` helper added later would leave
    # the call graph silently, which is the direction that INFLATES a gap.
    return {
        node.name: node
        for node in module.body
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef))
    }


def _literals_reachable_outside(language):
    """Grammar-kind literals held by top-level helpers the parse function calls.

    Transitive over module-level functions. Returns the node types a helper
    recognises that `_parse_<lang>_symbols` does not mention itself -- exactly
    the set the harvest cannot see.
    """
    tops = _top_level_functions()
    entry = tops.get(f"_parse_{language}_symbols")
    if entry is None:
        return frozenset()

    seen, queue, outside = set(), [entry], set()
    own = {
        n.value for n in ast.walk(entry)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }
    kinds = _grammar_kinds(language) or frozenset()
    while queue:
        node = queue.pop()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                helper = tops.get(sub.func.id)
                if helper is not None and helper.name not in seen:
                    seen.add(helper.name)
                    queue.append(helper)
                    outside |= {
                        n.value for n in ast.walk(helper)
                        if isinstance(n, ast.Constant) and isinstance(n.value, str)
                    }
    return frozenset((outside & kinds) - own)


# The languages whose helpers hold a grammar-kind literal the parse function
# does not, and the measurement that says it is not an inflated gap.
_HELPER_LITERAL_EXCEPTIONS = {
    "apex": (
        {"accessor_list", "modifiers", "modifier", "static", "final"},
        "reached through `apex_member_kind` and the SHARED `has_modifier_keyword`, "
        "which decide a recognised member's KIND rather than whether it is a "
        "symbol: `_parse_apex_symbols` recognises `field_declaration` itself "
        "(#774). MEASURED against the inventory fixture, not reasoned from the "
        "node names: none of these five appears in any language's rows (193 "
        "distinct nodes, zero hits), so none of them can inflate a gap in "
        "either direction. ⚠ An earlier wording here justified that by saying "
        "the rows hold `*_declaration`-shaped nodes only, which is false -- 90 "
        "of the 193 are `*_definition`, `*_item` or `*_spec` -- so the "
        "measurement is stated directly and a reader cannot reuse a wrong "
        "generalisation. `has_modifier_keyword` "
        "is shared with C# deliberately -- one grammar question, two node "
        "shapes -- and inlining it to satisfy this scan would be the second "
        "derivation the 08-19 standing lesson names",
    ),
    "fsharp": (
        {"string"},
        "reached through `_fs_spilled_and_offsets` (#856), which MASKS comment "
        "and string byte ranges out of the offside scan that decides whether a "
        "spilled `and` continues a `let`; it never decides whether a node is a "
        "symbol. MEASURED against the inventory fixture: `string` appears in "
        "none of its 185 distinct nodes, so it cannot inflate a gap in either "
        "direction",
    ),
    "sql": (
        {"function_declaration", "function_body"},
        "reached through the SHARED `_extract_name` and `_build_signature`, which "
        "NAME and SIGN a node rather than decide whether it is a symbol: "
        "`_parse_sql_symbols` recognises `create_function`, and "
        "`CREATE FUNCTION add_one(...)` yields ('add_one', 'function'), so the "
        "inventory row is honest",
    ),
}


def test_no_inline_language_recognises_node_types_outside_its_parse_function():
    """The measurement behind "the harvest cannot invent a gap".

    ⚠⚠ The first version of this test was VACUOUS and its docstring claimed
    otherwise, which is the worse half. It iterated the 32 languages that reach
    the scan -- a set that excludes `elixir` and `nix` BY CONSTRUCTION, being
    exactly the two `_checkable_languages()` drops -- and asserted none of them
    named `_walk_elixir` or `_walk_nix_bindings`. It could fail only on two
    hard-coded helper names appearing where they never would, while the
    ARCHAEOLOGY row cited it as holding the measurement. A guard written
    against two spellings, inside a file about guards written against
    spellings. Found in review, after I flagged it as suspect and was right.

    The property: a node type matched in a top-level helper the harvest does
    not read shrinks `recognised` and INFLATES that language's gap -- the one
    direction in which this scan could invent work for a human. This walks the
    top-level call graph and asserts the set is empty, or a named exception
    carrying the measurement that says it is harmless.
    """
    inline = [lang for lang, src in _inventory_sources().items() if src == "inline"]
    assert len(inline) >= 25, inline

    for language in sorted(inline):
        outside = _literals_reachable_outside(language)
        excused = _HELPER_LITERAL_EXCEPTIONS.get(language, (set(), ""))[0]
        assert not (outside - excused), (
            f"{language}: {sorted(outside - excused)} are grammar node types "
            f"matched in a helper `_parse_{language}_symbols` calls but does "
            f"not mention. The harvest cannot see them, so {language}'s "
            f"inventory rows may be INFLATED -- the scan inventing work rather "
            f"than hiding it. Widen the harvest, or add a "
            f"_HELPER_LITERAL_EXCEPTIONS entry with the measurement that says "
            f"the rows are honest."
        )


def test_the_helper_literal_exception_is_not_an_escape_hatch():
    """An exception must name something that is really reachable outside.

    The `_RESOLVED_BEFORE_NAME_FIELDS` rule from #712: an excuse for a thing
    that is not happening is an entry nobody can ever remove, and the list
    becomes the hatch. If `sql` stops reaching those literals through a shared
    helper, this fails and names the line to delete.
    """
    for language, (node_types, why) in _HELPER_LITERAL_EXCEPTIONS.items():
        outside = _literals_reachable_outside(language)
        assert node_types <= outside, (
            f"{language}: {sorted(node_types - outside)} is excused but is no "
            f"longer reachable outside the parse function ({why}). Remove it."
        )


def test_the_inline_half_names_what_it_cannot_reach():
    """`elixir` and `nix` are dropped, and the drop is asserted, not silent.

    Both take the compiled-grammar path and both harvest zero literals, so
    `_checkable_languages()` omits them. That is a real limit of the
    instrument. If a third language joins them -- or if one of these two is
    brought in -- this fails and the docstrings that quote 32 and 34 have to
    be re-measured rather than drifting.
    """
    from jcodemunch_mcp.parser.languages import LANGUAGE_REGISTRY

    compiled_path = set()
    for language, spec in LANGUAGE_REGISTRY.items():
        if getattr(spec, "symbol_node_types", None):
            continue
        kinds = _grammar_kinds(language)
        if kinds is None:
            continue
        node = _extractor_functions().get(f"_parse_{language}_symbols")
        if node is not None and "get_parser" in ast.unparse(node):
            compiled_path.add(language)

    reached = {lang for lang, src in _inventory_sources().items() if src == "inline"}

    assert compiled_path - reached == {"elixir", "nix"}, sorted(compiled_path - reached)
    assert len(compiled_path) == 34, len(compiled_path)
    assert len(reached) == 32, len(reached)


def test_the_inline_half_is_actually_covered():
    """The blind spot this file shipped with, asserted closed.

    Specs that declare no `symbol_node_types` and still parse with a compiled
    grammar were skipped as "regex-parsed", which exempted solidity, nim,
    graphql and vue -- languages that match node types against a hand-written
    list in a function body, where an omission is #698 exactly. A regression to
    the old filter drops these languages from `_checkable_languages()` and
    fails here.

    ⚠⚠ `vue` is in that list for the opposite reason to the other three and the
    correction matters, because two drafts of this paragraph got it wrong in
    opposite directions. It recognises five node types, NONE declaration-shaped
    (`attribute`, `comment`, `raw_text`, `script_element`, `start_tag`), so its
    unnamed-form set is EMPTY and it has no inventory rows at all: the
    declaration node types a reader sees in `_parse_vue_symbols` belong to the
    DELEGATED JavaScript grammar of its script block, not to vue's. The first
    draft called it a language hard-coding eight declaration node types; the
    second said the delegation was "why it has inventory rows at all", when it
    has none. Both were written from reading rather than from the fixture.
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


def test_a_recorded_inline_ghost_is_still_a_ghost():
    """An entry must FAIL once its ghost is fixed, or it becomes a false record.

    ⚠⚠ **`_INLINE_GHOSTS_FOUND` had NO READER for its whole life** -- one
    definition, one docstring mention, zero assertions -- so its entries could
    have outlived their defects and nothing would have objected. That is
    #561/#562's lesson inside the instrument #724 built to find that class.
    `_CONFIRMED_GAPS` has had `test_a_confirmed_gap_is_in_the_inventory` since
    #724; this is its missing sibling.

    ⚠⚠ **The first version of THIS test could not fail on the case it was
    written for, and review proved it by planting both deleted entries on the
    fixed tree and watching it pass.** It asserted the node type was absent from
    the GRAMMAR -- but a ghost is fixed by changing the EXTRACTOR literal, and
    the grammar never gains the typo, so the gate fired only in a case that
    cannot happen. Second guard in one session with that defect
    (`[[a-ratchet-can-pass-against-the-defect-it-names]]`).

    The property is about the EXTRACTOR, so it reads
    `_harvested_node_types`: a recorded ghost must still be a literal the parse
    function matches AND still be absent from the grammar. When either half
    stops holding the ghost is gone and the entry is a false record.
    """
    for language, (node_type, why) in _INLINE_GHOSTS_FOUND.items():
        kinds = _grammar_kinds(language)
        assert kinds is not None, f"{language} has no grammar in this pack"

        harvested = _harvested_node_types(language, kinds)
        assert node_type in harvested, (
            f"{language}: `{node_type}` is no longer a literal "
            f"_parse_{language}_symbols matches, so the ghost is FIXED ({why}). "
            f"DELETE this _INLINE_GHOSTS_FOUND entry -- do not adjust it."
        )
        assert node_type not in kinds, (
            f"{language}: the grammar now emits `{node_type}`, so it is not a "
            f"ghost any more ({why}). DELETE the entry."
        )


def test_the_inline_ghost_table_is_empty_and_that_is_deliberate():
    """Pins the vacuity of the test above so it cannot pass unnoticed forever.

    If someone adds an entry, this fails and points at the sibling gate, which
    is then no longer vacuous. If someone empties the table to silence that
    gate, this fails too and asks for the reason in the CHANGELOG.
    """
    assert _INLINE_GHOSTS_FOUND == {}, (
        f"_INLINE_GHOSTS_FOUND now carries {sorted(_INLINE_GHOSTS_FOUND)}. That "
        f"is fine -- it means a new ghost was found -- and it means "
        f"test_a_recorded_inline_ghost_is_still_a_ghost is no longer vacuous. "
        f"CHANGE THIS ASSERTION (a docstring edit leaves it red) and say in the "
        f"CHANGELOG which ghost and which issue tracks it."
    )
