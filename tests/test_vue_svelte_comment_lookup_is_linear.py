"""The doc-comment lookup in a Vue or Svelte `<script>` is linear in the script (LEDGER L-115).

`_preceding_comment` walked ALL of the parent's children, from the first one,
for every declaration it was asked about, so a script of N top-level functions
cost N * N child visits: a 441,827-char `.vue` of 6000 functions spent 14 s in
Python with its two C parses done in 0.12 s.

The property is counted, not timed: every loop on the parse path starts with
`parse_budget.checkpoint()` (L-116's rule), so the number of checkpoints passed
is the number of loop iterations. Doubling the script must roughly double it.
"""

import pytest

import jcodemunch_mcp.config as config_mod
from jcodemunch_mcp.parser import parse_budget
from jcodemunch_mcp.parser.extractor import parse_file


@pytest.fixture(autouse=True)
def _every_language_enabled(monkeypatch):
    # a developer config may disable a language; "0 symbols" would then pass the ratio
    monkeypatch.setattr(config_mod, "is_language_enabled", lambda *a, **k: True)


def _script(n: int) -> str:
    return "".join(f"// about f{i}\nfunction f{i}(a, b) {{\n  return a + b + {i}\n}}\n" for i in range(n))


def _vue(n: int) -> tuple[str, str]:
    return "big.vue", f"<template><div/></template>\n<script>\n{_script(n)}</script>\n"


def _svelte(n: int) -> tuple[str, str]:
    return "big.svelte", f"<script>\n{_script(n)}</script>\n<div/>\n"


def _checkpoints(monkeypatch, filename: str, content: str, language: str) -> tuple[int, list]:
    count = 0
    real = parse_budget.checkpoint

    def counting():
        nonlocal count
        count += 1
        real()

    monkeypatch.setattr(parse_budget, "checkpoint", counting)
    try:
        symbols = parse_file(content, filename, language)
    finally:
        monkeypatch.setattr(parse_budget, "checkpoint", real)
    return count, symbols


@pytest.mark.parametrize("language, build", [("vue", _vue), ("svelte", _svelte)])
def test_twice_the_script_is_about_twice_the_work(monkeypatch, language, build):
    small_n, big_n = 150, 300
    small, small_syms = _checkpoints(monkeypatch, *build(small_n), language)
    big, big_syms = _checkpoints(monkeypatch, *build(big_n), language)

    assert len([s for s in small_syms if s.kind == "function"]) == small_n
    assert len([s for s in big_syms if s.kind == "function"]) == big_n
    assert big / small < 2.6, (
        f"{language}: {small} loop iterations for {small_n} functions and {big} for {big_n} "
        f"(x{big / small:.2f}); a quadratic walk reads about x4"
    )


@pytest.mark.parametrize("language, build", [("vue", _vue), ("svelte", _svelte)])
def test_each_function_keeps_its_own_comment(language, build):
    filename, content = build(40)
    docs = {s.name: s.docstring for s in parse_file(content, filename, language) if s.kind == "function"}
    assert docs == {f"f{i}": f"about f{i}" for i in range(40)}


_CASES = """\
// about plain
function plain() { return 1 }

function bare() { return 2 }

// about first
const sep = 1
function after_a_statement() { return 3 }

/* block comment */
function block() { return 4 }

// stale
const x = 2
// fresh
function nearest() { return 5 }
"""

_EXPECTED = {
    "plain": "about plain",
    "bare": "",
    # a statement between the comment and the function breaks the link
    "after_a_statement": "",
    # the closing `*/` is kept today (LEDGER L-124); pinned so this change moves no docstring
    "block": "block comment */",
    "nearest": "fresh",
}


@pytest.mark.parametrize("filename, template", [
    ("c.vue", "<template><div/></template>\n<script>\n{}</script>\n"),
    ("c.svelte", "<script>\n{}</script>\n<div/>\n"),
])
def test_which_comment_belongs_to_which_function(filename, template):
    language = filename.rsplit(".", 1)[1]
    symbols = parse_file(template.format(_CASES), filename, language)
    docs = {s.name: s.docstring for s in symbols if s.kind == "function"}
    assert docs == _EXPECTED
