"""A PHP enum case is a symbol of its enum (#759).

`parse_file('<?php enum Suit { case Hearts; case Spades; }')` returned only
`Suit`. The grammar spells a case `enum_case`, and no `PHP_SPEC` map named
that node type, so the cases -- usually the only thing an enum contains --
were absent. #698's shape: a construct the grammar names and the spec does not.

⚠ The kind is `constant`, pure or backed. It is what the same enum's `const`
already is (`Suit.X`, since #744) and what Python's enum members are (`E.A`),
the only other language whose enum members are indexed. A pure case and a
backed case differ in their value, not in what they are to a reader: a named,
fixed member of the enum.

⚠ The case is OWNED (`Suit.Hearts`): `enum_declaration` is a container since
#744, so a bare `Hearts` would be a second asymmetry beside the one #746
recorded.
"""

import pytest

from jcodemunch_mcp.parser.extractor import parse_file


@pytest.fixture(autouse=True)
def _php_enabled(monkeypatch):
    """Patch `jcodemunch_mcp.config`: this box's config disables languages (see test_php_members)."""
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def _rows(source: str) -> list[tuple[str, str, str, int]]:
    return [(s.name, s.kind, s.qualified_name, s.line) for s in parse_file(source, "a.php", "php")]


def test_the_reported_case():
    rows = _rows("<?php\nenum Suit {\n  case Hearts;\n  case Spades;\n}\n")
    assert ("Hearts", "constant", "Suit.Hearts", 3) in rows, rows
    assert ("Spades", "constant", "Suit.Spades", 4) in rows, rows


def test_a_backed_case_is_the_same_kind():
    rows = _rows("<?php\nenum Suit: string {\n  case Hearts = 'H';\n  case Spades = 'S';\n}\n")
    assert ("Hearts", "constant", "Suit.Hearts", 3) in rows, rows
    assert ("Spades", "constant", "Suit.Spades", 4) in rows, rows


def test_cases_beside_a_const_a_method_and_an_interface():
    source = (
        "<?php\n"
        "namespace App;\n"
        "interface HasLabel { public function label(): string; }\n"
        "enum Status: int implements HasLabel {\n"
        "  case Active = 1;\n"
        "  const DEFAULT = self::Active;\n"
        "  case Archived = 2;\n"
        "  public function label(): string { return 'x'; }\n"
        "}\n"
    )
    by_name = {r[0]: r for r in _rows(source)}
    assert by_name["Active"][1:3] == ("constant", "Status.Active"), by_name
    assert by_name["Archived"][1:3] == ("constant", "Status.Archived"), by_name
    assert by_name["DEFAULT"][1:3] == ("constant", "Status.DEFAULT"), by_name
    assert by_name["label"][1] == "method", by_name


def test_every_case_once_and_nothing_else_new():
    """Non-vacuity: the enum yields itself plus exactly its cases, no duplicates."""
    rows = _rows("<?php\nenum E {\n  case A;\n  case B;\n  case C;\n}\n")
    assert sorted(r[2] for r in rows) == ["E", "E.A", "E.B", "E.C"], rows
