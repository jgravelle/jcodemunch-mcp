"""#1202: a linked worktree nested inside an indexed checkout is not that checkout.

``resolve_repo`` answers a linked worktree with ``indexed: false`` plus the main
index as a ``canonical_candidates`` entry (#277, #303), because the worktree can
be on any branch. That answer came from fast path 2, but fast path 1
(``source_root`` containment) runs first, and its boundary check (#492) counted
only an independent ``.git`` DIRECTORY. A worktree nested inside the checkout,
such as ``<repo>/.claude/worktrees/<name>``, has a ``.git`` FILE, so containment
matched the parent and returned ``indexed: true`` with the parent's
``source_root``. Every read then came from the other branch. The same worktree
at a sibling path got the right answer.

Reported by @zaphod72.
"""

import subprocess

import pytest

from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.resolve_repo import resolve_repo


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def main_repo(tmp_path):
    """An indexed checkout with two commits; worktrees are gitignored, as #372 assumes."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "repro@example.invalid")
    _git(repo, "config", "user.name", "repro")
    (repo / ".gitignore").write_text(".claude/\n")
    (repo / "a.py").write_text("def one():\n    return 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "one")
    (repo / "a.py").write_text("def two():\n    return 2\n")
    _git(repo, "commit", "-q", "-am", "two")
    store = tmp_path / "store"
    out = index_folder(str(repo), use_ai_summaries=False, storage_path=str(store))
    assert out.get("success"), out
    return repo, str(store), out["repo"]


def _add_worktree(repo, path):
    _git(repo, "worktree", "add", "-q", str(path), "HEAD~1")
    return path


def _assert_worktree_answer(res, repo_id):
    assert res["found"] is True, res
    assert res["indexed"] is False, res
    assert res.get("source_root") is None, res
    assert repo_id in [c["repo"] for c in res.get("canonical_candidates") or []], res


def test_a_nested_worktree_is_not_resolved_to_the_parent_index(main_repo):
    repo, store, repo_id = main_repo
    wt = _add_worktree(repo, repo / ".claude" / "worktrees" / "probe")
    _assert_worktree_answer(resolve_repo(str(wt), storage_path=store), repo_id)


def test_a_file_inside_a_nested_worktree_is_not_resolved_to_the_parent(main_repo):
    repo, store, repo_id = main_repo
    wt = _add_worktree(repo, repo / ".claude" / "worktrees" / "probe")
    _assert_worktree_answer(resolve_repo(str(wt / "a.py"), storage_path=store), repo_id)


def test_a_subdirectory_inside_a_nested_worktree_is_not_the_parent(main_repo):
    """The issue says "a path inside" the worktree, not only its root."""
    repo, store, repo_id = main_repo
    wt = _add_worktree(repo, repo / ".claude" / "worktrees" / "probe")
    sub = wt / "pkg"
    sub.mkdir()
    (sub / "m.py").write_text("x = 1\n")
    for p in (sub, sub / "m.py"):
        res = resolve_repo(str(p), storage_path=store)
        assert res["indexed"] is False and res.get("repo") != repo_id, (p, res)
        assert res.get("source_root") is None, (p, res)


def _norm(p):
    from jcodemunch_mcp.cli.hooks._common import _norm_path

    return _norm_path(str(p))


def test_steering_does_not_treat_a_nested_worktree_as_the_indexed_repo(main_repo):
    """The Read/Grep steering uses this test; it said "indexed" for the worktree."""
    from jcodemunch_mcp.cli.hooks._common import _path_overlaps

    repo, _, _ = main_repo
    wt = _add_worktree(repo, repo / ".claude" / "worktrees" / "probe")
    roots = [_norm(repo)]
    assert _path_overlaps(_norm(wt / "a.py"), roots) is False
    assert _path_overlaps(_norm(wt), roots) is False
    # Non-vacuity: the checkout's own files, a plain subdirectory, and a parent
    # folder that contains the indexed repo all still count.
    plain = repo / "lib"
    plain.mkdir()
    assert _path_overlaps(_norm(repo / "a.py"), roots) is True
    assert _path_overlaps(_norm(plain), roots) is True
    assert _path_overlaps(_norm(repo.parent), roots) is True


def test_the_subagent_briefing_does_not_brief_the_parent_in_a_nested_worktree(main_repo):
    from jcodemunch_mcp.cli.hooks.briefing import _select_repos

    repo, _, repo_id = main_repo
    wt = _add_worktree(repo, repo / ".claude" / "worktrees" / "probe")
    repos = [{"repo": repo_id, "source_root": str(repo)}]
    assert _select_repos(repos, str(wt)) == ([], [])
    assert _select_repos(repos, str(repo))[0] == repos  # non-vacuity


def test_a_sibling_worktree_gets_the_same_answer(main_repo, tmp_path):
    """The control: the layout that was already right, and must stay right."""
    repo, store, repo_id = main_repo
    wt = _add_worktree(repo, tmp_path / "sibling")
    _assert_worktree_answer(resolve_repo(str(wt), storage_path=store), repo_id)


def test_a_plain_subdirectory_still_resolves_to_the_parent(main_repo):
    """Non-vacuity: the boundary is the worktree's .git file, not depth or the name."""
    repo, store, repo_id = main_repo
    sub = repo / ".claude" / "worktrees" / "notaworktree"
    sub.mkdir(parents=True)
    res = resolve_repo(str(sub), storage_path=store)
    assert res["indexed"] is True and res["repo"] == repo_id, res


