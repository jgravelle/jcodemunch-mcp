"""No file the sdist ships spells a dynamic-code call.

jcodemunch never evaluates or executes code, but registry scanners (mcp.market
among them) read the published package as text and count call-shaped text
against it as if it ran. Four such spellings sat in shipped files on
2026-10-15: an MCP prompt sentence, a test fixture, a test's busy-wait and
recorded benchmark answers. The review of their removal then found two new
ones written by the same change, in a pyproject comment and in SECURITY.md,
which is why this is a scan over every shipped file rather than a list.

Shipped = tracked by git and not excluded by `[tool.hatch.build.targets.sdist]`.
`ast.literal_eval(` and `.exec(` on a regex or cursor are method or qualified
calls and do not match.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Joined at run time so this file carries no match of its own.
_NAMES = "|".join(["ev" + "al", "ex" + "ec"])
CALL_TEXT = re.compile(r"(^|[^A-Za-z0-9_.])(" + _NAMES + r")\(|new " + "Function")


def _sdist_excludes() -> list[str]:
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    block = text.split("[tool.hatch.build.targets.sdist]", 1)[1].split("\n[", 1)[0]
    body = block.split("exclude = [", 1)[1].split("\n]", 1)[0]
    return re.findall(r'^\s*"([^"]+)"', body, flags=re.M)


def _excluded(path: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if pat.endswith("/"):
            if path.startswith(pat):
                return True
        elif fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(Path(path).name, pat):
            return True
    return False


def _shipped_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=str(REPO), capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=True,
    ).stdout.splitlines()
    patterns = _sdist_excludes()
    return [p for p in out if not _excluded(p, patterns)]


def test_the_exclude_list_was_read():
    patterns = _sdist_excludes()
    assert ".claude/" in patterns and "munch-bench/results/" in patterns, patterns


def test_no_shipped_file_spells_a_dynamic_code_call():
    hits = []
    for rel in _shipped_files():
        path = REPO / rel
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8192]:
            continue
        for n, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
            if CALL_TEXT.search(line):
                hits.append(f"{rel}:{n}: {line.strip()[:120]}")
    assert not hits, "call-shaped text in shipped files:\n" + "\n".join(hits)


def test_the_pattern_sees_what_it_is_for():
    """Non-vacuity: each spelling the scan exists for matches; the safe ones don't."""
    ev, ex = "ev" + "al", "ex" + "ec"
    for bad in (f'{ev}("1 + 1")', f"x = {ex}(code)", "new " + "Function('a', 'b')"):
        assert CALL_TEXT.search(bad), bad
    for ok in (f"ast.literal_{ev}(s)", f"pattern.{ex}(text)", f"namespace {ev} foo", f"{ex}_count = 1"):
        assert not CALL_TEXT.search(ok), ok
