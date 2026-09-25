"""A Nim routine is indexed whatever its name is wrapped in (#843).

`proc runIt*(a: Audit): int` was not indexed at all. The export marker makes
the grammar put the name under `exported_symbol > identifier`, and
`_parse_nim_symbols` asked each routine for a DIRECT `identifier` child, so it
skipped every exported routine: on a real Nim package, the public API. The
type section already stripped the marker, and #812's object fields already
descended through `exported_symbol`; the routine branch was the one reader of
three still asking for the bare child.

⚠⚠ The probe found a second wrapper on the same field, and it has nothing to
do with export: an operator routine's name is `accent_quoted` (``proc `$`(a:
V): string``), so a plain operator was skipped too, and an exported operator
nests one wrapper in the other. The fix reads the routine's `name` field and
unwraps both, so every combination below is one path.

⚠ An operator's symbol name is the operator without the backticks (`$`, `+`):
the backticks are quoting syntax, and a caller asks `search_symbols` for `$`.
"""

import pytest

from jcodemunch_mcp.parser.extractor import parse_file

KINDS = ["proc", "func", "template", "macro", "method", "iterator", "converter"]


@pytest.fixture(autouse=True)
def _nim_enabled(monkeypatch):
    """⚠ This box's config disables nim, and a disabled language yields `[]`,
    indistinguishable from the defect (memory: patch the gate in `config`)."""
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def _rows(source: str) -> dict[str, tuple[str, int]]:
    return {s.name: (s.kind, s.line) for s in parse_file(source, "a.nim", "nim")}


def test_the_reported_case():
    rows = _rows("type Audit* = object\n  tally: int\n\nproc runIt*(a: Audit): int = a.tally\nproc speak(a: Audit) = discard\n")
    assert rows.get("runIt") == ("function", 4), rows
    assert rows.get("speak") == ("function", 5), rows


@pytest.mark.parametrize("kind", KINDS)
def test_every_routine_kind_with_and_without_the_export_marker(kind):
    rows = _rows(f"{kind} plain(a: int): int = a\n{kind} exported*(a: int): int = a\n")
    assert rows.get("plain") == ("function", 1), rows
    assert rows.get("exported") == ("function", 2), rows


@pytest.mark.parametrize("source, name", [
    ("proc gen*[T](a: T): T = a\n", "gen"),
    ("proc prag*(a: int): int {.inline.} = a\n", "prag"),
    ("proc `$`(a: V): string = \"\"\n", "$"),
    ("proc `+`*(a, b: V): V = a\n", "+"),
    ("func `==`*(a, b: V): bool = true\n", "=="),
])
def test_generic_pragma_and_operator_names(source, name):
    rows = _rows(source)
    assert rows.get(name) == ("function", 1), rows


def test_the_signature_names_the_routine_without_its_marker():
    (sym,) = [s for s in parse_file("proc runIt*(a: int): int = a\n", "a.nim", "nim") if s.name == "runIt"]
    assert sym.signature.startswith("proc runIt("), sym.signature
    assert sym.qualified_name == "runIt"


def test_nothing_is_indexed_twice():
    syms = parse_file("proc a*(x: int) = discard\nproc `+`*(a, b: int): int = a\n", "a.nim", "nim")
    assert len(syms) == len({s.id for s in syms}) == 2, [(s.name, s.id) for s in syms]
