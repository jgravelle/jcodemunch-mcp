"""Zig, PowerShell and MATLAB: a class member is owned, and the class's state is indexed (#809, #811).

Three custom parsers, one pass each, two defects in the same pass.

#809: each parser threaded the enclosing class's NAME down its own walk and
rebuilt `f"{scope}.{name}"` by hand, qualifying the member correctly and
leaving `parent` at None -- invisible to every parent-keyed reader (the file
summary's member count, `get_file_outline`'s tree). #788 fixed the identical
mechanism in five other custom parsers by asking ONE helper, `_member_of`,
for both halves; these three were found by the AST scan that fix ran and
were left for their own issue because Zig's kind change moves ids. Zig also
returned a struct's `fn` as `function`, the kind half D and Solidity had.

#811: each parser indexed the class and its methods and none of its state.
Zig's `ContainerField` (a struct field), a struct-level `const`/`var`,
PowerShell's `class_property_definition`, MATLAB's `properties` block: none
was read.

Rulings, per language, because a member-kind row is a design task, not a
copy (the audit's own words):
- Zig: a struct field and a struct-level `var` are `field`; a struct-level
  `const` is `constant`; a `fn` in a container is `method`. An enum's
  variants are `ContainerField`s with no `IDENTIFIER` and are not indexed
  yet: #759 ruled an enum member an owned `constant` family-wide, and Zig is
  a tracked gap in `docs/workflows/LEDGER.md` L-03. Zig has no
  property concept: role omitted.
- PowerShell: every class property is `field` -- `static` and `hidden` are
  visibility and lifetime, not immutability, and PowerShell has no readonly
  class property and no accessor property, so the immutable and property
  roles are omitted. The `$` sigil is not part of the name.
- MATLAB: a `properties` entry is `field`; under a `Constant` attribute it is
  `constant`; under `Dependent` it is `property` (computed through `get.`,
  the language's accessor form). The existing `get.view` method keeps the
  `method` kind and the bare name the parser already gave it.

Every id that #809 moves is Zig's `fn`-in-a-container (`function` ->
`method`); `PARSER_GENERATION` names it. Every other qualified name is
byte-identical to what the parser emitted before, so `parent` is populated
and no other id moves (#788's `test_the_qualified_name_does_not_move`,
applied here).
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.parser.extractor import parse_file


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    """Answer the parser, not the developer's config file (#411)."""
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def _rows(source: str, filename: str, language: str):
    symbols = parse_file(source, filename, language)
    by_id = {s.id: s for s in symbols}
    return {
        s.qualified_name: (s.kind, by_id[s.parent].name if s.parent else None)
        for s in symbols
    }


_ZIG = (
    "const Audit = struct {\n"
    "    tally: u32 = 0,\n"
    "    const LIMIT: u32 = 3;\n"
    "    pub var shared: u32 = 1;\n"
    "    pub fn runIt(self: *Audit) u32 { return self.tally; }\n"
    "};\n"
)
_PS = (
    "class Audit {\n"
    "    [int] $tally = 0\n"
    "    static [int] $LIMIT = 3\n"
    "    hidden [string] $secret\n"
    "    [int] RunIt() { return 1 }\n"
    "}\n"
)
_MATLAB = (
    "classdef Audit\n"
    "    properties\n"
    "        tally = 0\n"
    "    end\n"
    "    properties (Constant)\n"
    "        LIMIT = 3\n"
    "    end\n"
    "    properties (Dependent)\n"
    "        view\n"
    "    end\n"
    "    methods\n"
    "        function r = runIt(obj)\n"
    "            r = 1;\n"
    "        end\n"
    "    end\n"
    "end\n"
)


# --- #809: ownership ----------------------------------------------------------

def test_a_zig_struct_fn_is_a_method_owned_by_the_struct():
    assert _rows(_ZIG, "a.zig", "zig")["Audit.runIt"] == ("method", "Audit")


def test_a_powershell_class_method_is_owned():
    assert _rows(_PS, "a.ps1", "powershell")["Audit.RunIt"] == ("method", "Audit")


def test_a_matlab_class_method_is_owned():
    assert _rows(_MATLAB, "a.m", "matlab")["Audit.runIt"] == ("method", "Audit")


def test_a_zig_struct_level_const_is_owned():
    """Already emitted as `Audit.LIMIT` with no parent: qualified, not owned."""
    assert _rows(_ZIG, "a.zig", "zig")["Audit.LIMIT"] == ("constant", "Audit")


# --- #811: state --------------------------------------------------------------

