"""Pascal, F# and Nim: a class's members are indexed, and each is owned (#812).

Three custom parsers indexed the container and stopped: no field, no
method, no property. `TAudit = class ... end` gave `[TAudit class]`; F#'s
`type Audit() = ... member this.RunIt() = 1`, the language's own member
syntax, gave `[Audit type]`; Nim's `type Audit = object; tally: int` gave
`[Audit type]`. Zig, PowerShell and MATLAB (#809/#811) lost only the state;
here the walk never entered the body, so each parser needs a body walk
before it needs a member predicate (the issue's own distinction).

The walk threads the owner SYMBOL and asks `_member_of` (#788's one helper)
for both halves of a member's identity, so every member is qualified AND
carries `parent`. None of the three had a row in the member-kind audit,
which is why nothing enumerated the hole; three rows join it here.

Rulings, per language, because a member-kind row is a design task, not a
copy (the audit's own words):
- Pascal: a `declField` (N names) and a `class var` are `field`; a class-
  scoped `const` is `constant`; `procedure`/`function`/`constructor`/
  `destructor`/`class function` declared in the class are `method`;
  `property` is `property`. A `record`'s fields are owned the same way
  (the parser already calls a record `class`; unchanged).
- F#: `let mutable` in a type is `field`, `let` is `constant`, a `let`-bound
  function is `method` (a private method, which is how it compiles);
  `member this.M(args)` is `method`; `member this.P with get`, `member val`
  and an argument-less `member` or `static member` are `property` (F#
  semantics: a member without a parameter list is a property).
- Nim: an `object`'s fields are `field`, the export marker `*` stripped, in
  every branch of a `case` variant and behind `ref`/`ptr`. A `proc` taking
  the type as its first parameter stays a module-level `function`: Nim has
  no member-method declaration (UFCS is call syntax, not membership), so the
  audit's method role is omitted with that reason. No const or property
  member exists in an object.

Ids that move, named under `PARSER_GENERATION`: a Pascal class-scoped
`const` was emitted BARE (`LIMIT`, no owner) and is `TAudit.LIMIT` now.
Every other pre-existing qualified name is byte-identical.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.parser.extractor import parse_file


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    """Answer the parser, not the developer's config file (#411; nim is
    disabled in this box's config)."""
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def _rows(source: str, filename: str, language: str):
    symbols = parse_file(source, filename, language)
    by_id = {s.id: s for s in symbols}
    return {
        s.qualified_name: (s.kind, by_id[s.parent].name if s.parent else None)
        for s in symbols
    }


_PAS = (
    "unit U;\n"
    "interface\n"
    "type\n"
    "  TAudit = class\n"
    "  private\n"
    "    FTally, FOther: Integer;\n"
    "  public\n"
    "    const LIMIT = 3;\n"
    "    class var Shared: Integer;\n"
    "    constructor Create;\n"
    "    function RunIt: Integer;\n"
    "    class function Make: TAudit;\n"
    "    property Tally: Integer read FTally;\n"
    "  end;\n"
    "implementation\n"
    "end.\n"
)
_FS = (
    "type Audit() =\n"
    "    let mutable tally = 0\n"
    "    let limit = 3\n"
    "    let helper x = x + 1\n"
    "    member this.RunIt() = tally\n"
    "    member this.Count with get() = tally\n"
    "    member val Size = 0 with get, set\n"
    "    static member Zero = 0\n"
)
_NIM = (
    "type\n"
    "  Audit* = object\n"
    "    tally*: int\n"
    "    limit: int\n"
    "proc runIt(a: Audit): int = a.tally\n"
)


# --- the reported shapes ------------------------------------------------------

def test_a_pascal_class_method_is_owned():
    assert _rows(_PAS, "a.pas", "pascal")["TAudit.RunIt"] == ("method", "TAudit")


def test_an_fsharp_member_is_owned():
    assert _rows(_FS, "a.fs", "fsharp")["Audit.RunIt"] == ("method", "Audit")


def test_a_nim_object_field_is_owned():
    assert _rows(_NIM, "a.nim", "nim")["Audit.tally"] == ("field", "Audit")


# --- the whole answer, per language, and nothing else -------------------------

def test_the_pascal_class_answers_every_row_and_nothing_else():
    assert _rows(_PAS, "a.pas", "pascal") == {
        "TAudit": ("class", None),
        "TAudit.FTally": ("field", "TAudit"),
        "TAudit.FOther": ("field", "TAudit"),
        "TAudit.LIMIT": ("constant", "TAudit"),
        "TAudit.Shared": ("field", "TAudit"),
        "TAudit.Create": ("method", "TAudit"),
        "TAudit.RunIt": ("method", "TAudit"),
        "TAudit.Make": ("method", "TAudit"),
        "TAudit.Tally": ("property", "TAudit"),
    }


def test_the_fsharp_type_answers_every_row_and_nothing_else():
    assert _rows(_FS, "a.fs", "fsharp") == {
        "Audit": ("type", None),
        "Audit.tally": ("field", "Audit"),
        "Audit.limit": ("constant", "Audit"),
        "Audit.helper": ("method", "Audit"),
        "Audit.RunIt": ("method", "Audit"),
        "Audit.Count": ("property", "Audit"),
        "Audit.Size": ("property", "Audit"),
        "Audit.Zero": ("property", "Audit"),
    }


