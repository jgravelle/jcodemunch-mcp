"""A cache hit-rate must not be published as if key-presence were validity.

`cache_get` counts a hit when the key is in the LRU. That is the right measure
of how often the cache answered, and it is NOT a measure of how often the
answer still described the index. The session cache is invalidated only by
index-mutating tools **in this process**, so an out-of-process reindex -- the
PostToolUse `index-file` spawn, the watcher, a second server instance -- leaves
entries serving happily and every one of them counts as a hit.

⚠⚠ arXiv:2608.20280 measured this exact gap on semantic caches: raw hit rates
of **51-60%** fell to quality-adjusted rates of **1.1-2.2%** once validity was
checked. Publishing the raw number as a performance result is the defect that
paper names, and `analyze_perf` published exactly that number bare.

⚠⚠ **Three buckets, not two.** `search_symbols` revalidates its private
cache against `subject_state` and reports each check; the shared LRU
(`find_references`, `get_blast_radius`) checks every hit against the caller's
index load inside `cache_get` and counts it fresh there (LEDGER L-330; until
then it served hits with no check at all, and this file asserted that). A hit
nobody validated is UNKNOWN -- it is reported in its own bucket and never
folded into fresh or stale, the same rule `ledger_trust` applies to
unseparable ledger rows. A private-cache hit reported without a check is that
case, so the tests below build it with `cache_lookup_recorded`, the call a
private cache makes.

⚠ The raw rate is deliberately KEPT. It answers a real question, and replacing
it would trade one misleading number for another. What changes is that it now
carries a basis label and cannot be read alone.
"""

from __future__ import annotations

import types
import pytest

from jcodemunch_mcp.storage import token_tracker as tt

# The shared cache answers only for the index load it was filled from
# (LEDGER L-330); one stable stand-in object plays that index here.
_IDX = types.SimpleNamespace()


@pytest.fixture
def state(monkeypatch):
    """A private tracker so the developer's live session counters are untouched."""
    s = tt._State()
    monkeypatch.setattr(tt, "_state", s)
    return s


def _fill(state, tool="search_symbols", repo="o/r", key=("q",)):
    state.cache_put(tool, repo, key, {"results": []}, index=_IDX)


def test_raw_rate_still_measures_key_presence(state):
    """The raw number is not the thing being removed."""
    _fill(state)
    state.cache_get("search_symbols", "o/r", ("q",), index=_IDX)
    state.cache_get("search_symbols", "o/r", ("absent",), index=_IDX)

    stats = state.cache_stats()
    assert stats["hit_rate"] == 0.5
    assert stats["hit_rate_basis"] == "raw_key_presence"


def test_unvalidated_hits_are_unknown_not_fresh(state):
    """The defect: nobody checked, so nobody may claim it was fresh."""
    for _ in range(4):
        state.cache_lookup_recorded("search_symbols", hit=True)

    stats = state.cache_stats()
    assert stats["hit_rate"] == 1.0, "raw rate unchanged"
    assert stats["hits_unvalidated"] == 4
    assert stats["hits_validated_fresh"] == 0
    assert stats["hits_validated_stale"] == 0
    # ⚠ None, not 0.0 and not 1.0. Nothing was measured, so nothing is claimed.
    assert stats["hit_rate_revalidated"] is None
    assert stats["validated_share"] == 0.0


def test_a_stale_hit_is_not_a_fresh_hit(state):
    """The number the paper is about: served, counted, and wrong."""
    for _ in range(4):
        state.cache_lookup_recorded("search_symbols", hit=True)
    state.cache_hit_validated("search_symbols", stale=False)
    for _ in range(3):
        state.cache_hit_validated("search_symbols", stale=True)

    stats = state.cache_stats()
    assert stats["hit_rate"] == 1.0, "every lookup did hit the LRU"
    assert stats["hits_validated_fresh"] == 1
    assert stats["hits_validated_stale"] == 3
    assert stats["hits_unvalidated"] == 0
    assert stats["hit_rate_revalidated"] == 0.25
    assert stats["hit_rate"] != stats["hit_rate_revalidated"], (
        "a raw rate of 1.0 sitting beside a revalidated 0.25 is the entire "
        "point of reporting both"
    )


def test_validation_never_inflates_the_hit_count(state):
    """Reporting a verdict must not double-count the hit it describes."""
    state.cache_lookup_recorded("search_symbols", hit=True)
    before = state.cache_stats()["total_hits"]
    state.cache_hit_validated("search_symbols", stale=True)
    assert state.cache_stats()["total_hits"] == before


def test_buckets_account_for_every_hit(state):
    """The three buckets partition the hits. No hit is lost or counted twice."""
    _fill(state, tool="find_references", key=("b",))
    for _ in range(5):
        state.cache_lookup_recorded("search_symbols", hit=True)
    for _ in range(2):
        state.cache_get("find_references", "o/r", ("b",), index=_IDX)
    state.cache_hit_validated("search_symbols", stale=False)
    state.cache_hit_validated("search_symbols", stale=True)

    stats = state.cache_stats()
    assert (
        stats["hits_validated_fresh"]
        + stats["hits_validated_stale"]
        + stats["hits_unvalidated"]
        == stats["total_hits"] == 7
    )
    # The shared LRU checks each hit it serves against the caller's index
    # load (L-330), so its column is validated fresh, never unknown.
    fr = stats["by_tool"]["find_references"]
    assert fr["hits"] == 2 and fr["hits_validated_fresh"] == 2
    assert fr["hits_unvalidated"] == 0 and fr["hit_rate_revalidated"] == 1.0
    # search_symbols reported 5 hits and checked 2 of them.
    ss = stats["by_tool"]["search_symbols"]
    assert ss["hits_unvalidated"] == 3


def test_analyze_perf_cannot_publish_the_raw_rate_alone():
    """The outcome, at the surface that actually reports to a user.

    ⚠ Asserts on the emitted payload rather than on the producer, because the
    defect was a publishing decision: the number existed correctly and was
    presented without its basis.
    """
    from jcodemunch_mcp.tools import analyze_perf as ap

    out = ap.analyze_perf(window="session")
    totals = out["cache"]["totals"]
    assert "hit_rate" in totals
    for field in (
        "hit_rate_basis",
        "hit_rate_revalidated",
        "hits_validated_fresh",
        "hits_validated_stale",
        "hits_unvalidated",
    ):
        assert field in totals, f"analyze_perf published hit_rate without {field}"
    assert totals["hit_rate_basis"] == "raw_key_presence"
