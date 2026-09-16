"""#711: a remembered search miss is not an absence claim about another repo.

`SessionJournal` keeps two records of a search and they are not equivalent:

* ``record_search(query, result_count)`` -- a QUERY STRING and an integer, with
  no repository, no filters and no index generation. It is session history.
* ``record_negative_evidence({query, repo, verdict, ...})`` -- the producer's
  own absence finding, named to a repository, and only ever written when
  `retrieval/verdict.py` was willing to publish one (the v1.108.184 packer
  guard suppresses it when absence cannot be established at all).

`plan_turn`'s prior-negative-evidence check read the FIRST one, so a zero-result
search in repo A turned into "the feature does not exist in the indexed
codebase. Do NOT search again." in repo B -- in the same response that returned
the three symbols implementing it.

The tests below fix the rule, not the reproduction: an absence assertion must
come from the repo-scoped evidence channel, and current positive results always
outrank a remembered miss.
"""

import pytest
from pathlib import Path


ABSENT_QUERY = "nonexistent_xyz_redis_cache"
PRESENT_QUERY = "my_func"


@pytest.fixture(autouse=True)
def reset_session_journal():
    """The journal is a process-global singleton; every test gets a fresh one."""
    from jcodemunch_mcp.tools import session_journal

    with session_journal._journal_lock:
        session_journal._journal = None
    yield
    with session_journal._journal_lock:
        session_journal._journal = None


def _journal():
    from jcodemunch_mcp.tools.session_journal import get_journal

    return get_journal()


def _remember_miss(query: str, repo: str, verdict: str = "no_implementation_found") -> None:
    """Record a miss exactly as the dispatcher does (server.py, search_symbols arm)."""
    import time

    journal = _journal()
    journal.record_search(query, 0)
    journal.record_negative_evidence(
        {
            "query": query,
            "repo": repo,
            "verdict": verdict,
            "scanned_symbols": 12,
            "timestamp": time.time(),
        }
    )


def _asserts_absence(result: dict) -> bool:
    """Does this response tell the caller the thing does not exist?"""
    return (
        result.get("action") == "STOP_AND_REPORT_GAP"
        and result.get("prior_evidence") is not None
    )


def test_a_miss_in_another_repo_does_not_stop_work_in_this_one(tmp_path: Path):
    """The reported defect: repo A missed, repo B is told the feature does not exist."""
    from jcodemunch_mcp.tools.plan_turn import plan_turn
    from tests.conftest_helpers import create_mini_index

    repo, storage_path = create_mini_index(tmp_path)
    _remember_miss(PRESENT_QUERY, repo="local/some-other-repo")

    result = plan_turn(repo=repo, query=PRESENT_QUERY, storage_path=storage_path)

    assert result["recommended_symbols"], "fixture must return matches, or this proves nothing"
    assert result.get("prior_evidence") is None, (
        "a miss recorded against another repository became evidence about this one"
    )
    assert result["confidence"] != "none"
    assert result.get("action") != "STOP_AND_REPORT_GAP"


def test_current_matches_outrank_a_remembered_miss_in_the_same_repo(tmp_path: Path):
    """Same repo, and the index answers the question NOW. The miss is stale, not truth.

    A filter, a token budget or a reindex between the two calls all produce this
    shape, and none of them makes the symbols on the page disappear.
    """
    from jcodemunch_mcp.tools.plan_turn import plan_turn
    from tests.conftest_helpers import create_mini_index

    repo, storage_path = create_mini_index(tmp_path)
    _remember_miss(PRESENT_QUERY, repo=repo)

    result = plan_turn(repo=repo, query=PRESENT_QUERY, storage_path=storage_path)

    assert result["recommended_symbols"]
    assert not _asserts_absence(result), (
        "the response claimed absence while returning the implementation"
    )
    assert result["max_supplementary_reads"] > 0


def test_a_bare_zero_count_is_never_an_absence_claim(tmp_path: Path):
    """History alone cannot carry the claim: no repo, no verdict, no evidence.

    This is the shape the packer guard produces -- the producer REFUSED to
    publish negative evidence, and only the query/count pair was written.
    """
    from jcodemunch_mcp.tools.plan_turn import plan_turn
    from tests.conftest_helpers import create_mini_index

    repo, storage_path = create_mini_index(tmp_path)
    _journal().record_search(ABSENT_QUERY, 0)  # no record_negative_evidence

    result = plan_turn(repo=repo, query=ABSENT_QUERY, storage_path=storage_path)

    assert result.get("prior_evidence") is None, (
        "a query/count pair with no repository and no verdict asserted absence"
    )