def test_zig_struct_state_is_indexed_and_owned():
    rows = _rows(_ZIG, "a.zig", "zig")
    assert rows["Audit.tally"] == ("field", "Audit")
    assert rows["Audit.shared"] == ("field", "Audit")


def test_powershell_class_state_is_indexed_and_owned():
    rows = _rows(_PS, "a.ps1", "powershell")
    assert rows["Audit.tally"] == ("field", "Audit")
    assert rows["Audit.LIMIT"] == ("field", "Audit")
    assert rows["Audit.secret"] == ("field", "Audit")


def test_matlab_class_state_is_indexed_and_owned_by_attribute():
    rows = _rows(_MATLAB, "a.m", "matlab")
    assert rows["Audit.tally"] == ("field", "Audit")
    assert rows["Audit.LIMIT"] == ("constant", "Audit")
    assert rows["Audit.view"] == ("property", "Audit")


# --- the whole answer, per language, and nothing else -------------------------

def test_the_zig_class_answers_every_row_and_nothing_else():
    assert _rows(_ZIG, "a.zig", "zig") == {
        "Audit": ("class", None),
        "Audit.tally": ("field", "Audit"),
        "Audit.LIMIT": ("constant", "Audit"),
        "Audit.shared": ("field", "Audit"),
        "Audit.runIt": ("method", "Audit"),
    }


def test_the_powershell_class_answers_every_row_and_nothing_else():
    assert _rows(_PS, "a.ps1", "powershell") == {
        "Audit": ("class", None),
        "Audit.tally": ("field", "Audit"),
        "Audit.LIMIT": ("field", "Audit"),
        "Audit.secret": ("field", "Audit"),
        "Audit.RunIt": ("method", "Audit"),
    }


def test_the_matlab_class_answers_every_row_and_nothing_else():
    assert _rows(_MATLAB, "a.m", "matlab") == {
        "Audit": ("class", None),
        "Audit.tally": ("field", "Audit"),
        "Audit.LIMIT": ("constant", "Audit"),
        "Audit.view": ("property", "Audit"),
        "Audit.runIt": ("method", "Audit"),
    }


# --- what does not move ---------------------------------------------------------

def test_free_functions_and_file_scope_constants_are_unchanged():
    assert _rows("pub fn top() void {}\nconst N: u32 = 1;\n", "a.zig", "zig") == {
        "top": ("function", None), "N": ("constant", None),
    }
    assert _rows("function Top { return 1 }\n", "a.ps1", "powershell") == {"Top": ("function", None)}
    assert _rows("function r = top()\n    r = 1;\nend\n", "a.m", "matlab") == {"top": ("function", None)}


def test_enum_variants_are_not_indexed_in_zig_or_powershell():
    """A tracked gap (LEDGER L-03): #759 ruled enum members owned `constant`s family-wide."""
    assert _rows("const E = enum { a, b };\n", "a.zig", "zig") == {"E": ("type", None)}
    assert _rows("enum E {\n    a\n    b\n}\n", "a.ps1", "powershell") == {"E": ("type", None)}


def test_a_zig_nested_struct_is_owned_and_its_members_are_owned_by_it():
    source = "const Outer = struct {\n    const Inner = struct {\n        q: u8,\n    };\n};\n"
    rows = _rows(source, "a.zig", "zig")
    assert rows["Outer.Inner"] == ("class", "Outer")
    assert rows["Outer.Inner.q"] == ("field", "Inner")


def test_a_matlab_dependent_property_coexists_with_its_getter():
    """Found in review: `_rows` keys on the qualified name, so a `Dependent`
    property and its `get.view` accessor (both `Audit.view`) collapsed to one
    row and the ruling that the getter keeps `method` was asserted nowhere.
    Two symbols, two kinds, two ids, one owner."""
    source = (
        "classdef Audit\n"
        "    properties (Dependent)\n"
        "        view\n"
        "    end\n"
        "    methods\n"
        "        function v = get.view(obj)\n"
        "            v = 1;\n"
        "        end\n"
        "    end\n"
        "end\n"
    )
    symbols = parse_file(source, "a.m", "matlab")
    by_id = {s.id: s for s in symbols}
    views = sorted((s.kind, by_id[s.parent].name) for s in symbols if s.qualified_name == "Audit.view")
    assert views == [("method", "Audit"), ("property", "Audit")]
    assert len({s.id for s in symbols if s.qualified_name == "Audit.view"}) == 2


def test_matlab_multiple_properties_in_one_block_are_all_indexed():
    source = "classdef A\n    properties\n        x\n        y = 2\n    end\nend\n"
    rows = _rows(source, "a.m", "matlab")
    assert rows["A.x"] == ("field", "A") and rows["A.y"] == ("field", "A")
