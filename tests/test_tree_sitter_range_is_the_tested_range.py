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

import pytest
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10: pytest itself depends on tomli there
    import tomli as tomllib

NL = chr(10)

ROOT = Path(__file__).resolve().parent.parent


def _declared_ranges(text: str) -> list[SpecifierSet]:
    """Every requirement on the runtime itself that a `pyproject.toml` declares:
    the main list, each extra and each dependency group, with or without an
    environment marker. Not `tree-sitter-language-pack` or `tree-sitter-fsharp`.
    A bare name is an empty range, which admits everything."""
    data = tomllib.loads(text)
    project = data.get("project", {})
    lists = [project.get("dependencies", [])]
    lists += list(project.get("optional-dependencies", {}).values())
    lists += list(data.get("dependency-groups", {}).values())
    found = []
    for entries in lists:
        for entry in entries:
            if not isinstance(entry, str):  # a group's `{include-group = ...}`
                continue
            requirement = Requirement(entry)
            if canonicalize_name(requirement.name) == "tree-sitter":
                found.append(requirement.specifier)
    return found


def _declared_range() -> SpecifierSet:
    found = _declared_ranges((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert len(found) == 1, f"expected one `tree-sitter` requirement in pyproject.toml, found {found}"
    return found[0]


def _releases_past(locked: Version) -> list[Version]:
    """A grid of versions later than the locked minor: every later minor of its
    major and of the next three, several patches each."""
    return [
        Version(f"{major}.{minor}.{patch}")
        for major in range(locked.major, locked.major + 4)
        for minor in range(0, 100)
        for patch in (0, 1, 2, 9)
        if (major, minor) > (locked.major, locked.minor)
    ]


def _admitted_past(declared: SpecifierSet, locked: Version) -> list[Version]:
    return [version for version in _releases_past(locked) if version in declared]


def _locked_version() -> Version:
    text = (ROOT / "uv.lock").read_text(encoding="utf-8")
    found = re.findall(r'^name = "tree-sitter"\r?\nversion = "([^"]+)"', text, re.MULTILINE)
    assert len(found) == 1, f"expected one locked `tree-sitter` in uv.lock, found {found}"
    return Version(found[0])


def _range_recorded_in_the_lock() -> SpecifierSet:
    text = (ROOT / "uv.lock").read_text(encoding="utf-8")
    found = re.findall(r'\{ name = "tree-sitter"(?:, marker = "[^"]*")?, specifier = "([^"]+)" \}', text)
    assert len(found) == 1, f"expected one `tree-sitter` specifier in uv.lock's requires-dist, found {found}"
    return SpecifierSet(found[0])


def test_the_declared_range_admits_the_version_ci_runs():
    assert _locked_version() in _declared_range()


def test_the_declared_range_stops_before_the_next_minor():
    locked = _locked_version()
    declared = _declared_range()
    admitted = _admitted_past(declared, locked)
    assert admitted == [], (
        f"pyproject.toml declares tree-sitter{declared}, which admits {admitted[:4]} past the locked "
        f"{locked}. Nothing has run the parse-cancel guard on them: 0.26.0 removed "
        f"Parser.timeout_micros and the parse budget stopped working for every install that "
        f"resolved it. Cap the range at the locked minor; raise the cap together with the lock."
    )


@pytest.mark.parametrize(
    "written, holds",
    [
        (">=0.25,<0.26", True),
        ("~=0.25.0", True),
        ("==0.25.*", True),
        (">=0.25", False),
        (">=0.25,<0.27", False),
        (">=0.25,<1", False),
        ("~=0.25", False),
        (">=0.25,!=0.26.0", False),
        (">=0.25,!=0.26.0,<1", False),      # admits 0.26.1 and 0.27.0
        (">=0.25,!=0.26.*,<1", False),      # admits 0.27.0
        (">=0.25,!=0.26.0,!=1.0.0", False),
        ("", False),                        # a bare name
    ],
)
def test_the_check_holds_the_property_not_two_points(written, holds):
    """An exclusion (`!=0.26.0`) names the release that was reported and admits
    the ones after it; only an upper bound at the locked minor passes."""
    assert (_admitted_past(SpecifierSet(written), Version("0.25.2")) == []) is holds


def test_a_requirement_is_found_wherever_it_is_written():
    text = NL.join([
        '[project]',
        'keywords = ["tree-sitter"]',
        'dependencies = [',
        '    # "tree-sitter>=0.1" in a comment is not a requirement',
        '    "tree-sitter-language-pack>=0.7.0,<1.0.0",',
        '    "tree-sitter-fsharp==0.3.12",',
        '    "tree-sitter>=0.25,<0.26",',
        ']',
        '[project.optional-dependencies]',
        'all = ["x", "tree-sitter>=0.26; python_version >= ' + "'3.12'" + '"]',
        '[dependency-groups]',
        'dev = ["tree_sitter", {include-group = "other"}]',
        'other = ["Tree.Sitter[extra]<1"]',
    ])
    assert [str(r) for r in _declared_ranges(text)] == ["<0.26,>=0.25", ">=0.26", "", "<1"]


def test_the_release_that_removed_the_cancel_is_refused():
    assert Version("0.26.0") not in _declared_range()


def test_the_lock_records_the_declared_range():
    """`uv sync --locked` refuses a lock whose recorded range is not pyproject's."""
    assert _range_recorded_in_the_lock() == _declared_range()
