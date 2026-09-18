"""`max_nesting` could not see Python's control flow.

`_max_nesting_depth` counted BRACKETS, deliberately, to stay language-agnostic
across 70+ languages. In a brace language `{` tracks blocks and that is roughly
right. In Python, `if` / `for` / `while` open a block with a colon and an
indent and contribute NO bracket depth -- so the field silently reported the
deepest EXPRESSION instead, a different quantity wearing the same name.

⚠⚠ Measured on this repo's own `index_folder`: brackets said **3**, the AST
says **6**. An underreport by half, on the one axis that distinguishes a wide
flat dispatcher from deeply tangled logic. The number was not merely imprecise;
it supported the opposite conclusion about the symbol.

⚠ The fix takes the MAX of two channels rather than switching on language.
Taking the max can only RAISE a reported depth, so any language already
measured correctly by brackets still is -- which is why the brace-language and
minified cases below are as load-bearing as the Python one.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from jcodemunch_mcp.parser.complexity import (
    _BLOCK_OPENER_RE,
    _bracket_nesting_depth,
    _indent_nesting_depth,
    compute_complexity,
)

_REPO = pathlib.Path(__file__).resolve().parent.parent


def _nesting(body: str, signature: str = "") -> int:
    return compute_complexity(body, signature)[1]


def _ast_control_depth(node, d: int = 0) -> int:
    """Ground truth: deepest If/For/While nesting, from Python's own parser."""
    best = d
    for child in ast.iter_child_nodes(node):
        nd = d + 1 if isinstance(child, (ast.If, ast.For, ast.While)) else d
        best = max(best, _ast_control_depth(child, nd))
    return best


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------

def test_python_control_flow_is_visible():
    body = (
        "def f(x):\n"
        "    if x:\n"
        "        for i in x:\n"
        "            while i:\n"
        "                i -= 1\n"
    )
    assert _bracket_nesting_depth(body) == 0, "no brackets to see -- that is the defect"
    assert _nesting(body, "def f(x)") == 3


def test_python_expression_nesting_is_not_control_nesting():
    """The old channel measured THIS and called it nesting."""
    body = "def f(x):\n    return foo(bar(baz(qux(x))))\n"
    assert _bracket_nesting_depth(body) >= 3
    assert _indent_nesting_depth(body) == 0


def test_index_folder_matches_pythons_own_parser():
    """⚠ The symbol that exposed this, pinned against the AST.

    Not a literal: `index_folder` will change, and a hard-coded 6 would have to
    be edited every time it does -- at which point nobody checks whether the
    edit was correct. The AST is the oracle.
    """
    src = (_REPO / "src" / "jcodemunch_mcp" / "tools" / "index_folder.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(src)
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "index_folder"
    )
    body = "\n".join(src.split("\n")[fn.lineno - 1 : fn.end_lineno])
    truth = _ast_control_depth(fn)
    assert truth >= 4, "fixture symbol got simple; pick another deeply nested one"
    assert _nesting(body, "def index_folder(...)") == truth
    assert _bracket_nesting_depth(body) < truth, "bracket channel should still under-see it"


# ---------------------------------------------------------------------------
# No regression: the bracket channel must keep winning where it was right
# ---------------------------------------------------------------------------

_JAVA = """int f(int a) {
    if (a > 0) {
        for (int i = 0; i < a; i++) {
            if (i % 2 == 0) {
                System.out.println(i);
            }
        }
    }
    return a;
}"""

_MINIFIED = "function f(a){if(a){for(var i=0;i<a;i++){if(i%2){g(i)}}}return a}"


def test_brace_language_is_unchanged():
    assert _nesting(_JAVA, "int f(int a)") == _bracket_nesting_depth(_JAVA)


def test_minified_code_still_measurable():
    """⚠ The case that forbids replacing brackets with indentation outright.

    Minified source has no indentation at all, so the new channel returns 0 and
    the bracket channel is the only thing that can see the nesting.
    """
    assert _indent_nesting_depth(_MINIFIED) == 0
    assert _nesting(_MINIFIED, "function f(a)") >= 3


def test_flat_code_reports_zero():
    assert _nesting("def f(x):\n    return x + 1\n", "def f(x)") == 0


@pytest.mark.parametrize(
    "line,expected",
    [
        ("    if x:", True),
        ("    for i in y:", True),
        # ⚠ Brace-language syntax: NOT matched, and that is correct rather
        # than a gap. The indent channel is the fallback for
        # indentation-scoped languages; a body containing `} else {` has
        # braces, so the bracket channel measures it and max() takes that.
        ("    } else {", False),
        ("    iffy = 1", False),
        ("    format(x)", False),
        ("    formatted = do_thing()", False),
        ("    whilst = 2", False),
    ],
)
def test_opener_regex_needs_a_word_boundary(line, expected):
    """⚠⚠ Without `\\b`, `iffy` matches `if` and `format` matches `for`.

    The boundary was lost once during editing and reintroduced as a literal
    BACKSPACE character (0x08) inside the raw string -- which compiled, ran, and
    passed ruff. An invisible control character is not a lint problem; it is a
    correctness problem that only a behavioural test can see.
    """
    assert bool(_BLOCK_OPENER_RE.match(line)) is expected


def test_source_carries_no_stray_control_characters():
    """The guard for the above, over every tree that can carry a regex.

    ⚠⚠ **This scanned ONE FILE for its whole life, and the defect recurred
    outside it.** The lesson was recorded from `complexity.py`'s opener, where a
    literal BACKSPACE (0x08) had replaced a `\b` and compiled, ran and passed
    ruff -- so the guard was written against the file where it happened rather
    than against the property, which is [[a-guard-written-against-a-spelling]]
    applied to a guard. On 2026-09-18 the same 0x08-for-`\b` substitution landed
    in `.claude/hooks/dod_checklist.py`'s survival regex, in a PR that cites this
    lesson; every behavioural row there passed with the corrupt pattern, because
    a regex that matches nothing looks exactly like a strict one.

    ⚠ Second instance is this project's threshold for fixing the mechanism, so
    the scan walks `src/`, `tests/` and `.claude/hooks/` -- one `rglob`, cheaper
    than a per-case row in each consumer, and it covers the files written next.
    """
    roots = [_REPO / "src", _REPO / "tests", _REPO / ".claude" / "hooks"]
    offenders: dict[str, list[str]] = {}
    scanned = 0
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            scanned += 1
            bad = sorted(
                {hex(ord(c)) for c in text if ord(c) < 32 and c not in "\n\t"}
            )
            if bad:
                offenders[str(path.relative_to(_REPO))] = bad

    assert not offenders, f"control characters in source: {offenders}"
    # ⚠ Non-vacuity: a walk that reached no FILES would report clean forever.
    assert scanned > 500, f"only {scanned} files scanned; the walk is not reaching the tree"
