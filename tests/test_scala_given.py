"""#734: a Scala 3 `given` definition yields no symbol.

`given` is how Scala 3 replaced `implicit val`, so the declarations that drive
implicit resolution -- the ones hardest to find by reading, because the call
site never names them -- were the ones missing from the index, while the `val`
and the `def` beside them extracted.

⚠ **One name, one node, so this needs no channel.** `given_definition` carries a
`name` field, and `_extract_symbol` returns at most one symbol per node, which
is exactly the shape `symbol_node_types` + `name_fields` expresses. That is
#698's and #713's remedy, and it is the right one HERE -- the channel argument
in #735 and #731 applies only to forms that bind N names.

⚠⚠ **The grammar spells THREE things `given_definition` and only one has a
name.** `given ordering: Ordering[Int] = ???` carries an identifier;
`given Conv = ???` and `given [T]: Ord[T] = ???` are anonymous, and Scala
synthesises their names at compile time from the type. Those two extract
NOTHING and are asserted as a known, separate gap below rather than papered over
with the type name -- a fabricated identity is worse than an absence, because a
name that does not appear in the source cannot be searched for and cannot be
told apart from one that does.
"""

import pytest

from jcodemunch_mcp.parser.extractor import parse_file


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    """⚠ Patch `jcodemunch_mcp.config`, never the parser module (the
    `cli/policy.py` trap -- `parse_file`'s import of the gate is
    function-local).
    """
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def pairs(source: str, filename: str = "a.scala") -> set[tuple[str, str]]:
    return {(s.name, s.kind) for s in parse_file(source, filename, "scala")}


REPORTED = """object O {
  val x = 1
  given ordering: Ordering[Int] = ???
  def m = 2
}
"""


def test_a_named_given_is_a_symbol():
    """The reported case, through the product."""
    assert ("ordering", "constant") in pairs(REPORTED)


def test_the_val_and_the_def_beside_it_are_unaffected():
    """The blast radius of adding one node type to the map, asserted."""
    assert pairs(REPORTED) == {
        ("O", "class"),
        ("x", "constant"),
        ("ordering", "constant"),
        ("m", "method"),
    }


def test_a_given_takes_the_kind_the_val_beside_it_takes():
    """⚠ `constant`, because a `given` is a stable value and `val_definition`
    is already mapped to `constant` in this spec. A new kind would have to be
    appended to `KIND_ORDER`, which is PUBLISHED in the cached schema prefix,
    and it would say that a `given` is a different sort of thing from the `val`
    it replaced -- which it is not.
    """
    by_name = {s.name: s.kind for s in parse_file(REPORTED, "a.scala", "scala")}
    assert by_name["ordering"] == by_name["x"] == "constant"


def test_a_top_level_given_is_a_symbol():
    """Scala 3 allows a `given` at the top level, with no enclosing object."""
    source = """given ordering: Ordering[Int] = ???
"""
    assert pairs(source) == {("ordering", "constant")}


def test_a_given_in_a_trait_is_a_symbol():
    source = """trait T {
  given ordering: Ordering[Int] = ???
}
"""
    assert ("ordering", "constant") in pairs(source)


def test_a_given_with_a_using_clause_is_a_symbol():
    """The shape that makes implicit resolution recursive, and the hardest one
    to find by reading, since neither side names the other.
    """
    source = """object O {
  given listOrdering(using ord: Ordering[Int]): Ordering[List[Int]] = ???
}
"""
    assert ("listOrdering", "constant") in pairs(source)


ANONYMOUS = {
    "by type alone": """object O {
  given Conv = ???
}
""",
    "with type parameters": """object O {
  given [T]: Ord[T] = ???
}
""",
}


@pytest.mark.parametrize("shape", sorted(ANONYMOUS), ids=sorted(ANONYMOUS))
def test_an_anonymous_given_is_a_known_separate_gap(shape):
    """⚠⚠ An absence asserted ON PURPOSE, so it cannot be mistaken for coverage.

    An anonymous `given` has no name in the source; the compiler synthesises one
    from the type. Emitting the TYPE name here would put a symbol in the index
    that no reader can search for and that is indistinguishable from a `given`
    genuinely called `Conv` -- the fabricated-identity trap, and the same reason
    #714's built names (`this[]`) are spelled so they cannot collide with a
    real identifier.

    This test fails if the behaviour changes in either direction, which is what
    keeps the decision visible rather than accidental.
    """
    extracted = pairs(ANONYMOUS[shape])
    assert extracted == {("O", "class")}, (
        f"an anonymous given now yields {extracted} -- if that is deliberate, "
        f"the name it carries must be one a reader can search for, and this "
        f"test is where the decision gets recorded (#734)."
    )


def test_an_extension_is_not_this_issue():
    """⚠ `extension_definition` is unnamed in the spec too and is in the same
    inventory, but an extension's METHODS extract, so the gap is smaller and
    separate. Pinned here so fixing `given` is not read as having fixed it.
    """
    source = """object O {
  extension (s: String) def shout: String = ???
}
"""
    assert ("shout", "method") in pairs(source)
