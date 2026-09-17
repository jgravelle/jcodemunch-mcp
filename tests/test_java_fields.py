"""#735: a plain Java instance field is not indexed.

`private int field;` yields no symbol, while `public static final int CONST = 1;`
in the same class does. A Java class indexes with its methods and none of its
state -- the widest of the nine gaps #724's grammar inventory found, by volume,
because every Java class with state has it.

⚠⚠ **The gap looks like it is about `final` and it is about the NODE TYPE.**
`CONST` reaches the index through `constant_patterns`, a separate channel that
matches `field_declaration` and requires BOTH `static` and `final` (#428).
`JAVA_SPEC.symbol_node_types` never named the node type at all, so everything
that channel declines -- which is every ordinary field -- had no channel to fall
to. Reading the extractor finds a `field_declaration` branch and stops, exactly
as in #732.

⚠⚠ **One `field_declaration` can bind SEVERAL names.** `int a, b, c;` is one
node with three `variable_declarator` children, and the constant channel already
emits all three. A field channel that emitted only the first would make the
discriminator between "indexed" and "silently dropped" the presence of
`static final` -- the same shape as #732's capitalisation incoherence, where
which declarations became symbols depended on how they were spelled. Every
assertion about a field here runs against a multi-declarator form too.

⚠ These tests state OUTCOMES, never which node type the spec declares. Two
designs reach the same behaviour (declare `field_declaration` and name the
declarators, or declare `variable_declarator` and gate on its parent), and a
test that pinned one of them would be the mechanism-not-outcome shape Practice 9
exists to refuse.
"""

import pytest

from jcodemunch_mcp.parser.extractor import parse_file
from jcodemunch_mcp.parser.grammar_pack import get_parser
from jcodemunch_mcp.parser.symbols import VALID_KINDS


SOURCE = """package demo;

public class Account {
    private int balance;
    String owner;
    protected final Logger log = null;
    static int instances;
    public static final int MAX_RETRIES = 3;
    static final int LOW = 1, HIGH = 2;
    int a, b, c;
    private java.util.List<String> tags;
    int[] history = new int[4];
    @Deprecated private int legacy = 0;

    void deposit(int n) {
        int local = n;
        for (int i = 0; i < n; i++) {
            int inner = i;
        }
    }

    class Inner {
        int innerField = 1;
    }
}
"""


@pytest.fixture(scope="module")
def parsed():
    """A LIST, never a dict keyed by name [[a-set-cannot-count]].

    Two declarations can share a name across scopes, and a name-keyed dict drops
    one silently while the count assertions below measure the dict rather than
    the extraction.
    """
    return list(parse_file(SOURCE, "Account.java", "java"))


def _named(parsed, name):
    return [s for s in parsed if s.name == name]


def test_the_fixture_parses_without_error():
    """Separate "not extracted" from "never parsed"."""
    tree = get_parser("java").parse(SOURCE.encode("utf-8"))

    assert not tree.root_node.has_error, "the fixture is not valid Java"


def test_the_controls_extract(parsed):
    """The class and its method, so a total failure is distinguishable."""
    names = {s.name for s in parsed}

    assert {"Account", "deposit", "Inner"} <= names, sorted(names)


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------

def test_an_instance_field_is_indexed(parsed):
    """`private int balance;` is the reported case."""
    names = {s.name for s in parsed}

    assert "balance" in names, sorted(names)


@pytest.mark.parametrize(
    "name",
    ["balance", "owner", "log", "instances", "tags", "history", "legacy"],
    ids=["private", "package-private", "final", "static", "generic", "array", "annotated"],
)
def test_every_field_shape_is_indexed(parsed, name):
    """⚠ `instances` and `log` are the near-misses.

    A bare `static` field is mutable shared state and a bare `final` field is
    per-instance; #428's constant rule requires BOTH modifiers and correctly
    declines each. They are fields, and a fix that keyed on "has modifiers"
    rather than on the node type would drop exactly these two.
    """
    assert _named(parsed, name), f"{name} is absent"


def test_a_field_is_kind_field(parsed):
    """Not `constant`, and not `property`.

    ⚠ `field` is an ESTABLISHED kind, not a revived one: the Python parser has
    emitted it for dataclass attributes since before #571, which is how
    @devtomnl found that both gates rejected the kind while 399 of them sat in
    this repo's own index. Java joins it. (An earlier draft of this docstring
    called Java "the first live emitter" -- that sentence belongs to `property`
    and Kotlin in #732, where PHP had declared the kind and nothing emitted it,
    and it is false here. Caught in review.)
    """
    assert _named(parsed, "balance")[0].kind == "field"


