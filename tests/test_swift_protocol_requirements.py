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
  whatever it returns. The name is BUILT and spelled `subscript[]`, the way
  #714 built `this[]` for a C# indexer -- and the brackets are load-bearing, not
  cosmetic, because `_name_reachability` decides whether "no references found"
  is evidence by asking whether the NAME is identifier-shaped.

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
    assert ("value", "property") in pairs(REPORTED)


def test_a_subscript_is_a_symbol():
    assert ("subscript[]", "method") in pairs(REPORTED)


def test_the_reported_case_in_full():
    """The blast radius, asserted rather than argued.

    `ordinary` is the control: it extracted before this fix, so an equality that
    lost it would say the fix had broken the path it rode in on.
    """
    assert pairs(REPORTED) == {
        ("P", "type"),
        ("required", "method"),
        ("value", "property"),
        ("S", "class"),
        ("subscript[]", "method"),
        ("ordinary", "method"),
    }


def test_a_requirement_is_owned_by_the_protocol_that_declares_it():
    """⚠ The half #698 was about: a member that extracts without its owner has
    lost the thing that makes it findable. `protocol_declaration` is already in
    `container_node_types`, so this is asserted, not added.
    """
    assert {
        ("required", "method", "P.required"),
        ("value", "property", "P.value"),
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
    a different form. ⚠⚠ It took kind `constant` because SWIFT_SPEC mapped every
    `property_declaration` that way, including a mutable `var`. This docstring
    recorded that as pre-existing and warned that changing it moves every Swift
    symbol's kind. #769 made exactly that change: a `var` is a `property` now,
    and the kinds here moved with it. The form this test is about -- a settable
    requirement being the same node type as a gettable one -- is unchanged.
    """
    source = """protocol P {
  var rw: Int { get set }
}
"""
    assert pairs(source) == {("P", "type"), ("rw", "property")}


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
        ("shared", "property"),
    ),
    "generic subscript": (
        "struct S {\n  subscript<T>(i: T) -> T { return i }\n}\n",
        ("subscript[]", "method"),
    ),
    "static subscript": (
        "struct S {\n  static subscript(i: Int) -> Int { return i }\n}\n",
        ("subscript[]", "method"),
    ),
    "subscript with a setter": (
        "struct S {\n  subscript(i: Int) -> Int {\n    get { return i }\n    set { }\n  }\n}\n",
        ("subscript[]", "method"),
    ),
    "subscript in an enum": (
        "enum E {\n  subscript(i: Int) -> Int { return i }\n}\n",
        ("subscript[]", "method"),
    ),
    "subscript in an extension": (
        "extension S {\n  subscript(i: Int) -> Int { return i }\n}\n",
        ("subscript[]", "method"),
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


def test_a_subscript_is_named_the_way_the_c_sharp_indexer_is():
    """⚠ `subscript[]`, mirroring #714's `this[]` for the same construct.

    A Swift subscript and a C# indexer are the same thing in two languages, and
    a reader who has met one recognises the other spelled the same way.
    """
    assert ("subscript[]", "method") in pairs(REPORTED)


def test_the_built_name_is_refused_by_the_reference_reachability_rule():
    """⚠⚠ The brackets are LOAD-BEARING, and a bare `subscript` would have
    shipped #714's defect with the guard that prevents it left standing.

    `tools/_name_reachability.py` is THE ONE ANSWER to "is an absence of this
    name evidence about this symbol", and it asks a property of the STRING: a
    name that is not a plain identifier cannot be a call-site token in any
    language, so it refuses the absence claim. A subscript is invoked as `m[i]`
    and its declaration's name is never written at a call site.

    A bare `subscript` is identifier-shaped, so the predicate would have called
    it searchable and `check_delete_safe` would have read "no references found"
    as proof -- `safe_to_delete` for a member the corpus uses on every line that
    indexes the type. The guard would not have fired, would not have been
    changed, and would have been wrong: [[a-guard-written-against-a-spelling]],
    where the spelling was one WE chose.
    """
    from jcodemunch_mcp.tools._name_reachability import name_can_appear_at_a_call_site

    assert not name_can_appear_at_a_call_site("subscript[]")
    assert name_can_appear_at_a_call_site("subscript"), (
        "the control is gone: if a bare `subscript` is refused too, this test "
        "no longer says why the brackets are there"
    )


#: Names `_extract_name` returns as string LITERALS, each of which must be
#: refused by `name_can_appear_at_a_call_site`. ⚠ A roster alone is a list of
#: the spellings someone remembered; `_built_name_sites` is what keeps it
#: honest, in both directions.
_BUILT_NAME_ROSTER = {
    "subscript[]",  # swift, #733
    "this[]",       # csharp, #714
}

#: Helpers that return a name BORROWED from the source rather than built. A
#: return that calls one of these is not a built name and needs no roster entry.
_NAME_BORROWING_HELPERS = {
    "kotlin_property_name",
    "_swift_bound_identifier",
    "_extract_cpp_name",
}

#: How many interpolated builders `_extract_name` holds. ⚠⚠ PINNED, because the
#: first version of this check counted them implicitly and found ONE of three:
#: `return f"operator checked {token}" if checked else f"operator {token}"` is
#: an `ast.IfExp`, so a test for `isinstance(node.value, ast.JoinedStr)` walks
#: straight past both C# operator builders while its own `assert interpolated`
#: floor stayed satisfied by the third. Found in review, by planting an
#: identifier-shaped scaffolding in that arm and watching it stay green.
_INTERPOLATED_BUILDER_COUNT = 3


def _is_borrowed_slice(value) -> bool:
    """Is this expression exactly `source_bytes[a:b].decode(...)`?

    ⚠ Exactly, and nothing wrapping it. A concatenation, an f-string or a call
    around the borrow produces a name the source does not contain, which makes
    it a BUILT name however much borrowed text it carries.
    """
    import ast

    if not isinstance(value, ast.Call):
        return False
    func = value.func
    if not (isinstance(func, ast.Attribute) and func.attr == "decode"):
        return False
    sliced = func.value
    return (
        isinstance(sliced, ast.Subscript)
        and isinstance(sliced.value, ast.Name)
        and sliced.value.id == "source_bytes"
    )


def _built_name_sites(source: str):
    """Classify every `return` in `_extract_name`'s source.

    Returns `(literals, scaffoldings, unclassified)`:

    - `literals` -- returns of a plain string. These ARE the built name.
    - `scaffoldings` -- the literal parts of every f-string reachable from a
      return, found by walking the return's whole expression rather than
      testing its top node, so an `if`/`else` between the two does not hide it.
      The VALUE is not knowable statically (`f"operator {token}"` depends on the
      source being parsed) but a literal part carrying a character no identifier
      may hold refuses every possible substitution.
    - `unclassified` -- a return this function cannot show to be a BORROWED
      name. ⚠⚠ This third bucket is the one that closes the hole a roster and a
      literal scan leave open: `built = "x"; return built`, a concatenation, or
      any new route produces a name neither of the first two buckets sees, and
      counting them as fine by default is how a guard passes against the defect
      it names.
    """
    import ast

    literals: list[str] = []
    scaffoldings: list[tuple[str, str]] = []
    unclassified: list[str] = []

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Return):
            continue
        value = node.value

        if value is None or (isinstance(value, ast.Constant) and value.value is None):
            continue

        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            literals.append(value.value)
            continue

        joined = [n for n in ast.walk(value) if isinstance(n, ast.JoinedStr)]
        if joined:
            for one in joined:
                scaffolding = "".join(
                    part.value
                    for part in one.values
                    if isinstance(part, ast.Constant) and isinstance(part.value, str)
                )
                scaffoldings.append((scaffolding, ast.get_source_segment(source, one) or "<f-string>"))
            continue

        # A name read out of the file being parsed is BORROWED: the call site
        # writes it, so a reference search can see it.
        #
        # ⚠⚠ STRUCTURAL, never a substring over the return's text. The first
        # version asked whether the segment CONTAINED `source_bytes[` and
        # `.decode(`, and review measured what that admits:
        # `return "get_" + source_bytes[a:b].decode("utf-8")` builds the
        # identifier-shaped `get_foo` and was classified as borrowed -- a guard
        # written against a spelling, inside the guard written to close that
        # class. The whole expression must BE the borrow.
        if _is_borrowed_slice(value):
            continue
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) \
                and value.func.id in _NAME_BORROWING_HELPERS:
            continue

        unclassified.append(ast.get_source_segment(source, node) or "")

    return literals, scaffoldings, unclassified


def _extract_name_source() -> str:
    import inspect
    import textwrap

    from jcodemunch_mcp.parser import extractor

    return textwrap.dedent(inspect.getsource(extractor._extract_name))


def test_every_built_name_in_the_extractor_is_unreachable_by_name():
    """⚠⚠ The mechanism, not this instance (#733).

    `_name_reachability`'s correctness rests on a property of every BUILT name
    in the tree -- that none of them is identifier-shaped, so a reference search
    keyed on the name is never trusted about it -- and until this fix nothing
    asserted it. The C# three satisfy it by carrying an operator or a space; the
    Swift one satisfies it only because this fix chose brackets, and a bare
    `subscript` would have satisfied nothing while looking exactly as correct.

    Three buckets, and the third is what makes the first two mean something: a
    return this scan cannot show to be a BORROWED name fails, so a built name
    arriving by a route nobody anticipated -- a variable, a concatenation --
    cannot pass by being unrecognised.
    """
    from jcodemunch_mcp.tools._name_reachability import name_can_appear_at_a_call_site

    for built in _BUILT_NAME_ROSTER:
        assert not name_can_appear_at_a_call_site(built), built

    literals, scaffoldings, unclassified = _built_name_sites(_extract_name_source())

    unrostered = {name for name in literals if name not in _BUILT_NAME_ROSTER}
    assert not unrostered, (
        f"_extract_name returns built name(s) {sorted(unrostered)} that the "
        f"roster does not name. Add them, and check first that "
        f"`name_can_appear_at_a_call_site` refuses each one -- an "
        f"identifier-shaped built name silently re-arms #714 (#733)."
    )

    # ⚠ The other direction: an entry whose code is gone lingers otherwise, and
    # a roster nobody prunes is the escape hatch `_RESOLVED_BEFORE_NAME_FIELDS`
    # already had to grow a test against.
    stale = _BUILT_NAME_ROSTER - set(literals)
    assert not stale, (
        f"the roster names {sorted(stale)}, which `_extract_name` no longer "
        f"returns. Delete the entry."
    )

    assert len(scaffoldings) == _INTERPOLATED_BUILDER_COUNT, (
        f"expected {_INTERPOLATED_BUILDER_COUNT} interpolated builders in "
        f"`_extract_name`, found {len(scaffoldings)}: "
        f"{[rendered for _s, rendered in scaffoldings]}. If one was added, "
        f"check its literal parts refuse every substitution before raising this "
        f"number; if one was removed, lower it."
    )
    for scaffolding, rendered in scaffoldings:
        assert not name_can_appear_at_a_call_site(scaffolding), (
            f"{rendered} builds a name whose literal parts are "
            f"identifier-shaped, so some substitution produces a name a "
            f"reference search will be trusted about (#733, #714)"
        )

    assert not unclassified, (
        f"`_extract_name` has return(s) this guard cannot classify: "
        f"{unclassified}. If the name is BORROWED from the source, teach "
        f"`_built_name_sites` how to see that; if it is BUILT, check "
        f"`name_can_appear_at_a_call_site` refuses it and add it to the roster."
    )


_PLANTED_BUILT_NAMES = {
    "a bare literal": '''
def _extract_name(node, spec, source_bytes):
    return "subscriptIndexer"
''',
    # ⚠⚠ The shape the first version of this guard missed entirely. Both
    # builders hang off an `ast.IfExp`, so the return's TOP node is not a
    # JoinedStr and a check on `isinstance(node.value, ast.JoinedStr)` sees
    # neither -- which is how an identifier-shaped `operator_+` would have
    # shipped under a green test whose docstring claimed to cover it.
    "an f-string behind a conditional": '''
def _extract_name(node, spec, source_bytes):
    return f"operator_checked_{token}" if checked else f"operator_{token}"
''',
    # Neither bucket sees this, which is why the third exists.
    "a built name behind a variable": '''
def _extract_name(node, spec, source_bytes):
    built = "indexer"
    return built
''',
    "a built name by concatenation": '''
def _extract_name(node, spec, source_bytes):
    return "operator" + token
''',
    # ⚠⚠ The narrowest route, and the one a SUBSTRING borrow-check admits: the
    # return carries `source_bytes[` and `.decode(`, so a text scan calls it
    # borrowed, while the name it produces (`get_foo`) appears nowhere in the
    # file being parsed. Review measured this against the substring version and
    # got `unclassified=0`. `_is_borrowed_slice` asks the SHAPE instead.
    "a partial borrow with a built prefix": '''
def _extract_name(node, spec, source_bytes):
    return "get_" + source_bytes[node.start_byte:node.end_byte].decode("utf-8")
''',
}


@pytest.mark.parametrize(
    "shape", sorted(_PLANTED_BUILT_NAMES), ids=sorted(_PLANTED_BUILT_NAMES)
)
def test_the_built_name_guard_fires_on_a_planted_identifier_shaped_name(shape):
    """⚠⚠ The non-vacuity pass, and it is not optional here.

    The guard above passed on its FIRST run, which is the shape
    [[a-ratchet-can-pass-against-the-defect-it-names]] names -- and it was in
    fact passing while reaching one of three interpolated builders, found by
    review planting exactly the second case below.

    Each source here produces a name a reference search would be trusted about.
    The guard must refuse all four: the first two by the predicate, the last two
    by being unable to classify the return at all.
    """
    from jcodemunch_mcp.tools._name_reachability import name_can_appear_at_a_call_site

    literals, scaffoldings, unclassified = _built_name_sites(_PLANTED_BUILT_NAMES[shape])

    accepted = [n for n in literals if name_can_appear_at_a_call_site(n)]
    accepted += [r for s, r in scaffoldings if name_can_appear_at_a_call_site(s)]

    assert accepted or unclassified, (
        f"{shape}: the guard saw nothing wrong with a built name a reference "
        f"search will be trusted about, so it would not have caught #733's "
        f"first draft either"
    )


def test_every_name_borrowing_helper_still_exists():
    """The allowlist's second direction (#733).

    ⚠⚠ `_NAME_BORROWING_HELPERS` is an ALLOWLIST, and allowlists in this repo
    grow: the next author whose helper trips the third bucket can silence the
    guard by adding a name, which is the `_HELPER_LITERAL_EXCEPTIONS` shape this
    same PR deleted an entry from. Two limits, stated rather than papered over:

    - Nothing here PROVES a listed helper borrows rather than builds. That is
      not statically decidable, so the roster is a judgement each entry's author
      has to make, and this test only stops a stale one lingering.
    - A helper that starts BUILDING a name keeps its entry and goes unchecked.
      The remedy if that ever matters is to roster the helper's own returns the
      way `_extract_name`'s are rostered, not to grow this list further.
    """
    from jcodemunch_mcp.parser import extractor

    missing = {
        name for name in _NAME_BORROWING_HELPERS if not hasattr(extractor, name)
    }
    assert not missing, (
        f"{sorted(missing)} is excused as a name-borrowing helper and no longer "
        f"exists in `extractor`. Delete the entry."
    )


def test_the_planted_guard_still_accepts_the_real_thing():
    """The control: a guard that refused every source would pass the test above
    while saying nothing about `_extract_name`.
    """
    from jcodemunch_mcp.tools._name_reachability import name_can_appear_at_a_call_site

    literals, scaffoldings, unclassified = _built_name_sites(_extract_name_source())

    assert not unclassified
    assert literals and scaffoldings
    assert not [n for n in literals if name_can_appear_at_a_call_site(n)]
    assert not [s for s, _r in scaffoldings if name_can_appear_at_a_call_site(s)]


def test_a_subscript_in_a_protocol_is_a_requirement_too():
    """The same node type appears in both bodies, so both must be covered by one
    entry rather than by a class-body special case.
    """
    source = """protocol P {
  subscript(i: Int) -> Int { get }
}
"""
    assert ("subscript[]", "method", "P.subscript[]") in qualified(source)


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
    subs = [s for s in parse_file(source, "a.swift", "swift") if s.name == "subscript[]"]
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
    assert pairs(source) == {("S", "class"), ("MAX", "constant"), ("x", "property")}


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


# ---------------------------------------------------------------------------
# The destructive surface this fix creates (#733, mirroring #714's review)
# ---------------------------------------------------------------------------

_DECL_SOURCE = """struct Matrix {
  subscript(i: Int) -> Int { return i }
  func ordinary(_ n: Int) {}
}
"""

_USE_SOURCE = """func go() {
  let m = Matrix()
  let x = m[0]
  m.ordinary(1)
}
"""


@pytest.fixture(scope="module")
def two_file_repo(tmp_path_factory):
    """A Swift repo where the subscript is USED, indexed through the product."""
    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path_factory.mktemp("swift_repo")
    (root / "Matrix.swift").write_text(_DECL_SOURCE, encoding="utf-8")
    (root / "Use.swift").write_text(_USE_SOURCE, encoding="utf-8")
    storage = str(root / "idx")
    result = index_folder(path=str(root), use_ai_summaries=False, storage_path=storage)
    return result["repo"], storage


def test_a_subscript_in_use_is_not_certified_deletable(two_file_repo):
    """⚠⚠ The defect this fix would otherwise have CREATED.

    Before the fix a subscript was absent from the index, so `check_delete_safe`
    could not be asked about it. Now it can, and a subscript is invoked as
    `m[0]` -- its declaration's name is never written at a call site, so a
    reference search keyed on the name finds nothing. #714 measured what that
    costs: `safe_to_delete` at confidence 1.0, with "No callers or refs found",
    for a member used on the line below the one it certified.

    ⚠ #566's lesson on new surface, which is why this test exists rather
    than a sentence in the PR: capping a report does not cap the tool that ACTS
    on it, and a fix that makes a form visible hands every consumer of that form
    a question it could not previously be asked.

    The ordinary method is the control -- it is correctly blocked, which is what
    makes a `safe_to_delete` on the subscript a defect rather than a thin corpus.
    """
    from jcodemunch_mcp.tools.check_delete_safe import check_delete_safe

    repo, storage = two_file_repo

    control = check_delete_safe(
        repo, "Matrix.swift::Matrix.ordinary#method", storage_path=storage
    )
    assert control["verdict"] != "safe_to_delete", (
        "the control is not blocked; the fixture proves nothing"
    )

    got = check_delete_safe(
        repo, "Matrix.swift::Matrix.subscript[]#method", storage_path=storage
    )
    assert got["verdict"] != "safe_to_delete", (
        f"a subscript in use was certified deletable "
        f"(verdict={got['verdict']}, confidence={got['confidence']})"
    )
    assert got["verdict"] == "name_not_searchable", got["verdict"]
    assert got["confidence"] <= 0.6, got["confidence"]
    assert got["stop_rule"]["terminal"] is False, got["stop_rule"]
