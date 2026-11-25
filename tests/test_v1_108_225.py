"""v1.108.225 — freshness stops contradicting itself, twice.

@rknighton, [#405](https://github.com/jgravelle/jcodemunch-mcp/issues/405) and
[#404](https://github.com/jgravelle/jcodemunch-mcp/issues/404). A matched pair:
one is a per-file classifier answering a per-file question with a repo-wide
fact, the other is a cached row keeping an answer that was computed for a
different moment.

**#405 — the repo SHA short-circuited per-file evidence.** `classify()` returned
`stale_index` the instant the index SHA differed from live HEAD, ten lines above
the per-file mtime comparison that would have shown the file byte-identical to
what was indexed. So one commit touching one file marked *every* file stale.
That is not the safe-direction caution it looks like: `unknown` costs nothing,
but `stale_index` is a positive claim that the indexed content no longer
describes the file — asserted against a measurement already taken and not read.

**#404 — a revalidated cache hit still reported `fresh`.** The verdict was
re-checked and disclosed `revalidated.stale_cache: true`; the rows kept the
`_freshness` stamped when the entry was filled. One payload asserting both.

⚠ Together they change a contract worth stating: `repo_is_stale: true` beside
rows reading `fresh` is now legitimate and non-contradictory — the repository
moved, those files did not. That is the discrimination #405 exists to restore.

`TestNotBlanketLoosening` is the load-bearing class. Both fixes make a signal
say `fresh` more often, and both could be "passed" by always saying it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from jcodemunch_mcp.retrieval.freshness import FreshnessProbe


def _ns(seconds: float) -> int:
    """CodeIndex.file_mtimes stores nanoseconds."""
    return int(seconds * 1e9)


# ── #405: per-file evidence outranks the repo-wide SHA ────────────────────


class TestPerFileEvidenceOutranksRepoSha:
    def test_untouched_file_is_fresh_when_another_file_moved_head(self, tmp_path):
        """A commit that touched some OTHER file must not mark this one stale.

        The file is byte-identical to what was indexed and its mtime has not
        moved, which is positive evidence the index still describes it.
        """
        f = tmp_path / "alpha.py"
        f.write_text("def alpha_fn():\n    return 'alpha'\n", encoding="utf-8")
        probe = FreshnessProbe(
            source_root=str(tmp_path),
            indexed_at="2026-04-25T00:00:00Z",
            index_sha="aaa",
            current_sha="bbb",  # HEAD moved: some other file was committed
            file_mtimes={"alpha.py": _ns(f.stat().st_mtime)},
        )
        assert probe.repo_is_stale is True  # the repo-level fact is unchanged
        assert probe.classify("alpha.py") == "fresh"

    def test_the_repo_level_fact_is_still_reported(self, tmp_path):
        """⚠ The new contract: a stale repo and a fresh row in one payload.

        #405 moves per-file evidence ahead of the SHA; it does NOT hide the
        SHA. A caller that wants the repo-wide answer still has it.
        """
        f = tmp_path / "alpha.py"
        f.write_text("x = 1\n", encoding="utf-8")
        probe = FreshnessProbe(
            source_root=str(tmp_path),
            indexed_at="2026-04-25T00:00:00Z",
            index_sha="aaa",
            current_sha="bbb",
            file_mtimes={"alpha.py": _ns(f.stat().st_mtime)},
        )
        rows = [{"file": "alpha.py"}]
        probe.annotate(rows)
        summary = probe.summary(rows)
        assert rows[0]["_freshness"] == "fresh"
        assert summary["repo_is_stale"] is True


class TestNotBlanketLoosening:
    def test_changed_file_reads_stale_index_not_edited_when_head_moved(self, tmp_path):
        """A file that DID move, on a repo whose HEAD also moved, is stale_index.

        Guards the LABEL: it must not degrade to `edited_uncommitted`, which
        would describe a committed change as an uncommitted one.
        """
        f = tmp_path / "alpha.py"
        f.write_text("def alpha_fn():\n    return 'changed'\n", encoding="utf-8")
        probe = FreshnessProbe(
            source_root=str(tmp_path),
            indexed_at="2026-04-25T00:00:00Z",
            index_sha="aaa",
            current_sha="bbb",
            file_mtimes={"alpha.py": _ns(f.stat().st_mtime - 3600)},
        )
        assert probe.classify("alpha.py") == "stale_index"

    def test_changed_file_on_clean_repo_is_edited_uncommitted(self, tmp_path):
        """Unchanged behaviour: HEAD did not move, the file did."""
        f = tmp_path / "alpha.py"
        f.write_text("def alpha_fn():\n    return 'changed'\n", encoding="utf-8")
        probe = FreshnessProbe(
            source_root=str(tmp_path),
            indexed_at="2026-04-25T00:00:00Z",
            index_sha="aaa",
            current_sha="aaa",
            file_mtimes={"alpha.py": _ns(f.stat().st_mtime - 3600)},
        )
        assert probe.classify("alpha.py") == "edited_uncommitted"

    def test_no_per_file_baseline_still_falls_back_to_repo_sha(self, tmp_path):
        """Without per-file evidence the repo-wide signal is all there is, and
        it must still answer stale_index rather than inventing a verdict."""
        f = tmp_path / "alpha.py"
        f.write_text("x = 1\n", encoding="utf-8")
        probe = FreshnessProbe(
            source_root=str(tmp_path),
            indexed_at="2026-04-25T00:00:00Z",
            index_sha="aaa",
            current_sha="bbb",
            file_mtimes=None,  # nothing recorded per file
        )
        assert probe.classify("alpha.py") == "stale_index"

    def test_a_file_with_no_recorded_mtime_still_falls_back(self, tmp_path):
        """⚠ Not in the issue: `file_mtimes` can be SPARSE.

        A file present in the index but absent from `file_mtimes` has no
        per-file evidence of its own, and must not inherit the exemption from
        a sibling that does.
        """
        (tmp_path / "alpha.py").write_text("x = 1\n", encoding="utf-8")
        (tmp_path / "beta.py").write_text("y = 2\n", encoding="utf-8")
        probe = FreshnessProbe(
            source_root=str(tmp_path),
            indexed_at="2026-04-25T00:00:00Z",
            index_sha="aaa",
            current_sha="bbb",
            file_mtimes={"alpha.py": _ns((tmp_path / "alpha.py").stat().st_mtime)},
        )
        assert probe.classify("alpha.py") == "fresh"
        assert probe.classify("beta.py") == "stale_index"

    def test_missing_file_is_not_reported_fresh(self, tmp_path):
        """v1.108.209's invariant: never answer `fresh` for a comparison that
        could not be made. The reorder must not reach `fresh` via a recorded
        mtime for a file that is no longer on disk."""
        probe = FreshnessProbe(
            source_root=str(tmp_path),
            indexed_at="2026-04-25T00:00:00Z",
            index_sha="aaa",
            current_sha="aaa",
            file_mtimes={"gone.py": _ns(1.0)},
        )
        assert probe.classify("gone.py") != "fresh"

    def test_missing_file_on_a_stale_repo_is_not_reported_fresh(self, tmp_path):
        """The same invariant on the branch the reorder actually moved."""
        probe = FreshnessProbe(
            source_root=str(tmp_path),
            indexed_at="2026-04-25T00:00:00Z",
            index_sha="aaa",
            current_sha="bbb",
            file_mtimes={"gone.py": _ns(1.0)},
        )
        assert probe.classify("gone.py") != "fresh"

    def test_empty_file_field_is_unknown_not_fresh(self, tmp_path):
        probe = FreshnessProbe(
            source_root=str(tmp_path),
            indexed_at="2026-04-25T00:00:00Z",
            index_sha="aaa",
            current_sha="bbb",
            file_mtimes={"alpha.py": _ns(1.0)},
        )
        assert probe.classify("") == "unknown"


# ── #404: a revalidated cache hit must not still report `fresh` ───────────


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def indexed_repo(tmp_path):
    """A committed, indexed one-file repo plus its own throwaway storage."""
    from jcodemunch_mcp.tools.index_folder import index_folder

    root = tmp_path / "repo"
    root.mkdir()
    storage = str(tmp_path / "store")
    (root / "alpha.py").write_text(
        "def alpha_fn():\n    return 'ORIGINAL'\n", encoding="utf-8"
    )
    _git(root, "init", "--quiet")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", "-A")
    _git(root, "commit", "--quiet", "-m", "initial")
    result = index_folder(str(root), use_ai_summaries=False, storage_path=storage)
    if isinstance(result, str):
        result = json.loads(result)
    yield root, storage, (result.get("repo") or result.get("repo_id"))


def _search(repo, storage, query="alpha_fn"):
    from jcodemunch_mcp.tools.search_symbols import search_symbols

    res = search_symbols(repo=repo, query=query, storage_path=storage, max_results=5)
    if isinstance(res, str):
        res = json.loads(res)
    return res


def _row_freshness(res, name="alpha_fn"):
    hits = [r for r in (res.get("results") or []) if r.get("name") == name]
    return hits[0].get("_freshness") if hits else None


def _commit_edit(root: Path):
    time.sleep(1.1)  # classify() uses a 1.0s mtime tolerance
    (root / "alpha.py").write_text(
        "def alpha_fn():\n    return 'EDITED'\n", encoding="utf-8"
    )
    _git(root, "add", "-A")
    _git(root, "commit", "--quiet", "-m", "edit alpha")


pytestmark_git = pytest.mark.skipif(
    shutil.which("git") is None, reason="git not on PATH"
)


@pytestmark_git
class TestCachedFreshnessIsRevalidated:
    def test_cache_hit_after_commit_does_not_report_fresh(self, indexed_repo):
        root, storage, repo = indexed_repo
        assert _row_freshness(_search(repo, storage)) == "fresh"  # warm the cache
        _commit_edit(root)
        res = _search(repo, storage)  # same key -> cache hit
        assert res["_meta"].get("cache_hit") is True
        assert _row_freshness(res) != "fresh"

    def test_cached_rows_agree_with_the_verdict_in_the_same_payload(self, indexed_repo):
        """The specific defect: one payload asserting both stale_cache and fresh."""
        root, storage, repo = indexed_repo
        _search(repo, storage)
        _commit_edit(root)

        res = _search(repo, storage)
        meta = res["_meta"]
        revalidated = (meta.get("verdict") or {}).get("revalidated") or {}
        assert revalidated.get("stale_cache") is True, "precondition: cache went stale"
        assert meta["freshness"]["repo_is_stale"] is True
        # alpha.py is the file the commit touched, so its own mtime moved too —
        # #405's exemption does not apply and this row really is not fresh.
        assert meta["freshness"]["fresh"] == 0

    def test_cached_result_matches_an_uncached_query_at_the_same_instant(
        self, indexed_repo
    ):
        """Same repo, same symbol, same moment: the cached path must not
        disagree with a path that never touched the cache."""
        root, storage, repo = indexed_repo
        _search(repo, storage, "alpha_fn")  # warms ONLY this key
        _commit_edit(root)
        cached = _row_freshness(_search(repo, storage, "alpha_fn"))
        uncached = _row_freshness(_search(repo, storage, "alpha"))  # different key
        assert cached == uncached

    def test_unchanged_repo_still_serves_a_fresh_cache_hit(self, indexed_repo):
        """Guard: re-annotating must not make every cache hit look stale."""
        root, storage, repo = indexed_repo
        assert _row_freshness(_search(repo, storage)) == "fresh"
        res = _search(repo, storage)
        assert res["_meta"].get("cache_hit") is True
        assert _row_freshness(res) == "fresh"

    def test_a_revalidated_hit_does_not_corrupt_later_hits(self, indexed_repo):
        """Re-annotating on the hit path must not write through to the STORED
        entry. Same leak class as `evidence_ref` in #377 item 3, one level down:
        `_result_cache_get` copied the top level and `_meta` but not the rows.
        """
        root, storage, repo = indexed_repo
        _search(repo, storage)
        _commit_edit(root)
        first_hit = _row_freshness(_search(repo, storage))
        second_hit = _row_freshness(_search(repo, storage))
        assert first_hit == second_hit != "fresh", (
            "a revalidated hit must be reproducible, not a one-shot correction"
        )
