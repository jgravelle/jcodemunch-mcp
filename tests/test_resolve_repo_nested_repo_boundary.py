"""#492: containment is a filesystem fact; the caller wants a repository one.

``resolve_repo``'s fast path 1 matched on ``source_root`` containment alone and
returned the enclosing parent index as ``indexed: true`` for a path belonging to
an independent git repository nested inside it. The caller was then bound to a
corpus built from a different checkout with a different history, and nothing in
the response said so.

The ordering is deliberate - fast path 1 exists to avoid the
``resolve_index_identity`` walk that can hang (#303) - so the guard is a
filesystem stat, never a subprocess.
"""

import subprocess

import pytest

from jcodemunch_mcp.tools import resolve_repo as resolve_repo_mod
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.resolve_repo import resolve_repo


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=True,
        capture_output=True, text=True,
    ).stdout.strip()


def _make_repo(path, files, gitignore=None):
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "repro@example.invalid")
    _git(path, "config", "user.name", "repro")
    for name, text in files.items():
        (path / name).write_text(text)
    if gitignore:
        (path / ".gitignore").write_text(gitignore)
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")
    return path


@pytest.fixture
def store(tmp_path):
    d = tmp_path / "store"
    d.mkdir()
    return str(d)


class TestANestedIndependentRepoIsNotTheParent:
    """The reported defect. Both cases must change, not just the visible one."""

    @pytest.mark.parametrize(
        "gitignored",
        [
            pytest.param(True, id="nested-gitignored-by-parent"),
            pytest.param(False, id="parent-absorbed-nested-files"),
        ],
    )
    def test_a_path_in_a_nested_clone_does_not_resolve_to_the_parent(
        self, tmp_path, store, gitignored
    ):
        """⚠ The second case is the one that matters for the acceptance
        criteria. When the parent's walk absorbed the nested files the read
        SUCCEEDS and nothing looks wrong - same mis-resolution, no symptom.
        """
        parent = _make_repo(
            tmp_path / "parent",
            {"marker.py": "def marker():\n    return 1\n"},
            gitignore="nested-repo/\n" if gitignored else None,
        )
        nested = _make_repo(
            parent / "nested-repo", {"alpha.py": "def alpha():\n    return 1\n"}
        )
        parent_result = index_folder(
            path=str(parent), identity_mode="local", use_ai_summaries=False,
            storage_path=store,
        )
        assert parent_result["success"] is True

        got = resolve_repo(path=str(nested / "alpha.py"), storage_path=store)

        assert got.get("indexed") is not True, (
            f"returned the parent index as indexed for a path in another "
            f"repository: {got.get('repo')!r} via "
            f"{(got.get('_meta') or {}).get('match_path')!r}"
        )
        assert got.get("repo") != parent_result["repo"]

    def test_the_nested_repo_resolves_to_itself_once_indexed(self, tmp_path, store):
        """Control: the resolver does the right thing as soon as an exact index
        exists, which holds the path and both repositories valid."""
        parent = _make_repo(
            tmp_path / "parent", {"marker.py": "def marker():\n    return 1\n"},
            gitignore="nested-repo/\n",
        )
        nested = _make_repo(
            parent / "nested-repo", {"alpha.py": "def alpha():\n    return 1\n"}
        )
        index_folder(path=str(parent), identity_mode="local",
                     use_ai_summaries=False, storage_path=store)
        nested_result = index_folder(path=str(nested), identity_mode="local",
                                     use_ai_summaries=False, storage_path=store)

        got = resolve_repo(path=str(nested / "alpha.py"), storage_path=store)

        assert got.get("repo") == nested_result["repo"]
        assert got.get("indexed") is True
        assert (got.get("_meta") or {}).get("match_path") == "exact_source_root"


