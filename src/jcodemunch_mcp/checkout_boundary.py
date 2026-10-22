"""Is a path inside an indexed root really part of that root's checkout?

Containment is a statement about the FILESYSTEM; every caller wants one about
the REPOSITORY. A nested independent clone, or a linked worktree nested inside
an indexed checkout (``<repo>/.claude/worktrees/<name>``), satisfies the first
and fails the second (#492, #1202).

⚠⚠ A LEAF, stdlib only, stats and small reads, never a subprocess. The session
hooks start on every tool call, and importing ``tools.resolve_repo`` for these
two functions would add that module's whole import chain to every hook (run
``python -X importtime -c "import jcodemunch_mcp.tools.resolve_repo"`` to see
its cost on your box); ``resolve_repo``'s fast path 1 exists to avoid a process
spawn that can hang (#303). Moved here from ``tools/resolve_repo.py`` so
``resolve_repo``, ``index_file`` and the session hooks share one rule.
``tests/test_resolve_repo_nested_worktree.py`` pins the stdlib-only imports.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


def dotgit_kind(directory: Path) -> Optional[str]:
    """Classify a ``.git`` entry: "repo", "submodule", "worktree", or None.

    A ``.git`` DIRECTORY is an independent repository. A ``.git`` FILE carries a
    ``gitdir:`` pointer, and where it points is the whole distinction #372 drew:
    ``.git/worktrees/<name>`` is a linked worktree, ``.git/modules/<path>`` is a
    submodule. Anything else pointing outside both is a ``--separate-git-dir``
    clone, which is independent.
    """
    dotgit = directory / ".git"
    try:
        if dotgit.is_dir():
            return "repo"
        if not dotgit.is_file():
            return None
        text = dotgit.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return None
    if not text.startswith("gitdir:"):
        return None
    target = Path(text[len("gitdir:"):].strip())
    if not target.is_absolute():
        target = directory / target
    parent_name = target.parent.name
    if parent_name == "worktrees":
        return "worktree"
    # A submodule name can span directories below .git/modules.
    if parent_name == "modules" or any(
        ancestor.name == "modules" and ancestor.parent.name == ".git"
        for ancestor in target.parents
    ):
        return "submodule"
    return "repo"


def separate_checkout_between(
    path: Path, source_root: Path, *, worktrees: bool = False,
) -> Optional[Path]:
    """Root of a separate checkout between ``path`` and ``source_root``, or None.

    An independent repository always counts. With ``worktrees=True`` a linked
    worktree counts too. Returns the deepest such root, or None when ``path``
    really does belong to ``source_root``.

    ⚠ Submodules deliberately do NOT count. Their content IS indexed into the
    parent, which is the boundary #372 drew when it excluded linked worktrees
    without changing submodule behaviour.

    ⚠⚠ Who passes ``worktrees=True`` (#1202). A worktree shares the repository
    but not the branch, and #372 keeps a nested one out of the parent's corpus.
    ``resolve_repo`` passes it: its containment fast path ran before its worktree
    path, so a worktree nested under ``source_root`` came back as the parent's
    index and every read came from another branch. The session hooks pass it
    for the same reason: they briefed and steered an agent in a nested worktree
    with the parent's index. ``index_file`` leaves it off: it refuses a
    nested-worktree file with its own, accurate ``nested_worktree`` reason, and
    a worktree boundary there would replace that with "a different
    repository", which is false.
    """
    try:
        candidates = [path, *path.parents]
    except (OSError, ValueError):
        return None
    for directory in candidates:
        if directory == source_root:
            return None  # reached the indexed root without crossing a boundary
        try:
            if not directory.is_relative_to(source_root):
                return None
        except (OSError, ValueError, AttributeError):
            return None
        kind = dotgit_kind(directory)
        if kind == "repo" or (worktrees and kind == "worktree"):
            return directory
    return None
