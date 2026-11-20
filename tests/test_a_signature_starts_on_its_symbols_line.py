"""A symbol's stored signature starts on the symbol's `line` (LEDGER L-332).

`check_references._name_through` finds a same-named sibling's declaration
line within the lines its signature spans, counted from the symbol's `line`:
`line` through `line + signature.count(newline)`. That bound keeps the search
out of the body, so it cannot stop at a call there and hide it (L-331). It
holds only while each parser's signature starts at `line`. A parser that
prepended doc or annotation lines to the signature while `line` pointed at the
name would push the bound that many lines into the body, and a call there
would read as part of the declaration. Nothing pinned it; measured on
2026-11-20, every parser below keeps it, whether `line` points at the first
annotation (Java, Kotlin, C#, PHP, Swift, Scala, C++) or at the name with the
annotations left out (Python, TypeScript, Rust).

Two checks: an annotated, documented, multi-line declaration per language, and
every multi-line signature the parser produces for this repository's own
fixtures and source.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jcodemunch_mcp.parser.extractor import parse_file
from jcodemunch_mcp.parser.languages import get_language_for_path

NL = chr(10)
ROOT = Path(__file__).resolve().parent.parent

# Each declaration has a doc comment and an annotation spread over two lines
# above it, and a signature spread over several lines below it.
SNIPPETS = {
    "python": ("a.py", ["import functools", "", "@functools.lru_cache(", "    maxsize=None,", ")",
                        "def target(", "    first,", "    second,", "):", '    """Doc."""',
                        "    return target(first, second)", ""]),
    "java": ("A.java", ["class A {", "    /**", "     * Doc.", "     */", "    @Deprecated",
                        "    @SuppressWarnings(", '        "unchecked")', "    public int target(",
                        "            int first,", "            int second) {",
                        "        return target(first, second);", "    }", "}", ""]),
    "kotlin": ("A.kt", ["class A {", "    /** Doc. */", "    @Deprecated(", '        "x")',
                        "    fun target(", "        first: Int,", "        second: Int,", "    ): Int {",
                        "        return target(first, second)", "    }", "}", ""]),
    "csharp": ("A.cs", ["class A {", "    /// <summary>Doc.</summary>", "    [Obsolete(", '        "x")]',
                        "    public int Target(", "        int first,", "        int second) {",
                        "        return Target(first, second);", "    }", "}", ""]),
    "typescript": ("a.ts", ["class A {", "  /** Doc. */", "  @dec(", "    1)", "  target(",
                            "    first: number,", "    second: number,", "  ): number {",
                            "    return this.target(first, second);", "  }", "}", ""]),
    "javascript": ("a.js", ["/**", " * Doc.", " */", "function target(", "  first,", "  second,", ") {",
                            "  return target(first, second);", "}", ""]),
    "rust": ("a.rs", ["/// Doc.", "#[inline]", "#[allow(", "    dead_code)]", "fn target(", "    first: i32,",
                      "    second: i32,", ") -> i32 {", "    target(first, second)", "}", ""]),
    "go": ("a.go", ["package a", "", "// Target does it.", "func Target(", "\tfirst int,", "\tsecond int,",
                    ") int {", "\treturn Target(first, second)", "}", ""]),
    "php": ("a.php", ["<?php", "class A {", "    /** Doc. */", "    #[Deprecated(", "        'x')]",
                      "    public function target(", "        int $first,", "        int $second",
                      "    ): int {", "        return $this->target($first, $second);", "    }", "}", ""]),
    "swift": ("a.swift", ["class A {", "    /// Doc.", "    @available(*,", "        deprecated)",
                          "    func target(", "        first: Int,", "        second: Int", "    ) -> Int {",
                          "        return target(first: first, second: second)", "    }", "}", ""]),
    "scala": ("a.scala", ["object A {", "  /** Doc. */", "  @deprecated(", '    "x")', "  def target(",
                          "      first: Int,", "      second: Int", "  ): Int = {",
                          "    target(first, second)", "  }", "}", ""]),
    "cpp": ("a.cpp", ["/// Doc.", "[[nodiscard]]", "int target(", "    int first,", "    int second) {",
                      "  return target(first, second);", "}", ""]),
}

_DROP = set(" \t\r[]`\"'")


def _squash(text: str) -> str:
    """Whitespace and quoting removed: SQL stores `[dbo].[f]` as `dbo.f`."""
    return "".join(c for c in text if c not in _DROP)


def _misplaced(lines: list[str], line: int, signature: str) -> "str | None":
    """Why the bound `line`..`line + newlines` could reach past the declaration, or None.

    The signature's first line must be source line `line`, and each later
    signature line must sit at least one source line below the one before it.
    Then `line + newlines` can only fall on or before the declaration's last
    line. A parser may join source lines into one signature line (SQL's
    `) RETURNS varchar(8)`), which shortens the bound; that is the safe side,
    so a signature line may match up to three joined source lines.
    """
    parts = signature.split(NL)
    if not 1 <= line <= len(lines):
        return f"line {line} is outside a {len(lines)}-line file"
    first = _squash(parts[0])
    if first and first not in _squash(lines[line - 1]):
        return f"signature starts {parts[0].strip()!r}, but source line {line} is {lines[line - 1].strip()!r}"
    at = line - 1
    for offset, part in enumerate(parts[1:], start=2):
        want = _squash(part)
        if not want:
            at += 1
            continue
        hits = [k for k in range(at + 1, min(len(lines), at + 40))
                if want in _squash("".join(lines[k:k + 3]))]
        if not hits:
            return f"signature line {offset} {part.strip()!r} is not below source line {at + 1}"
        at = hits[0]
    return None


@pytest.mark.parametrize("language", sorted(SNIPPETS))
def test_an_annotated_multi_line_signature_starts_on_its_line(language):
    filename, lines = SNIPPETS[language]
    symbols = [s for s in parse_file(NL.join(lines), filename, language) if s.name.lower() == "target"]
    assert symbols, f"{language}: no symbol named target"
    sym = symbols[0]
    assert NL in (sym.signature or ""), f"{language}: the signature is one line, so this checks nothing"
    why = _misplaced(lines, sym.line, sym.signature)
    assert why is None, f"{language}: {why}"


def test_the_check_sees_a_signature_that_starts_early():
    """Non-vacuity: a signature carrying the line above `line` is caught."""
    lines = ["/// Doc.", "fn target(", "    first: i32,", ") -> i32 {", "}"]
    assert _misplaced(lines, 2, NL.join(["fn target(", "    first: i32,", ") -> i32"])) is None
    early = NL.join(["/// Doc.", "fn target(", "    first: i32,"])
    assert _misplaced(lines, 2, early) is not None
    # A signature line made of two joined source lines shortens the bound: allowed.
    sql = ["CREATE FUNCTION [dbo].[f](", "    @a int", ")", "RETURNS int", "AS BEGIN RETURN 1 END"]
    assert _misplaced(sql, 1, NL.join(["CREATE FUNCTION dbo.f(", "    @a int", ") RETURNS int"])) is None
    # A signature line that is not below its predecessor is caught.
    assert _misplaced(lines, 2, NL.join(["fn target(", "/// Doc."])) is not None


def _corpus_files():
    for base in ("tests/fixtures", "src/jcodemunch_mcp"):
        for path in sorted((ROOT / base).rglob("*")):
            if path.is_file() and get_language_for_path(str(path)):
                yield path


def test_every_multi_line_signature_in_this_repository_starts_on_its_line():
    checked, wrong = 0, []
    seen, parsed, unparsed = set(), set(), []
    for path in _corpus_files():
        language = get_language_for_path(str(path))
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = path.relative_to(ROOT).as_posix()
        seen.add(language)
        try:
            symbols = parse_file(text, rel, language)
        except Exception as exc:  # noqa: BLE001 - recorded below, never dropped unseen
            unparsed.append(f"{rel}: {type(exc).__name__}")
            continue
        parsed.add(language)
        lines = text.splitlines()
        for sym in symbols:
            if NL not in (sym.signature or "") or not sym.line:
                continue
            checked += 1
            why = _misplaced(lines, sym.line, sym.signature)
            if why:
                wrong.append(f"{rel}:{sym.line} {sym.name}: {why}")
    # A parser that starts raising for every file of a language would drop that
    # language from this check without moving the count much, so it fails here.
    dropped = sorted(seen - parsed)
    assert not dropped, f"no file parsed for {dropped}; failures: {unparsed[:20]}"
    # Measured on origin/main f23bfb98, 2026-11-20: 20 multi-line signatures in
    # tests/fixtures and 1,044 in src. A floor well under that keeps the check
    # from passing because it read nothing.
    assert checked >= 500, f"only {checked} multi-line signatures checked; parse failures: {unparsed[:20]}"
    assert not wrong, NL.join(wrong[:20])
