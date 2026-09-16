"""#714: C# operators, conversion operators and indexers are not indexed.

A class resolves while the operations inside it do not exist as symbols, so
`Vec + Vec` has no definition to jump to and a cast operator cannot be found at
all.

⚠⚠ **These three cannot be fixed by adding `name_fields` entries, and that is
the point of the issue.** None of them has an identifier to borrow:

* `operator_declaration` carries an `operator` field holding the TOKEN (`+`),
  and a bare `+` is not a name anyone would search for;
* `conversion_operator_declaration` has no name field at all -- what identifies
  it is the direction (`explicit`/`implicit`) and the TARGET type;
* `indexer_declaration` has no name field either; it is spelled `this[...]`.

#712 is the rule that a declared node type must be nameable. This file is the
case where the name has to be BUILT, and `_extract_name`'s csharp branch is
where that happens -- the same place `field_declaration` already walks to a
`variable_declarator`.
"""

import pytest

from jcodemunch_mcp.parser.extractor import parse_file
from jcodemunch_mcp.parser.grammar_pack import get_parser
from jcodemunch_mcp.parser.languages import CSHARP_SPEC


SOURCE = """public class Vec {
    public int X;
    public static Vec operator +(Vec a, Vec b) { return a; }
    public static Vec operator -(Vec a) { return a; }
    public static explicit operator string(Vec v) { return null; }
    public static implicit operator int(Vec v) { return 0; }
    public int this[int i] { get { return i; } }
    public int Ordinary(int x) { return x; }
}
"""


@pytest.fixture(scope="module")
def parsed():
    """Every symbol as a LIST, never a dict keyed by name.

    Two operators can share a name (`operator +` binary and unary), so a
    name-keyed dict would silently drop one and the count assertions below
    would be measuring the dict rather than the extraction.
    """
    return list(parse_file(SOURCE, "Vec.cs", "csharp"))


def _names(parsed):
    return {s.name for s in parsed}


def test_the_fixture_parses_without_error():
    """Separate "the spec does not name it" from "the file never parsed"."""
    tree = get_parser("csharp").parse(SOURCE.encode("utf-8"))

    assert not tree.root_node.has_error, "the fixture is not valid C#"


def test_the_controls_extract(parsed):
    """An ordinary method and a field, with owners."""
    names = _names(parsed)

    assert {"Vec", "Ordinary", "X"} <= names, sorted(names)
    ordinary = [s for s in parsed if s.name == "Ordinary"][0]
    assert ordinary.parent == "Vec.cs::Vec#class"


def test_a_binary_operator_is_indexed_and_named_the_way_it_is_written(parsed):
    """`operator +`, not `+`.

    The grammar hands us the token. A symbol called `+` is not findable: it
    collides with every other language's punctuation in a lexical index and
    matches nothing a reader would type.
    """
    names = _names(parsed)

    assert "operator +" in names, sorted(n for n in names if "oper" in n or n == "+")


def test_a_unary_operator_is_indexed(parsed):
    assert "operator -" in _names(parsed)


def test_both_conversion_directions_are_indexed_and_distinguishable(parsed):
    """`explicit operator string` and `implicit operator int`.

    The direction is part of the identity: `explicit` requires a cast at the
    call site and `implicit` does not, so collapsing them to one name would
    hide the distinction a caller needs.
    """
    names = _names(parsed)

    assert "explicit operator string" in names, sorted(names)
    assert "implicit operator int" in names, sorted(names)


def test_an_indexer_is_indexed(parsed):
    """`this[]` -- the spelling a C# reader recognises."""
    assert "this[]" in _names(parsed), sorted(_names(parsed))


def test_every_new_symbol_knows_its_owner(parsed):
    """An operation that belongs to no type is not findable from the type.

    The #713 half: a name without an owner leaves the index holding a member
    that belongs to nothing.
    """
    wanted = {
        "operator +",
        "operator -",
        "explicit operator string",
        "implicit operator int",
        "this[]",
    }
    owned = {s.name: s.parent for s in parsed if s.name in wanted}

    assert set(owned) == wanted, sorted(wanted - set(owned))
    for name, parent in sorted(owned.items()):
        assert parent == "Vec.cs::Vec#class", (name, parent)


def test_the_new_forms_are_methods(parsed):
    """Kind, stated once so a later change has to argue with a test.

    All five are callable members of the type. `method` is what C#'s ordinary
    callable members already use here, and an operator is invoked rather than
    read.
    """
    for symbol in parsed:
        if symbol.name.startswith(("operator ", "explicit operator ", "implicit operator ")) or symbol.name == "this[]":
            assert symbol.kind == "method", (symbol.name, symbol.kind)


def test_two_overloads_of_one_operator_both_survive():
    """Overload identity: two `operator +` on one type must both be indexed.

    ⚠ A NAME cannot carry this -- both are `operator +`. This asserts the count,
    which is what a set-keyed implementation silently gets wrong
    [[a-set-cannot-count]]; if they are ever distinguished by signature, this
    test still passes and the name assertions above are the ones to revisit.
    """
    source = """public class V {
    public static V operator +(V a, V b) { return a; }
    public static V operator +(V a, int b) { return a; }
}
"""
    plus = [s for s in parse_file(source, "V.cs", "csharp") if s.name == "operator +"]

    assert len(plus) == 2, f"expected both overloads, got {len(plus)}"


def test_the_three_node_types_are_declared(parsed):
    """The spec half of the contract.

    ⚠ They are deliberately NOT in `name_fields`: none has an identifier to
    point at, so their names are built in `_extract_name`'s csharp branch. When
    #712's registry ratchet (PR #723) merges, these three belong in its
    `_RESOLVED_BEFORE_NAME_FIELDS` list, which requires exactly such a branch.
    """
    declared = set(CSHARP_SPEC.symbol_node_types)

    assert {
        "operator_declaration",
        "conversion_operator_declaration",
        "indexer_declaration",
    } <= declared, sorted(declared)


def test_an_accessor_is_not_promoted_to_a_symbol(parsed):
    """A STATED BOUNDARY: the indexer is one symbol, not three.

    `this[int i] { get { ... } }` parses with an `accessor_declaration` named
    `get` inside it. Indexing that would put a symbol called `get` on every
    type with a property, which is noise rather than navigation -- and property
    accessors have never been indexed here.
    """
    assert "get" not in _names(parsed)
