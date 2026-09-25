"""A Pascal method's implementation is indexed, owned by its class (#844).

`function TAudit.RunIt: Integer; begin ... end;` in the implementation
section yielded nothing. The grammar puts a qualified name under
`declProc > genericDot(identifier TAudit . identifier RunIt)`, and
`_parse_pascal_symbols` asked the `declProc` for a direct `identifier`
child, so every method BODY in a Delphi unit was absent and
`get_symbol_source` on the method returned the one-line declaration.

⚠ Which symbol owns the id: both. Delphi declares a method in the class and
implements it after `implementation`, the model Objective-C has with
`@interface` / `@implementation`, and this extractor already answers that
one (`A.run#method~1` for the declaration, `~2` for the body). Pascal
follows it: the declaration and the implementation are each a `method`
under the same qualified name, parented to the class, and the shared
duplicate-id rule orders them. So the declaration's id MOVES from
`TAudit.RunIt#method` to `TAudit.RunIt#method~1` wherever the method is
implemented in the same unit (named under `PARSER_GENERATION`).

⚠ The owner is the dotted path before the name, read from the whole
`genericDot` chain: a nested class (`TOuter.TInner.Deep`) nests one chain in
another, and a generic owner (`TBox<T>.Get`) wraps its name in
`genericTpl`, whose type parameters belong to the signature, not the name.
"""

import pytest

from jcodemunch_mcp.parser.extractor import parse_file

_UNIT = (
    "unit U;\n"                        # 1
    "interface\n"                      # 2
    "type\n"                           # 3
    "  TAudit = class\n"               # 4
    "    function RunIt: Integer;\n"   # 5
    "  end;\n"                         # 6
    "implementation\n"                 # 7
    "function TAudit.RunIt: Integer;\n"  # 8
    "begin\n"                          # 9
    "  Result := 1;\n"                 # 10
    "end;\n"                           # 11
    "end.\n"                           # 12
)


def _syms(source: str):
    return parse_file(source, "u.pas", "pascal")


def test_the_reported_case_indexes_the_body_as_a_method_of_its_class():
    rows = [(s.qualified_name, s.kind, s.line, s.end_line, s.parent) for s in _syms(_UNIT)]
    assert ("TAudit.RunIt", "method", 5, 5, "u.pas::TAudit#class") in rows, rows
    assert ("TAudit.RunIt", "method", 8, 11, "u.pas::TAudit#class") in rows, rows


def test_the_implementation_carries_the_body():
    (impl,) = [s for s in _syms(_UNIT) if s.qualified_name == "TAudit.RunIt" and s.line == 8]
    body = _UNIT.encode()[impl.byte_offset:impl.byte_offset + impl.byte_length].decode()
    assert "Result := 1;" in body, body
    assert impl.name == "RunIt"
    assert impl.signature == "function TAudit.RunIt: Integer", impl.signature


def test_declaration_and_implementation_have_distinct_ids():
    ids = sorted(s.id for s in _syms(_UNIT) if s.qualified_name == "TAudit.RunIt")
    assert ids == ["u.pas::TAudit.RunIt#method~1", "u.pas::TAudit.RunIt#method~2"], ids


@pytest.mark.parametrize("header, member", [
    ("constructor TAudit.Create;", "Create"),
    ("destructor TAudit.Destroy;", "Destroy"),
    ("procedure TAudit.Go(a: Integer);", "Go"),
    ("class function TAudit.Make: TAudit;", "Make"),
    ("class operator TAudit.Add(a, b: TAudit): TAudit;", "Add"),
])
def test_every_routine_kind_in_the_implementation(header, member):
    source = (
        "unit U;\ninterface\ntype\n  TAudit = class\n  end;\n"
        f"implementation\n{header}\nbegin\nend;\nend.\n"
    )
    rows = {(s.qualified_name, s.kind, s.parent) for s in _syms(source)}
    assert (f"TAudit.{member}", "method", "u.pas::TAudit#class") in rows, rows


def test_a_nested_class_owns_its_implementation():
    source = (
        "unit U;\ninterface\ntype\n  TOuter = class\n  type\n    TInner = class\n"
        "      function Deep: Integer;\n    end;\n  end;\n"
        "implementation\nfunction TOuter.TInner.Deep: Integer;\nbegin\nend;\nend.\n"
    )
    rows = {(s.qualified_name, s.kind, s.parent, s.line) for s in _syms(source)}
    assert ("TOuter.TInner", "class", "u.pas::TOuter#class", 6) in rows, rows
    assert ("TOuter.TInner.Deep", "method", "u.pas::TOuter.TInner#class", 11) in rows, rows


# ---------------------------------------------------------------------------
# Review round 1: the same direct-`identifier` guard in the other readers. A
# generic type (#846) or routine wraps its name in `genericTpl`, and a helper
# type's body (`declHelper`) was never walked. The first draft's generic test
# declared no class, so it could not see that the owner was absent.
# ---------------------------------------------------------------------------

def test_a_generic_owner_is_named_without_its_parameters():
    """#846: `TBox<T> = class` was absent entirely; it is `TBox`, and it owns
    its field, its method declaration and the body."""
    source = (
        "unit U;\ninterface\ntype\n  TBox<T> = class\n    Value: T;\n    function Get: T;\n  end;\n"
        "implementation\nfunction TBox<T>.Get: T;\nbegin\nend;\nend.\n"
    )
    rows = {(s.qualified_name, s.kind, s.parent, s.line) for s in _syms(source)}
    assert rows == {
        ("TBox", "class", None, 4),
        ("TBox.Value", "field", "u.pas::TBox#class", 5),
        ("TBox.Get", "method", "u.pas::TBox#class", 6),
        ("TBox.Get", "method", "u.pas::TBox#class", 9),
    }, rows


