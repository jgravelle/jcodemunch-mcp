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

from unittest import mock

import pytest

from jcodemunch_mcp.parser import extractor
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


# ---------------------------------------------------------------------------
# Locals: the blast radius this fix must NOT take (#732 review round 2)
# ---------------------------------------------------------------------------

_LOCALS = """class Holder {
    val member = 1
    fun work() {
        val localOrdinary = 3
        val LOCAL_SCREAM = 2
        var localVar = 4
        for (x in 1..3) { val inner = x }
    }
}

val topLevel = 5
fun topFn() { val topLocal = 9 }
val lambdaHost = run { val insideLambda = 1; insideLambda }
"""


#: Every scope a `property_declaration` can sit in, as
#: (label, source, the name that must NOT be a symbol, the name that must be).
#:
#: ⚠⚠ **This matrix exists because the first gate was a DENYLIST of local
#: scope spellings and was wrong for three of these.** It listed
#: `{function_body, lambda_literal, anonymous_initializer}` and walked
#: ancestors, which misses a secondary constructor (`statements` directly under
#: `secondary_constructor`), an `if`/`when` body (`control_structure_body`),
#: and therefore any local inside a class-scope initialiser -- so
#: `class C { constructor() { val inCtor = 2 } }` published `inCtor` as a
#: property of `C`. Every one of those passed the three tests above, because
#: the `_LOCALS` fixture has no constructor, no `init`, no `if` body and no
#: `when` body in it. Found in review round 3; the gate is an allowlist of
#: MEMBER parents now, derived by asking the grammar rather than by listing
#: what came to mind.
_SCOPE_MATRIX = [
    ("function body", "class C {\n  val m = 1\n  fun f() {\n    val x = 2\n  }\n}\n"),
    ("secondary ctor", "class C {\n  val m = 1\n  constructor() {\n    val x = 2\n  }\n}\n"),
    ("init block", "class C {\n  val m = 1\n  init {\n    val x = 2\n  }\n}\n"),
    ("getter body", "class C {\n  val m: Int\n    get() {\n      val x = 2\n      return x\n    }\n}\n"),
    ("setter body", "class C {\n  var m: Int = 0\n    set(v) {\n      val x = v\n      field = x\n    }\n}\n"),
    ("if body", "class C {\n  val m = if (true) {\n    val x = 2\n    1\n  } else 3\n}\n"),
    ("when body", "class C {\n  val m = when (1) {\n    else -> {\n      val x = 2\n      1\n    }\n  }\n}\n"),
    ("for body", "class C {\n  val m = 1\n  fun f() {\n    for (i in 1..2) {\n      val x = i\n    }\n  }\n}\n"),
    ("while body", "class C {\n  val m = 1\n  fun f() {\n    while (true) {\n      val x = 2\n    }\n  }\n}\n"),
    ("try block", "class C {\n  val m = 1\n  fun f() {\n    try {\n      val x = 2\n    } finally {}\n  }\n}\n"),
    ("lambda", "class C {\n  val m = 1\n  fun f() {\n    run {\n      val x = 2\n    }\n  }\n}\n"),
    ("expression-bodied fun", "class C {\n  val m = 1\n  fun f() = run {\n    val x = 2\n  }\n}\n"),
]


@pytest.mark.parametrize("label,source", _SCOPE_MATRIX, ids=[m[0] for m in _SCOPE_MATRIX])
@pytest.mark.parametrize("local_name", ["x", "MAX_X"])
def test_no_scope_publishes_a_local_as_a_member(label, source, local_name):
    """A local is not a symbol in ANY scope, whatever it is called.

    ⚠⚠ The `local_name` axis is the capitalisation property, and it is the
    half that was asserted over one scope while being FALSE one scope over:
    a SCREAMING_CASE local was dropped by the constant channel's own gate
    while the ordinary local beside it was published, so which declarations
    became symbols depended on how they were spelled. Both spellings run
    through every scope here.

    ⚠ `m` is the non-vacuity control: a gate that dropped everything would
    satisfy the absence assertion alone.
    """
    source = source.replace("val x =", f"val {local_name} =")
    names = {s.name for s in parse_file(source, "Scope.kt", "kotlin")}

    assert local_name not in names, f"{label}: local published as a member; got {sorted(names)}"
    assert "m" in names, f"{label}: the enclosing member vanished; got {sorted(names)}"


