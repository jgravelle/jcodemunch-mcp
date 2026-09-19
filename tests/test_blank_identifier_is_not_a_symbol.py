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
    ("go: a discard sharing a declaration with a real name", "a.go", "go",
     "package m\n\nconst Width, _, Depth = 4, 5, 6\n", ["Width", "Depth"]),
    ("go var: the interface assertion idiom", "a.go", "go",
     "package m\n\ntype T struct{}\n\nvar _ I = (*T)(nil)\nvar _, Y = 1, 2\n", ["T", "Y"]),
    ("rust: an unnamed const, the static-assertion idiom", "a.rs", "rust",
     "const _: () = ();\nconst REAL: u8 = 1;\n", ["REAL"]),
    ("swift wildcard", "a.swift", "swift", "let _ = 1\nlet real = 2\n", ["real"]),
    ("scala wildcard", "a.scala", "scala",
     "object O {\n  val _ = 1\n  val real = 2\n}\n", ["O", "real"]),
    # Found by review, not by the first probe: the allowlist had four entries.
    ("ocaml: `let _ = main ()`, the entry-point idiom", "a.ml", "ocaml",
     "let _ = print_string \"x\"\nlet real = 2\n", ["real"]),
    ("nim: two discards used to collect ~1/~2", "a.nim", "nim",
     "let _ = 1\nconst _ = 2\nlet real = 3\n", ["real"]),
    ("julia: an all-underscore identifier is write-only", "a.jl", "julia",
     "_(x) = x\nkeep(x) = x\n", ["keep"]),
    # EVERY kind, not only constants: none of these can be referenced either.
    ("go func: the compile-time assertion idiom", "a.go", "go",
     "package m\n\nfunc _() {}\nfunc Real() {}\n", ["Real"]),
    ("go type", "a.go", "go", "package m\n\ntype _ int\ntype Real int\n", ["Real"]),
    ("go method", "a.go", "go",
     "package m\n\ntype T struct{}\n\nfunc (t T) _() {}\nfunc (t T) Real() {}\n",
     ["T", "Real"]),
    ("swift func", "a.swift", "swift", "func _() {}\nfunc real() {}\n", ["real"]),
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


def test_a_backticked_underscore_is_a_real_scala_name():
    """`` object `_` `` is a name someone chose. It keeps its backticks in the
    symbol name, so the bare-underscore rule never sees it, and its member stays
    owned by it. Pinned because a rule that normalised backticks would orphan
    `inner`."""
    symbols = parse_file("object `_` {\n  val inner = 1\n}\n", "a.scala", "scala")
    owner = next(s for s in symbols if s.kind == "class")
    assert owner.name == "`_`"
    assert [s.parent for s in symbols if s.name == "inner"] == [owner.id]


def test_the_allowlist_is_the_languages_this_file_covers():
    """A language added to the allowlist owes this file a case, and a case here
    for a language outside it would be passing for some other reason."""
    from jcodemunch_mcp.parser.extractor import _BLANK_IDENTIFIER_LANGUAGES

    covered = {
        case[2]
        for mark in test_a_discard_is_not_a_symbol.pytestmark
        for case in mark.args[1]
    }
    assert covered == set(_BLANK_IDENTIFIER_LANGUAGES)


def test_no_discard_leaves_a_disambiguation_ordinal_behind():
    """Two `_` used to become `_#constant~1` and `~2`; nothing may carry an ordinal here."""
    source = "package m\n\nconst _ = 1\nconst _ = 2\nconst Real = 3\n"
    symbols = parse_file(source, "a.go", "go")
    assert [(s.name, "~" in s.id) for s in symbols] == [("Real", False)]
