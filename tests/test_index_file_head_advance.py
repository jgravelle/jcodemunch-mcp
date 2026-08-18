"""#493: a single-file refresh must not certify a corpus it never re-read.

``index_file`` wrote live HEAD as the repository's stored SHA. ``repo_is_stale``
is "index SHA differs from live HEAD", so refreshing one file out of a two-file
commit cleared the staleness signal for the file that was never refreshed, and
``get_file_content`` served commit-A content reporting ``channels.index: fresh``
against a clean working tree.

The write itself is not the defect. ``index_folder``'s
``_refresh_git_head_if_advanced`` performs the identical write on a no-change run
(#330) and is correct, because that run walked the corpus first. What differs is
what has been proven before the write, which is where the fix lives.
"""

import subprocess

import pytest

from jcodemunch_mcp.tools import index_file as index_file_mod
from jcodemunch_mcp.tools.get_file_content import get_file_content
from jcodemunch_mcp.tools.index_file import index_file
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.list_repos import list_repos
from jcodemunch_mcp.tools.search_symbols import search_symbols


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=True,
        capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A two-file git repo at commit A, indexed. Returns a small helper object."""
    work = tmp_path / "repo"
    work.mkdir()
    store = tmp_path / "store"
    store.mkdir()
    _git(work, "init", "-q")
    _git(work, "config", "user.email", "repro@example.invalid")
    _git(work, "config", "user.name", "repro")
    (work / "alpha.py").write_text("def alpha_v1():\n    return 1\n")
    (work / "beta.py").write_text("def beta_v1():\n    return 1\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "A")

    result = index_folder(
        path=str(work), identity_mode="local", use_ai_summaries=False,
        storage_path=str(store),
    )
    assert result["success"] is True

    class Repo:
        path = work
        store_path = str(store)
        repo_id = result["repo"]

        @staticmethod
        def stored_head():
            for entry in list_repos(storage_path=str(store)).get("repos", []):
                if entry.get("repo") == result["repo"]:
                    return entry.get("git_head") or ""
            return ""

        @staticmethod
        def live_head():
            return _git(work, "rev-parse", "HEAD")

        @staticmethod
        def repo_is_stale():
            response = search_symbols(
                repo=result["repo"], query="beta", detail_level="full",
                max_results=5, storage_path=str(store),
            )
            freshness = (response.get("_meta") or {}).get("freshness") or {}
            return freshness.get("repo_is_stale")

        @staticmethod
        def refresh(name):
            return index_file(
                path=str(work / name), use_ai_summaries=False,
                storage_path=str(store),
            )

    return Repo


class TestAPartialRefreshDoesNotCertifyTheCorpus:
    """The reported defect, as its two observable halves."""

    def test_multi_file_commit_then_one_refresh_leaves_the_repo_stale(self, repo):
        (repo.path / "alpha.py").write_text("def alpha_v2():\n    return 2\n")
        (repo.path / "beta.py").write_text("def beta_v2():\n    return 2\n")
        _git(repo.path, "add", "-A")
        _git(repo.path, "commit", "-q", "-m", "B")
        assert repo.repo_is_stale() is True, "precondition: the repo moved"

        repo.refresh("alpha.py")

        assert repo.repo_is_stale() is True, (
            "refreshing alpha.py cleared the staleness signal for beta.py, "
            "which is still at commit A"
        )

    def test_the_unrefreshed_file_is_not_served_as_fresh(self, repo):
        (repo.path / "alpha.py").write_text("def alpha_v2():\n    return 2\n")
        (repo.path / "beta.py").write_text("def beta_v2():\n    return 2\n")
        _git(repo.path, "add", "-A")
        _git(repo.path, "commit", "-q", "-m", "B")
        repo.refresh("alpha.py")

        got = get_file_content(
            repo=repo.repo_id, file_path="beta.py", storage_path=repo.store_path,
        )
        # The served bytes really are the old commit's - that half is expected,
        # and is why the signal matters.
        assert "beta_v1" in got.get("content", "")
        channels = ((got.get("_meta") or {}).get("verdict") or {}).get("channels") or {}
        assert channels.get("index") != "fresh", (
            f"served commit-A content with channels.index={channels.get('index')!r} "
            "against a clean working tree"
        )


class TestTheHeadStillAdvancesWhenItIsEarned:
    """These are what constrain the fix. Refusing to ever advance would pass
    every assertion in the class above and be a worse bug: every repository
    would read stale forever."""

    def test_head_advances_when_the_refreshed_file_was_the_only_change(self, repo):
        (repo.path / "alpha.py").write_text("def alpha_v2():\n    return 2\n")
        _git(repo.path, "add", "-A")
        _git(repo.path, "commit", "-q", "-m", "B")
        assert repo.repo_is_stale() is True

        repo.refresh("alpha.py")

        assert repo.stored_head() == repo.live_head()
        assert repo.repo_is_stale() is False, (
            "the refresh brought the corpus into line with HEAD; the head must "
            "advance or every single-file commit leaves the repo reading stale"
        )

    def test_an_uncommitted_edit_leaves_the_head_where_it_was(self, repo):
        """The hook path: PostToolUse refreshes on every write, with no commit.

        Control - stored and live head are equal, so the write is a no-op either
        way. It passes before and after the fix.
        """
        before = repo.stored_head()
        (repo.path / "alpha.py").write_text("def alpha_v2():\n    return 2\n")
        repo.refresh("alpha.py")
        assert repo.stored_head() == before == repo.live_head()

    def test_a_non_source_file_change_does_not_block_the_advance(self, repo):
        """#330's intent, applied here: a commit touching only files the indexer
        would never carry leaves the corpus accurate."""
        (repo.path / "alpha.py").write_text("def alpha_v2():\n    return 2\n")
        (repo.path / "NOTES.txt").write_text("not indexed\n")
        _git(repo.path, "add", "-A")
        _git(repo.path, "commit", "-q", "-m", "B")

        repo.refresh("alpha.py")

        assert repo.stored_head() == repo.live_head()

    def test_a_no_change_index_folder_run_still_advances_the_head(self, repo):
        """#330 must not regress. That fix is the same write, made correct by a
        walk that established nothing indexed had changed."""
        (repo.path / "NOTES.txt").write_text("not indexed\n")
        _git(repo.path, "add", "-A")
        _git(repo.path, "commit", "-q", "-m", "B")

        index_folder(
            path=str(repo.path), identity_mode="local", use_ai_summaries=False,
            storage_path=repo.store_path,
        )

        assert repo.stored_head() == repo.live_head()
        assert repo.repo_is_stale() is False

    def test_a_full_reindex_records_the_current_head(self, repo):
        (repo.path / "alpha.py").write_text("def alpha_v2():\n    return 2\n")
        (repo.path / "beta.py").write_text("def beta_v2():\n    return 2\n")
        _git(repo.path, "add", "-A")
        _git(repo.path, "commit", "-q", "-m", "B")

        index_folder(
            path=str(repo.path), identity_mode="local", use_ai_summaries=False,
            storage_path=repo.store_path,
        )

        assert repo.stored_head() == repo.live_head()
        assert repo.repo_is_stale() is False


class TestUnknownIsNeverProof:
    """v1.108.209's rule, applied to a different comparison: never answer
    'fresh' for a question that could not be asked."""

    def test_an_added_source_file_blocks_the_advance(self, repo):
        """Not in the corpus, so it cannot be 'a file we carry that moved' - but
        the indexer would take it, so advancing certifies an index missing a
        file."""
        (repo.path / "alpha.py").write_text("def alpha_v2():\n    return 2\n")
        (repo.path / "gamma.py").write_text("def gamma():\n    return 3\n")
        _git(repo.path, "add", "-A")
        _git(repo.path, "commit", "-q", "-m", "B")

        repo.refresh("alpha.py")

        assert repo.stored_head() != repo.live_head()
        assert repo.repo_is_stale() is True

    def test_an_unanswerable_diff_does_not_advance(self, repo, monkeypatch):
        (repo.path / "alpha.py").write_text("def alpha_v2():\n    return 2\n")
        _git(repo.path, "add", "-A")
        _git(repo.path, "commit", "-q", "-m", "B")
        before = repo.stored_head()

        monkeypatch.setattr(
            index_file_mod, "_paths_changed_between", lambda *a, **k: None
        )
        repo.refresh("alpha.py")

        assert repo.stored_head() == before, (
            "a git failure must leave the head behind - unknown resolves to "
            "'cannot prove', never to 'nothing changed'"
        )

    def test_an_empty_result_is_distinguishable_from_a_failure(self, repo):
        """The helper returns None for 'could not ask' and a set for 'asked'.
        Collapsing the two would make a failed git call read as a clean diff,
        which is the defect this whole file is about wearing a new hat."""
        head = repo.live_head()
        same = index_file_mod._paths_changed_between(repo.path, head, head)
        assert same == set(), "two identical commits differ in no paths"
        assert index_file_mod._paths_changed_between(
            repo.path, "0" * 40, head
        ) is None, "an unknown commit is unanswerable, not empty"
