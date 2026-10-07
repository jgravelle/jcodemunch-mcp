"""The tree-sitter range we declare is the range the parse-cancel guard ran on.

The per-file parse budget stops a slow C parse through `Parser.timeout_micros`
(`parser/parse_budget.py`, LEDGER L-114). tree-sitter 0.26.0 removed that
attribute. The dependency read `tree-sitter>=0.25` with no upper bound, so an
install resolved 0.26.0 and the budget could no longer stop a parse; the only
sign was one warning line at index time (reported by Dave, 2026-10-07, from
`jcodemunch-mcp init` on 1.108.331).

`tests/test_parse_budget_cancels.py` already fails on a binding without the
attribute, and it never saw this: CI installs from `uv.lock`, which pins
0.25.2, and a user installs from the declared range. So the range itself is
held here: it admits the locked version and stops before the next minor, the
first release nobody has run that guard on. Raising the cap is then a lock
change, and the guard runs on the new version before a user gets it.
"""
from __future__ import annotations

import re
from pathlib import Path

from packaging.specifiers import SpecifierSet
from packaging.version import Version

ROOT = Path(__file__).resolve().parent.parent


def _declared_range() -> SpecifierSet:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    # the runtime itself, not `tree-sitter-language-pack` or `tree-sitter-fsharp`
    found = re.findall(r'^\s*"tree-sitter\s*([<>=!~][^";]*)"', text, re.MULTILINE)
    assert len(found) == 1, f"expected one `tree-sitter` requirement in pyproject.toml, found {found}"
    return SpecifierSet(found[0])


def _locked_version() -> Version:
    text = (ROOT / "uv.lock").read_text(encoding="utf-8")
    found = re.findall(r'^name = "tree-sitter"\r?\nversion = "([^"]+)"', text, re.MULTILINE)
    assert len(found) == 1, f"expected one locked `tree-sitter` in uv.lock, found {found}"
    return Version(found[0])


def _range_recorded_in_the_lock() -> SpecifierSet:
    text = (ROOT / "uv.lock").read_text(encoding="utf-8")
    found = re.findall(r'\{ name = "tree-sitter", specifier = "([^"]+)" \}', text)
    assert len(found) == 1, f"expected one `tree-sitter` specifier in uv.lock's requires-dist, found {found}"
    return SpecifierSet(found[0])


def test_the_declared_range_admits_the_version_ci_runs():
    assert _locked_version() in _declared_range()


def test_the_declared_range_stops_before_the_next_minor():
    locked = _locked_version()
    declared = _declared_range()
    next_minor = Version(f"{locked.major}.{locked.minor + 1}.0")
    next_major = Version(f"{locked.major + 1}.0.0")
    assert next_minor not in declared and next_major not in declared, (
        f"pyproject.toml declares tree-sitter{declared}, which admits a release past the locked "
        f"{locked}. Nothing has run the parse-cancel guard on it: 0.26.0 removed "
        f"Parser.timeout_micros and the parse budget stopped working for every install that "
        f"resolved it. Cap the range at the locked minor; raise the cap together with the lock."
    )


def test_the_release_that_removed_the_cancel_is_refused():
    assert Version("0.26.0") not in _declared_range()


def test_the_lock_records_the_declared_range():
    """`uv sync --locked` refuses a lock whose recorded range is not pyproject's."""
    assert _range_recorded_in_the_lock() == _declared_range()
