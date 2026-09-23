"""Each name in a grouped Go `var`/`const` block records the span of its own spec (#826).

`const ( P = 1; Q = 2 )` gave `P` and `Q` the same bytes -- the whole block --
so `get_symbol_source` on `P` returned `Q`'s declaration too, and every
consumer reading a span got the block for either name. `_variable_symbol`'s
docstring justified it: a grouped block "has no narrower node containing one
name alone". Go's grammar spells a `const_spec` and a `var_spec` per line, so
the clause was false, and #817 had already used the sibling `type_spec` under
the rule THE WIDEST NODE THAT ADDRESSES THIS NAME ALONE: the declaration when
it binds one spec (unchanged bytes for the common case), the spec when it
binds several.

⚠ A spec that itself binds several names (`const D, E = 5, 6`) is the
narrowest node addressing either, so `D` and `E` share it; that is the same
rule, not an exception, and it is pinned so nobody later synthesises a range
that addresses no bytes (#414).

⚠ The rule is asked of ONE function, `_go_binding_span_node`, which #817's
`_go_type_span_node` became; the type test still passes because the rule did
not change, only its reach.
"""

from __future__ import annotations

from collections import Counter

import pytest

from jcodemunch_mcp.parser.extractor import parse_file


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    """Answer the parser, not the developer's config file (#411)."""
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def _symbols(source: str):
    return {s.name: s for s in parse_file(source, "a.go", "go")}


def _recorded(source: str, sym) -> str:
    return source.encode()[sym.byte_offset : sym.byte_offset + sym.byte_length].decode()


_GROUPED_CONST = "package p\n\nconst (\n\tP = 1\n\tQ = 2\n)\n"
_GROUPED_VAR = "package p\n\nvar (\n\tX = 1\n\tY = 2\n\tZ error\n)\n"


def test_each_grouped_constant_records_its_own_line():
    syms = _symbols(_GROUPED_CONST)
    assert _recorded(_GROUPED_CONST, syms["P"]) == "P = 1"
    assert _recorded(_GROUPED_CONST, syms["Q"]) == "Q = 2"


def test_each_grouped_variable_records_its_own_line():
    syms = _symbols(_GROUPED_VAR)
    assert _recorded(_GROUPED_VAR, syms["X"]) == "X = 1"
    assert _recorded(_GROUPED_VAR, syms["Y"]) == "Y = 2"
    assert _recorded(_GROUPED_VAR, syms["Z"]) == "Z error"


@pytest.mark.parametrize("source", [_GROUPED_CONST, _GROUPED_VAR])
def test_no_two_names_in_a_grouped_block_share_a_span(source):
    """The property, not the instance: a span is an identity, and this repo
    has paid twice for identities that collided (the Rust fidelity set,
    #821's pre-renumbering parent)."""
    spans = Counter((s.byte_offset, s.byte_length) for s in parse_file(source, "a.go", "go"))
    assert all(n == 1 for n in spans.values()), spans


def test_the_signature_is_the_recorded_bytes():
    """#817's second finding, applied here: `signature` and the span must
    describe the same node, or a reader is handed the block and shown the
    line."""
    for source in (_GROUPED_CONST, _GROUPED_VAR):
        for sym in parse_file(source, "a.go", "go"):
            assert sym.signature == _recorded(source, sym).strip(), sym.name


def test_a_single_declaration_keeps_the_bytes_it_had():
    """Unchanged for the common case, keyword included -- the same line #817
    drew for `type S struct{}` so that no existing index moves."""
    source = "package p\n\nconst S = 3\nvar T = 4\n"
    syms = _symbols(source)
    assert _recorded(source, syms["S"]) == "const S = 3"
    assert _recorded(source, syms["T"]) == "var T = 4"
    assert syms["S"].line == 3 and syms["T"].line == 4


def test_a_spec_binding_two_names_is_shared_by_both():
    """No narrower node addresses `D` alone, so `D` and `E` record the spec:
    the whole declaration when ungrouped, the one line when grouped."""
    source = "package p\n\nconst D, E = 5, 6\nvar (\n\tF, G = 7, 8\n\tH = 9\n)\n"
    syms = _symbols(source)
    assert _recorded(source, syms["D"]) == "const D, E = 5, 6"
    assert _recorded(source, syms["E"]) == "const D, E = 5, 6"
    assert _recorded(source, syms["F"]) == "F, G = 7, 8"
    assert _recorded(source, syms["G"]) == "F, G = 7, 8"
    assert _recorded(source, syms["H"]) == "H = 9"


def test_an_iota_continuation_records_its_own_name():
    """`J` has no `=`; its spec is the bare identifier, and that is still a
    node that addresses `J` alone."""
    source = "package p\n\nconst (\n\tI = iota\n\tJ\n)\n"
    syms = _symbols(source)
    assert _recorded(source, syms["I"]) == "I = iota"
    assert _recorded(source, syms["J"]) == "J"


def test_a_grouped_block_of_one_spec_records_the_declaration():
    """One spec in parentheses binds one name, so the declaration is the widest
    node addressing it alone -- the same answer #817 gives `type ( S struct{} )`."""
    source = "package p\n\nconst (\n\tOnly = 1\n)\n"
    syms = _symbols(source)
    assert _recorded(source, syms["Only"]) == "const (\n\tOnly = 1\n)"


def test_the_line_numbers_follow_the_span():
    source = "package p\n\nconst (\n\tP = 1\n\tQ = 2\n)\n"
    syms = _symbols(source)
    assert (syms["P"].line, syms["P"].end_line) == (4, 4)
    assert (syms["Q"].line, syms["Q"].end_line) == (5, 5)