def test_the_field_kind_was_already_live_before_java(parsed):
    """The correction above, asserted rather than left as a comment.

    A docstring that names another language's behaviour is a claim, and this
    file is where the next reader checks it. Python dataclass attributes are the
    prior emitter; if that ever stops being true, the sentence above needs
    rewriting and this says so by failing.
    """
    python_kinds = {
        s.kind
        for s in parse_file(
            "from dataclasses import dataclass\n"
            "@dataclass\n"
            "class P:\n"
            "    x: int = 0\n",
            "p.py",
            "python",
        )
    }

    assert "field" in python_kinds, sorted(python_kinds)


def test_a_field_knows_its_owner(parsed):
    """A field without its owner is #698's complaint in another language.

    `Account.balance`, never a bare `balance`: an index that cannot say which
    class holds the state answers the question nobody asked.
    """
    balance = _named(parsed, "balance")[0]

    assert balance.qualified_name == "Account.balance", balance.qualified_name


def test_a_field_of_an_inner_class_is_owned_by_the_inner_class(parsed):
    """Nesting is the discriminator a flat name cannot express."""
    inner = _named(parsed, "innerField")

    assert inner, "innerField is absent"
    assert inner[0].qualified_name == "Account.Inner.innerField", inner[0].qualified_name


# ---------------------------------------------------------------------------
# Several names, one declaration
# ---------------------------------------------------------------------------

def test_every_declarator_of_a_multi_name_field_is_indexed(parsed):
    """⚠⚠ `int a, b, c;` is ONE node and THREE fields.

    The constant channel already emits every declarator
    (`_extract_java_constants`, "N declarators per node"). A field channel that
    named only the first would silently drop the rest, and the discriminator
    between indexed and dropped would be whether the declaration happened to be
    `static final`.
    """
    names = {s.name for s in parsed}

    assert {"a", "b", "c"} <= names, sorted(names)


def test_every_declarator_of_a_multi_name_constant_is_still_a_constant(parsed):
    """The control for the test above: the constant channel is unchanged."""
    assert [s.kind for s in _named(parsed, "LOW")] == ["constant"]
    assert [s.kind for s in _named(parsed, "HIGH")] == ["constant"]


# ---------------------------------------------------------------------------
# The two channels must not both fire
# ---------------------------------------------------------------------------

def test_a_static_final_field_is_still_a_constant(parsed):
    """#428's rule is unchanged: both modifiers, and only both."""
    assert [s.kind for s in _named(parsed, "MAX_RETRIES")] == ["constant"]


def test_no_declaration_is_emitted_twice(parsed):
    """⚠⚠ `field_declaration` is in `constant_patterns` already.

    `_walk_tree` runs the constant check INDEPENDENTLY of symbol extraction on
    the same node -- not as an `elif` -- so declaring the form without a rule
    about which channel owns it emits `static final int MAX_RETRIES` twice, once
    as a constant and once as a field. Keyed on (name, line) rather than on name
    alone: two legitimately same-named declarations in different scopes are not
    the same thing as one declaration emitted by two channels.
    """
    seen = [(s.name, s.line) for s in parsed]

    assert len(seen) == len(set(seen)), sorted(n for n in seen if seen.count(n) > 1)


# ---------------------------------------------------------------------------
# Locals are not fields
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("local", ["local", "inner", "i"])
def test_a_local_variable_is_not_a_field(parsed, local):
    """⚠ Java spells a local `local_variable_declaration`, a DIFFERENT node type
    from `field_declaration` -- which is why this language has no analogue of
    #732's Kotlin scope problem, where one node type served both.

    Asserted anyway rather than argued: the claim that the two are separate node
    types is the whole reason no scope gate is needed here, and an assertion is
    cheaper than the next reader taking it on trust. A fix routed through
    `variable_declarator` -- which locals DO use -- fails this.
    """
    assert not _named(parsed, local), f"{local} is a local variable, not a field"


def test_the_kind_is_one_the_wire_can_carry():
    """#571's shape, checked before it can repeat.

    A kind absent from `VALID_KINDS` is refused by `search_symbols`'
    `kind_filter` check and omitted from the derived schema enum, so the symbol
    is indexed and unreachable through the one filter meant to find it.
    """
    assert "field" in VALID_KINDS
