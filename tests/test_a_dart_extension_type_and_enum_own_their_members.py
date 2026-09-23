"""A Dart `extension type` is a symbol, and an `enum` body's data is owned (#819, #820).

Two absences in one spec, one container list.

#819: `extension_type_declaration` was in `DART_SPEC.symbol_node_types`
nowhere and in `container_node_types` nowhere, so `extension type Meters(int
v) { ... }` was absent, its getters and methods were published BARE
(`parent=None`, colliding in ranking with every other `doubled`), and its
`static const` was withheld by #818's owner gate rather than published bare
too. #698's shape (TypeScript's `abstract_class_declaration`) one language
over: a node type a spec does not name looks like an absence of the thing.

#820: `enum_declaration` was in `symbol_node_types` and `type_patterns` and
NOT in `container_node_types`, the list #818's member gate asks, so an enum
body's methods were owned (they reach their owner by the walk's parent
chain) while its `static const` and `final` members were absent -- an
inconsistent answer inside one construct, which invites more trust than a
missing one.

Rulings, written down because both issues asked:
- An `extension type` is a `type`, not a `class`: it is Dart 3.3's zero-cost
  wrapper over a representation type, erased at runtime; `enum` and
  `type_alias` are `type` here already and `extension_declaration` (which
  adds methods to an existing type) is the `class` outlier, left alone.
- The representation (`int v`) is a `field` owned by the extension type: it
  is the type's only state and every member reads it. It has no
  `declaration` node, so `representation_declaration` is its own
  `field_patterns` entry.
- Enum VARIANTS (`a`, `b`) are NOT indexed. No spec indexes enum variants
  today (PHP's cases are #759, open); giving Dart alone a second answer to
  a family-wide question is the second-derivation shape, so the family
  decides it once, there.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.parser.extractor import parse_file


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    """Answer the parser, not the developer's config file (#411)."""
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def _rows(source: str):
    symbols = parse_file(source, "a.dart", "dart")
    by_id = {s.id: s for s in symbols}
    return {
        s.qualified_name: (s.kind, by_id[s.parent].name if s.parent else None)
        for s in symbols
    }


_EXTENSION_TYPE = (
    "extension type Meters(int v) {\n"
    "  int get doubled => v * 2;\n"
    "  static const int CAP = 1;\n"
    "  void shout() {}\n"
    "}\n"
)

_ENUM = (
    "enum E {\n"
    "  a, b;\n"
    "  static const int CAP = 1;\n"
    "  final int w = 2;\n"
    "  int get v => 1;\n"
    "  void m() {}\n"
    "}\n"
)


# --- #819 -------------------------------------------------------------------

def test_an_extension_type_is_a_type_symbol():
    rows = _rows(_EXTENSION_TYPE)
    assert rows["Meters"] == ("type", None)


def test_an_extension_types_getter_and_method_are_owned():
    rows = _rows(_EXTENSION_TYPE)
    assert rows["Meters.doubled"] == ("method", "Meters")
    assert rows["Meters.shout"] == ("method", "Meters")
    assert "doubled" not in rows and "shout" not in rows


def test_an_extension_types_static_const_is_a_constant_it_owns():
    rows = _rows(_EXTENSION_TYPE)
    assert rows["Meters.CAP"] == ("constant", "Meters")


def test_the_representation_is_a_field_the_extension_type_owns():
    """Decided, not defaulted: `int v` is the type's only state."""
    rows = _rows(_EXTENSION_TYPE)
    assert rows["Meters.v"] == ("field", "Meters")


def test_an_extension_type_answers_the_reported_rows_and_nothing_else():
    assert _rows(_EXTENSION_TYPE) == {
        "Meters": ("type", None),
        "Meters.v": ("field", "Meters"),
        "Meters.doubled": ("method", "Meters"),
        "Meters.CAP": ("constant", "Meters"),
        "Meters.shout": ("method", "Meters"),
    }


# --- #820 -------------------------------------------------------------------

def test_an_enum_bodys_static_const_is_a_constant_it_owns():
    rows = _rows(_ENUM)
    assert rows["E.CAP"] == ("constant", "E")


