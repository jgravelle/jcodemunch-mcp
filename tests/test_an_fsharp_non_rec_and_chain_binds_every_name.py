"""A non-`rec` F# `let ... and ...` chain binds every name (#856).

`let a = 1 / and b = 2` is valid F# (spec 8.6: `let rec? function-or-value-defns`)
and tree-sitter-fsharp 0.3.12, the newest release, cannot parse it: at
module level `and b = 2` spills into an `infix_expression` whose head is an
IDENTIFIER spelled `and`, so `b` was absent; in a type body the `and` lands
under an `ERROR` that swallows the members after it, so `b` AND every later
member (`M`) were absent. `let rec` parses clean and binds both since #824.

Rulings:
- The walk re-parses with each spilled `and` replaced by `let`, the same
  three bytes, so every offset is unchanged and the tree is walked against
  the ORIGINAL source. Two consecutive `let`s bind the names a non-`rec`
  chain binds (only scope differs, which extraction does not read).
- Only a spilled `and` is rewritten: an `identifier` spelled `and` (a
  keyword is never an identifier) or an `'and'` token directly under an
  `ERROR`. A `let rec` chain, a `type` chain and a property's `with get ...
  and set ...` parse clean and are untouched.
- The grammar gives each binding its own node here, so each binding records
  its own bytes (#837's widest node addressing the name alone). Reporter:
  @jgravelle.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.parser.extractor import parse_file


def _rows(source: str):
    symbols = parse_file(source, "a.fs", "fsharp")
    by_id = {s.id: s for s in symbols}
    return [
        (s.kind, s.qualified_name, by_id[s.parent].name if s.parent else None, s.byte_offset, s.byte_length)
        for s in symbols
    ]


def test_the_reported_module_level_chain_binds_both():
    assert _rows("let a = 1\nand b = 2\n") == [
        ("constant", "a", None, 0, 9),
        ("constant", "b", None, 10, 9),
    ]


def test_the_reported_type_body_chain_binds_both_and_keeps_the_members_after_it():
    source = "type T() =\n    let mutable a = 1\n    and b = 2\n    member this.M() = a\n"
    rows = [(k, q, o) for k, q, o, _, _ in _rows(source)]
    # `mutable` is `a`'s alone; an immutable body `let` is a `constant`, as
    # on a line of its own.
    assert rows == [
        ("type", "T", None),
        ("field", "T.a", "T"),
        ("constant", "T.b", "T"),
        ("method", "T.M", "T"),
    ]


def test_a_function_chain_binds_every_function():
    assert [(k, q) for k, q, _, _, _ in _rows("let f x = x\nand g y = y\nand h z = z\n")] == [
        ("function", "f"), ("function", "g"), ("function", "h"),
    ]


def test_a_chain_inside_a_named_module_is_qualified():
    rows = [(k, q) for k, q, _, _, _ in _rows("module M =\n    let a = 1\n    and b = 2\n")]
    assert ("constant", "M.a") in rows and ("constant", "M.b") in rows, rows


def test_the_chain_answers_what_the_separate_lets_answer():
    """Same names, kinds and owners as two `let`s over the same bytes."""
    for chain, separate in [
        ("let a = 1\nand b = 2\n", "let a = 1\nlet b = 2\n"),
        ("type T() =\n    let a = 1\n    and b = 2\n    member this.M() = a\n",
         "type T() =\n    let a = 1\n    let b = 2\n    member this.M() = a\n"),
    ]:
        assert _rows(chain) == _rows(separate)


def test_the_source_text_is_the_original_bytes():
    """The tree is re-parsed from rewritten bytes but read against the
    original: no symbol carries text the file does not contain."""
    source = "type T() =\n    let a = 1\n    and b = 2\n"
    raw = source.encode()
    for s in parse_file(source, "a.fs", "fsharp"):
        assert raw[s.byte_offset:s.byte_offset + s.byte_length].decode() in source
    t = next(s for s in parse_file(source, "a.fs", "fsharp") if s.name == "T")
    assert "and b = 2" in raw[t.byte_offset:t.byte_offset + t.byte_length].decode()


@pytest.mark.parametrize("source, names", [
    ("let rec a = 1\nand b = 2\n", ["a", "b"]),
    ("type A = int\nand B = int\n", ["A", "B"]),
    ("type P() =\n    let mutable v = 0\n    member this.X with get() = v and set x = v <- x\n", ["P", "P.v", "P.X"]),
])
def test_a_clean_and_is_untouched(source, names):
    assert [q for _, q, _, _, _ in _rows(source)] == names


def test_a_type_chain_broken_by_an_if_directive_never_becomes_constants():
    """Found on FsToolkit.ErrorHandling (CancellableTaskOption.fs): a `type`
    chain whose `and` lines sit between `#if`/`#else` spills the same way,
    and the first draft rewrote those `and`s too, publishing six types as
    `constant`s. Only an `and` at a `let`'s column continues a `let` (the
    offside rule). The later types stay absent, as on main (LEDGER L-27)."""
    source = "type A = int\n#if X\nand B = int\n#else\nand B = string\n#endif\nand C = float\n"
    rows = [(k, q) for k, q, _, _, _ in _rows(source)]
    assert rows[0] == ("type", "A")
    assert all(k == "type" for k, _ in rows), rows


@pytest.mark.parametrize("source, ids", [
    ("let a = 1\n#if X\nand b = 2\n#else\nand b = 3\n#endif\n",
     ["a#constant", "b#constant~1", "b#constant~2"]),
    ("let rec f x = g x\n#if X\nand g y = f y\n#else\nand g y = y\n#endif\n",
     ["f#function", "g#function~1", "g#function~2"]),
    ("let a = 1\n// a comment\n[<Obsolete>]\nand b = 2\n", ["a#constant", "b#constant"]),
])
def test_a_let_chain_across_directives_and_comments_binds_every_branch(source, ids):
    """Each `#if` branch is source text and binds, as ordinal twins."""
    assert [s.id.split("::", 1)[1] for s in parse_file(source, "a.fs", "fsharp")] == ids


@pytest.mark.parametrize("source", [
    "let a = 1\nand\n",
    "module M =\n    let a = 1\nand b = 2\n",
    "let a = 1\n  and b = 2\n",
])
def test_an_and_not_at_a_lets_column_is_not_rewritten(source):
    """A bare `and`, an `and` outdented past its `let`'s block, and one
    indented under it are not a chain the offside rule reads; nothing new."""
    assert "b" not in [s.name for s in parse_file(source, "a.fs", "fsharp")]
