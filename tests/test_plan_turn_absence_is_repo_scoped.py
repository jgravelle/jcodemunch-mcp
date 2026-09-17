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


def _remember_miss(
    query: str,
    repo: str,
    verdict: str = "no_implementation_found",
    verdict_state: str = "absent",
) -> None:
    """Record a miss exactly as the dispatcher does (server.py, search_symbols arm).

    ⚠ Hand-built, so it can drift from the producer. `test_the_dispatcher_records
    _what_the_reader_requires` runs the real `call_tool` and is the guard against
    that -- a mock broad enough to satisfy an assertion can bypass what the
    assertion is about.
    """
    import time

    journal = _journal()
    journal.record_search(query, 0)
    journal.record_negative_evidence(
        {
            "query": query,
            "repo": repo,
            "verdict": verdict,
            "verdict_state": verdict_state,
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
            "verdict_state": "absent",
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


def test_a_degraded_scan_is_not_an_absence_however_its_verdict_reads(tmp_path: Path):
    """The second refusal reason (#711 review): the STATE, not just the verdict.

    `verdict.py`'s `_packed_empty` guard withholds `negative_evidence` for six
    degraded cases and does NOT cover `index_changed` or incomplete coverage:
    both fall through to `elif result_count == 0` and publish
    `no_implementation_found` on a scan `handoff.absence_refusal` refuses --
    "only 'absent' can prove absence". Re-running the same terms is precisely
    what a stale or rewritten index needs, so the recommendation's sentence
    would be false for exactly these.
    """
    from jcodemunch_mcp.tools.plan_turn import plan_turn
    from tests.conftest_helpers import create_mini_index

    repo, storage_path = create_mini_index(tmp_path)
    _remember_miss(ABSENT_QUERY, repo=repo, verdict_state="degraded")

    result = plan_turn(repo=repo, query=ABSENT_QUERY, storage_path=storage_path)

    assert result.get("prior_evidence") is None, (
        "a degraded scan was replayed as proof the feature does not exist"
    )


def test_an_entry_with_no_recorded_state_is_refused(tmp_path: Path):
    """UNKNOWN blocks, the rule every other absence surface here follows.

    An entry restored from a state file written before `verdict_state` existed
    cannot say whether its scan could prove anything, so it stops asserting.
    Losing a stop signal is recoverable; a false absence is what #711 is.
    """
    import time

    from jcodemunch_mcp.tools.plan_turn import plan_turn
    from tests.conftest_helpers import create_mini_index

    repo, storage_path = create_mini_index(tmp_path)
    _journal().record_negative_evidence(
        {
            "query": ABSENT_QUERY,
            "repo": repo,
            "verdict": "no_implementation_found",
            "scanned_symbols": 12,
            "timestamp": time.time(),
        }  # pre-#711 shape: no verdict_state
    )

    result = plan_turn(repo=repo, query=ABSENT_QUERY, storage_path=storage_path)

    assert result.get("prior_evidence") is None


def test_the_resolved_repo_spelling_also_matches(tmp_path: Path):
    """The search and the plan need not spell the repository the same way."""
    from jcodemunch_mcp.tools.plan_turn import plan_turn
    from tests.conftest_helpers import create_mini_index

    repo, storage_path = create_mini_index(tmp_path)
    owner, _, name = repo.partition("/")
    assert owner and name

    # Recorded under the resolved identity; the caller asks with the same
    # string here, and `aliases` is what closes the gap when they differ.
    _remember_miss(ABSENT_QUERY, repo=f"{owner}/{name}")

    result = plan_turn(repo=repo, query=ABSENT_QUERY, storage_path=storage_path)

    assert result.get("prior_evidence") is not None


def test_the_snapshot_does_not_publish_another_repos_dead_end(tmp_path: Path):
    """Second consumer (#711 review): the text the model reads at every compact.

    `get_session_snapshot` rendered every log entry under "don't re-search",
    dropped the repository and kept every verdict -- the reported defect in the
    widest surface it had. An entry earns that heading only on the conditions
    `citable_absence` applies, and the repository is named on the line, because
    a session-wide snapshot has no repo of its own to filter against.
    """
    from jcodemunch_mcp.tools.get_session_snapshot import get_session_snapshot

    _remember_miss("gone_from_here", repo="local/repo-a")
    _remember_miss("weak_here", repo="local/repo-b", verdict="low_confidence_matches")
    _remember_miss("degraded_here", repo="local/repo-c", verdict_state="degraded")

    snapshot = get_session_snapshot()

    # The dead-ends block only. "Key searches" lists every query searched and is
    # supposed to, so asserting over the whole snapshot would be asserting about
    # the wrong block -- and it passed for the wrong reason when it did.
    markdown = snapshot["snapshot"]
    assert "### Dead ends" in markdown
    section = markdown.split("### Dead ends", 1)[1]
    dead_ends = snapshot["structured"]["dead_ends"]

    assert "gone_from_here" in section
    assert "local/repo-a" in section, "the dead end did not name the repo it was measured in"
    assert "weak_here" not in section, "weak matches were published as a dead end"
    assert "degraded_here" not in section, "a degraded scan was published as a dead end"
    assert [e["query"] for e in dead_ends] == ["gone_from_here"]
    assert dead_ends[0]["repo"] == "local/repo-a", (
        "the structured half dropped the repository the claim belongs to"
    )


def test_the_dispatcher_records_what_the_reader_requires(tmp_path: Path, monkeypatch):
    """End to end through `call_tool`, with `meta_fields: []` -- the shipped default.

    Every other test here hand-builds the journal entry, which is a contract the
    producer might not supply: rename `repo` or `verdict_state` in `server.py`
    and they all stay green while the product breaks. This arm runs the real
    dispatcher over two real indexes and is the reproduction from the issue.

    ⚠ `_meta` is stripped for the caller further down the same function, so the
    verdict state has to be read at the recording site or not at all. That is
    exactly what this asserts.
    """
    import asyncio

    from jcodemunch_mcp import config
    from jcodemunch_mcp.server import call_tool
    from jcodemunch_mcp.tools.index_folder import index_folder

    # The dispatcher resolves its store from CODE_INDEX_PATH, not from a
    # `storage_path` argument -- `plan_turn` through `call_tool` takes no such
    # argument, which is the whole reason this arm exercises something the
    # direct-call tests cannot.
    storage = str(tmp_path / "store")
    monkeypatch.setenv("CODE_INDEX_PATH", storage)
    handles = {}
    for name, source in [
        ("a", "def apple():\n    return 1\n"),
        ("b", "def target():\n    return 1\ndef target_two():\n    return 2\n"),
    ]:
        root = tmp_path / name
        root.mkdir()
        (root / "example.py").write_text(source)
        handles[name] = index_folder(
            path=str(root), storage_path=storage, use_ai_summaries=False,
            incremental=False, identity_mode="local",
        )["repo"]

    saved = dict(config._GLOBAL_CONFIG)
    config._GLOBAL_CONFIG.update(
        {"session_journal": True, "meta_fields": [], "share_savings": False,
         "use_ai_summaries": False}
    )
    try:
        async def _run():
            import json

            await call_tool("search_symbols", {"repo": handles["a"], "query": "target"})
            after = await call_tool("plan_turn", {"repo": handles["b"], "query": "target"})
            content = after if isinstance(after, list) else getattr(after, "content", [])
            return json.loads(getattr(content[0], "text", "{}"))

        payload = asyncio.run(_run())
    finally:
        config._GLOBAL_CONFIG.clear()
        config._GLOBAL_CONFIG.update(saved)

    assert "error" not in payload, payload
    assert payload["recommended_symbols"], "repo b must still answer the question"
    assert payload.get("prior_evidence") is None, (
        "repo a's miss reached repo b through the real dispatcher"
    )
    assert payload.get("action") != "STOP_AND_REPORT_GAP"


def test_both_consumers_read_one_predicate(tmp_path: Path):
    """A condition added once must reach every consumer (#711 review, note 1).

    The planner and the snapshot both decide whether a log entry is an absence
    worth repeating. Written as a comprehension in each, a third condition
    added later reaches one of them -- which is the second-derivation shape
    this whole issue is about, reproduced inside its own fix.

    This asserts the wiring rather than the spelling: patch the shared
    predicate to refuse everything, and BOTH consumers must fall silent. A
    consumer holding its own copy keeps talking.
    """
    from unittest import mock

    from jcodemunch_mcp.tools.get_session_snapshot import get_session_snapshot
    from jcodemunch_mcp.tools.plan_turn import plan_turn
    from jcodemunch_mcp.tools.session_journal import SessionJournal
    from tests.conftest_helpers import create_mini_index

    repo, storage_path = create_mini_index(tmp_path)
    _remember_miss(ABSENT_QUERY, repo=repo)

    # Both speak while the predicate accepts.
    assert plan_turn(repo=repo, query=ABSENT_QUERY,
                     storage_path=storage_path).get("prior_evidence") is not None
    assert get_session_snapshot()["structured"]["dead_ends"]

    with mock.patch.object(SessionJournal, "entry_is_citable", return_value=False):
        planner = plan_turn(repo=repo, query=ABSENT_QUERY, storage_path=storage_path)
        snapshot = get_session_snapshot()

    assert planner.get("prior_evidence") is None, (
        "the planner did not go through the shared predicate"
    )
    assert snapshot["structured"]["dead_ends"] == [], (
        "the snapshot kept its own copy of the conditions"
    )


def test_the_dispatcher_arm_holds_with_full_metadata(tmp_path: Path, monkeypatch):
    """The issue asked for both metadata settings; `meta_fields: []` is the other.

    The recording site reads `_meta.verdict.state` before the strip, so the
    default (`[]`) is the case that could silently lose it -- that arm is
    `test_the_dispatcher_records_what_the_reader_requires`. This is the
    complement: with `_meta` retained, nothing about the journal changes.
    """
    import asyncio
    import json

    from jcodemunch_mcp import config
    from jcodemunch_mcp.server import call_tool
    from jcodemunch_mcp.tools.index_folder import index_folder

    storage = str(tmp_path / "store")
    monkeypatch.setenv("CODE_INDEX_PATH", storage)
    handles = {}
    for name, source in [
        ("a", "def apple():\n    return 1\n"),
        ("b", "def target():\n    return 1\ndef target_two():\n    return 2\n"),
    ]:
        root = tmp_path / name
        root.mkdir()
        (root / "example.py").write_text(source)
        handles[name] = index_folder(
            path=str(root), storage_path=storage, use_ai_summaries=False,
            incremental=False, identity_mode="local",
        )["repo"]

    saved = dict(config._GLOBAL_CONFIG)
    config._GLOBAL_CONFIG.update(
        {"session_journal": True, "meta_fields": ["verdict"], "share_savings": False,
         "use_ai_summaries": False}
    )
    try:
        async def _run():
            await call_tool("search_symbols", {"repo": handles["a"], "query": "target"})
            after = await call_tool("plan_turn", {"repo": handles["b"], "query": "target"})
            content = after if isinstance(after, list) else getattr(after, "content", [])
            return json.loads(getattr(content[0], "text", "{}"))

        payload = asyncio.run(_run())
    finally:
        config._GLOBAL_CONFIG.clear()
        config._GLOBAL_CONFIG.update(saved)

    assert payload["recommended_symbols"]
    assert payload.get("prior_evidence") is None
