"""L-195: the two answers to "does this index belong to this folder" agree.

`tools/_visibility.in_workspace` scopes `list_repos` and the bare-name choice;
`cli/hooks/_common._path_overlaps` scopes the briefing hook and the steering
hooks. Both count a folder as matching when it is the index's folder, inside
it, or contains it, and until this file nothing kept them agreeing.

They differ on purpose in one place, which this file pins as well: the
listing treats the home folder and a filesystem root as no project at all
(`is_project_folder`, jjg's ruling 2026-10-15), because "the indexes under
home" is nearly the whole store. The hooks ask a different question, whether
jcm can serve a search there, and a search over home that contains an indexed
repository still can.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from jcodemunch_mcp.cli.hooks._common import _norm_path, _path_overlaps
from jcodemunch_mcp.tools._visibility import in_workspace, is_project_folder


def _cases(tmp_path: Path) -> list[tuple[str, Path, Path]]:
    proj = tmp_path / "proj"
    sub = proj / "pkg" / "deep"
    sibling = tmp_path / "proj2"  # shares a string prefix with `proj`
    other = tmp_path / "elsewhere" / "repo"
    for d in (sub, sibling, other):
        d.mkdir(parents=True)
    cases = [
        ("same folder", proj, proj),
        ("workspace inside the index", sub, proj),
        ("index inside the workspace", proj, sub),
        ("prefix sibling", proj, sibling),
        ("prefix sibling, reversed", sibling, proj),
        ("unrelated", proj, other),
    ]
    if os.name == "nt":
        cases.append(("case differs", Path(str(proj).upper()), proj))
    return cases


def test_both_rules_give_the_same_answer_for_a_project_folder(tmp_path):
    disagreements = []
    for label, workspace, index_root in _cases(tmp_path):
        listing = in_workspace({"source_root": str(index_root)}, workspace.resolve())
        hooks = _path_overlaps(_norm_path(str(workspace)), [_norm_path(str(index_root))])
        if listing != hooks:
            disagreements.append(f"{label}: in_workspace={listing} _path_overlaps={hooks}")
    assert not disagreements, disagreements


def test_the_cases_are_not_all_one_answer(tmp_path):
    """A table where every row matches (or none does) could not see a split."""
    answers = {
        in_workspace({"source_root": str(r)}, w.resolve()) for _, w, r in _cases(tmp_path)
    }
    assert answers == {True, False}


@pytest.mark.parametrize("which", ["home", "root"])
def test_home_and_root_are_the_one_deliberate_difference(which):
    folder = Path.home().resolve() if which == "home" else Path(Path.home().resolve().anchor)
    inside = Path.home().resolve() / "some" / "repo"

    assert is_project_folder(folder) is False, "the listing treats it as no project"
    assert _path_overlaps(_norm_path(str(folder)), [_norm_path(str(inside))]) is True, (
        "the hooks still serve a search that contains an indexed repository"
    )
