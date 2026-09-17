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
    matched = [
        s for s in parsed
        if s.name.startswith(("operator ", "explicit operator ", "implicit operator "))
        or s.name == "this[]"
    ]

    # ⚠ Without this the loop matches nothing pre-fix and the test passes
    # against the defect -- one of the four red-arm passes was vacuous for
    # exactly this reason.
    assert len(matched) == 5, sorted(s.name for s in matched)
    for symbol in matched:
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
    # ⚠ The bare `"get" not in names` this used to be passes on a tree where no
    # indexer symbol exists AT ALL, so it could not tell "one symbol, not three"
    # from "nothing". Assert the indexer is present FIRST, and that exactly one
    # symbol covers it.
    indexers = [s for s in parsed if s.name == "this[]"]
    assert len(indexers) == 1, [s.name for s in indexers]

    assert "get" not in _names(parsed)
    assert "set" not in _names(parsed)


# ---------------------------------------------------------------------------
# #714 review: the destructive surface these new symbols created
# ---------------------------------------------------------------------------

_USED_SOURCE = """public class Use {
    public void Go() {
        var a = new Vec();
        var b = new Vec();
        var c = a + b;
        var d = (string)a;
        int i = a[0];
        a.Ordinary(1);
    }
}
"""


@pytest.fixture(scope="module")
def two_file_repo(tmp_path_factory):
    """A repo where every new member is USED, indexed through the product."""
    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path_factory.mktemp("csharp_repo")
    (root / "Vec.cs").write_text(SOURCE, encoding="utf-8")
    (root / "Use.cs").write_text(_USED_SOURCE, encoding="utf-8")
    storage = str(root / "idx")
    result = index_folder(path=str(root), use_ai_summaries=False, storage_path=storage)
    return result["repo"], storage


def test_a_syntactically_invoked_member_is_not_certified_deletable(two_file_repo):
    """⚠⚠ The defect this PR would otherwise have SHIPPED (#714 review).

    An operator is invoked as `a + b`. Its name never appears at a call site, so
    a reference search keyed on the name finds nothing -- and `check_delete_safe`
    read that nothing as proof, returning `safe_to_delete` at confidence 1.0
    with "No callers or refs found", for a member the corpus uses on the line
    below the one it certified.

    This is #566's lesson on new surface: capping the report does not cap the
    tool that ACTS on it. The ordinary method in the same file is the control --
    it is correctly blocked, which is what makes the operator's verdict a defect
    rather than a thin corpus.
    """
    from jcodemunch_mcp.tools.check_delete_safe import check_delete_safe

    repo, storage = two_file_repo

    control = check_delete_safe(repo, "Vec.cs::Vec.Ordinary#method", storage_path=storage)
    assert control["verdict"] != "safe_to_delete", (
        "the control is not blocked; the fixture proves nothing"
    )

    for symbol_id in (
        "Vec.cs::Vec.operator +#method",
        "Vec.cs::Vec.this[]#method",
        "Vec.cs::Vec.explicit operator string#method",
    ):
        got = check_delete_safe(repo, symbol_id, storage_path=storage)
        assert got["verdict"] != "safe_to_delete", (
            f"{symbol_id} was certified deletable while in use "
            f"(verdict={got['verdict']}, confidence={got['confidence']})"
        )
        assert got["verdict"] == "name_not_searchable", got["verdict"]
        assert got["confidence"] <= 0.6, got["confidence"]


def test_the_refusal_is_not_terminal(two_file_repo):
    """A refusal that ends the investigation is its own defect.

    `name_not_searchable` says the NAME channel cannot answer, not that the
    question is closed: reading the call sites or ingesting runtime evidence
    still settles it. Practice 7 -- `terminal` means FINAL, not SAFE.
    """
    from jcodemunch_mcp.tools.check_delete_safe import check_delete_safe

    repo, storage = two_file_repo
    got = check_delete_safe(repo, "Vec.cs::Vec.operator +#method", storage_path=storage)

    assert got["stop_rule"]["terminal"] is False, got["stop_rule"]


def test_an_ordinary_name_is_still_searchable():
    """The predicate must not refuse everything -- then it would prove nothing."""
    from jcodemunch_mcp.tools._name_reachability import name_can_appear_at_a_call_site

    for ordinary in ("Ordinary", "my_func", "MyClass", "_private", "a1", "Foo.Bar",
                     "math_utils::multiply"):
        assert name_can_appear_at_a_call_site(ordinary), ordinary

    for built in ("operator +", "this[]", "explicit operator string",
                  "implicit operator int", "operator ==", ""):
        assert not name_can_appear_at_a_call_site(built), built


def test_a_checked_operator_is_distinguishable_from_its_unchecked_twin():
    """C# 11: a type may declare both, and they are different members.

    ⚠ The first version of the fix read the `operator` field alone, so both
    built the name `operator +`. They stayed id-distinct through `~1`/`~2`, so
    nothing was dropped -- but two members published one name, and a
    name-keyed search cannot recover which is which. Found in review.
    """
    source = """public class C {
    public static C operator +(C a, C b) { return a; }
    public static C operator checked +(C a, C b) { return a; }
    public static explicit operator int(C a) { return 0; }
    public static explicit operator checked int(C a) { return 0; }
}
"""
    names = {s.name for s in parse_file(source, "C.cs", "csharp")}

    assert {"operator +", "operator checked +",
            "explicit operator int", "explicit operator checked int"} <= names, sorted(names)