class TestTheBoundariesTheGuardMustNotCross:
    """These constrain the fix. A guard that blocked everything would satisfy
    the class above and break more than it fixed, so each of these passes both
    before and after by design."""

    def test_a_path_in_a_submodule_still_resolves_to_the_parent(
        self, tmp_path, store
    ):
        """Submodule content IS indexed into the parent. #372 excluded linked
        worktrees specifically WITHOUT changing submodule behaviour, and this
        keeps that boundary where it was."""
        upstream = _make_repo(
            tmp_path / "upstream", {"s.py": "def s():\n    return 1\n"}
        )
        parent = _make_repo(
            tmp_path / "parent", {"p.py": "def p():\n    return 1\n"}
        )
        subprocess.run(
            ["git", "-c", "protocol.file.allow=always", "submodule", "add", "-q",
             str(upstream), "vendor"],
            cwd=str(parent), check=True, capture_output=True, text=True,
        )
        assert (parent / "vendor" / ".git").is_file(), "expected a submodule marker"
        parent_result = index_folder(
            path=str(parent), identity_mode="local", use_ai_summaries=False,
            storage_path=store,
        )

        got = resolve_repo(path=str(parent / "vendor" / "s.py"), storage_path=store)

        assert got.get("repo") == parent_result["repo"]
        assert got.get("indexed") is True

    def test_a_file_outside_the_corpus_still_resolves_to_the_parent(
        self, tmp_path, store
    ):
        """Being outside the corpus and belonging to another repository are
        different conditions. Only the second changes which repo is returned."""
        parent = _make_repo(
            tmp_path / "parent", {"p.py": "def p():\n    return 1\n"},
            gitignore="ignored/\n",
        )
        (parent / "ignored").mkdir()
        (parent / "ignored" / "skipped.py").write_text("def skipped():\n    return 1\n")
        parent_result = index_folder(
            path=str(parent), identity_mode="local", use_ai_summaries=False,
            storage_path=store,
        )

        got = resolve_repo(
            path=str(parent / "ignored" / "skipped.py"), storage_path=store
        )

        assert got.get("repo") == parent_result["repo"]
        assert got.get("indexed") is True

    def test_an_ordinary_path_resolves_by_containment_with_no_subprocess(
        self, tmp_path, store, monkeypatch
    ):
        """⚠ Fast path 1 exists to avoid a walk that can HANG (#303). A
        correctness guard on it that spawns a process would trade the reported
        bug for the one the fast path was built to prevent."""
        parent = _make_repo(
            tmp_path / "parent", {"p.py": "def p():\n    return 1\n"}
        )
        (parent / "pkg").mkdir()
        (parent / "pkg" / "mod.py").write_text("def mod():\n    return 1\n")
        _git(parent, "add", "-A")
        _git(parent, "commit", "-q", "-m", "pkg")
        parent_result = index_folder(
            path=str(parent), identity_mode="local", use_ai_summaries=False,
            storage_path=store,
        )

        def forbidden(*args, **kwargs):
            raise AssertionError(f"fast path 1 spawned a subprocess: {args!r}")

        monkeypatch.setattr(resolve_repo_mod.subprocess, "run", forbidden)
        got = resolve_repo(path=str(parent / "pkg" / "mod.py"), storage_path=store)

        assert got.get("repo") == parent_result["repo"]
        assert got.get("indexed") is True
        assert (got.get("_meta") or {}).get("match_path") == "source_root_containment"


class TestTheClassifier:
    """``_dotgit_kind`` is where the whole distinction lives, so test it against
    real git layouts rather than only through the resolver."""

    def test_a_plain_clone_is_a_repo(self, tmp_path):
        path = _make_repo(tmp_path / "plain", {"a.py": "x = 1\n"})
        assert resolve_repo_mod._dotgit_kind(path) == "repo"

    def test_a_submodule_marker_is_a_submodule(self, tmp_path):
        upstream = _make_repo(tmp_path / "up", {"s.py": "x = 1\n"})
        parent = _make_repo(tmp_path / "par", {"p.py": "x = 1\n"})
        subprocess.run(
            ["git", "-c", "protocol.file.allow=always", "submodule", "add", "-q",
             str(upstream), "vendor"],
            cwd=str(parent), check=True, capture_output=True, text=True,
        )
        assert resolve_repo_mod._dotgit_kind(parent / "vendor") == "submodule"

    def test_a_linked_worktree_marker_is_a_worktree(self, tmp_path):
        main = _make_repo(tmp_path / "main", {"a.py": "x = 1\n"})
        wt = tmp_path / "main" / "wt"
        _git(main, "worktree", "add", "-q", str(wt), "-b", "side")
        assert resolve_repo_mod._dotgit_kind(wt) == "worktree"

    def test_a_directory_with_no_dotgit_is_nothing(self, tmp_path):
        plain = tmp_path / "plain"
        plain.mkdir()
        assert resolve_repo_mod._dotgit_kind(plain) is None

    def test_a_separate_git_dir_clone_counts_as_independent(self, tmp_path):
        """``git clone --separate-git-dir`` leaves a ``.git`` FILE pointing at
        neither ``worktrees/`` nor ``modules/``. Classifying by "is it a file"
        instead of by where it points would read this as a submodule."""
        work = tmp_path / "work"
        work.mkdir()
        gitdir = tmp_path / "elsewhere.git"
        subprocess.run(
            ["git", "init", "-q", "--separate-git-dir", str(gitdir), str(work)],
            check=True, capture_output=True, text=True,
        )
        assert (work / ".git").is_file()
        assert resolve_repo_mod._dotgit_kind(work) == "repo"
