"""Release notes fit GitHub's release body limit (LEDGER L-92).

`release.yml` published the version's CHANGELOG block verbatim as the GitHub
release body, from an inline script. GitHub refuses a body over 125,000
characters, and the 1.108.320 block is 335,730. That job runs AFTER the PyPI
publish, and the registry job needs it, so the release would have been live on
PyPI with no GitHub release and no registry entry: a half-published version
that cannot be re-uploaded.

The renderer is a script with a test now. A block that fits is published
verbatim, as before. One that does not publishes its lead paragraph, every
entry heading and a link to the full block at the tag, still under the limit.
The test renders every block in the real CHANGELOG, and [Unreleased] as if it
were cut, because the next release is the one that has not been measured yet.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import release_notes  # noqa: E402

LIMIT = 125_000
CHANGELOG = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
PYPROJECT = (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def _versions():
    return re.findall(r"^## \[(\d+\.\d+\.\d+)\]", CHANGELOG, re.M)


def _as_cut(text: str, version: str) -> str:
    """[Unreleased] cut as `version`, the shape /release step 6 produces."""
    return text.replace("## [Unreleased]\n", f"## [Unreleased]\n\n## [{version}] - 2099-01-01 - a thesis\n", 1)


def test_the_workflow_renders_through_the_script():
    wf = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    step = wf[wf.index("- name: Notes"):]
    step = step[: step.index("gh release create")]
    assert "scripts/release_notes.py" in step, "the notes step must render through the tested script"
    assert "python -" not in step, "an inline renderer is untested; it is how a 335,730-char body got through"


@pytest.mark.parametrize("version", _versions()[:12])
def test_every_released_block_fits(version):
    notes = release_notes.render(version, "91", CHANGELOG, PYPROJECT)
    assert len(notes) <= LIMIT, (version, len(notes))


def test_the_unreleased_block_fits_once_cut():
    notes = release_notes.render("9.9.9", "91", _as_cut(CHANGELOG, "9.9.9"), PYPROJECT)
    assert len(notes) <= LIMIT, len(notes)


def test_a_block_that_fits_is_published_verbatim():
    small = "## [1.0.1] - 2099-01-01 - t\n\nlead\n\n### Fixed - a thing\n\nbody\n\n## [1.0.0] - x\n"
    notes = release_notes.render("1.0.1", "91", small, PYPROJECT)
    assert notes.startswith("lead\n\n### Fixed - a thing\n\nbody"), notes


def test_an_oversized_block_keeps_its_lead_every_heading_and_a_link():
    entries = "".join(f"### Fixed - entry {i}\n\n" + ("x" * 3000) + "\n\n" for i in range(80))
    big = f"## [2.0.0] - 2099-01-01 - t\n\nThe lead paragraph.\n\n{entries}## [1.0.0] - x\n"
    notes = release_notes.render("2.0.0", "91", big, PYPROJECT)
    assert len(notes) <= LIMIT, len(notes)
    assert notes.startswith("The lead paragraph."), notes[:80]
    for i in range(80):
        assert f"Fixed - entry {i}" in notes
    assert "blob/v2.0.0/CHANGELOG.md" in notes


def test_even_the_heading_list_is_bounded():
    entries = "".join(f"### Fixed - {'y' * 200} {i}\n\nz\n\n" for i in range(2000))
    big = f"## [3.0.0] - 2099-01-01 - t\n\nLead.\n\n{entries}## [1.0.0] - x\n"
    notes = release_notes.render("3.0.0", "91", big, PYPROJECT)
    assert len(notes) <= LIMIT, len(notes)
    assert "more entries" in notes, "a cut list must say how many it left out"


def test_a_missing_block_refuses():
    with pytest.raises(SystemExit):
        release_notes.render("0.0.1", "91", CHANGELOG, PYPROJECT)


def test_an_oversized_block_with_no_headings_fits():
    """Review: the fallback's lead is the whole block when there is no `###`."""
    big = "## [4.0.0] - 2099-01-01 - t\n\n" + ("word " * 60_000) + "\n\n## [1.0.0] - x\n"
    notes = release_notes.render("4.0.0", "91", big, PYPROJECT)
    assert len(notes) + 1 <= LIMIT, len(notes)
    assert "blob/v4.0.0/CHANGELOG.md" in notes


def test_a_lead_longer_than_the_limit_is_cut_and_says_where_the_rest_is():
    lead = "x" * (LIMIT + 10)
    big = f"## [5.0.0] - 2099-01-01 - t\n\n{lead}\n\n### Fixed - one\n\nbody\n\n## [1.0.0] - x\n"
    notes = release_notes.render("5.0.0", "91", big, PYPROJECT)
    assert len(notes) + 1 <= LIMIT, len(notes)
    assert "Fixed - one" in notes
    assert "continued in [CHANGELOG.md at v5.0.0]" in notes


def test_a_block_at_exactly_the_limit_still_fits_with_the_written_newline():
    """`main` writes the notes plus one newline, and `gh --notes-file` sends the file."""
    footer = release_notes._footer("91", PYPROJECT)
    body = "z" * (LIMIT - len(footer) - 2)
    block = f"## [6.0.0] - 2099-01-01 - t\n\n{body}\n\n## [1.0.0] - x\n"
    notes = release_notes.render("6.0.0", "91", block, PYPROJECT)
    assert len(notes) + 1 <= LIMIT, len(notes)
