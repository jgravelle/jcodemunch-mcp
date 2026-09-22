"""One declaration, N names, every language that spells one, one table (#817).

Go's `type ( A ...; B ...; C ... )` indexed A and nothing else, and it was the
THIRD channel to arrive with that symptom: `const` got its walk in #428, `var`
in #731, `type` in #817, each found separately. Java's `int a, b, c` (#735) and
JS's `let x = 1, y = 2` (#741, #742) are the same sentence in two more
languages.

The guard that should have caught it did not, and the reason generalises:
`tests/test_declared_forms_extract.py` asserts that every declared node type
extracts its declared kind, and every sample in it **binds one name**. The row
for Go's type declaration was true and blind at once -- a declaration that
yields one symbol satisfies it whether or not the source bound three. It is the
#699 lesson exactly: a fixture that cannot express the shape cannot fail on it.

So this file asks the other question. Per language: a declaration that the
LANGUAGE says binds several names, and whether `parse_file` binds them all.

⚠ A row is about what the language declares, never what the parser can
currently see. `_GAPS` carries the cells that are broken, each with the issue
tracking it, and a gap entry that stops being true FAILS -- which is how a fix
in one language announces itself here instead of being found again in the next.

⚠ What this file cannot see, stated: it asks only whether the NAMES appear. A
name bound with the wrong kind, the wrong owner or a span that addresses
somebody else's bytes passes here and is somebody else's table
(`test_member_kind_audit.py` for the kind and the owner). The one exception is
Go, whose spans collide by construction when they are wrong, and which is
pinned in `tests/test_a_grouped_go_type_block_binds_every_name.py`.

⚠ Deliberately not a row: a language whose grouped form has no narrower node
per name. There is none in the sample set -- every one of these spells a spec,
a declarator or a clause -- and a language that genuinely lacks one needs a
decision, not a row.
"""

import pytest

from jcodemunch_mcp.parser.extractor import parse_file


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    """This box disables some languages in its own config; the gate is function-local."""
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


#: language -> (form, filename, source, the names the language binds).
#:
#: ⚠ `form` names the SPELLING, because a language may have more than one and a
#: fix for one is not a fix for the other -- which is this defect class's whole
#: history.
_SAMPLES: dict[str, tuple[str, str, str, frozenset[str]]] = {
    "go": ("grouped type block", "a.go", "type (\n\tA int\n\tB int\n)\n", frozenset({"A", "B"})),
    "go_var": ("grouped var block", "b.go", "var (\n\tX = 1\n\tY = 2\n)\n", frozenset({"X", "Y"})),
    "go_const": ("grouped const block", "c.go", "const (\n\tP = 1\n\tQ = 2\n)\n", frozenset({"P", "Q"})),
    "java": ("multi-declarator field", "A.java", "class H { int a, b; }\n", frozenset({"a", "b"})),
    "javascript": ("multi-declarator let", "a.js", "let x = 1, y = 2;\n", frozenset({"x", "y"})),
    "typescript": ("multi-declarator const", "a.ts", "const x = 1, y = 2;\n", frozenset({"x", "y"})),
    "cpp": ("multi-declarator field", "a.cpp", "struct S { int x, y; };\n", frozenset({"x", "y"})),
    "c": ("multi-declarator typedef", "a.c", "typedef int A, B;\n", frozenset({"A", "B"})),
    "cpp_typedef": ("multi-declarator typedef", "b.cpp", "typedef int A, B;\n", frozenset({"A", "B"})),
    "fsharp": ("`and`-chained type", "a.fs", "type A = int\nand B = int\n", frozenset({"A", "B"})),
    "nim": ("type section", "a.nim", "type\n  A* = int\n  B* = int\n", frozenset({"A", "B"})),
    "pascal": ("type section", "a.pas", "type\n  A = Integer;\n  B = Integer;\n", frozenset({"A", "B"})),
    "ocaml": ("`and`-chained type", "a.ml", "type a = int\nand b = int\n", frozenset({"a", "b"})),
}

#: The language each row is PARSED as, where the key says which spelling.
_LANGUAGE_OF = {"go_var": "go", "go_const": "go", "cpp_typedef": "cpp"}

#: Rows that are broken today, with the issue tracking each.
#:
#: ⚠⚠ Found by the scan #817 asked for rather than by another fix, which is the
#: only reason they are enumerated instead of being discovered one at a time.
#: Filing them beat absorbing them: each is a different mechanism (a C
#: declarator list, an F# `and` chain) and a fix for one is not a fix for the
#: other.
_GAPS: dict[str, str] = {
    "c": "#823: a C typedef declarator list binds only its first name",
    "cpp_typedef": "#823: same declaration node, the second of the two specs",
    "fsharp": "#824: an F# `and`-chained type declares only the first",
}


def _bound(row: str) -> frozenset[str]:
    _form, filename, source, _want = _SAMPLES[row]
    language = _LANGUAGE_OF.get(row, row)
    return frozenset(s.name for s in parse_file(source, filename, language))


@pytest.mark.parametrize("row", sorted(set(_SAMPLES) - set(_GAPS)))
def test_one_declaration_binds_every_name_it_declares(row):
    """The property, over every spelling that is supposed to work."""
    form, _filename, _source, want = _SAMPLES[row]
    missing = want - _bound(row)
    assert not missing, (
        f"{row} ({form}) declares {sorted(want)} and {sorted(missing)} is absent "
        f"from the index. A declaration that binds N names needs a node that "
        f"binds one -- see #817."
    )


@pytest.mark.parametrize("row", sorted(_GAPS))
def test_a_tracked_gap_is_still_a_gap(row):
    """A gap that closed must be recorded as closed, in the same commit.

    ⚠ This FAILS when the defect is fixed, which is the notification. Delete
    the `_GAPS` entry; the test above then covers the row.
    """
    form, _filename, _source, want = _SAMPLES[row]
    assert want - _bound(row), (
        f"{row} ({form}) now binds every name it declares, so "
        f"{_GAPS[row]!r} has closed. Delete its `_GAPS` entry."
    )


def test_every_gap_names_an_issue():
    """A gap with no tracker is a decision nobody made."""
    untracked = [row for row, note in _GAPS.items() if not note.startswith("#")]
    assert not untracked, f"{untracked} are excused without an issue number"


def test_every_gap_row_is_sampled():
    """The other direction: a gap for a row this file does not measure.

    ⚠ Cheap, and it caught the version of `_LANGUAGE_OF` that spelled a key
    two ways -- a row excused under a name nothing parses is an excuse that
    can never expire.
    """
    assert not set(_GAPS) - set(_SAMPLES)
