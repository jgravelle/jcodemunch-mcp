"""A Go `type ( ... )` block binds every name in it (#817).

`type ( A int; B int; C struct{ N int } )` indexed A and nothing else. Not
mis-kinded, not unowned: B, C, C's field and the ownership of every method on
either were absent, while the same types written on separate `type` lines all
indexed.

⚠⚠ **The issue's diagnosis was wrong about WHERE, and the correction is the
fix.** It read the gap as `type_patterns` naming the declaration with nothing
walking down from it -- #731's shape, where `_extract_go_variables` descends to
the specs. But `type_patterns` is read by NOTHING (#725, asserted by
`tests/test_grammar_spelled_forms.py`): Go's type came from
`symbol_node_types`, where `type_declaration` mapped to `type`, and
`_extract_symbol` returns `Optional[Symbol]` -- **at most one symbol per node,
by signature.** No name extractor could have made that channel bind three
names.

So there is no descent to share and no fourth channel to add. The declaration
was simply the wrong node: `type_spec` is what binds one name, it is a direct
child in BOTH forms, and the generic walk already visits it. The spec is the
symbol node now, which is why this fix ADDS no machinery -- docstrings,
interface keywords and lexical nesting keep working because they were never
Go's own code.

⚠⚠ **A span must address one name, and that is what makes the grouped form
work at all.** #778's second pass joins a method to its receiver by BYTE
OFFSET. Give three grouped types the declaration's span -- which is what the
`var` and `const` channels do with their own grouped blocks -- and all three
share one offset, so the join collapses and two of the three keep no owner. The
rule here is **the widest node that addresses this name alone**: the
declaration when it binds one name (today's bytes, `type` keyword included),
the spec when it binds several.

⚠ Out of scope, measured and unchanged in BOTH forms: a type ALIAS
(`type C = int`) is a `type_alias` node, in no spec's map, and indexes nowhere
-- grouped or not. The equivalence test below covers an alias precisely so the
two forms cannot start disagreeing about it; making it a symbol is a different
issue.
"""

import pytest

from jcodemunch_mcp.parser.extractor import parse_file


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    """This box disables some languages in its own config; the gate is function-local."""
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


_GROUPED = (
    "package p\n"
    "\n"
    "type (\n"
    "\tA int\n"
    "\tB int\n"
    "\tC struct{ N int }\n"
    ")\n"
)

_SEPARATE = (
    "package p\n"
    "\n"
    "type A int\n"
    "type B int\n"
    "type C struct{ N int }\n"
)


def _syms(source: str, filename: str = "d.go"):
    return parse_file(source, filename, "go")


def _shape(source: str) -> set[tuple[str, str, str, object]]:
    """What each symbol IS, with nothing positional in it.

    Spans and ids differ between the two forms because the bytes differ; kind,
    name, qualified name and owner are what a consumer reads, and those must
    not depend on which spelling the author used.
    """
    return {(s.kind, s.name, s.qualified_name, s.parent) for s in _syms(source)}


def test_a_grouped_block_binds_every_type():
    """The reported case: three types in, three types out."""
    types = {s.name for s in _syms(_GROUPED) if s.kind == "type"}
    assert types == {"A", "B", "C"}


def test_the_grouped_and_separate_forms_produce_the_same_symbols():
    """The property, not the instance: a spelling is not a different program.

    This is what stops the three Go declaration channels drifting apart again
    -- `var` and `const` already agreed with themselves across the two forms
    and `type` did not, which is how the gap survived #731.
    """
    assert _shape(_GROUPED) == _shape(_SEPARATE)


def test_the_two_forms_agree_about_a_type_alias():
    """An alias indexes in NEITHER form, so neither form may start inventing it.

    ⚠ This pins agreement, not the absence itself: `type C = int` is a
    `type_alias`, which no spec maps, and whether it should be a symbol is a
    separate question. What must never happen again is one spelling answering
    differently from the other.
    """
    grouped = "package p\n\ntype (\n\tA = int\n\tB int\n)\n"
    separate = "package p\n\ntype A = int\ntype B int\n"
    assert _shape(grouped) == _shape(separate)
    assert {s.name for s in _syms(grouped)} == {"B"}


def test_a_grouped_structs_fields_are_indexed_and_owned():
    """C's field was absent because C was; #778's channel had nothing to hang it on."""
    syms = _syms(_GROUPED)
    owner = next(s for s in syms if s.kind == "type" and s.name == "C")
    fields = [s for s in syms if s.kind == "field"]
    assert [(f.qualified_name, f.parent) for f in fields] == [("C.N", owner.id)]


def test_a_method_on_a_grouped_type_is_qualified_by_it():
    """#778 resolves an owner by byte offset, and an absent type has none.

    The method kept a bare name -- absence rather than fabrication, and caused
    here rather than there.
    """
    source = (
        "package p\n"
        "\n"
        "type (\n"
        "\tA int\n"
        "\tB struct{ N int }\n"
        ")\n"
        "\n"
        "func (b B) Do() {}\n"
        "func (a A) Go() {}\n"
    )
    syms = _syms(source)
    owners = {s.name: s.id for s in syms if s.kind == "type"}
    methods = {s.name: s for s in syms if s.kind == "method"}
    assert methods["Do"].qualified_name == "B.Do"
    assert methods["Do"].parent == owners["B"]
    assert methods["Go"].qualified_name == "A.Go"
    assert methods["Go"].parent == owners["A"]