def test_a_member_of_a_local_type_is_still_a_member():
    """The allowlist's other direction, and the denylist got this wrong too.

    A class declared inside a function IS indexed, so its properties are
    declared members of an indexed type and belong in the index with it. An
    ancestor walk cannot see that -- it finds the enclosing `function_body`
    and calls the member a local -- which is why the rule reads the DIRECT
    parent. Same for a property of an anonymous `object`.
    """
    local_class = "fun outer() {\n  class Local {\n    val localMember = 1\n  }\n}\n"
    names = {s.name for s in parse_file(local_class, "T.kt", "kotlin")}
    assert {"Local", "localMember"} <= names, sorted(names)

    anon = "fun outer() {\n  val o = object {\n    val anonProp = 1\n  }\n}\n"
    symbols = {s.name: s for s in parse_file(anon, "T.kt", "kotlin")}
    assert "anonProp" in symbols, sorted(symbols)
    # `o` itself is a local and stays out, so this is not a blanket widening.
    assert "o" not in symbols, sorted(symbols)


def test_the_gate_is_an_allowlist_of_member_parents():
    """⚠⚠ The DIRECTION is the rule, not the contents.

    An allowlist fails CLOSED: a container spelling the set does not know
    yields no symbol, which is the pre-#732 status quo. A denylist of local
    scopes fails OPEN and publishes a local as class state, which moves every
    published dead-code grade. A future Kotlin grammar that renames a body
    node must not be able to turn this into false members.
    """
    from jcodemunch_mcp.parser.extractor import _KOTLIN_MEMBER_PARENTS

    invented = "class C {\n  val m = 1\n  fun f() {\n    val x = 2\n  }\n}\n"
    with mock.patch.object(
        extractor, "_KOTLIN_MEMBER_PARENTS", frozenset(_KOTLIN_MEMBER_PARENTS) - {"class_body"}
    ):
        names = {s.name for s in parse_file(invented, "T.kt", "kotlin")}

    # Removing a spelling LOSES a member; it never gains a false one.
    assert "m" not in names, sorted(names)
    assert "x" not in names, sorted(names)


def test_a_local_variable_is_not_a_property():
    """⚠⚠ Kotlin spells a local `val` with the SAME node type as a member.

    So declaring `property_declaration` without a scope gate indexes every
    local variable in every Kotlin file. Measured before the gate:
    `Holder.work.localOrdinary`, `Holder.work.localVar`, `Holder.work.inner`
    and `topFn.topLocal` were all symbols, one of them declared in a `for`
    body. Nothing in the first version of this PR declared that widening, and
    the comment beside `_CLASS_SCOPED_CONSTANT_LANGUAGES` refuses exactly it
    for the constant channel because it moves symbol counts in every index and
    every published dead-code grade. Found in review.
    """
    names = {s.name for s in parse_file(_LOCALS, "Holder.kt", "kotlin")}

    for local in ("localOrdinary", "localVar", "inner", "topLocal", "insideLambda"):
        assert local not in names, (local, sorted(names))


def test_the_local_gate_is_not_keyed_on_capitalisation():
    """The incoherence that made the hole visible, asserted as a property.

    Before the gate, a local `val LOCAL_SCREAM` was DROPPED (the constant
    channel refuses a function parent) while the `val localOrdinary` beside it
    was INDEXED -- the discriminator between a symbol and nothing was the
    variable's capitalisation. Both are locals and neither is a symbol now, for
    the same reason.
    """
    names = {s.name for s in parse_file(_LOCALS, "Holder.kt", "kotlin")}

    assert "LOCAL_SCREAM" not in names, sorted(names)
    assert "localOrdinary" not in names, sorted(names)


def test_the_gate_keeps_members_and_top_level_declarations():
    """Non-vacuity: a gate that dropped everything would pass the two above."""
    symbols = parse_file(_LOCALS, "Holder.kt", "kotlin")
    by_name = {s.name: s for s in symbols}

    assert by_name["member"].kind == "property"
    # File scope is a module binding, not class state (#807): an initialised
    # `val` is a `constant`, the Swift `let` / Scala `val` rule.
    assert by_name["topLevel"].kind == "constant"
    # A top-level property whose INITIALISER is a lambda is still a symbol;
    # only the declaration inside the lambda is local.
    assert by_name["lambdaHost"].kind == "constant"
