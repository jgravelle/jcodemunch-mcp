"""A compact answer keeps every `_meta` key the dispatcher adds after the display filter.

LEDGER L-313 (from review 1 of the compact `get_file_tree` encoding). Each
schema encoder keeps only the `_meta` keys it lists plus `UNIVERSAL_META_JSON`.
The dispatcher adds keys after the `meta_fields` strip, so on the shipped
default (`meta_fields: []`) they are the only `_meta` keys an answer carries:
`secrets_redacted` (redaction is on by default), `budget` (the session is
near or over its token budget) and `already_delivered`. A compact answer from
any schema encoder dropped them, where the same answer as JSON kept them: a
response the server had altered by redacting a secret reached the agent with
no word that it had been altered.

`get_file_tree`'s encoder already carried its unlisted keys itself. The carrier
is now in `schema_driven.encode`, so every schema encoder, and the next one
written, inherits it.
"""

from __future__ import annotations

import inspect
import json
import re

import pytest

import jcodemunch_mcp.config as config_module
from jcodemunch_mcp import server
from jcodemunch_mcp.encoding import schema_driven as sd

# The AWS documentation's example access key id: matches the redaction rule,
# grants nothing.
_FAKE_KEY = "AKIA" + "IOSFODNN7EXAMPLE"


def _content(res):
    from mcp.types import CallToolResult

    return res.content if isinstance(res, CallToolResult) else res


def _decode(text: str) -> dict:
    from jcodemunch_mcp.encoding.decoder import decode

    assert not text.lstrip().startswith("{"), (
        "the answer was served as JSON, not compact"
    )
    return decode(text)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    from jcodemunch_mcp.tools.index_folder import index_folder
    from jcodemunch_mcp.tools.search_symbols import _result_cache

    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / ".idx"))
    monkeypatch.setenv("JCODEMUNCH_USE_AI_SUMMARIES", "false")
    real = config_module.get

    def _get(key, default=None, **kw):
        if key == "meta_fields":
            return []  # the shipped default
        return real(key, default, **kw)

    monkeypatch.setattr(config_module, "get", _get)
    src = tmp_path / "src"
    src.mkdir()
    for i in range(12):
        (src / f"handler{i}.py").write_text(
            f"def handle_request_{i}(request, response):\n"
            f"    '''Handle request {i} and write the response.'''\n"
            f"    key = '{_FAKE_KEY}'\n"
            f"    return response\n",
            encoding="utf-8",
        )
    res = index_folder(str(src), use_ai_summaries=False)
    _result_cache.clear()
    return res["repo"]


async def _call(tool: str, args: dict) -> dict:
    out = _content(await server.call_tool(tool, {**args, "format": "compact"}))
    return _decode(out[0].text)


@pytest.mark.asyncio
async def test_a_compact_answer_says_a_secret_was_redacted(repo):
    back = await _call(
        "search_text", {"repo": repo, "query": "AKIA", "max_results": 20}
    )
    assert _FAKE_KEY not in json.dumps(back)
    assert back.get("_meta", {}).get("secrets_redacted", 0) > 0


@pytest.mark.asyncio
async def test_a_compact_answer_keeps_the_budget_note(repo, monkeypatch):
    from jcodemunch_mcp.storage import token_tracker

    note = {"limit": 1000, "spent": 1500, "state": "over"}
    monkeypatch.setattr(token_tracker, "budget_status", lambda: dict(note))
    back = await _call(
        "search_symbols", {"repo": repo, "query": "handle request", "max_results": 10}
    )
    assert back.get("_meta", {}).get("budget") == note


_TABLES = [sd.TableSpec(key="results", tag="r", cols=["id", "name"])]


@pytest.mark.parametrize("key", sorted(sd.DISPATCHER_META_JSON))
def test_the_helper_carries_a_dispatcher_key_no_schema_lists(key):
    value = {"count": 2, "symbols": ["a", "b"]}
    response = {"results": [{"id": "a", "name": "a"}], "_meta": {key: value}}
    payload, _ = sd.encode("t", response, "t1", _TABLES)
    assert sd.decode(payload, _TABLES)["_meta"] == {key: value}


def test_an_ordinary_unlisted_meta_key_stays_off_the_wire():
    """The steering-hint review ruled that a tool's own unlisted `_meta` keys
    (`hint`) stay off the compact wire; only the dispatcher's keys are added."""
    response = {"results": [{"id": "a", "name": "a"}], "_meta": {"hint": "tool hint"}}
    payload, _ = sd.encode("t", response, "t1", _TABLES)
    assert "_meta" not in sd.decode(payload, _TABLES)


# The keys the dispatcher writes into `_meta` between the `meta_fields` filter
# and the compact encoding step. `powered_by` is written by the filter itself,
# and only when the caller listed it; `tokens_saved_basis` is copied there and
# is in UNIVERSAL_META_JSON anyway.
_WRITE = re.compile(
    r'\["_meta"\]\s*\[\s*"(\w+)"\]'
    r'|setdefault\(\s*"_meta"\s*,\s*\{\}\s*\)\s*\[\s*"(\w+)"\]'
    r'|\b(?:meta|m|_meta)\[\s*"(\w+)"\]\s*='
)
_WRITTEN_BY_THE_FILTER = {"powered_by", "tokens_saved_basis"}


def _keys_written(text: str) -> set[str]:
    return {next(g for g in m.groups() if g) for m in _WRITE.finditer(text)}


def _post_filter_window() -> str:
    src = inspect.getsource(server._call_tool_impl)
    start = src.index('meta_fields = config_module.get("meta_fields")')
    end = src.index("encode_response(name, result", start)
    helpers = "".join(
        inspect.getsource(getattr(server, f))
        for f in ("_label_lifetime_total", "_label_savings_basis", "_steer_note_call")
    )
    return src[start:end] + helpers


def test_every_key_the_dispatcher_adds_after_the_filter_is_carried():
    written = _keys_written(_post_filter_window()) - _WRITTEN_BY_THE_FILTER
    assert {"secrets_redacted", "budget", "already_delivered", "steer_hint"} <= written
    missing = written - sd.CARRIED_META_JSON
    assert not missing, (
        f"the dispatcher adds {sorted(missing)} to _meta after the meta_fields filter; "
        "add them to schema_driven.DISPATCHER_META_JSON or a compact answer drops them"
    )


def test_the_scan_sees_a_new_key_written_after_the_filter():
    """Non-vacuity: the scan reports a key written in the window's own spellings."""
    window = _post_filter_window() + "".join(
        (
            '\n        result.setdefault("_meta", {})["invented_later"] = 1',
            '\n        meta["also_new"] = 2\n',
        )
    )
    written = _keys_written(window) - _WRITTEN_BY_THE_FILTER
    assert {"invented_later", "also_new"} <= written - sd.CARRIED_META_JSON
