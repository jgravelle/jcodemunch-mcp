"""An F# `and` chain binds every name it declares (#824).

`type A = int / and B = int` indexed `A` and nothing else; a mutually
recursive pair is exactly the shape where losing the second type loses the
relationship a reader is looking for. `let rec f x = ... / and g y = ...`
indexed `f` alone. `_parse_fsharp_symbols` is a custom extractor and each
of its three sites asked `_first_child_of_type` for ONE definition or ONE
binding left: #817's mechanism (one node, N names, one symbol) with no
spec map to correct, so the walk learns the chain itself. OCaml spells the
same construct and binds both, so the language family is not the
discriminator. Reporter: @jgravelle.

Rulings:
- Every definition in a `type ... and ...` chain is a symbol with the kind
  and qualified name the separate-line form gives, its members owned by
  it. Span: the whole `type_definition` (keyword included) when it holds
  ONE definition, byte-identical to before; the definition node when it
  holds several (#837's rule: the widest node addressing the name alone).
- Every binding in a `let rec ... and ...` chain is a symbol (`function`
  for a function left, `constant` for a value left). The grammar has NO
  node addressing one binding alone (the left and its body are siblings
  of the defn), so every binding records the whole defn: the rule, as for
  Go's `const D, E = 5, 6`, never a synthesised range (#414).
- New symbols on unchanged content; the first type of a chain moves its
  span to its definition and keeps its id. `PARSER_GENERATION` names it.
"""

from __future__ import annotations

from jcodemunch_mcp.parser.extractor import parse_file


def _rows(source: str):
    symbols = parse_file(source, "a.fs", "fsharp")
    by_id = {s.id: s for s in symbols}
    return [
        (s.kind, s.qualified_name, by_id[s.parent].name if s.parent else None, s.byte_offset, s.byte_length)
        for s in symbols
    ]


def test_the_reported_alias_chain_binds_both_types():
    assert _rows("type A = int\nand B = int\n") == [
        ("type", "A", None, 5, 7),
        ("type", "B", None, 17, 7),
    ]


def test_a_class_chain_binds_both_types_and_owns_their_members():
    source = "type A() =\n    member this.X = 1\nand B() =\n    member this.Y = 2\n"
    rows = [(k, q, o) for k, q, o, _, _ in _rows(source)]
    assert rows == [
        ("type", "A", None),
        ("property", "A.X", "A"),
        ("type", "B", None),
        ("property", "B.Y", "B"),
    ]


def test_a_record_and_union_chain_binds_both():
    rows = [(k, q) for k, q, _, _, _ in _rows("type R = { X: int }\nand U = | Q of int\n")]
    assert rows == [("type", "R"), ("type", "U")]


def test_a_three_type_chain_binds_every_type_with_its_own_span():
    source = "type A = int\nand B = string\nand C = float\n"
    rows = _rows(source)
    assert [(k, q) for k, q, _, _, _ in rows] == [("type", "A"), ("type", "B"), ("type", "C")]
    spans = [(o, n) for _, _, _, o, n in rows]
    assert len(set(spans)) == 3, spans


def test_a_single_type_records_the_bytes_it_always_did():
    """Keyword included, byte-identical to before."""
    assert _rows("type A = int\n") == [("type", "A", None, 0, 12)]


def test_the_chain_answers_what_the_separate_lines_answer():
    chain = [(k, q, o) for k, q, o, _, _ in _rows("type A = int\nand B = int\n")]
    separate = [(k, q, o) for k, q, o, _, _ in _rows("type A = int\ntype B = int\n")]
    assert chain == separate


def test_a_let_rec_function_chain_binds_every_function():
    """No node addresses one binding alone, so both record the whole defn."""
    source = "let rec f x = g x\nand g y = f y\n"
    assert _rows(source) == [
        ("function", "f", None, 0, 31),
        ("function", "g", None, 0, 31),
    ]


def test_a_let_rec_value_chain_binds_every_value():
    source = "let rec a = 1\nand b = 2\n"
    assert _rows(source) == [
        ("constant", "a", None, 0, 23),
        ("constant", "b", None, 0, 23),
    ]


def test_a_let_chain_inside_a_type_body_binds_every_member():
    source = "type T() =\n    let rec f x = g x\n    and g y = f y\n    member this.M() = f 1\n"
    rows = [(k, q, o) for k, q, o, _, _ in _rows(source)]
    assert rows == [
        ("type", "T", None),
        ("method", "T.f", "T"),
        ("method", "T.g", "T"),
        ("method", "T.M", "T"),
    ]
    # Review: each chained member's signature is its OWN left, not the
    # defn's first line (which names the first binding).
    sigs = {s.qualified_name: s.signature for s in parse_file(source, "a.fs", "fsharp")}
    assert sigs["T.f"] == "let f x"
    assert sigs["T.g"] == "let g y"
    # A single let in a body keeps the line it always had.
    single = {s.qualified_name: s.signature for s in parse_file("type T() =\n    let h z = z\n", "a.fs", "fsharp")}
    assert single["T.h"] == "let h z = z"


def test_the_first_type_of_a_chain_keeps_its_id():
    chain = {s.name: s.id for s in parse_file("type A = int\nand B = int\n", "a.fs", "fsharp")}
    single = {s.name: s.id for s in parse_file("type A = int\n", "a.fs", "fsharp")}
    assert chain["A"] == single["A"]
