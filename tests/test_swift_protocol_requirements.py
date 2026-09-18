"""#733: Swift protocol requirements and subscripts yield no symbols.

A protocol indexes as a bare name. Its method and property requirements ARE the
protocol -- they are the contract a caller reads and the names a caller
searches -- and none of them reached the index, while an ordinary method in a
struct beside them extracted normally.

⚠⚠ **Three node types, three DIFFERENT shapes, and only the first is a
`name_fields` entry.** The class this belongs to (#698, #712, #713, #722, #732,
#735) is "the grammar spells a form the spec never names", and the reflex remedy
is to add the node type to `symbol_node_types` and its name field beside it.
That is correct for exactly one of these three:

- `protocol_function_declaration`'s `name` field is a `simple_identifier`.
- `protocol_property_declaration`'s `name` field is a `pattern` whose text is
  **`var value`**, because inside a protocol body the binding keyword moves
  INSIDE the pattern. The same field on `property_declaration` in a class body
  yields the bare name, because there the keyword is a SIBLING. One field name,
  two nestings -- [[a-guard-written-against-a-spelling]] arriving in the grammar
  rather than in our code.
- `subscript_declaration`'s `name` field is a `user_type` holding the RETURN
  type, so a `name_fields` entry would index every subscript in a corpus under
  whatever it returns. The name is BUILT, the way #714 built `this[]` for a C#
  indexer.

⚠ `test_a_subscript_is_not_named_after_its_return_type` and
`test_a_protocol_property_requirement_drops_its_binding_keyword` are the two
that fail against the reflex fix. They are the reason this file exists rather
than three lines in the spec.
"""

import pytest

from jcodemunch_mcp.parser.extractor import parse_file


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    """⚠ Patch `jcodemunch_mcp.config`, never the parser module: `parse_file`
    imports the gate function-locally, so patching the parser does nothing
    (the `cli/policy.py` trap).
    """
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def pairs(source: str, filename: str = "a.swift") -> set[tuple[str, str]]:
    return {(s.name, s.kind) for s in parse_file(source, filename, "swift")}


def qualified(source: str) -> set[tuple[str, str, str]]:
    return {
        (s.name, s.kind, s.qualified_name)
        for s in parse_file(source, "a.swift", "swift")
    }


REPORTED = """protocol P {
  func required()
  var value: Int { get }
}
struct S {
  subscript(i: Int) -> Int { return i }
  func ordinary() {}
}
"""


def test_a_protocol_method_requirement_is_a_symbol():
    """The reported case, through the product."""
    assert ("required", "method") in pairs(REPORTED)


def test_a_protocol_property_requirement_is_a_symbol():
    assert ("value", "constant") in pairs(REPORTED)


def test_a_subscript_is_a_symbol():
    assert ("subscript", "method") in pairs(REPORTED)


def test_the_reported_case_in_full():
    """The blast radius, asserted rather than argued.

    `ordinary` is the control: it extracted before this fix, so an equality that
    lost it would say the fix had broken the path it rode in on.
    """
    assert pairs(REPORTED) == {
        ("P", "type"),
        ("required", "method"),
        ("value", "constant"),
        ("S", "class"),
        ("subscript", "method"),
        ("ordinary", "method"),
    }


def test_a_requirement_is_owned_by_the_protocol_that_declares_it():
    """⚠ The half #698 was about: a member that extracts without its owner has
    lost the thing that makes it findable. `protocol_declaration` is already in
    `container_node_types`, so this is asserted, not added.
    """
    assert {
        ("required", "method", "P.required"),
        ("value", "constant", "P.value"),
    } <= qualified(REPORTED)


def test_a_protocol_property_requirement_drops_its_binding_keyword():
    """⚠⚠ The test that fails against the reflex fix.

    `protocol_property_declaration`'s `name` field is a `pattern` that CONTAINS
    the `value_binding_pattern`, so the field's own text is `var value`. A
    `name_fields` entry pointing at it indexes a symbol whose name has a space
    in it: unsearchable, and indistinguishable from a fabricated identity -- the
    trap #734 refused for anonymous `given`s and #714 refused for C# operators.
    """
    names = {name for name, _kind in pairs(REPORTED)}
    assert "var value" not in names
    assert not any(" " in name for name in names), sorted(names)


def test_a_get_set_requirement_is_a_symbol():
    """A settable requirement is the same node type and must not be treated as
    a different form. ⚠ It takes kind `constant` because SWIFT_SPEC maps every
    `property_declaration` that way, including a mutable `var`; that convention
    is pre-existing and changing it moves every Swift symbol's kind.
    """
    source = """protocol P {
  var rw: Int { get set }
}
"""
    assert pairs(source) == {("P", "type"), ("rw", "constant")}


def test_a_static_requirement_is_a_symbol():
    """`static func` puts a `modifiers` node ahead of the name. The field still
    resolves, asserted rather than assumed -- an untested shape is where #734's
    regression was hiding.
    """
    source = """protocol P {
  static func make() -> P
}
"""
    assert ("make", "method") in pairs(source)