def test_index_file_keeps_its_own_reason_for_a_nested_worktree_file(main_repo):
    """The boundary is resolve_repo's. index_file shares the helper and must still
    say the file is in a nested worktree, never that it is another repository."""
    from jcodemunch_mcp.tools.index_file import index_file

    repo, store, _ = main_repo
    wt = _add_worktree(repo, repo / ".claude" / "worktrees" / "probe")
    out = index_file(str(wt / "a.py"), storage_path=store)
    assert out["success"] is False and out.get("skipped") == "nested_worktree", out


def test_the_startup_note_does_not_claim_an_index_in_a_nested_worktree(main_repo, monkeypatch):
    """The server's "this repo is indexed" note reads resolve_repo's answer, so a
    session started in a nested worktree must not be told the parent's index is its own."""
    from jcodemunch_mcp import server

    repo, store, _ = main_repo
    wt = _add_worktree(repo, repo / ".claude" / "worktrees" / "probe")
    monkeypatch.setenv("CODE_INDEX_PATH", store)
    monkeypatch.chdir(wt)
    assert server._indexed_here() is None
    monkeypatch.chdir(repo)
    assert isinstance(server._indexed_here(), int)  # non-vacuity: the parent still counts


def test_a_nested_worktree_resolves_to_itself_once_indexed(main_repo):
    repo, store, _ = main_repo
    wt = _add_worktree(repo, repo / ".claude" / "worktrees" / "probe")
    own = index_folder(str(wt), use_ai_summaries=False, storage_path=store, identity_mode="local")
    assert own.get("success"), own
    res = resolve_repo(str(wt), storage_path=store)
    assert res["indexed"] is True and res["repo"] == own["repo"], res



def _non_stdlib_imports(source: str) -> set:
    """Imports in ``source`` that are not the standard library (relative ones included)."""
    import ast
    import sys

    stdlib = set(sys.stdlib_module_names) | {"__future__"}
    bad = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            bad.update(a.name for a in node.names if a.name.split(".")[0] not in stdlib)
        elif isinstance(node, ast.ImportFrom):
            if node.level or (node.module or "").split(".")[0] not in stdlib:
                bad.add("." * node.level + (node.module or ""))
    return bad


def test_the_boundary_module_is_a_stdlib_leaf():
    """The session hooks import it on every tool call: it may import only the
    standard library, and it must not spawn a process (#303)."""
    from pathlib import Path as _P

    import jcodemunch_mcp.checkout_boundary as cb

    src = _P(cb.__file__).read_text(encoding="utf-8")
    assert _non_stdlib_imports(src) == set()
    assert _process_spawns(src) == set()


def _process_spawns(source: str) -> set:
    """Process-spawning imports and calls, read from the syntax tree.

    Not a text search: the module's docstring says "never a subprocess", and
    a text scan fails on the explanation instead of the defect.
    """
    import ast

    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names if a.name.split(".")[0] == "subprocess")
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "subprocess":
            found.add(node.module)
        elif (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
              and node.value.id == "os" and (node.attr in ("system", "popen") or node.attr.startswith("spawn"))):
            found.add(f"os.{node.attr}")
    return found


@pytest.mark.parametrize("line", ["from ..storage import IndexStore", "import jcodemunch_mcp.config", "import numpy"])
def test_the_leaf_scan_sees_a_non_stdlib_import(line):
    """Non-vacuity: the scan flags exactly what the leaf rule forbids."""
    assert _non_stdlib_imports(line) != set()


@pytest.mark.parametrize("line", ["import subprocess", "from subprocess import run", "os.system('git status')", "os.popen('x')"])
def test_the_spawn_scan_sees_a_process_spawn(line):
    assert _process_spawns(line) != set()