def test_an_enum_bodys_final_member_is_a_field_it_owns():
    """`final` is a field because Dart has `const` to reserve `constant` for
    (#775's rule, shared, not restated)."""
    rows = _rows(_ENUM)
    assert rows["E.w"] == ("field", "E")


def test_an_enum_bodys_methods_are_still_owned_by_the_same_mechanism():
    rows = _rows(_ENUM)
    assert rows["E.v"] == ("method", "E")
    assert rows["E.m"] == ("method", "E")


def test_enum_variants_are_not_indexed_and_that_is_a_ruling():
    """See the module docstring: no spec indexes variants; the family decides
    once (#759)."""
    rows = _rows(_ENUM)
    assert "E.a" not in rows and "E.b" not in rows and "a" not in rows
    assert _rows("enum Plain { x, y }\n") == {"Plain": ("type", None)}


def test_an_enum_answers_the_reported_rows_and_nothing_else():
    assert _rows(_ENUM) == {
        "E": ("type", None),
        "E.CAP": ("constant", "E"),
        "E.w": ("field", "E"),
        "E.v": ("method", "E"),
        "E.m": ("method", "E"),
    }


# --- the property, and what does not move ----------------------------------

def test_every_dart_member_has_an_owner():
    """#698's complaint, as a property over every container Dart has."""
    source = (
        "class C { int a = 1; int get g => a; void m() {} }\n"
        "mixin M { int b = 2; void n() {} }\n"
        "extension X on String { int get l => 1; }\n"
        + _EXTENSION_TYPE + _ENUM
    )
    for s in parse_file(source, "a.dart", "dart"):
        if s.kind in ("method", "field", "constant", "property"):
            assert s.parent is not None, (s.kind, s.qualified_name)


#: One sample per container in `DART_SPEC.container_node_types`, each holding
#: a `static const` member. The ratchet below fails when the list gains an
#: entry with no row here, so a new container cannot withhold its data in
#: silence -- the shape the first draft's second list (`_DART_MEMBER_HOLDERS`)
#: would have allowed, and review refused.
_A_MEMBER_IN_EVERY_CONTAINER = {
    "class_definition": ("class Holder { static const int CAP = 1; }\n", "Holder"),
    "mixin_declaration": ("mixin Holder { static const int CAP = 1; }\n", "Holder"),
    "extension_declaration": ("extension Holder on String { static const int CAP = 1; }\n", "Holder"),
    "extension_type_declaration": ("extension type Holder(int v) { static const int CAP = 1; }\n", "Holder"),
    "enum_declaration": ("enum Holder { a; static const int CAP = 1; }\n", "Holder"),
}


def test_every_container_in_the_spec_owns_a_member_and_every_container_is_sampled():
    """The gate asks the container list and the grammar's `body` field, and
    keeps no list of its own; this is what makes that true for the NEXT
    container too."""
    from jcodemunch_mcp.parser.languages import DART_SPEC

    assert set(DART_SPEC.container_node_types) == set(_A_MEMBER_IN_EVERY_CONTAINER), (
        "a container in DART_SPEC has no member sample here (or a sample names no container)"
    )
    for node_type, (source, owner) in _A_MEMBER_IN_EVERY_CONTAINER.items():
        rows = _rows(source)
        assert rows.get(f"{owner}.CAP") == ("constant", owner), (node_type, rows)


def test_a_class_a_mixin_and_an_extension_are_unchanged():
    """The three containers #818 already answered, byte for byte in shape."""
    assert _rows("class C { int a = 1; static const int K = 1; void m() {} }\n") == {
        "C": ("class", None), "C.a": ("field", "C"), "C.K": ("constant", "C"), "C.m": ("method", "C"),
    }
    assert _rows("mixin M { int b = 2; void n() {} }\n") == {
        "M": ("class", None), "M.b": ("field", "M"), "M.n": ("method", "M"),
    }
    assert _rows("extension X on String { int get l => 1; }\n") == {
        "X": ("class", None), "X.l": ("method", "X"),
    }


def test_a_top_level_function_and_a_local_are_unchanged():
    rows = _rows("int top() { final int local = 1; return local; }\n")
    assert rows == {"top": ("function", None)}