def test_a_generic_type_with_several_parameters_is_named_without_them():
    source = "unit U;\ninterface\ntype\n  TPair<K, V> = record\n    Key: K;\n  end;\nimplementation\nend.\n"
    syms = _syms(source)
    (owner,) = [s for s in syms if s.name == "TPair"]
    assert owner.qualified_name == "TPair"
    assert [(s.qualified_name, s.parent) for s in syms if s.kind == "field"] == [("TPair.Key", owner.id)]


def test_a_generic_method_is_declared_and_implemented():
    source = (
        "unit U;\ninterface\ntype\n  TA = class\n    function F<T>: T;\n  end;\n"
        "implementation\nfunction TA.F<T>: T;\nbegin\nend;\nend.\n"
    )
    rows = {(s.qualified_name, s.kind, s.parent, s.line) for s in _syms(source)}
    assert ("TA.F", "method", "u.pas::TA#class", 5) in rows, rows
    assert ("TA.F", "method", "u.pas::TA#class", 8) in rows, rows


def test_a_nested_generic_owner():
    source = (
        "unit U;\ninterface\ntype\n  TO = class\n  type\n    TI<T> = class\n      procedure P;\n    end;\n  end;\n"
        "implementation\nprocedure TO.TI<T>.P;\nbegin\nend;\nend.\n"
    )
    rows = {(s.qualified_name, s.kind, s.parent, s.line) for s in _syms(source)}
    assert ("TO.TI.P", "method", "u.pas::TO.TI#class", 11) in rows, rows
    # `TI<T>` was skipped, so its member was filed under the OUTER class.
    assert not any(q == "TO.P" for q, *_ in rows), rows


@pytest.mark.parametrize("helper", ["class helper for TA", "record helper for Integer"])
def test_a_helper_owns_its_members_and_bodies(helper):
    source = (
        f"unit U;\ninterface\ntype\n  TH = {helper}\n    procedure P;\n  end;\n"
        "implementation\nprocedure TH.P;\nbegin\nend;\nend.\n"
    )
    rows = {(s.qualified_name, s.kind, s.parent, s.line) for s in _syms(source)}
    assert rows == {
        ("TH", "type", None, 4),
        ("TH.P", "method", "u.pas::TH#type", 5),
        ("TH.P", "method", "u.pas::TH#type", 8),
    }, rows


@pytest.mark.parametrize("head", ["TBox<T> = class", "TBox = class helper for TA", "TBox = record helper for Integer"])
def test_what_a_generic_or_helper_body_declares_is_owned_not_filed_one_scope_up(head):
    """Review round 2: the walk skipped a generic type or a helper, then walked
    its body with the ENCLOSING owner, so a `const` and a nested type in it
    were indexed at file scope (`C#constant`, `TIn#class`). Each moves under
    its owner; the bare spellings must be gone."""
    source = (
        f"unit U;\ninterface\ntype\n  {head}\n    const C = 1;\n  type\n"
        "    TIn = class\n      procedure Q;\n    end;\n  end;\nimplementation\nend.\n"
    )
    syms = _syms(source)
    (owner,) = [s for s in syms if s.qualified_name == "TBox"]
    rows = {(s.qualified_name, s.kind, s.parent) for s in syms}
    assert ("TBox.C", "constant", owner.id) in rows, rows
    assert ("TBox.TIn", "class", owner.id) in rows, rows
    assert ("TBox.TIn.Q", "method", "u.pas::TBox.TIn#class") in rows, rows
    assert not {q for q, _k, _p in rows} & {"C", "TIn", "TIn.Q"}, rows


def test_arity_twins_share_a_name_and_differ_by_signature():
    """Review round 3: `TProc`, `TProc<T>` and `TProc<T1, T2>` (Delphi's
    SysUtils shape) are all named `TProc`, as C# names `Action<T>`, so they
    are ordinal twins in document order and the signature carries the arity."""
    source = (
        "unit U;\ninterface\ntype\n  TProc = procedure;\n  TProc<T> = procedure(a: T);\n"
        "  TProc<T1, T2> = procedure(a: T1; b: T2);\nimplementation\nend.\n"
    )
    rows = [(s.id, s.signature) for s in _syms(source)]
    assert rows == [
        ("u.pas::TProc#type~1", "type TProc"),
        ("u.pas::TProc#type~2", "type TProc<T>"),
        ("u.pas::TProc#type~3", "type TProc<T1, T2>"),
    ], rows


def test_a_free_generic_function_is_named_without_its_parameters():
    source = "unit U;\ninterface\nimplementation\nfunction Max<T>(a, b: T): T;\nbegin\nend;\nend.\n"
    rows = [(s.id, s.kind) for s in _syms(source)]
    assert rows == [("u.pas::Max#function", "function")], rows


def test_a_record_owns_its_implementation():
    source = (
        "unit U;\ninterface\ntype\n  TRec = record\n    function Sum: Integer;\n  end;\n"
        "implementation\nfunction TRec.Sum: Integer;\nbegin\nend;\nend.\n"
    )
    syms = _syms(source)
    # The grammar parses a record WITH methods as `declClass`, so its kind is
    # whatever the interface section already gave it; the body follows it.
    (owner,) = [s for s in syms if s.qualified_name == "TRec"]
    rows = {(s.qualified_name, s.kind, s.parent, s.line) for s in syms}
    assert ("TRec.Sum", "method", owner.id, 8) in rows, rows


def test_a_free_function_is_unchanged():
    source = "unit U;\ninterface\nfunction Free: Integer;\nimplementation\nfunction Free: Integer;\nbegin\nend;\nend.\n"
    rows = [(s.id, s.kind, s.parent) for s in _syms(source)]
    assert rows == [("u.pas::Free#function", "function", None)], rows