#: The shapes the grammar wraps in modifiers, effects or type parameters before
#: the name -- one row per spelling found by probing, because an untested shape
#: is where #734's regression was hiding and enumerating them in prose is what
#: let it hide.
OTHER_SHAPES = {
    "async throws requirement": (
        "protocol P {\n  func fetch() async throws -> Int\n}\n",
        ("fetch", "method"),
    ),
    "mutating requirement": (
        "protocol P {\n  mutating func bump()\n}\n",
        ("bump", "method"),
    ),
    "generic requirement": (
        "protocol P {\n  func map<T>(_ t: T) -> T\n}\n",
        ("map", "method"),
    ),
    "static property requirement": (
        "protocol P {\n  static var shared: P { get }\n}\n",
        ("shared", "constant"),
    ),
    "generic subscript": (
        "struct S {\n  subscript<T>(i: T) -> T { return i }\n}\n",
        ("subscript", "method"),
    ),
    "static subscript": (
        "struct S {\n  static subscript(i: Int) -> Int { return i }\n}\n",
        ("subscript", "method"),
    ),
    "subscript with a setter": (
        "struct S {\n  subscript(i: Int) -> Int {\n    get { return i }\n    set { }\n  }\n}\n",
        ("subscript", "method"),
    ),
    "subscript in an enum": (
        "enum E {\n  subscript(i: Int) -> Int { return i }\n}\n",
        ("subscript", "method"),
    ),
    "subscript in an extension": (
        "extension S {\n  subscript(i: Int) -> Int { return i }\n}\n",
        ("subscript", "method"),
    ),
}


@pytest.mark.parametrize("shape", sorted(OTHER_SHAPES), ids=sorted(OTHER_SHAPES))
def test_every_probed_shape_still_yields_its_symbol(shape):
    source, expected = OTHER_SHAPES[shape]
    assert expected in pairs(source)


def test_a_subscript_is_not_named_after_its_return_type():
    """⚠⚠ The second test that fails against the reflex fix.

    `subscript_declaration` HAS a `name` field and it holds the return type, so
    a `name_fields` entry is not merely useless here -- it is confidently wrong,
    and every subscript in a corpus would index as `Int`, `String` or `Element`.
    """
    names = {name for name, _kind in pairs(REPORTED)}
    assert "Int" not in names, (
        "the subscript took its RETURN type as its name; the `name` field on "
        "`subscript_declaration` is a user_type, not an identifier (#733)"
    )


def test_a_subscript_is_named_what_a_reader_would_type():
    """⚠ The built name is `subscript`, which is the word a Swift developer
    writes at the declaration -- #714's rule, that searching the declaration's
    own text must find the symbol. `this[]` was chosen for C# by the same rule,
    because `this[...]` is what a C# developer writes there.
    """
    assert ("subscript", "method") in pairs(REPORTED)


def test_a_subscript_in_a_protocol_is_a_requirement_too():
    """The same node type appears in both bodies, so both must be covered by one
    entry rather than by a class-body special case.
    """
    source = """protocol P {
  subscript(i: Int) -> Int { get }
}
"""
    assert ("subscript", "method", "P.subscript") in qualified(source)


def test_two_subscripts_in_one_type_stay_distinct():
    """⚠ A type may declare several subscripts, and they share the built name.

    This is #714's accepted limit, recorded rather than discovered later: the
    alternative is committing the name to a parameter list that overloads
    disagree about. They stay distinct by id and by line, which is what a
    reader following a result needs; a name-only search cannot separate them.
    """
    source = """struct M {
  subscript(i: Int) -> Int { return i }
  subscript(row: Int, col: Int) -> Int { return row }
}
"""
    subs = [s for s in parse_file(source, "a.swift", "swift") if s.name == "subscript"]
    assert len(subs) == 2
    assert len({s.id for s in subs}) == 2
    assert len({s.line for s in subs}) == 2


def test_an_ordinary_property_keeps_its_bare_name():
    """The OTHER nesting, pinned. `property_declaration` in a class body carries
    its `value_binding_pattern` as a sibling of the pattern, so its `name` field
    was already the bare name and must stay that way -- a fix that descended
    every Swift pattern would be invisible here and wrong one case over.
    """
    source = """struct S {
  let MAX = 1
  var x = 2
}
"""
    assert pairs(source) == {("S", "class"), ("MAX", "constant"), ("x", "constant")}


