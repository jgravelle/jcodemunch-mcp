"""A Pascal interface's members are indexed, owned by the interface (#845, Pascal half).

`IFoo = interface procedure Bar; property Q: Integer read GetQ; end;` yielded
`type IFoo` and nothing in it. #812 gave `_parse_pascal_symbols` a body walk
for the containers it names, `declClass` and `declRecord` (and #844 added
`declHelper`), and the grammar spells an interface body `declIntf`, so it
was never entered: a grammar node the parser never names reads as the
language having no such thing (Standing lesson 09-15).

⚠ The interface keeps its kind, `type`, so its id does not move. A method
declared in an interface is a `method` (it has no body to tell it apart from
a class method's declaration), and a property is a `property`. A
`dispinterface` is the same node and reads the same way.

⚠ The F# half of #845 (abstract members, `interface ... with`, `new()`) is a
separate change in a separate parser.
"""

import pytest

from jcodemunch_mcp.parser.extractor import parse_file


def _rows(source: str) -> set[tuple[str, str, str | None]]:
    return {(s.qualified_name, s.kind, s.parent) for s in parse_file(source, "u.pas", "pascal")}


def test_the_reported_case():
    source = (
        "unit U;\ninterface\ntype\n  IFoo = interface\n    procedure Bar;\n"
        "    function GetQ: Integer;\n    property Q: Integer read GetQ;\n  end;\nimplementation\nend.\n"
    )
    assert _rows(source) == {
        ("IFoo", "type", None),
        ("IFoo.Bar", "method", "u.pas::IFoo#type"),
        ("IFoo.GetQ", "method", "u.pas::IFoo#type"),
        ("IFoo.Q", "property", "u.pas::IFoo#type"),
    }


def test_a_guid_is_not_a_member():
    source = (
        "unit U;\ninterface\ntype\n  IFoo = interface\n"
        "    ['{00000000-0000-0000-0000-000000000000}']\n    procedure Bar;\n  end;\nimplementation\nend.\n"
    )
    assert _rows(source) == {("IFoo", "type", None), ("IFoo.Bar", "method", "u.pas::IFoo#type")}


@pytest.mark.parametrize("head, owner", [
    ("IGen<T> = interface(IFoo)", "IGen"),
    ("IDisp = dispinterface", "IDisp"),
])
def test_a_generic_interface_and_a_dispinterface(head, owner):
    source = f"unit U;\ninterface\ntype\n  {head}\n    procedure M;\n  end;\nimplementation\nend.\n"
    assert (f"{owner}.M", "method", f"u.pas::{owner}#type") in _rows(source)


def test_the_interface_it_extends_is_not_a_member():
    source = "unit U;\ninterface\ntype\n  IB = interface(IFoo)\n    procedure M;\n  end;\nimplementation\nend.\n"
    assert _rows(source) == {("IB", "type", None), ("IB.M", "method", "u.pas::IB#type")}


# ---------------------------------------------------------------------------
# Review round 1: an interface nested in a class or record. The walk did not
# enter it but walked its body with the ENCLOSING owner, so its members were
# indexed as the class's own (`TOuter.Foo`). They move one scope down, and a
# class member that shares a name is no longer an ordinal twin of them.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("outer", ["class", "record", "class helper for TA"])
def test_a_nested_interface_owns_its_members_not_the_enclosing_type(outer):
    source = (
        f"unit U;\ninterface\ntype\n  TOuter = {outer}\n  type\n    IInner = interface\n"
        "      procedure Foo;\n      property P: Integer read GetP;\n    end;\n  end;\nimplementation\nend.\n"
    )
    # A record holding a nested type parses as `declClass` (LEDGER L-09), so
    # read the owner's id rather than assume its kind.
    (owner,) = [s for s in parse_file(source, "u.pas", "pascal") if s.qualified_name == "TOuter"]
    rows = _rows(source)
    inner = "u.pas::TOuter.IInner#type"
    assert ("TOuter.IInner", "type", owner.id) in rows, rows
    assert ("TOuter.IInner.Foo", "method", inner) in rows, rows
    assert ("TOuter.IInner.P", "property", inner) in rows, rows
    assert not {q for q, _k, _p in rows} & {"TOuter.Foo", "TOuter.P"}, rows


def test_a_class_member_sharing_a_name_with_its_nested_interface_member_is_not_its_twin():
    source = (
        "unit U;\ninterface\ntype\n  TOuter = class\n  type\n    IInner = interface\n"
        "      procedure M;\n    end;\n  public\n    procedure M;\n  end;\nimplementation\nend.\n"
    )
    ids = sorted(s.id for s in parse_file(source, "u.pas", "pascal") if s.name == "M")
    assert ids == ["u.pas::TOuter.IInner.M#method", "u.pas::TOuter.M#method"], ids
