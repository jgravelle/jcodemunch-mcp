"""The shared result cache must not hand out the object it is holding (#572).

Reported by @rknighton with a standard-library reproduction, one issue after
the ``KeyError: '_meta'`` crash of #570 -- and the point of the report is that
the crash was the loud case. ``cache_put`` stored the caller's dict and
``cache_get`` returned that same dict, so the dispatcher's DISPLAY step edited
the cache:

* ``server.py`` deletes ``_meta`` when ``meta_fields`` is ``[]`` (the shipped
  default) or when a call passes ``suppress_meta``;
* it replaces ``_meta`` with a subset for any other ``meta_fields``;
* and it writes budget / agent-selector fields INTO ``_meta``.

⚠⚠ **The reachable-only-on-a-default-config framing is too narrow, and the
widening is this file's own finding.** ``suppress_meta`` is a per-CALL
argument, so on a machine with ordinary ``meta_fields`` a single call passing
it empties the shared entry, and the next caller -- who asked for metadata --
is served the damage. Measured pre-fix on that exact sequence: the second call
came back with an empty ``_meta``.

⚠ **The window is the MISS path, not the hit path**, which is the other
correction the report invites. Both cached tools already do
``result = dict(cached)`` and rebuild ``_meta`` on a hit, so a repeat call
survives; it is the call that FILLS the cache that returns the stored object
to a dispatcher that then edits it. That is why a two-call reproduction shows
the crash and a two-call reproduction of the quiet cases shows nothing.

⚠⚠ **Fixed in the CACHE, not at the two call sites**, and that is the whole
argument of the report: ``search_symbols`` keeps a separate cache and has
already paid for this twice in its own ``_result_cache_get`` -- #377 item 3
for ``_meta.verdict``, then #404 (also @rknighton) for the rows -- and neither
fix reached the shared one. A third per-consumer patch leaves the trap armed
for the tool written next. Standing lesson: *we fix the reported call site and
leave the mechanism.*
"""

from __future__ import annotations

import types
import json

import pytest

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.storage.token_tracker import (
    _isolate,
    _state,
    result_cache_get,
    result_cache_invalidate,
    result_cache_put,
)

KEY = ("sym", 1, 0, False, False)

# The shared cache answers only for the index load it was filled from
# (LEDGER L-330); one stable stand-in object plays that index here.
_IDX = types.SimpleNamespace()


def _clear():
    result_cache_invalidate()
    with _state._lock:
        _state._cache_hits.clear()
        _state._cache_misses.clear()


# ---------------------------------------------------------------------------
# The cache contract
# ---------------------------------------------------------------------------


class TestTheCacheKeepsItsOwnCopy:

    def setup_method(self):
        _clear()

    def test_the_caller_may_mutate_what_it_handed_over(self):
        """The MISS path: a tool returns the very dict it just cached."""
        payload = {"symbol": "foo", "_meta": {"timing_ms": 42.0}}
        result_cache_put("get_blast_radius", "o/r", KEY, payload, index=_IDX)
        payload.pop("_meta")  # exactly what the dispatcher's meta strip does

        got = result_cache_get("get_blast_radius", "o/r", KEY, index=_IDX)
        assert got["_meta"] == {"timing_ms": 42.0}

    def test_the_caller_may_mutate_what_it_was_served(self):
        """The HIT path. Both current tools copy defensively themselves, so
        this is about the tool written next -- the reason the fix is here."""
        result_cache_put("find_references", "o/r", KEY, {"_meta": {"a": 1}}, index=_IDX)
        first = result_cache_get("find_references", "o/r", KEY, index=_IDX)
        first.pop("_meta")

        second = result_cache_get("find_references", "o/r", KEY, index=_IDX)
        assert second["_meta"] == {"a": 1}

    def test_the_isolation_is_not_only_top_level(self):
        """⚠ A top-level copy would pass every assertion above and still leak.

        The dispatcher writes INTO ``_meta`` (budget, agent selector) and pops
        ``_meta`` out of nested ``results`` rows, and both cached tools run
        row-level annotators -- ``_attach_runtime_to_response`` stamps
        ``_runtime_confidence`` on every reference IN PLACE. That is #404 one
        cache over.
        """
        payload = {
            "_meta": {"timing_ms": 1.0},
            "references": [{"file": "a.py", "line": 1}],
            "results": [{"_meta": {"nested": True}}],
        }
        result_cache_put("find_references", "o/r", KEY, payload, index=_IDX)
        got = result_cache_get("find_references", "o/r", KEY, index=_IDX)

        got["_meta"]["budget_warning"] = "spent"
        got["references"][0]["_runtime_confidence"] = "confirmed"
        got["results"][0].pop("_meta")

        fresh = result_cache_get("find_references", "o/r", KEY, index=_IDX)
        assert "budget_warning" not in fresh["_meta"]
        assert "_runtime_confidence" not in fresh["references"][0]
        assert fresh["results"][0]["_meta"] == {"nested": True}

    def test_equality_survives_and_identity_does_not(self):
        """The price of the fix, stated once so it is a decision not a drift.

        ``tests/test_result_cache.py`` asserted ``get(...) is put(...)`` in
        seven places. Identity was never the contract anyone wanted -- it was
        the defect, written down -- so those became ``==``. Values are
        untouched.
        """
        payload = {"depth": 1, "rows": [{"a": 1}]}
        result_cache_put("get_blast_radius", "o/r", KEY, payload, index=_IDX)
        got = result_cache_get("get_blast_radius", "o/r", KEY, index=_IDX)
        assert got == payload
        assert got is not payload
        assert got["rows"] is not payload["rows"]


