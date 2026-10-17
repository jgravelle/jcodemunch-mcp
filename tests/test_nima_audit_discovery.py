"""Regressions for two discovery blind spots.

1. ``get_project_intel`` traversal crossed repository boundaries: a
   checkout containing a linked worktree had the worktree's
   infrastructure files leak into the parent repo's intel inventory
   even though the index walk prunes linked worktrees (#372).
2. ``suggest_corrections`` recommended removing ``AskUserQuestion`` --
   a harness tool named in prose in a GLOBAL ``~/.claude/CLAUDE.md`` --
   because it was absent from the repo index. Absence from one
   repository's index is not staleness evidence for an external tool
   name, and no single index can speak for a global config.

The stale-symbol check itself stays: a project config referencing a
name with a fuzzy near-match in the index still warns.
"""

from pathlib import Path

from jcodemunch_mcp.tools.audit_agent_config import _check_stale_symbols
from jcodemunch_mcp.tools.get_project_intel import _discover_intel_files
from jcodemunch_mcp.tools.suggest_corrections import _stale_config_corrections


def _write(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _dotgit_file(dir_path: Path, gitdir: str) -> None:
    _write(dir_path / ".git", f"gitdir: {gitdir}\n")


# ---------------------------------------------------------------------------
# 1. Repository boundary in the intel discovery walk
# ---------------------------------------------------------------------------


def test_linked_worktree_checkout_is_not_in_parent_intel(tmp_path):
    """A linked worktree whose
    ``.git`` FILE points at ``.git/worktrees/<name>`` (#372 boundary)."""
    _write(tmp_path / "Dockerfile", "FROM alpine\n")
    wt = tmp_path / ".linked-worktree"
    _write(wt / "Dockerfile", "FROM scratch\n")
    _dotgit_file(wt, f"{tmp_path.as_posix()}/.git/worktrees/-linked-worktree")

    infra = [Path(p) for p in _discover_intel_files(str(tmp_path))["infra"]]

    assert tmp_path / "Dockerfile" in infra
    assert not any(".linked-worktree" in p.parts for p in infra)


def test_nested_checkout_with_git_directory_reaches_parent_intel(tmp_path):
    """Old-form submodules can carry `.git` as a directory; intel agrees
    with index_folder by pruning only linked worktrees."""
    _write(tmp_path / "Dockerfile", "FROM alpine\n")
    sub = tmp_path / "vendored-checkout"
    _write(sub / "Dockerfile", "FROM scratch\n")
    (sub / ".git").mkdir()

    infra = [Path(p) for p in _discover_intel_files(str(tmp_path))["infra"]]

    assert tmp_path / "Dockerfile" in infra
    assert sub / "Dockerfile" in infra


def test_submodule_content_still_reaches_parent_intel(tmp_path):
    """Submodules are the #372 exception: their content IS indexed into
    the parent, so intel keeps reporting it."""
    _write(tmp_path / "Dockerfile", "FROM alpine\n")
    sub = tmp_path / "vendor-lib"
    _write(sub / "Dockerfile", "FROM busybox\n")
    _dotgit_file(sub, f"{tmp_path.as_posix()}/.git/modules/vendor-lib")

    infra = [Path(p) for p in _discover_intel_files(str(tmp_path))["infra"]]

    assert tmp_path / "Dockerfile" in infra
    assert sub / "Dockerfile" in infra


def test_nested_submodule_name_still_reaches_parent_intel(tmp_path):
    """A relative gitdir may have a multi-segment name below .git/modules."""
    _write(tmp_path / "Dockerfile", "FROM alpine\n")
    sub = tmp_path / "vendor-lib"
    _write(sub / "Dockerfile", "FROM busybox\n")
    _dotgit_file(sub, "../.git/modules/vendor/widget")

    infra = [Path(p) for p in _discover_intel_files(str(tmp_path))["infra"]]

    assert tmp_path / "Dockerfile" in infra
    assert sub / "Dockerfile" in infra


def test_non_worktree_gitdir_still_reaches_parent_intel(tmp_path):
    """A gitdir outside `.git/worktrees` is not a linked worktree; intel
    agrees with index_folder instead of inventing a second boundary."""
    _write(tmp_path / "Dockerfile", "FROM alpine\n")
    sub = tmp_path / "old-form-submodule"
    _write(sub / "Dockerfile", "FROM scratch\n")
    _dotgit_file(sub, "../metadata/modules/vendor/widget")

    infra = [Path(p) for p in _discover_intel_files(str(tmp_path))["infra"]]

    assert tmp_path / "Dockerfile" in infra
    assert sub / "Dockerfile" in infra


def test_scoped_intel_allows_old_form_submodule_root(tmp_path):
    """Scoped discovery must agree with index_folder for old-form submodules."""
    sub = tmp_path / "vendored-checkout"
    _write(sub / "Dockerfile", "FROM scratch\n")
    (sub / ".git").mkdir()

    infra = [
        Path(p)
        for p in _discover_intel_files(str(tmp_path), "vendored-checkout")["infra"]
    ]

    assert infra == [sub / "Dockerfile"]


def test_scoped_intel_allows_old_form_submodule_descendant(tmp_path):
    sub = tmp_path / "vendored-checkout"
    _write(sub / "pkg" / "Dockerfile", "FROM scratch\n")
    (sub / ".git").mkdir()

    infra = [
        Path(p)
        for p in _discover_intel_files(str(tmp_path), "vendored-checkout/pkg")["infra"]
    ]

    assert infra == [sub / "pkg" / "Dockerfile"]


def test_scoped_intel_refuses_linked_worktree(tmp_path):
    wt = tmp_path / ".linked-worktree"
    _write(wt / "Dockerfile", "FROM scratch\n")
    _dotgit_file(wt, f"{tmp_path.as_posix()}/.git/worktrees/-linked-worktree")

    found = _discover_intel_files(str(tmp_path), ".linked-worktree")

    assert all(not rows for rows in found.values())


def test_scoped_intel_allows_submodule(tmp_path):
    sub = tmp_path / "vendor-lib"
    _write(sub / "Dockerfile", "FROM busybox\n")
    _dotgit_file(sub, "../.git/modules/vendor/widget")

    infra = [
        Path(p) for p in _discover_intel_files(str(tmp_path), "vendor-lib")["infra"]
    ]

    assert infra == [sub / "Dockerfile"]


# ---------------------------------------------------------------------------
# 2. Evidence-qualified stale-symbol findings
# ---------------------------------------------------------------------------


def _config(content: str, scope: str = "project") -> dict:
    return {"path": "CLAUDE.md", "content": content, "scope": scope}


def test_near_match_on_project_config_is_still_a_stale_warning():
    findings = _check_stale_symbols(
        _config("Use `handleAut` for authentication"), None, {"handleAuth"}
    )
    assert findings[0]["severity"] == "warning"
    assert findings[0]["category"] == "stale_reference"


def test_absent_external_tool_reference_is_unresolved_not_stale():
    """A harness tool named in config prose. Absence from the index
    must not be reported as a stale reference."""
    findings = _check_stale_symbols(
        _config(
            "when clarification is required use AskUserQuestion "
            "instead of ending with a prose question"
        ),
        None,
        {"authenticateUser", "processOrder"},
    )
    assert len(findings) == 1
    f = findings[0]
    assert f["severity"] == "info"
    assert f["category"] == "unresolved_reference"


def test_global_config_absence_is_unresolved_even_with_near_match():
    """A global config spans repositories; one repo's index cannot
    establish staleness for it."""
    findings = _check_stale_symbols(
        _config("Use `handleAut` for authentication", scope="global"),
        None,
        {"handleAuth"},
    )
    assert findings[0]["severity"] == "info"
    assert findings[0]["category"] == "unresolved_reference"


# ---------------------------------------------------------------------------
# 3. Corrections stay evidence-qualified
# ---------------------------------------------------------------------------


def test_unresolved_references_do_not_become_removal_corrections():
    audit = {
        "findings": [
            {
                "category": "unresolved_reference",
                "severity": "info",
                "message": "References 'AskUserQuestion' which was not found ...",
                "file": "C:/Users/x/.claude/CLAUDE.md",
                "line": 41,
            },
            {
                "category": "stale_reference",
                "severity": "warning",
                "message": "References symbol 'oldFn' ... Did you mean 'newFn'?",
                "file": "CLAUDE.md",
                "line": 3,
            },
        ]
    }
    corrections = _stale_config_corrections(audit)
    assert len(corrections) == 1
    assert corrections[0]["evidence"]["line"] == 3
