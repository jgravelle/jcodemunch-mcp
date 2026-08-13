"""`CLAUDE.md`'s `Current State` block names the three newest releases, in order.

That block is three long bullets maintained by hand, and every release rewrites
the top one and demotes the rest. The edit is string surgery on prose, so it
fails QUIETLY: a clobbered entry, a stale `Prior (...)` label, or an `Older
releases (X and earlier)` boundary that no longer lines up all read fine and are
all wrong. On 2026-08-02 a single release produced two of those three in one
sitting -- `1.108.212` was overwritten rather than demoted, and the boundary line
was left naming a version still in rotation.

Nothing downstream breaks when this drifts, which is exactly the problem: the
file is loaded into every session under this directory and is read as current
state. A wrong version history here misinforms the reader silently and forever.

Same argument as `test_lockfile_version_sync.py`: a convention that has already
failed twice needs a gate, not another checklist line.

Failure here means: fix the `Current State` bullets at the top of CLAUDE.md so
they name the three newest CHANGELOG releases, newest first, with the `Older
releases (X and earlier)` line naming the fourth.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROTATION_SIZE = 3  # CLAUDE.md maintenance practice: keep the 3 newest only


def _pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match, "no version found in pyproject.toml"
    return match.group(1)


def _changelog_versions() -> list[str]:
    """Released versions, newest first. `## [Unreleased]` is skipped by the
    pattern -- it carries no version and must never enter the rotation."""
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    return re.findall(r"^## \[(\d+\.\d+\.\d+)\]", text, re.MULTILINE)


# ---------------------------------------------------------------------------
# Heading well-formedness (2026-08-13)
# ---------------------------------------------------------------------------
#
# ⚠⚠ Every check above matches `^## \[...\]` -- LINE-ANCHORED. A heading fused
# to the end of a preceding paragraph is invisible to all of them, so the file
# can be visibly corrupt while the whole gate passes.
#
# Demonstrated, not hypothetical. On 2026-08-13 a rebase on PR #451 dropped the
# `1.108.273` section; the restore then re-inserted it TWICE -- once correctly,
# and once fused to the `.274` section's closing sentence:
#
#     open against it.## [1.108.273] - 2026-08-12 - A pattern that names two ...
#
# The rotation gate found exactly one well-formed `.273`, agreed with CLAUDE.md's
# boundary, and passed on all nine CI jobs over a changelog that rendered `.274`
# ending mid-sentence followed by a duplicate release section.
#
# ⚠ The maintainer review missed it for the SAME reason: the fix was verified
# with a line-anchored regex, i.e. the same instrument as the gate, carrying the
# same blind spot. It reported "byte-identical, nothing lost, 517 versions both
# sides" -- all true, and all blind to duplication. **A check that shares an
# assumption with the thing it checks is not independent evidence.** These two
# functions exist to break that shared assumption: `_glued_headings` deliberately
# does NOT anchor, and `_duplicate_versions` counts rather than compares.

_ANY_VERSION_HEADING = re.compile(r"## \[(\d+\.\d+\.\d+)\]")


def _glued_headings(text: str) -> list[str]:
    """Version headings that are not at the start of a line.

    Deliberately UNANCHORED -- that is the entire point. Returns the offending
    line's text (trimmed) for each, so a failure names what to look for rather
    than only a count.
    """
    out: list[str] = []
    for m in _ANY_VERSION_HEADING.finditer(text):
        if m.start() == 0 or text[m.start() - 1] == "\n":
            continue
        line_start = text.rfind("\n", 0, m.start()) + 1
        line_end = text.find("\n", m.start())
        out.append(text[line_start: line_end if line_end != -1 else len(text)].strip()[:120])
    return out


def _duplicate_versions(versions: list[str]) -> list[str]:
    """Versions whose heading appears more than once, in first-seen order."""
    seen: set[str] = set()
    dupes: list[str] = []
    for v in versions:
        if v in seen and v not in dupes:
            dupes.append(v)
        seen.add(v)
    return dupes


def _claude_md() -> str:
    return (ROOT / "CLAUDE.md").read_text(encoding="utf-8")


def _rotation_versions() -> list[str]:
    """The versions CLAUDE.md's Current State claims, newest first."""
    text = _claude_md()
    current = re.search(r"^- \*\*Version:\*\*\s*(\d+\.\d+\.\d+)", text, re.MULTILINE)
    assert current, "no `- **Version:** X.Y.Z` line in CLAUDE.md"
    priors = re.findall(r"^- \*\*Prior \((\d+\.\d+\.\d+)\)", text, re.MULTILINE)
    return [current.group(1)] + priors


def _older_boundary() -> str:
    match = re.search(
        r"^- \*\*Older releases \((\d+\.\d+\.\d+) and earlier\)", _claude_md(), re.MULTILINE
    )
    assert match, "no `- **Older releases (X and earlier):**` line in CLAUDE.md"
    return match.group(1)