def test_the_nim_object_answers_every_row_and_nothing_else():
    assert _rows(_NIM, "a.nim", "nim") == {
        "Audit": ("type", None),
        "Audit.tally": ("field", "Audit"),
        "Audit.limit": ("field", "Audit"),
        "runIt": ("function", None),
    }


# --- the other spellings ----------------------------------------------------------

def test_a_pascal_record_owns_its_fields():
    source = "type\n  TRec = record\n    X, Y: Integer;\n  end;\n"
    rows = _rows(source, "a.pas", "pascal")
    assert rows["TRec.X"] == ("field", "TRec")
    assert rows["TRec.Y"] == ("field", "TRec")


def test_a_pascal_destructor_and_procedure_are_methods():
    source = (
        "type\n  TA = class\n    procedure Go;\n    destructor Destroy; override;\n  end;\n"
    )
    rows = _rows(source, "a.pas", "pascal")
    assert rows["TA.Go"] == ("method", "TA")
    assert rows["TA.Destroy"] == ("method", "TA")


def test_a_nim_variant_object_owns_the_fields_of_every_branch():
    source = (
        "type\n"
        "  Shape = object\n"
        "    case kind: bool\n"
        "    of true: r: int\n"
        "    else: w, h: int\n"
    )
    rows = _rows(source, "a.nim", "nim")
    for name in ("kind", "r", "w", "h"):
        assert rows[f"Shape.{name}"] == ("field", "Shape"), name


def test_a_nim_ref_object_with_a_base_owns_its_fields():
    source = "type\n  Base = ref object of RootObj\n    q*: int\n"
    assert _rows(source, "a.nim", "nim")["Base.q"] == ("field", "Base")


def test_a_nim_ptr_object_owns_its_fields():
    """Found in review: the grammar spells `ptr object` as `pointer_type`,
    and the first draft asked for `ptr_type`, so `ptr` yielded no fields
    while the claim said otherwise (Standing lesson 09-01)."""
    source = "type\n  P = ptr object\n    a*, b: int\n"
    rows = _rows(source, "a.nim", "nim")
    assert rows["P.a"] == ("field", "P")
    assert rows["P.b"] == ("field", "P")


def test_fsharp_static_member_val_is_a_property_named_from_its_pattern():
    """Found in review: the grammar takes `val` as the member name and binds
    `Total` as its arguments, so the first draft fabricated `A.val` as a
    `method`. The grammar also spills `with get, set` to file level and loses
    every member after it (filed); this pins the one it can name."""
    source = "type A() =\n    static member val Total = 0 with get, set\n"
    rows = _rows(source, "a.fs", "fsharp")
    assert rows["A.Total"] == ("property", "A")
    assert "A.val" not in rows


def test_fsharp_member_val_with_an_accessibility_modifier_is_named_after_the_name():
    """Found in review, round 3: `val private Count` puts the modifier in a
    pattern of its own ahead of the name, and the first draft read the first
    pattern, publishing `E.private` (Standing lesson 09-01 a second time)."""
    rows = _rows("type E() =\n    static member val private Count = 0 with get, set\n", "a.fs", "fsharp")
    assert rows["E.Count"] == ("property", "E")
    assert "E.private" not in rows and "E.val" not in rows
    rows = _rows("type F() =\n    member val private Size = 0 with get, set\n", "a.fs", "fsharp")
    assert rows["F.Size"] == ("property", "F")
    assert "F.private" not in rows


def test_fsharp_static_let_state_is_owned():
    """Found in review: `static let` sits under `member_defn >
    value_declaration`, not directly under the type body, and was unread."""
    source = "type A() =\n    static let mutable count = 0\n    static let cache = 1\n"
    rows = _rows(source, "a.fs", "fsharp")
    assert rows["A.count"] == ("field", "A")
    assert rows["A.cache"] == ("constant", "A")


def test_an_fsharp_indexer_property_is_a_property():
    source = "type A() =\n    member this.Item with get(i) = i\n"
    assert _rows(source, "a.fs", "fsharp")["A.Item"] == ("property", "A")


def test_an_fsharp_record_member_is_owned_by_the_record():
    source = "type Point = { X: int; Y: int } with member p.Sum() = p.X + p.Y\n"
    assert _rows(source, "a.fs", "fsharp")["Point.Sum"] == ("method", "Point")


# --- what does not move ---------------------------------------------------------

def test_file_scope_symbols_are_unchanged():
    pas = (
        "unit U;\ninterface\nconst\n  MAX = 10;\nimplementation\n"
        "function Top: Integer;\nbegin\n  Result := 1;\nend;\nend.\n"
    )
    assert _rows(pas, "a.pas", "pascal") == {"MAX": ("constant", None), "Top": ("function", None)}
    assert _rows("let top x = x\nlet n = 1\n", "a.fs", "fsharp") == {
        "top": ("function", None), "n": ("constant", None),
    }
    assert _rows("proc top() = discard\nconst N = 1\n", "a.nim", "nim") == {
        "top": ("function", None), "N": ("constant", None),
    }


def test_a_nim_proc_taking_the_type_is_not_re_parented():
    """UFCS is call syntax, not membership: `a.runIt` is `runIt(a)`."""
    rows = _rows(_NIM, "a.nim", "nim")
    assert rows["runIt"] == ("function", None)
    assert "Audit.runIt" not in rows
