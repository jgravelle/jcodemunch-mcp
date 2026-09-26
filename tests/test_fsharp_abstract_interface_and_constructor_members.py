"""F# abstract members, interface implementations and secondary constructors
are indexed (#845, F# half).

#812 gave `_parse_fsharp_symbols` a member walk that reads
`function_or_value_defn`, `member_defn > method_or_prop_defn`,
`member_defn > property_or_ident` and `member_defn > value_declaration`.
Three member forms sit under other nodes and were never read:

- `abstract Area : float` is `member_defn > abstract + member_signature`, so
  an interface type (`type IShape = abstract ...`) indexed as an empty type
  and a class's abstract slot was absent.
- `interface IDisposable with member this.Dispose() = ()` is an
  `interface_implementation`, a sibling of `member_defn`, never entered.
- `new() = C(0)` is `member_defn > additional_constr_defn`.

⚠ Kinds follow #812's rule for concrete members: an abstract member with an
argument list (`abstract Scale : float -> IShape`) is a `method`, without one
(`abstract Area : float`) a `property`. An interface implementation's members
are owned by the enclosing type. A secondary constructor is a `method` named
after its type (`C.C`), as C#, Java and PowerShell constructors index.
"""

import pytest

from jcodemunch_mcp.parser.extractor import parse_file


def _rows(source: str) -> set[tuple[str, str, str | None]]:
    return {(s.qualified_name, s.kind, s.parent) for s in parse_file(source, "a.fs", "fsharp")}


def test_an_interface_type_owns_its_abstract_members():
    source = "type IShape =\n    abstract Area : float\n    abstract Scale : float -> IShape\n"
    assert _rows(source) == {
        ("IShape", "type", None),
        ("IShape.Area", "property", "a.fs::IShape#type"),
        ("IShape.Scale", "method", "a.fs::IShape#type"),
    }


@pytest.mark.parametrize("decl, kind", [
    ("abstract Name : string", "property"),
    ("abstract member Name : string", "property"),
    ("abstract Name : string with get, set", "property"),
    ("abstract Run : unit -> unit", "method"),
    ("abstract Add : int * int -> int", "method"),
])
def test_an_abstract_member_in_a_class(decl, kind):
    source = f"type C(x: int) =\n    {decl}\n    member this.X = x\n"
    assert ("C.Name" if "Name" in decl else "C." + decl.split()[1], kind, "a.fs::C#type") in _rows(source)


def test_an_interface_implementation_s_members_are_owned_by_the_enclosing_type():
    source = (
        "type C() =\n    member this.X = 1\n    interface System.IDisposable with\n"
        "        member this.Dispose() = ()\n"
    )
    assert _rows(source) == {
        ("C", "type", None),
        ("C.X", "property", "a.fs::C#type"),
        ("C.Dispose", "method", "a.fs::C#type"),
    }


def test_a_secondary_constructor_is_a_method_named_after_its_type():
    source = "type C(x: int) =\n    new() = C(0)\n    member this.X = x\n"
    assert ("C.C", "method", "a.fs::C#type") in _rows(source)


def test_an_abstract_slot_and_its_default_are_ordinal_twins():
    """The declaration and the implementation share a qualified name and kind,
    as Pascal's and Objective-C's do, so the duplicate-id rule orders them."""
    source = 'type C() =\n    abstract Name : string\n    default this.Name = "c"\n'
    ids = sorted((s.id, s.line) for s in parse_file(source, "a.fs", "fsharp") if s.name == "Name")
    assert ids == [("a.fs::C.Name#property~1", 2), ("a.fs::C.Name#property~2", 3)], ids


# ---------------------------------------------------------------------------
# Review round 1: the other spellings of the same members. `interface ... end`
# is `interface_type_defn` and was not in the defn list, so the reported
# interface type indexed as NOTHING in that spelling; `struct ... end` puts
# its members directly under the definition; `delegate of` was absent; and an
# indexer (`with get`) is a property whatever its argument list says.
# ---------------------------------------------------------------------------

def test_an_interface_end_type_is_indexed_with_its_members():
    source = "type I =\n    interface\n        abstract M : int -> int\n        abstract P : int\n    end\n"
    assert _rows(source) == {
        ("I", "type", None),
        ("I.M", "method", "a.fs::I#type"),
        ("I.P", "property", "a.fs::I#type"),
    }


def test_a_struct_end_body_is_read():
    source = (
        "type S =\n    struct\n        val X : int\n        new(x) = { X = x }\n"
        "        member this.Get() = this.X\n    end\n"
    )
    rows = _rows(source)
    assert ("S.S", "method", "a.fs::S#type") in rows, rows
    assert ("S.Get", "method", "a.fs::S#type") in rows, rows


def test_a_delegate_type_is_indexed():
    assert _rows("type D = delegate of int -> int\n") == {("D", "type", None)}


def test_an_abstract_indexer_is_a_property_and_twins_its_default():
    source = (
        "type C() =\n    abstract Item : int -> string with get\n"
        "    default this.Item with get(i) = string i\n"
    )
    ids = sorted(s.id for s in parse_file(source, "a.fs", "fsharp") if s.name == "Item")
    assert ids == ["a.fs::C.Item#property~1", "a.fs::C.Item#property~2"], ids


def test_two_constructors_are_ordinal_twins():
    source = "type C(x: int) =\n    new() = C(0)\n    new(s: string) = C(int s)\n    member this.X = x\n"
    ids = sorted((s.id, s.line) for s in parse_file(source, "a.fs", "fsharp") if s.name == "C" and s.kind == "method")
    assert ids == [("a.fs::C.C#method~1", 2), ("a.fs::C.C#method~2", 3)], ids


def test_a_type_chained_to_an_interface_end_type_spans_its_own_definition():
    """Review round 2: with `interface_type_defn` counted, the chain holds two
    definitions, so #837's rule gives each its own node's span; the class no
    longer spans (and signs with) the whole `type ... and ...` statement."""
    source = "type C() =\n    member this.M() = 1\nand I =\n    interface\n        abstract N : int\n    end\n"
    (c,) = [s for s in parse_file(source, "a.fs", "fsharp") if s.qualified_name == "C"]
    assert (c.line, c.end_line) == (1, 2), (c.line, c.end_line)
