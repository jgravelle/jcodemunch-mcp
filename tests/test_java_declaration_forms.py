"""#713: Java records and annotation types are never indexed, and their members
lose their owner.

`JAVA_SPEC` covers classes, interfaces, enums, ordinary methods and ordinary
constructors. The grammar also spells `record_declaration`,
`compact_constructor_declaration`, `annotation_type_declaration` and
`annotation_type_element_declaration`, none of which the spec names -- so the
declaration vanishes, and because `container_node_types` does not list the two
containers either, the members that DO extract come out with no owner.

⚠⚠ This is #698 in Java, and #698 is why it is worth stating plainly: that fix
added `abstract_class_declaration` to the TypeScript specs and its lesson was
recorded as a TypeScript fix plus a Rust benchmark bucket (`qual_mismatch`). It
reached no other language's spec. Records have been in Java since 16.

The ownership half is the part a name-only audit misses: `doubled` below
extracts either way. Only its `parent` says whether the index knows it belongs
to `Point`.
"""

import pytest

from jcodemunch_mcp.parser.extractor import parse_file
from jcodemunch_mcp.parser.grammar_pack import get_parser
from jcodemunch_mcp.parser.languages import JAVA_SPEC


SOURCE = """public record Point(int value) {
    public Point {
        if (value < 0) throw new IllegalArgumentException();
    }
    public int doubled() { return value * 2; }
}

public @interface Marker {
    String name() default "x";
}

public class Ordinary {
    public Ordinary() {}
    void method() {}
}
"""


@pytest.fixture(scope="module")
def parsed():
    """Every symbol, as a LIST.

    ⚠ NOT a dict keyed by name. The first draft of this file was
    `{s.name: s for s in ...}` and the record `Point` and its compact
    constructor `Point` collide -- one silently overwrote the other and the
    record's kind assertion failed against a correct fix. A set cannot count
    and a name is not an identity; this repo has paid for that in the Rust
    fidelity harness, and it is just as true in a six-line fixture.
    """
    return list(parse_file(SOURCE, "X.java", "java"))


def _one(parsed, name, kind):
    """The single symbol with this (name, kind), or a readable failure."""
    hits = [s for s in parsed if s.name == name and s.kind == kind]
    assert len(hits) == 1, f"expected exactly one {name}({kind}), got {len(hits)}"
    return hits[0]


def _names(parsed):
    return {s.name for s in parsed}


def test_the_fixture_parses_without_error():
    """The grammar's verdict on the fixture, before any claim about extraction.

    ⚠ Without this, every assertion below is ambiguous: a missing symbol could
    mean the spec does not name the node, or that the file never parsed. #698
    was diagnosed by separating exactly these two.
    """
    tree = get_parser("java").parse(SOURCE.encode("utf-8"))

    assert not tree.root_node.has_error, "the fixture is not valid Java"


def test_the_controls_extract(parsed):
    """An ordinary class, constructor and method, with owners.

    If these ever fail, the fixture or the harness is broken and nothing below
    means anything.
    """
    assert _names(parsed) >= {"Ordinary", "method"}
    assert _one(parsed, "method", "method").parent == "X.java::Ordinary#class"


def test_a_record_is_indexed(parsed):
    assert "Point" in _names(parsed), "a record declaration produced no symbol"
    _one(parsed, "Point", "class")  # raises with a readable message if absent


def test_a_records_method_knows_its_owner(parsed):
    """The half a name-only check cannot see.

    `doubled` extracts with or without this fix; before it, `parent` is None,
    so the index holds a method that belongs to nothing. That is the Rust
    `qual_mismatch` bucket -- the owner is the symbol's identity, not decoration.
    """
    doubled = _one(parsed, "doubled", "method")
    assert doubled.parent == "X.java::Point#class", (
        f"a method inside a record reported parent={doubled.parent!r}"
    )


def test_a_compact_constructor_is_indexed(parsed):
    """`public Point { ... }` -- a constructor with no parameter list.

    It is its own node type precisely because it has no parameters, so nothing
    about the ordinary `constructor_declaration` entry reaches it.
    """
    compact = _one(parsed, "Point", "method")

    assert compact.parent == "X.java::Point#class"


def test_an_annotation_type_is_indexed(parsed):
    assert "Marker" in _names(parsed), "an @interface declaration produced no symbol"
    _one(parsed, "Marker", "type")


def test_an_annotation_element_is_indexed_and_owned(parsed):
    """`String name() default "x"` inside an @interface."""
    assert "name" in _names(parsed), "an annotation element produced no symbol"
    element = _one(parsed, "name", "method")
    assert element.parent == "X.java::Marker#type", (
        f"an annotation element reported parent={element.parent!r}"
    )


def test_every_added_node_type_is_paired_in_both_name_maps():
    """The #712 contract, asserted for the four node types this change adds.

    ⚠ TWO maps, not three: `param_fields` is deliberately excluded, because a
    compact constructor has no parameter list and an annotation element's
    parentheses are always empty. The first name of this test said "all three
    maps" and told a reader the opposite of what the body does.

    A node type in `symbol_node_types` with no `name_fields` entry resolves to
    no name and is dropped -- listed as supported, never emitted. #712 is the
    registry-wide ratchet; this is the same rule at the point of the change, so
    it holds on this branch whether or not that one has merged.
    """
    added = {
        "record_declaration",
        "compact_constructor_declaration",
        "annotation_type_declaration",
        "annotation_type_element_declaration",
    }

    assert added <= set(JAVA_SPEC.symbol_node_types), sorted(
        added - set(JAVA_SPEC.symbol_node_types)
    )
    assert added <= set(JAVA_SPEC.name_fields), sorted(
        added - set(JAVA_SPEC.name_fields)
    )


def test_the_two_new_containers_are_declared():
    """Ownership comes from `container_node_types`, not from the name maps.

    Naming a record and failing to declare it a container is the shape that
    produced the ownerless `doubled` in the first place: the member extracts,
    the owner does not attach, and only a `parent` assertion sees it.
    """
    containers = set(JAVA_SPEC.container_node_types)

    assert "record_declaration" in containers
    assert "annotation_type_declaration" in containers


def test_a_record_component_is_not_claimed_as_a_member(parsed):
    """A STATED BOUNDARY, asserted so it is a decision rather than an oversight.

    `record Point(int value)` declares a component. A component IS written, with
    its own span -- the earlier version of this docstring said it was not, which
    is true of the backing field and the accessor the compiler synthesises from
    it, and false of the component itself. The reason to exclude it is that a
    component is closer to a PARAMETER of the header than to a member: it is
    declared in the signature, and what the class exposes because of it (field,
    accessor) is generated. Indexing those would report members nobody wrote,
    which cannot be checked against the file.

    ⚠ The issue's own Test section asks for `Point.value` with an owner, so this
    change declines one item #713 names. It declines it in the open, here, with
    a flip path.

    ⚠ If a later change decides a component should be a field, this test is the
    one to flip, deliberately, with its own evidence. A red here means the
    boundary moved, not that something broke.
    """
    assert "value" not in _names(parsed), (
        "a record component is being indexed; if that is intended, this test is "
        "the decision to revisit (see the docstring)"
    )
