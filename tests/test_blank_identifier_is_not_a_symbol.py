"""#763: a language's DISCARD is not a name, so it is not a symbol.

Go's `_`, Rust's `const _`, Swift's `let _` and Scala's `val _` bind nothing:
the identifier cannot be referenced, several may sit in one file, and each one
indexed became a symbol called `_` competing in every ranking (and, being
duplicates, collected `~1`/`~2` ordinals). The fix for Go's constant channel was
first proposed by @fathirramadhan-web (PR #765); #741's review had already
closed Go's `var` channel and left this one named.

⚠ The property is per LANGUAGE, never per spelling: in JavaScript and
TypeScript `_` is an ordinary identifier (lodash is conventionally bound to
it), and dropping it there would delete a real symbol. Both directions are
asserted.
"""

import pytest

from jcodemunch_mcp.parser.extractor import parse_file


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


def _names(source, filename, language):
    return [s.name for s in parse_file(source, filename, language)]


@pytest.mark.parametrize("label, filename, language, source, expected", [
    ("go iota ladder (the report)", "a.go", "go",
     "package m\n\nconst (\n\t_ = iota\n\tKB\n\tMB\n)\n", ["KB", "MB"]),
    ("go: the discard beside a real name keeps the real name", "a.go", "go",
     "package m\n\nconst _, B = 1, 2\n", ["B"]),
    ("go var: the interface assertion idiom", "a.go", "go",
     "package m\n\ntype T struct{}\n\nvar _ I = (*T)(nil)\nvar _, Y = 1, 2\n", ["T", "Y"]),
    ("rust: an unnamed const, the static-assertion idiom", "a.rs", "rust",
     "const _: () = ();\nconst REAL: u8 = 1;\n", ["REAL"]),
    ("swift wildcard", "a.swift", "swift", "let _ = 1\nlet real = 2\n", ["real"]),
    ("scala wildcard", "a.scala", "scala",
     "object O {\n  val _ = 1\n  val real = 2\n}\n", ["O", "real"]),
])
def test_a_discard_is_not_a_symbol(label, filename, language, source, expected):
    assert _names(source, filename, language) == expected, label


@pytest.mark.parametrize("filename, language, source", [
    ("a.js", "javascript", "const _ = require('lodash');\nconst REAL = 2;\n"),
    ("a.ts", "typescript", "const _ = 1;\nconst REAL = 2;\n"),
])
def test_an_ordinary_identifier_spelled_underscore_stays(filename, language, source):
    """The over-suppression direction, written first (CLAUDE.md 09-01, #569)."""
    assert "_" in _names(source, filename, language)


@pytest.mark.parametrize("filename, language, source, kept", [
    ("a.rs", "rust", "static _S: u8 = 0;\nconst _UNUSED: u8 = 1;\n", ["_S", "_UNUSED"]),
    ("a.go", "go", "package m\n\nconst _x = 1\nvar __ = 2\n", ["_x", "__"]),
])
def test_only_the_bare_underscore_is_the_discard(filename, language, source, kept):
    """A leading underscore is a naming convention; `_x` and `__` are names."""
    assert _names(source, filename, language) == kept


def test_no_discard_leaves_a_disambiguation_ordinal_behind():
    """Two `_` used to become `_#constant~1` and `~2`; nothing may carry an ordinal here."""
    source = "package m\n\nconst _ = 1\nconst _ = 2\nconst Real = 3\n"
    symbols = parse_file(source, "a.go", "go")
    assert [(s.name, "~" in s.id) for s in symbols] == [("Real", False)]
