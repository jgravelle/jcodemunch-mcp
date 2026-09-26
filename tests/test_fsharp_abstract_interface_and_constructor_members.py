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