class TestIsolateCopiesContainersOnly:
    """⚠ The cost decision, pinned.

    ``copy.deepcopy`` would also clone every leaf. Measured on this box, an
    800 KB response: **16.58 ms deepcopy vs 4.15 ms container-only** (1.67 vs
    0.42 at 80 KB), against a cache hit that is otherwise sub-millisecond. A
    tool result is JSON-serialisable by the time it reaches the wire, so its
    leaves are immutable and cloning them buys nothing.

    ⚠⚠ Depth is deliberately UNBOUNDED. A rule shaped to the containers the
    two current callers happen to use would be a guard written against a
    spelling, which is the mechanism this fix exists to stop.
    """

    def test_leaves_are_shared(self):
        leaf = object()
        src = {"a": leaf, "b": [leaf]}
        out = _isolate(src)
        assert out["a"] is leaf
        assert out["b"][0] is leaf

    def test_every_container_at_every_depth_is_new(self):
        src = {"a": [{"b": [{"c": []}]}]}
        out = _isolate(src)
        assert out == src
        assert out["a"] is not src["a"]
        assert out["a"][0] is not src["a"][0]
        assert out["a"][0]["b"][0]["c"] is not src["a"][0]["b"][0]["c"]

    def test_a_scalar_result_is_returned_unchanged(self):
        assert _isolate(7) == 7
        assert _isolate(None) is None


# ---------------------------------------------------------------------------
# Through the dispatcher, on a real index
# ---------------------------------------------------------------------------


def _seed(tmp_path) -> tuple[str, str]:
    """@rknighton's two-file repo, from the #572 reproduction."""
    from jcodemunch_mcp.tools.index_folder import index_folder

    (tmp_path / "owner.py").write_text("def target_symbol():\n    return 1\n")
    (tmp_path / "consumer.py").write_text(
        "from owner import target_symbol\n\n"
        "def consume():\n    return target_symbol()\n"
    )
    store_path = str(tmp_path / "idx")
    repo = index_folder(
        path=str(tmp_path), use_ai_summaries=False, storage_path=store_path,
        incremental=False, identity_mode="local",
    )["repo"]
    return repo, store_path


async def _call(tool: str, arguments: dict) -> dict:
    from jcodemunch_mcp.server import call_tool

    res = await call_tool(tool, arguments)
    assert isinstance(res, list) and res, res
    return json.loads(res[0].text)


@pytest.fixture
def dispatcher_repo(tmp_path, monkeypatch):
    from jcodemunch_mcp import config as config_module

    repo, store_path = _seed(tmp_path)
    monkeypatch.setenv("CODE_INDEX_PATH", store_path)
    original = config_module._GLOBAL_CONFIG.copy()
    config_module._GLOBAL_CONFIG.clear()
    config_module._GLOBAL_CONFIG["server_output"] = "raw"
    _clear()
    try:
        yield repo, config_module
    finally:
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(original)
        _clear()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool,args",
    [
        ("find_references", {"identifier": "target_symbol"}),
        ("get_blast_radius", {"symbol": "target_symbol"}),
    ],
)
async def test_repeat_calls_survive_the_default_meta_strip(dispatcher_repo, tool, args):
    """#572's headline: the second call crashed with ``KeyError: '_meta'``.

    ``meta_fields: []`` is the shipped default, so this is the out-of-the-box
    path, and it is invisible on any box carrying a config that keeps ``_meta``.

    ⚠ On the non-vacuity pass only the ``find_references`` arm goes red: #570's
    ``cached.get("_meta", {})`` already tolerates the stripped entry in
    ``get_blast_radius``. That guard is kept -- it costs nothing and it is the
    reason one of these two arms cannot regress -- but it covers one tool, and
    it cannot see either quiet case below.
    """
    repo, config_module = dispatcher_repo
    config_module._GLOBAL_CONFIG["meta_fields"] = []
    arguments = {"repo": repo, **args}

    for _ in range(3):
        payload = await _call(tool, arguments)
        assert "error" not in payload, payload


@pytest.mark.asyncio
async def test_one_call_suppressing_meta_does_not_disarm_the_next(dispatcher_repo):
    """⚠⚠ The quiet case, and the one that needs no unusual config.

    ``suppress_meta`` is per-call. The call that FILLS the cache hands the
    dispatcher the stored object; stripping it there served the next caller --
    who asked for metadata -- an empty envelope. Pre-fix this sequence returned
    a ``_meta`` with nothing in it.
    """
    repo, _config = dispatcher_repo
    base = {"repo": repo, "identifier": "target_symbol"}

    first = await _call("find_references", dict(base, suppress_meta=True))
    assert "_meta" not in first  # the caller asked for that, and gets it

    second = await _call("find_references", dict(base))
    assert "error" not in second, second
    assert second["_meta"].get("cache_hit") is True
    assert "timing_ms" in second["_meta"]


@pytest.mark.asyncio
async def test_a_partial_meta_filter_does_not_shrink_the_stored_entry(dispatcher_repo):
    """The third write site: ``meta_fields: ["powered_by"]`` REPLACES ``_meta``.

    Asserted against the cache rather than a response, because with the filter
    still in force every response looks correct by construction -- the loss is
    in what the entry can serve a caller whose filter differs.
    """
    repo, config_module = dispatcher_repo
    config_module._GLOBAL_CONFIG["meta_fields"] = ["powered_by"]

    payload = await _call(
        "find_references", {"repo": repo, "identifier": "target_symbol"}
    )
    assert "error" not in payload, payload
    assert set(payload["_meta"]) == {"powered_by"}

    cached = result_cache_get(
        "find_references", repo, ("target_symbol", 50, False),
        index=IndexStore().load_index(*repo.split("/", 1)),
    )
    assert cached is not None, "the call did not fill the cache; the key shape moved"
    assert "timing_ms" in cached["_meta"], cached["_meta"]
    assert "powered_by" not in cached["_meta"]
