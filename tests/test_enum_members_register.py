"""Which languages index an enum's members: the register behind LEDGER L-03 (#759).

#759 ruled (jjg, 2026-09-25) that an enum member is a `constant` owned by its
enum in every language, and shipped PHP. This file pins, per language, what
`parse_file` answers for a two-member enum today, so the ledger row's list is
MEASURED here rather than typed there, and a language that starts indexing its
members FAILS this file until it moves to `MEMBERS`, which is the notice that
L-03 shrank.

Classes (members are `A`/`B` in each language's own spelling):
- `MEMBERS`: at least one member is a symbol. The ruling holds.
- `ENUM_ONLY`: the enum is a symbol and no member is. LEDGER L-03.
- `NO_ENUM`: nothing comes out, not even the enum. LEDGER L-04: a different
  question, and the sample itself may be the cause, so it is unverified.

Excluded, with the reason: languages with no enum construct (go, lua, ruby,
perl, r, elixir, erlang, ...), sum-type languages whose "variants" are data
constructors rather than an enum (haskell, ocaml, gleam), markup, config and
style languages, and host formats (svelte, vue, astro) whose enums are their
script language's.
"""

import re

import pytest

from jcodemunch_mcp.parser.extractor import parse_file
from jcodemunch_mcp.parser.languages import LANGUAGE_REGISTRY


@pytest.fixture(autouse=True)
def _all_languages_enabled(monkeypatch):
    """Patch `jcodemunch_mcp.config`: this box's config disables languages (see test_php_members)."""
    import jcodemunch_mcp.config as config

    monkeypatch.setattr(config, "is_language_enabled", lambda *a, **k: True)


SAMPLES = {
    "ada": ("a.adb", "package P is\n   type E is (A, B);\nend P;\n"),
    "al": ("a.al", "enum 50100 E\n{\n    value(0; A) { }\n    value(1; B) { }\n}\n"),
    "apex": ("a.cls", "public enum E { A, B }\n"),
    "arduino": ("a.ino", "enum E { A, B };\n"),
    "c": ("a.c", "enum E { A, B };\n"),
    "cpp": ("a.cpp", "enum class E { A, B };\n"),
    "csharp": ("a.cs", "enum E { A, B }\n"),
    "dart": ("a.dart", "enum E { a, b }\n"),
    "dlang": ("a.d", "enum E { A, B }\n"),
    "fortran": ("a.f90", "module m\n  enum, bind(c)\n    enumerator :: A = 0, B\n  end enum\nend module m\n"),
    "fsharp": ("a.fs", "type E =\n    | A = 0\n    | B = 1\n"),
    "gdscript": ("a.gd", "enum E { A, B }\n"),
    "graphql": ("a.graphql", "enum E { A B }\n"),
    "groovy": ("a.groovy", "enum E { A, B }\n"),
    "java": ("A.java", "enum E { A, B; }\n"),
    "julia": ("a.jl", "@enum E A B\n"),
    "kotlin": ("a.kt", "enum class E { A, B }\n"),
    "matlab": ("E.m", "classdef E\n    enumeration\n        A, B\n    end\nend\n"),
    "nim": ("a.nim", "type E = enum\n  A, B\n"),
    "objc": ("a.m", "typedef NS_ENUM(NSInteger, E) { A, B };\nenum F { C, D };\n"),
    "pascal": ("a.pas", "unit u;\ninterface\ntype E = (A, B);\nimplementation\nend.\n"),
    "php": ("a.php", "<?php\nenum E { case A; case B; }\n"),
    "powershell": ("a.ps1", "enum E {\n    A\n    B\n}\n"),
    "proto": ("a.proto", 'syntax = "proto3";\nenum E { A = 0; B = 1; }\n'),
    "python": ("a.py", "import enum\nclass E(enum.Enum):\n    A = 1\n    B = 2\n"),
    "razor": ("Page.cshtml", "@functions {\n    enum E { A, B }\n}\n"),
    "rust": ("a.rs", "enum E { A, B }\n"),
    "scala": ("a.scala", "enum E:\n  case A, B\n"),
    "solidity": ("a.sol", "enum E { A, B }\n"),
    "sql": ("a.sql", "CREATE TYPE e AS ENUM ('a', 'b');\n"),
    "swift": ("a.swift", "enum E { case a, b }\n"),
    "tsx": ("a.tsx", "enum E { A, B }\n"),
    "typescript": ("a.ts", "enum E { A, B }\n"),
    "verilog": ("a.sv", "typedef enum { A, B } E;\n"),
    "verse": ("a.verse", "E := enum:\n    A\n    B\n"),
    "vhdl": ("a.vhd", "package p is\n  type E is (A, B);\nend package;\n"),
    "zig": ("a.zig", "const E = enum { a, b };\n"),
}

MEMBERS = frozenset({"al", "php", "python"})
ENUM_ONLY = frozenset({"ada", "apex", "arduino", "c", "cpp", "csharp", "dart", "dlang", "fsharp", "gdscript", "graphql", "groovy", "java", "kotlin", "matlab", "nim", "pascal", "powershell", "proto", "razor", "rust", "scala", "solidity", "swift", "tsx", "typescript", "verilog", "verse", "vhdl", "zig"})
NO_ENUM = frozenset({"fortran", "julia", "objc", "sql"})


def _classify(language: str) -> str:
    filename, source = SAMPLES[language]
    names = {re.split(r"[.:]+", s.qualified_name)[-1].lower() for s in parse_file(source, filename, language)}
    if names & {"a", "b"}:
        return "members"
    return "enum_only" if "e" in names else "no_enum"


def test_the_register_covers_every_sample_once_and_only_registered_languages():
    assert MEMBERS | ENUM_ONLY | NO_ENUM == set(SAMPLES)
    assert not (MEMBERS & ENUM_ONLY or MEMBERS & NO_ENUM or ENUM_ONLY & NO_ENUM)
    assert set(SAMPLES) <= set(LANGUAGE_REGISTRY)


@pytest.mark.parametrize("language", sorted(SAMPLES))
def test_each_language_answers_what_the_register_says(language):
    expected = "members" if language in MEMBERS else "enum_only" if language in ENUM_ONLY else "no_enum"
    got = _classify(language)
    assert got == expected, (
        f"{language}: the register says {expected!r} and parse_file answers {got!r}. "
        f"If members arrived, move {language!r} to MEMBERS and shrink "
        f"docs/workflows/LEDGER.md L-03 (or L-04) in the same change."
    )


def test_php_is_in_members_because_of_759():
    assert "php" in MEMBERS
