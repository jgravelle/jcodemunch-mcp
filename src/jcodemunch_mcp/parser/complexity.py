"""Lightweight complexity metrics derived from symbol body text.

All metrics are computed from the raw source text — no AST required.
These are approximations suitable for ranking, not for strict formal analysis.
"""

from __future__ import annotations

import re

from . import parse_budget

# Branch keywords that increment cyclomatic complexity
_BRANCH_RE = re.compile(
    r"\bif\b|\belif\b|\belse\b|\bfor\b|\bwhile\b|\bdo\b"
    r"|\bexcept\b|\bcatch\b|\bfinally\b"
    r"|\bcase\b|\bwhen\b"
    r"|\band\b|\bor\b"      # Python logical operators
    r"|\|\||&&"             # C-style logical operators
    r"|\?(?!:)",            # ternary ? (but not ?: in C#/Swift)
)

# Characters that increase nesting depth
_OPEN_BRACKETS = frozenset("{[(")
_CLOSE_BRACKETS = frozenset("}])")

#: Line-leading keywords that OPEN a control-flow block, across the language
#: families this parser covers. Used for the indentation channel below.
#:
#: ⚠ Continuation-only keywords (`else`, `elif`, `except`, `finally`, `catch`)
#: are deliberately INCLUDED: each opens a block at its own level, and omitting
#: them under-counts an `else:` that itself contains nesting.
_BLOCK_OPENERS = (
    "if", "elif", "else", "for", "while", "do", "try", "except", "finally",
    "catch", "switch", "case", "when", "match", "foreach", "unless", "loop",
    "with", "guard", "repeat", "begin", "rescue", "ensure", "until", "async",
)
_BLOCK_OPENER_RE = re.compile(
    r"^\s*(?:" + "|".join(_BLOCK_OPENERS) + r")\b"
)


def _count_params(signature: str) -> int:
    """Count parameters in the first parenthesised group of *signature*.

    Handles nested brackets (generics, tuples) by tracking bracket depth.
    Returns 0 for signatures with no parentheses or empty param lists.
    """
    m = re.search(r"\(([^)]*)\)", signature, re.DOTALL)
    if not m:
        # Try wider match when params span multiple lines / contain nested ()
        start = signature.find("(")
        if start == -1:
            return 0
        depth = 0
        end = start
        for i, ch in enumerate(signature[start:], start):
            parse_budget.checkpoint()
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        params_str = signature[start + 1 : end].strip()
    else:
        params_str = m.group(1).strip()

    # Empty or C-style "no parameters" marker (void foo(void) → 0 params)
    if not params_str or params_str == "void":
        return 0

    # Count top-level commas (not inside nested brackets)
    depth = 0
    commas = 0
    for ch in params_str:
        if ch in _OPEN_BRACKETS:
            depth += 1
        elif ch in _CLOSE_BRACKETS:
            depth -= 1
        elif ch == "," and depth == 0:
            commas += 1
    return commas + 1


def _bracket_nesting_depth(body: str) -> int:
    """Max bracket-nesting depth relative to the symbol start.

    The opening bracket on the first line is treated as depth 0; each
    additional ``{``, ``[``, or ``(`` increments depth.
    """
    base_depth: int | None = None
    max_depth = 0
    depth = 0
    for ch in body:
        if ch in _OPEN_BRACKETS:
            depth += 1
            if base_depth is None:
                base_depth = depth
            relative = depth - base_depth
            if relative > max_depth:
                max_depth = relative
        elif ch in _CLOSE_BRACKETS:
            depth = max(0, depth - 1)
    return max_depth


def _indent_nesting_depth(body: str) -> int:
    """Max control-flow block depth inferred from INDENTATION.

    ⚠⚠ Brackets cannot see Python's control flow. `if` / `for` / `while` open a
    block with a colon and an indent, contributing NO bracket depth, so the
    bracket channel silently reports the deepest EXPRESSION instead -- a
    different quantity wearing the same field name. Measured on this repo's own
    `index_folder`: brackets said 3, real control flow nests 6.

    A stack of open block indents gives the depth directly, and it needs no
    language parameter: a brace language that also indents lands on the same
    answer, and one that does not still has the bracket channel.
    """
    stack: list[int] = []
    max_depth = 0
    for raw in body.split("\n"):
        parse_budget.checkpoint()
        if not raw.strip() or not _BLOCK_OPENER_RE.match(raw):
            continue
        indent = len(raw) - len(raw.lstrip())
        # A sibling or outdented opener closes everything at or below it.
        while stack and stack[-1] >= indent:
            parse_budget.checkpoint()
            stack.pop()
        stack.append(indent)
        if len(stack) > max_depth:
            max_depth = len(stack)
    # The symbol's own `def`/`function` line is depth 0, so an opener directly
    # inside the body is depth 1 -- which is what the stack length already is.
    return max_depth


def _max_nesting_depth(body: str) -> int:
    """Deepest nesting the symbol reaches, by whichever channel can see it.

    ⚠ The MAX of two channels, never one: brackets are the only signal in a
    minified or brace-heavy body, and indentation is the only signal in an
    indentation-scoped language. Taking the max can only raise the reported
    depth, so a language that was measured correctly before still is.
    """
    return max(_bracket_nesting_depth(body), _indent_nesting_depth(body))


def compute_complexity(body: str, signature: str = "") -> tuple[int, int, int]:
    """Compute (cyclomatic, max_nesting, param_count) for a symbol.

    Args:
        body:      Full source text of the symbol (signature + body).
        signature: Signature line used for parameter counting.

    Returns:
        Tuple of (cyclomatic, max_nesting, param_count):
        - cyclomatic: McCabe complexity — branch count + 1.  Range: [1, ∞).
        - max_nesting: Maximum bracket-nesting depth relative to the opening
          brace / bracket.  Range: [0, ∞).
        - param_count: Number of parameters extracted from the signature.
          Range: [0, ∞).
    """
    branch_count = len(_BRANCH_RE.findall(body))
    cyclomatic = branch_count + 1
    nesting = _max_nesting_depth(body)
    params = _count_params(signature or body.split("\n")[0])
    return cyclomatic, nesting, params
