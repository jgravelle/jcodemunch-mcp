"""Every test starts from a fresh SESSION, as a fresh server process does (#801).

The delivery ledger (`token_tracker._state._delivered`, the v1.108.167
`_meta.already_delivered` advisory) and the steering counter
(`server._steer_state`, the v1.108.158 `_meta.hint`) live for the whole
process, and nothing reset them between tests. So a test that reads a
response's `_meta` saw whatever earlier tests had served:
`test_v1_108_183.py::TestCompatibility::test_a_call_without_receipt_is_byte_identical`
passed when its file ran whole (an earlier test had already delivered the
symbol, so its "cold" call was a repeat) and failed when selected by id,
which is how a touched-files run (Practice 10) reaches it.

⚠ The two tests below are a PAIR and depend on running in file order: the
first writes session state and the second asserts none of it arrived. Under
the pre-#801 conftest the second fails; run alone it passes either way, which
is exactly the property it guards.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from jcodemunch_mcp import server
from jcodemunch_mcp.storage import token_tracker

_SID = "src/leak.py::leaked#function"


def test_1_a_test_writes_session_state():
    token_tracker.note_delivered([(_SID, 50, True)])
    server._steer_state["hops"] = 7
    server._steer_state["nudged"] = True
    assert _SID in token_tracker._state._delivered


def test_2_the_next_test_starts_from_a_fresh_session():
    assert _SID not in token_tracker._state._delivered
    assert token_tracker.note_delivered([(_SID, 50, True)]) == []
    assert server._steer_state == {"hops": 0, "bundles": 0, "nudged": False, "repos": []}


@pytest.fixture()
def _indexed(tmp_path, monkeypatch):
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / "idx"))
    proj = tmp_path / "proj"
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "session.py").write_text(
        "def refresh_token(user):\n    return user\n\n\ndef caller():\n    return refresh_token(1)\n",
        encoding="utf-8",
    )
    body = json.loads(_text(asyncio.run(server.call_tool("index_folder", {"path": str(proj)}))))
    return body["repo"]


def _text(content) -> str:
    return content[0].text if isinstance(content, list) else content.content[0].text


def _key_set(tool: str, args: dict):
    body = json.loads(_text(asyncio.run(server.call_tool(tool, dict(args)))))
    return sorted(body), sorted(body["_meta"]) if "_meta" in body else None


@pytest.mark.parametrize("tool,args", [
    ("search_symbols", {"query": "refresh_token"}),
    ("find_references", {"identifier": "refresh_token"}),
    ("get_blast_radius", {"symbol": "refresh_token"}),
])
def test_a_result_cache_hit_answers_with_the_cold_calls_key_set(_indexed, monkeypatch, tool, args):
    """The issue's second reading, ruled out and pinned: the three result-cache
    consumers (#572) answer a cache HIT with the key set of the cold MISS.

    Between the calls ONLY the delivery ledger and the steering counter are
    reset, so the second call differs from the first by the cache's
    temperature alone; `already_delivered` on a repeat is the session's
    advisory, by design, and not what this measures. ⚠⚠ Never a fresh
    `_State()` here: the shared result cache lives in it, and the first draft
    of this test compared a cold miss with a second cold miss for two of the
    three tools (review). The hit counter proves the second call was a hit.
    """
    args = dict(args, repo=_indexed, format="json")
    cold = _key_set(tool, args)
    assert "error" not in cold[0], cold  # two identical errors would compare equal
    hits_before = _hits()
    token_tracker._state._delivered.clear()
    server._steer_state.update({"hops": 0, "bundles": 0, "nudged": False, "repos": []})
    warm = _key_set(tool, args)
    assert _hits() > hits_before, "the second call was not a cache hit"
    assert warm == cold


def _hits() -> int:
    """Hits on EITHER cache. `find_references` and `get_blast_radius` count in
    `total_hits`; `search_symbols` keeps its own cache and records only a
    VALIDATED hit, so it moves `hits_validated_*` and never `total_hits`."""
    stats = token_tracker.result_cache_stats()
    return stats["total_hits"] + stats["hits_validated_fresh"] + stats["hits_validated_stale"]
