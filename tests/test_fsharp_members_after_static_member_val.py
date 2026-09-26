"""An F# member written after a `static member val ... with get, set` line is
indexed (#848).

The grammar bundled in tree-sitter-language-pack 0.x mis-parses that line: it
takes `val` as the member name and spills `with get, set` and every member
after it out of the type body as a file-level expression chain, so a type
indexed its auto-property and nothing written below it. No walker can read a
member the grammar placed outside the body.

The fix is the grammar. The pack's newest release parses the line, but it is
the 1.x download generation the `<1.0.0` pin refuses (grammar_pack.py), so
F# is parsed by the standalone `tree-sitter-fsharp` wheel, pinned in
pyproject, which compiles its grammar in.

⚠ The capability certificate names that wheel's version beside the pack's:
it decides what F# parses, and a certificate naming only the pack would
describe an install that no longer parses F#.
"""

import pytest

from jcodemunch_mcp.parser.extractor import parse_file


def _rows(source: str) -> set[tuple[str, str]]:
    return {(s.qualified_name, s.kind) for s in parse_file(source, "a.fs", "fsharp")}


def test_the_reported_type_keeps_every_member():
    source = (
        "type A() =\n    static member val Total = 0 with get, set\n"
        "    static member Make() = A()\n    member val Size = 0\n"
    )
    assert _rows(source) == {
        ("A", "type"),
        ("A.Total", "property"),
        ("A.Make", "method"),
        ("A.Size", "property"),
    }


@pytest.mark.parametrize("line", [
    "static member val Total = 0 with get, set",
    "static member val Total = 0 with get",
    "static member val Total : int = 0 with get, set",
    "member val Total = 0 with get, set",
])
def test_a_member_after_an_auto_property_is_in_the_body(line):
    source = f"type A() =\n    {line}\n    member this.Run() = ()\n"
    assert ("A.Run", "method") in _rows(source), _rows(source)


def test_the_next_type_is_not_swallowed():
    source = (
        "type A() =\n    static member val Total = 0 with get, set\n"
        "type B() =\n    member this.Run() = ()\n"
    )
    rows = _rows(source)
    assert ("B", "type") in rows and ("B.Run", "method") in rows, rows


def test_the_capability_certificate_names_the_fsharp_grammar():
    from jcodemunch_mcp.evidence.capability import parser_fingerprint

    packages = parser_fingerprint()["packages"]
    assert packages.get("tree-sitter-fsharp") not in (None, "absent"), packages


# ---------------------------------------------------------------------------
# The pinned grammar spells two shapes differently from the pack's, and the
# extractor reads both. A function with a return-type annotation is a VALUE
# whose pattern applies the name to its arguments, which named it by the
# whole pattern (`g (y: int) : int`, a `constant`). And an access modifier
# sits INSIDE `type_name` (`internal X`); the pack had that shape too, on
# the few files it parsed.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("decl, sig", [
    ("let g (y: int) : int = y", "let g (y: int) : int"),
    ("let g y : int = y", "let g y : int"),
    ("let private g (y: int) : int = y", "let g (y: int) : int"),
    ("let g ((a, b): int * int) : int = a", "let g ((a, b): int * int) : int"),
])
def test_an_annotated_function_is_a_function_named_by_its_identifier(decl, sig):
    (s,) = parse_file(decl + "\n", "a.fs", "fsharp")
    assert (s.qualified_name, s.kind, s.signature) == ("g", "function", sig)


def test_an_annotated_value_stays_a_value():
    (s,) = parse_file("let x : int = 1\n", "a.fs", "fsharp")
    assert (s.qualified_name, s.kind) == ("x", "constant")


def test_an_annotated_let_function_in_a_type_body_is_a_method():
    source = "type T() =\n    let g (y: int) : int = y\n    member _.M() = g 1\n"
    assert ("T.g", "method") in _rows(source), _rows(source)


@pytest.mark.parametrize("modifier", ["internal", "private", "public"])
def test_a_type_s_access_modifier_is_not_its_name(modifier):
    source = f"type {modifier} X() =\n    member _.M = 1\n"
    assert _rows(source) == {("X", "type"), ("X.M", "property")}


@pytest.mark.parametrize("source", ["[<Measure>] type kg\n", "type Opaque\n"])
def test_a_bodiless_type_is_indexed(source):
    """`type_declaration`, the bare `type X` form the pack's grammar could not
    parse; the inventory ratchet named it on the grammar swap."""
    name = source.split()[-1]
    assert _rows(source) == {(name, "type")}


def test_members_after_a_multi_line_new_then_constructor_are_kept():
    """LEDGER L-16, the same spill for another construct: the pack's grammar
    put `then` and every later member outside the type."""
    source = (
        "type C(x: int) =\n    new() as this =\n        C(0)\n        then ()\n"
        "    member this.X = x\n    member this.Y = 2\n"
    )
    rows = _rows(source)
    assert {("C.X", "property"), ("C.Y", "property")} <= rows, rows
