"""Every `LanguageSpec` field is read by the product, or it does not exist (#725).

`type_patterns`, `return_type_fields` and `param_fields` were filled in by every
spec and read by nothing in `src/`. A field nothing reads cannot disagree with
anything, so it rots without a symptom: #713 added an entry to two of them
believing they did something, and `CSHARP_SPEC` and `JAVA_SPEC` came to spell
the same construct differently in a list neither consumer existed for. It is
the `entry_point_patterns` shape of #561/#562, one dataclass over.

⚠⚠ The rule is the PROPERTY over the dataclass, never the three names. A fourth
field added and never wired fails here on arrival, whatever it is called. The
choice this forces is the one #725 asked for: give the field a reader, or delete
it from `LanguageSpec` and every spec.

⚠ A keyword at construction (`param_fields={...}` in `languages.py`) is WRITING
the field, not reading it. Counting it would make every field look consumed,
which is how three of them hid in plain sight.
"""

import ast
import dataclasses
import pathlib

from jcodemunch_mcp.parser.languages import LanguageSpec

_SRC = pathlib.Path(__file__).resolve().parent.parent / "src"


def _read_names() -> set[str]:
    """Every attribute name `src/` reads, by `x.name`, `getattr(x, "name")` or `x["name"]`."""
    names: set[str] = set()
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                names.add(node.attr)
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) > 1
                and isinstance(node.args[1], ast.Constant)
            ):
                names.add(node.args[1].value)
            elif isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
                names.add(node.slice.value)
    return names


def test_every_language_spec_field_has_a_reader():
    read = _read_names()
    unread = sorted(f.name for f in dataclasses.fields(LanguageSpec) if f.name not in read)
    assert not unread, (
        f"LanguageSpec fields {unread} are written by the specs and read by nothing "
        f"in src/. Wire each into extraction or delete it from LanguageSpec and "
        f"every spec (#725): a field no consumer reads rots without a symptom."
    )


def test_the_scan_sees_a_read_and_refuses_a_name_nobody_reads():
    """Non-vacuity: the scan must find a real read and must not find everything."""
    read = _read_names()
    assert "name_fields" in read, "the scan missed spec.name_fields, which the extractor reads"
    assert "a_field_no_code_reads_725" not in read, "the scan reports every name as read"


def test_the_deleted_fields_are_gone_from_every_spec():
    """The three #725 named are removed, not merely left unread."""
    names = {f.name for f in dataclasses.fields(LanguageSpec)}
    assert not names & {"type_patterns", "return_type_fields", "param_fields"}, sorted(names)