def test_current_state_names_the_shipping_version():
    rotation = _rotation_versions()
    pyproject = _pyproject_version()
    assert rotation[0] == pyproject, (
        f"CLAUDE.md leads with {rotation[0]} but pyproject.toml says {pyproject}. "
        f"The version bump did not reach the Current State block."
    )


def test_rotation_holds_exactly_the_three_newest_releases():
    rotation = _rotation_versions()
    expected = _changelog_versions()[:ROTATION_SIZE]
    assert rotation == expected, (
        f"CLAUDE.md Current State lists {rotation}, but the {ROTATION_SIZE} newest "
        f"CHANGELOG releases are {expected}. An entry was clobbered, demoted out of "
        f"order, or left behind."
    )


def test_rotation_has_no_duplicate_or_missing_entries():
    """A clobbered entry shows up as a short rotation, a duplicated label as a
    repeat. Both are silent on their own."""
    rotation = _rotation_versions()
    assert len(rotation) == ROTATION_SIZE, (
        f"expected {ROTATION_SIZE} entries (1 Version + {ROTATION_SIZE - 1} Prior), "
        f"found {len(rotation)}: {rotation}"
    )
    assert len(set(rotation)) == len(rotation), f"duplicate versions in rotation: {rotation}"


def test_older_releases_boundary_starts_below_the_rotation():
    """The boundary must name the FOURTH-newest release. Naming one still in
    rotation claims it is archived while it is quoted in full above."""
    changelog = _changelog_versions()
    rotation = _rotation_versions()
    boundary = _older_boundary()
    assert boundary not in rotation, (
        f"`Older releases ({boundary} and earlier)` names a version still in the "
        f"Current State rotation {rotation}."
    )
    if len(changelog) > ROTATION_SIZE:
        expected = changelog[ROTATION_SIZE]
        assert boundary == expected, (
            f"boundary says {boundary}; the release below the rotation is {expected}."
        )


# ---------------------------------------------------------------------------
# Heading well-formedness gate (2026-08-13, from PR #451)
# ---------------------------------------------------------------------------

def test_no_version_heading_is_fused_to_a_paragraph():
    """A `## [X.Y.Z]` heading must start its own line.

    ⚠ This is the one check in this file that does NOT anchor its pattern.
    Every other check here would pass over the exact corruption it catches.
    """
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    glued = _glued_headings(text)
    assert not glued, (
        "CHANGELOG.md has version heading(s) fused to the preceding text, so "
        "they render as body copy and are invisible to every line-anchored "
        f"check in this file: {glued}"
    )


def test_no_release_section_appears_twice():
    """A released version may head exactly one section.

    A duplicate is what a re-inserted section looks like when the first attempt
    was not fully removed. Counting catches it; comparing against `main` does
    not, because both copies are byte-identical to the original.
    """
    versions = _changelog_versions()
    dupes = _duplicate_versions(versions)
    assert not dupes, f"CHANGELOG.md heads these versions more than once: {dupes}"


# --- non-vacuity: prove both predicates fire on the real 2026-08-13 shape ----

_CORRUPT_FIXTURE = """# Changelog

## [1.108.276] - 2026-08-13 - A title

Body text of the newest release.

## [1.108.274] - 2026-08-12 - Another title

with @elfrost's [PR #443](https://example.invalid/pull/443)
open against it.## [1.108.273] - 2026-08-12 - A pattern that names two extensions

Duplicated body.

## [1.108.273] - 2026-08-12 - A pattern that names two extensions

Duplicated body.
"""


def test_glued_heading_predicate_fires_on_the_known_bad_shape():
    """⚠ Guards against the gate being written so it cannot fail.

    The fixture is the real shape from PR #451, not an invented one.
    """
    glued = _glued_headings(_CORRUPT_FIXTURE)
    assert len(glued) == 1
    assert glued[0].startswith("open against it.## [1.108.273]")


def test_duplicate_predicate_fires_on_the_known_bad_shape():
    """The line-anchored extractor sees ONE `.273` in the fixture, which is
    exactly why the duplicate check alone would not have caught this and the
    glued check is the load-bearing half."""
    anchored = re.findall(r"^## \[(\d+\.\d+\.\d+)\]", _CORRUPT_FIXTURE, re.MULTILINE)
    assert anchored.count("1.108.273") == 1, (
        "if this ever counts 2, the fixture no longer reproduces the failure "
        "mode -- the whole point is that the fused copy is invisible to an "
        "anchored pattern"
    )
    assert _duplicate_versions(anchored) == []


def test_both_predicates_pass_on_a_clean_changelog():
    """Control: neither predicate fires on well-formed input, so a green result
    on the real file means something."""
    clean = _CORRUPT_FIXTURE.replace(
        "open against it.## [1.108.273] - 2026-08-12 - A pattern that names two extensions\n\nDuplicated body.\n\n",
        "open against it.\n\n",
    )
    assert _glued_headings(clean) == []
    assert _duplicate_versions(
        re.findall(r"^## \[(\d+\.\d+\.\d+)\]", clean, re.MULTILINE)
    ) == []
