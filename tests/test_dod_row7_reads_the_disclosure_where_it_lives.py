"""DoD item 7 looks for the disclosure in the section where it lives (workflow FINDINGS W-54).

The item asks that a new background, persistent or network behaviour changes
the "Background behavior, fully disclosed" section. `dod_checklist.py` looked
for the words in the diff of `README.md`. The section moved to `SECURITY.md`
when the README was shortened, so a change that disclosed itself there read
`unmet` and `gh pr create` was refused (#1040, whose watcher change wrote the
sentence in the right place).

The row now compares the section's BODY at the merge base and at HEAD, in
either file. A change elsewhere in the file is not a disclosure.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / ".claude" / "hooks"


@pytest.fixture(scope="module")
def dod():
    sys.path.insert(0, str(HOOKS))
    try:
        spec = importlib.util.spec_from_file_location("_wf_dod_row7", HOOKS / "dod_checklist.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(HOOKS))


SECURITY = (
    "# Security\n\n## Summary of Controls\n\n- a control\n\n"
    "## Background behavior, fully disclosed\n\n- **File watching.** Re-indexes files when they change.\n\n"
    "## Optional extras\n\n- an extra\n"
)
README = "# Product\n\nEverything is enumerated in SECURITY.md.\n"


def test_a_sentence_added_to_the_section_in_security_md_is_a_disclosure(dod):
    head = SECURITY.replace("when they change.", "when they change. It also reads `HEAD` once a second.")
    assert dod.disclosure_changed({"SECURITY.md": SECURITY, "README.md": README},
                                  {"SECURITY.md": head, "README.md": README}) is True


def test_a_change_elsewhere_in_the_file_is_not_a_disclosure(dod):
    head = SECURITY.replace("- a control", "- a control, reworded").replace("- an extra", "- another extra")
    assert dod.disclosure_changed({"SECURITY.md": SECURITY, "README.md": README},
                                  {"SECURITY.md": head, "README.md": README}) is False


def test_an_unchanged_tree_is_not_a_disclosure(dod):
    texts = {"SECURITY.md": SECURITY, "README.md": README}
    assert dod.disclosure_changed(texts, dict(texts)) is False


def test_the_section_in_the_readme_still_counts(dod):
    """A repository that keeps the section in its README is graded the same way."""
    base = README + "\n## Background behavior, fully disclosed\n\n- nothing runs.\n"
    head = base.replace("- nothing runs.", "- a watcher runs.")
    assert dod.disclosure_changed({"README.md": base}, {"README.md": head}) is True


def test_a_section_that_is_new_at_head_is_a_disclosure(dod):
    assert dod.disclosure_changed({"SECURITY.md": "# Security\n"}, {"SECURITY.md": SECURITY}) is True


def test_a_section_that_only_disappeared_is_not_a_disclosure(dod):
    assert dod.disclosure_changed({"SECURITY.md": SECURITY}, {"SECURITY.md": "# Security\n"}) is False


def test_trailing_whitespace_is_not_a_disclosure(dod):
    head = SECURITY.replace("when they change.\n", "when they change.   \n")
    assert dod.disclosure_changed({"SECURITY.md": SECURITY}, {"SECURITY.md": head}) is False


def test_the_section_ends_at_the_next_heading(dod):
    assert dod.disclosure_section(SECURITY) == "- **File watching.** Re-indexes files when they change."
    assert dod.disclosure_section(README) is None


def test_the_real_security_md_holds_the_section(dod):
    """The heading the row looks for is the one the document has."""
    body = dod.disclosure_section((ROOT / "SECURITY.md").read_text(encoding="utf-8"))
    assert body and "File watching" in body
