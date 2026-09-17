"""#732: Kotlin properties are not indexed at all.

A Kotlin class indexes with its methods and none of its state. `val name`,
`var count` and a top-level `val topLevel` all yield no symbol, so a data
class -- whose entire surface is properties -- is an empty name in the index.

⚠⚠ **Reading `extractor.py` says this is already covered and it is not.** There
IS a `property_declaration` branch keyed on kotlin (#428), and it is a CONSTANT
extractor: it returns None for anything that is not a `val`, and for any `val`
whose name does not read as SCREAMING_CASE. It does exactly what it was written
to do. Nothing was ever wrong with it; ordinary properties simply had no
channel at all. Found by #724's grammar inventory and confirmed by running the
product, which is the only way this was ever going to surface -- the branch's
existence is what makes it invisible to a reader.

⚠⚠ **A class-scoped constant is the shape that breaks a careless fix, and the
first fixture could not express it.** `const val` inside a `companion object`
is THE idiomatic Kotlin constant, and the constant channel is gated on
`parent_symbol is None` unless the language is in
`_CLASS_SCOPED_CONSTANT_LANGUAGES` -- which Kotlin was not. So once
`property_declaration` was declared, `_extract_name` DECLINED those nodes to a
channel that could not accept them and they were emitted by neither: disjoint,
but not exhaustive. The first fixture had no `companion object`, no `object`
and no class-body SCREAMING_CASE `val`, so every test passed over a hole
([[a-fixture-that-cannot-express-the-reported-shape-cannot-fail-on-it]]).
Found in review.

⚠⚠ **The two channels must not both fire.** `property_declaration` sits in
`KOTLIN_SPEC.constant_patterns`, and `_walk_tree` runs the constant check
INDEPENDENTLY of symbol extraction on the same node -- not as an `elif`. So
declaring the node type in `symbol_node_types` without a rule about which
channel owns it emits `const val MAX` TWICE, once as a constant and once as a
property. One predicate decides, and both channels ask it.
"""

import pytest

from jcodemunch_mcp.parser.extractor import parse_file
from jcodemunch_mcp.parser.grammar_pack import get_parser
from jcodemunch_mcp.parser.languages import KOTLIN_SPEC


SOURCE = """package demo

const val MAX_RETRIES = 3
val SCREAMING_NAME = "x"
val topLevel = 1
var mutableTop = 2

class Account(val id: String) {
    val owner: String = "me"
    var balance: Int = 0
    private val secret: String = "s"
    lateinit var session: String
    val computed: Int get() = balance * 2
    val MAX_SIZE = 10
    fun deposit(n: Int) { balance += n }

    companion object {
        const val INNER_CONST = 3
        val INNER_SCREAM = "s"
    }
}

object Registry {
    const val BAR_CONST = 4
    val entries = 0
}

data class Point(val x: Int, val y: Int)
"""


@pytest.fixture(scope="module")
def parsed():
    """A LIST, never a dict keyed by name.

    Two declarations can share a name across scopes, and a name-keyed dict
    would drop one silently while the count assertions below measured the dict
    rather than the extraction [[a-set-cannot-count]].
    """
    return list(parse_file(SOURCE, "Account.kt", "kotlin"))


def _named(parsed, name):
    return [s for s in parsed if s.name == name]


def test_the_fixture_parses_without_error():
    """Separate "not extracted" from "never parsed"."""
    tree = get_parser("kotlin").parse(SOURCE.encode("utf-8"))

    assert not tree.root_node.has_error, "the fixture is not valid Kotlin"


def test_the_controls_extract(parsed):
    """The class and its method, so a total failure is distinguishable."""
    names = {s.name for s in parsed}

    assert {"Account", "deposit", "Point"} <= names, sorted(names)


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------

def test_a_class_property_is_indexed(parsed):
    """`val owner` and `var balance` are the reported case."""
    names = {s.name for s in parsed}

    assert "owner" in names, sorted(names)
    assert "balance" in names, sorted(names)


def test_a_mutable_property_is_not_indexed_as_a_constant(parsed):
    """⚠⚠ `var balance` is mutable; calling it a constant would be a lie.

    The existing branch extracts constants and this is the reason the fix
    cannot simply widen it: every constant-oriented consumer -- the constant
    census, `check_delete_safe`'s reasoning, an outline that groups constants
    -- would be told a mutable field never changes.
    """
    balance = _named(parsed, "balance")

    assert balance, "var balance is not indexed at all"
    assert balance[0].kind == "property", balance[0].kind


def test_a_private_property_is_indexed(parsed):
    """Visibility is not the question; a private property is still a symbol."""
    assert _named(parsed, "secret"), [s.name for s in parsed]


def test_a_top_level_property_is_indexed(parsed):
    """Kotlin allows a file-scope `val`/`var`, and both are declarations."""
    names = {s.name for s in parsed}

    assert "topLevel" in names, sorted(names)
    assert "mutableTop" in names, sorted(names)


def test_a_class_property_knows_its_owner(parsed):
    """A property that belongs to nothing is not findable from its class.

    #713's half: an index holding a member that belongs to nothing.
    """
    for name in ("owner", "balance", "secret"):
        matches = _named(parsed, name)
        assert matches, name
        assert matches[0].parent == "Account.kt::Account#class", (name, matches[0].parent)