def test_low_confidence_matches_is_not_an_absence_verdict(tmp_path: Path):
    """`low_confidence_matches` says the matches were weak, not that nothing exists."""
    from jcodemunch_mcp.tools.plan_turn import plan_turn
    from tests.conftest_helpers import create_mini_index

    repo, storage_path = create_mini_index(tmp_path)
    _remember_miss(ABSENT_QUERY, repo=repo, verdict="low_confidence_matches")

    result = plan_turn(repo=repo, query=ABSENT_QUERY, storage_path=storage_path)

    assert result.get("prior_evidence") is None, (
        "a weak-match verdict was read as proof the feature does not exist"
    )


def test_a_recorded_absence_in_this_repo_still_reaches_the_caller(tmp_path: Path):
    """Non-vacuity, and the feature #205 asked for.

    The point of #711 is not to delete the stop signal. When the producer DID
    publish an absence for THIS repository and the index still returns nothing,
    the caller must still be told -- otherwise the fix is a silent removal and
    these tests would pass against a deleted block.
    """
    from jcodemunch_mcp.tools.plan_turn import plan_turn
    from tests.conftest_helpers import create_mini_index

    repo, storage_path = create_mini_index(tmp_path)
    _remember_miss(ABSENT_QUERY, repo=repo)

    result = plan_turn(repo=repo, query=ABSENT_QUERY, storage_path=storage_path)

    assert not result["recommended_symbols"], "fixture must miss, or this proves nothing"
    assert result.get("prior_evidence") is not None
    assert result["prior_evidence"]["previously_searched"] is True
    assert result["action"] == "STOP_AND_REPORT_GAP"


def test_the_claim_never_outruns_the_repository_it_was_measured_in(tmp_path: Path):
    """The property behind all five: an absence entry answers for ONE repo.

    Two repositories, one query, absence recorded against exactly one of them.
    The scan must answer differently for the two, which is what a query-keyed
    lookup cannot do however it is spelled.
    """
    from jcodemunch_mcp.tools.plan_turn import plan_turn
    from tests.conftest_helpers import create_mini_index

    here, storage_path = create_mini_index(tmp_path / "here")
    there, other_storage = create_mini_index(tmp_path / "there")
    assert here != there

    _remember_miss(ABSENT_QUERY, repo=here)

    mine = plan_turn(repo=here, query=ABSENT_QUERY, storage_path=storage_path)
    theirs = plan_turn(repo=there, query=ABSENT_QUERY, storage_path=other_storage)

    assert mine.get("prior_evidence") is not None
    assert theirs.get("prior_evidence") is None, (
        "the same remembered miss answered for a repository it never ran against"
    )


def test_a_restored_session_keeps_the_evidence_not_just_the_counts(tmp_path: Path):
    """The second spelling (#711): `save` persisted the log and `restore` dropped it.

    A resumed or compacted session replayed `record_search` counts and nothing
    else, so the absence FINDINGS -- the half that names a repository -- were
    written to disk and read by no one. While the claim came from the counts
    that was invisible; now that it comes from the evidence, losing the
    evidence would retire #205's stop signal at every resume.
    """
    import time

    from collections import OrderedDict

    from jcodemunch_mcp.tools.session_state import SessionState

    state = SessionState(base_path=str(tmp_path))
    source = _journal()
    source.record_search(ABSENT_QUERY, 0)
    source.record_negative_evidence(
        {
            "query": ABSENT_QUERY,
            "repo": "local/some-repo",
            "verdict": "no_implementation_found",
            "scanned_symbols": 12,
            "timestamp": time.time(),
        }
    )
    state.save(source, OrderedDict(),
               negative_evidence_log=source.get_negative_evidence_log())

    from jcodemunch_mcp.tools.session_journal import SessionJournal

    restored = SessionJournal()
    state.restore_journal(restored, state.load())

    assert restored.citable_absence("local/some-repo", ABSENT_QUERY) is not None, (
        "the repo-scoped absence did not survive the round trip"
    )
    assert restored.citable_absence("local/another-repo", ABSENT_QUERY) is None