def test_every_type_in_a_block_addresses_its_own_bytes():
    """Two symbols at one offset are one symbol as far as any join is concerned.

    ⚠ The `var` and `const` channels give every name in a grouped block the
    DECLARATION's span, so their names collide on `(offset, length)` today.
    That is survivable for them -- nothing joins to a constant by offset -- and
    it is exactly what a type may not do, because #778's receiver pass is such
    a join. Asserted here as a property rather than trusted as a consequence.
    """
    types = [s for s in _syms(_GROUPED) if s.kind == "type"]
    spans = [(s.byte_offset, s.byte_length) for s in types]
    assert len(set(spans)) == len(types)


def test_an_ungrouped_type_still_spans_its_own_declaration():
    """The bytes a reader opens, unchanged: `type S struct{...}`, keyword and all.

    ⚠ The narrowest node binding `S` alone is the SPEC in both forms, and
    taking it uniformly would have moved the offset of every Go type in every
    index and dropped `type` from every signature -- a fix paid for by every
    user of the common case. The rule is the WIDEST node that addresses this
    name alone.
    """
    source = "package p\n\ntype S struct{ N int }\n"
    sym = next(s for s in _syms(source) if s.kind == "type")
    text = source.encode()[sym.byte_offset : sym.byte_offset + sym.byte_length]
    assert text.decode().startswith("type S struct")
    assert sym.signature.startswith("type S struct")


def test_a_one_name_grouped_block_records_bytes_that_close():
    """The shape that fell between the other two span tests, and it was broken.

    ⚠⚠ A grouped block binding ONE name widens to the declaration, and the
    first draft of that widening moved only the START: the end stayed on the
    spec, so the recorded bytes were `type (\\n\\tA int` -- unbalanced Go, a
    `content_hash` over a fragment, and an `end_line` disagreeing with the
    `signature` built from the wider node. Neither existing test could see it:
    one uses the ungrouped spelling, the other three names. Found in review.

    The property is that one span comes from one node, so it is asserted as
    agreement between what the symbol says and what the bytes are, not as a
    literal length.
    """
    source = "package p\n\ntype (\n\tA int\n)\n"
    sym = next(s for s in _syms(source) if s.kind == "type")
    recorded = source.encode()[sym.byte_offset : sym.byte_offset + sym.byte_length]
    assert recorded.decode() == "type (\n\tA int\n)"
    assert sym.signature == recorded.decode()
    # ⚠ `rindex`, so a future fixture holding a `func()` or a parenthesised
    # type cannot silently compute the wrong expected line. The closing paren
    # of the block is the LAST one in this sample by construction.
    assert sym.end_line == source.count("\n", 0, source.rindex(")") + 1) + 1


def test_a_grouped_local_type_block_binds_every_name_under_its_function():
    """A local type is indexed today and keeps its function as owner.

    Go spells a function-local `type` with the same node as a package-level
    one, so a channel gated on package scope would have DELETED a symbol class
    while fixing another -- the denylist/allowlist trap #732 spent a review
    round on, one node type over.
    """
    source = "package p\n\nfunc f() {\n\ttype (\n\t\tL1 int\n\t\tL2 int\n\t)\n\t_ = L1(0)\n}\n"
    syms = _syms(source)
    owner = next(s for s in syms if s.kind == "function")
    locals_ = {s.name: s for s in syms if s.kind == "type"}
    assert set(locals_) == {"L1", "L2"}
    assert {s.qualified_name for s in locals_.values()} == {"f.L1", "f.L2"}
    assert {s.parent for s in locals_.values()} == {owner.id}


def test_an_interface_in_a_block_keeps_the_keywords_dispatch_reads():
    """Rerouting the symbol node must not quietly drop what hung off the old one.

    `_detect_interface_keywords` is read by implementation resolution; it took
    the declaration and now takes the spec, which is the node the `interface`
    keyword actually sits under.
    """
    source = "package p\n\ntype (\n\tR interface{ Read() }\n\tN int\n)\n"
    syms = {s.name: s for s in _syms(source) if s.kind == "type"}
    assert set(syms) == {"R", "N"}
    assert "interface" in syms["R"].keywords
    assert "interface" not in syms["N"].keywords


def test_each_grouped_type_carries_its_own_doc_comment():
    """One block, one comment per spec -- and the block's own comment is not
    copied onto every member of it."""
    source = (
        "package p\n"
        "\n"
        "// Block doc.\n"
        "type (\n"
        "\t// A counts.\n"
        "\tA int\n"
        "\tB int\n"
        ")\n"
    )
    syms = {s.name: s for s in _syms(source) if s.kind == "type"}
    assert "A counts." in syms["A"].docstring
    assert "Block doc." not in syms["A"].docstring
    assert not syms["B"].docstring.strip()


def test_an_empty_type_block_yields_nothing():
    """`type ()` binds no name, and a channel that answered one would be
    inventing a symbol out of punctuation."""
    assert _syms("package p\n\ntype ()\n\ntype Z int\n")
    assert {s.name for s in _syms("package p\n\ntype ()\n\ntype Z int\n")} == {"Z"}