# ---------------------------------------------------------------------------
# The regression guard: #428's constants must not move
# ---------------------------------------------------------------------------

def test_a_declared_constant_is_still_a_constant(parsed):
    """`const val MAX_RETRIES` keeps kind `constant`, not `property`.

    #428 added that branch deliberately. A fix for #732 that reclassified
    constants would be trading one defect for another, and the published kind
    is what consumers group on.
    """
    max_retries = _named(parsed, "MAX_RETRIES")

    assert max_retries, "the #428 constant extraction regressed"
    assert max_retries[0].kind == "constant", max_retries[0].kind


def test_a_screaming_case_val_is_still_a_constant(parsed):
    """The naming-convention half of #428, which has no `const` keyword."""
    screaming = _named(parsed, "SCREAMING_NAME")

    assert screaming, "the #428 naming-convention branch regressed"
    assert screaming[0].kind == "constant", screaming[0].kind


def test_no_declaration_is_emitted_twice(parsed):
    """⚠⚠ The trap this fix has to avoid, asserted directly.

    `property_declaration` is in `constant_patterns` AND would now be in
    `symbol_node_types`, and `_walk_tree` runs the constant check independently
    of symbol extraction on the same node. Without one predicate deciding which
    channel owns a declaration, `const val MAX_RETRIES` comes out twice.

    ⚠ Counted per (name, line), because two DIFFERENT declarations may share a
    name legitimately -- the duplicate this catches is one declaration emitted
    by two channels, which is the same name at the same line.
    """
    seen = {}
    for symbol in parsed:
        seen.setdefault((symbol.name, symbol.line), []).append(symbol.kind)

    duplicates = {k: v for k, v in seen.items() if len(v) > 1}

    assert not duplicates, duplicates


def test_a_constructor_property_is_not_double_counted(parsed):
    """`class Account(val id: String)` declares a property in the parameter list.

    ⚠ A constructor `val` is a real property and Kotlin generates an accessor
    for it, but it parses as `class_parameter`, not `property_declaration`, so
    it is out of this issue's scope. Stated as a test rather than left silent,
    because a later reader will otherwise read its absence as this fix being
    incomplete, and because if a future change starts emitting it this test
    says where the decision was made.
    """
    ids = _named(parsed, "id")

    assert len(ids) <= 1, [(s.kind, s.line) for s in ids]


def test_a_class_scoped_constant_is_emitted_by_exactly_one_channel(parsed):
    """⚠⚠ The hole review found: emitted by NEITHER, not emitted twice.

    `val MAX_SIZE` in a class body, `const val INNER_CONST` in a companion
    object, and `const val BAR_CONST` in an `object` are all constants by
    #428's rule, so `_extract_name` declines them. Before Kotlin joined
    `_CLASS_SCOPED_CONSTANT_LANGUAGES` the constant channel could not run at
    that scope either, and all three vanished.
    """
    for name in ("MAX_SIZE", "INNER_CONST", "INNER_SCREAM", "BAR_CONST"):
        matches = _named(parsed, name)
        assert len(matches) == 1, (name, [(m.kind, m.line) for m in matches])
        assert matches[0].kind == "constant", (name, matches[0].kind)


def test_every_property_shape_survives(parsed):
    """`lateinit var`, a `get()`-backed val, and a property in an `object`.

    Three shapes the first fixture did not carry. `computed` has a getter and
    no initialiser; `session` has `lateinit` in its modifiers and no value at
    all; `entries` sits in an `object` rather than a `class`.
    """
    for name in ("session", "computed", "entries"):
        matches = _named(parsed, name)
        assert len(matches) == 1, (name, [(m.kind, m.line) for m in matches])
        assert matches[0].kind == "property", (name, matches[0].kind)


def test_the_kind_is_one_the_wire_can_carry():
    """⚠⚠ #571 almost repeated (#732 review).

    A kind absent from `KIND_ORDER` is rejected by `search_symbols`'
    `kind_filter not in VALID_KINDS` check AND omitted from the published
    schema enum, so the symbol is indexed and unreachable through the one
    filter meant to find it. `property` was mapped by `PHP_SPEC` for years
    while nothing emitted one, so the whole suite stayed green over a
    declared-but-dead kind; Kotlin is the first live emitter.

    `test_every_spec_kind_is_a_valid_kind` is the general form of this, over
    every spec.
    """
    from jcodemunch_mcp.parser.symbols import VALID_KINDS

    assert "property" in VALID_KINDS, sorted(VALID_KINDS)


# ---------------------------------------------------------------------------
# The spec half
# ---------------------------------------------------------------------------

def test_the_node_type_is_declared():
    """#724's inventory half: the form the grammar spells is now named.

    When this passes, `kotlin/property_declaration` leaves
    `tests/fixtures/grammar_declaration_inventory.json` and the `_CONFIRMED_GAPS`
    entry naming it must go with it -- `test_a_confirmed_gap_is_in_the_inventory`
    fails otherwise, which is the record refusing to outlive the defect.
    """
    assert "property_declaration" in KOTLIN_SPEC.symbol_node_types, sorted(
        KOTLIN_SPEC.symbol_node_types
    )