def test_a_tuple_binding_is_a_known_separate_gap():
    """⚠⚠ An absence asserted ON PURPOSE, so the fix cannot be read as covering it.

    `let (a, b) = (1, 2)` binds two names and yields ONE symbol called `(a, b)`.
    That is the N-names channel argument from #731 and #735 reaching
    `property_declaration`, which this fix does not touch, and the name is
    unsearchable for the same reason `var value` would have been. Filed
    separately; this test fails in either direction so the decision stays
    visible rather than accidental.
    """
    extracted = pairs("struct S {\n  let (a, b) = (1, 2)\n}\n")
    assert extracted == {("S", "class"), ("(a, b)", "constant")}, (
        f"a Swift tuple binding now yields {extracted} -- if that is deliberate, "
        f"it belongs to the separate issue about `property_declaration` binding "
        f"N names, and this test is where the decision gets recorded (#733)."
    )


def _pattern_of(source: str, node_type: str):
    """The `name` field of the first `node_type` in a parsed Swift file."""
    from jcodemunch_mcp.parser.extractor import get_parser

    tree = get_parser("swift").parse(source.encode())
    stack = [tree.root_node]
    while stack:
        current = stack.pop(0)
        if current.type == node_type:
            return current.child_by_field_name("name")
        stack.extend(current.children)
    raise AssertionError(f"no {node_type} in the sample; the probe is wrong")


def test_the_name_resolver_declines_a_pattern_that_binds_several_names():
    """⚠⚠ Asserted as a UNIT, because the product cannot reach it.

    `_swift_bound_identifier` returns None for a pattern binding none or several
    names, and that branch is what stops a blanket descent from publishing `a`
    and dropping `b` in `let (a, b) = (1, 2)`. No Swift source can exercise it
    through `parse_file`: a protocol property always binds exactly one name, and
    the tuple form is `property_declaration`, which does not call this helper.

    ⚠ A guard nothing can reach is a claim nothing can falsify -- the shape
    [[a-guard-covered-only-by-positive-tests-can-be-deleted]] names. Calling it
    directly with the tuple pattern is what makes the decline real rather than
    decorative, and it is the assertion that fails if the helper is ever changed
    to return the first identifier it finds.
    """
    from jcodemunch_mcp.parser.extractor import _swift_bound_identifier

    source = "struct S {\n  let (a, b) = (1, 2)\n}\n"
    pattern = _pattern_of(source, "property_declaration")

    assert _swift_bound_identifier(pattern, source.encode()) is None, (
        "the resolver picked one name out of a pattern that binds two; that is "
        "a silent drop, which is why it declines instead (#733)"
    )


def test_the_name_resolver_finds_the_one_name_a_requirement_binds():
    """The control for the test above: a decline that declined EVERYTHING would
    pass it while resolving nothing, and every product assertion here would then
    be carried by some other path.
    """
    from jcodemunch_mcp.parser.extractor import _swift_bound_identifier

    source = "protocol P {\n  var value: Int { get }\n}\n"
    pattern = _pattern_of(source, "protocol_property_declaration")

    assert _swift_bound_identifier(pattern, source.encode()) == "value"


def test_no_declaration_is_emitted_twice():
    """⚠ Keyed on `(name, line)`, not on the name: `subscript` is a BUILT name
    shared by every subscript in a type, so a set of names cannot tell a
    double-emit from two legal overloads (#735's rule, and it bites harder here
    because the collision is by construction).
    """
    source = """protocol P {
  func required()
  var value: Int { get }
  subscript(i: Int) -> Int { get }
}
struct S {
  subscript(i: Int) -> Int { return i }
  subscript(row: Int, col: Int) -> Int { return row }
  func ordinary() {}
}
"""
    seen = [(s.name, s.line) for s in parse_file(source, "a.swift", "swift")]
    assert len(seen) == len(set(seen)), f"a declaration was emitted twice: {seen}"


def test_the_ordinary_swift_forms_are_unaffected():
    """The blast radius over the forms that already worked.

    ⚠ `deinit` is deliberately absent from this equality and has its own test
    below: it is #754, a gap that predates this fix and is tracked by PR #756's
    sample table.
    """
    source = """class C {
  init(x: Int) {}
  func m() {}
}
typealias Alias = Int
func free() {}
"""
    assert pairs(source) == {
        ("C", "class"),
        ("init", "method"),
        ("m", "method"),
        ("Alias", "type"),
        ("free", "function"),
    }


def test_a_deinit_is_a_known_separate_gap():
    """⚠⚠ Found while probing this fix, and deliberately NOT fixed here.

    `deinit_declaration` is declared in SWIFT_SPEC with a `name_fields` entry,
    and the grammar sets no `name` field on it at all -- its only named child is
    the body -- so the symbol is dropped while `init` beside it extracts. That is
    #743's shape (a name field the grammar never sets) in a second language, it
    is already filed as **#754**, and PR #756 pins it as a tracked gap with a
    test that FAILS when it closes. Fixing it here would close someone else's
    gap entry from an unrelated branch.
    """
    extracted = pairs("class C {\n  init() {}\n  deinit {}\n}\n")
    assert extracted == {("C", "class"), ("init", "method")}, (
        f"a Swift deinit now yields {extracted} -- that is #754 closing, which "
        f"belongs to PR #756's gap table, not to this fix (#733)."
    )
