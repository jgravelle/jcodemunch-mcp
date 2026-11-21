"""A compact answer keeps every `_meta` key the dispatcher adds after the display filter.

LEDGER L-313 (from review 1 of the compact `get_file_tree` encoding). Each
schema encoder keeps only the `_meta` keys it lists plus `UNIVERSAL_META_JSON`.
The dispatcher writes `_meta` keys after the `meta_fields` strip, so they reach
a default install (`meta_fields: []`). Most are in `UNIVERSAL_META_JSON`; three
were not: `secrets_redacted` (redaction is on by default), `budget` (the
session is near or over its token budget) and `already_delivered`. A compact
answer from any schema encoder dropped those three where the same answer as
JSON kept them: a response the server had altered by redacting a secret
reached the agent with no word that it had been altered.

`schema_driven` carries them now (`DISPATCHER_META_JSON`), so every schema
encoder inherits it."""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

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


# The dispatcher's `_meta` writes between the `meta_fields` filter and the
# compact encoding step, read from the syntax tree, not the text. A `_meta`
# expression is `x["_meta"]`, `x.setdefault("_meta", ...)`, `x.get("_meta")`,
# or a name bound to one. A write is a string key assigned on one, passed to
# its `setdefault`, or given to its `update`, or a key of a dict literal
# assigned to `x["_meta"]`. Every function of `server` the window calls is
# read the same way, and every function those call. `powered_by` is written by
# the filter itself, only when the caller listed it; `tokens_saved_basis` is
# copied there and is in UNIVERSAL_META_JSON anyway. A function reached
# through another module (`token_tracker.budget_status`) is not read: it
# returns a value, and the write is here.
_WRITTEN_BY_THE_FILTER = {"powered_by", "tokens_saved_basis"}


def _str(node) -> str | None:
    return (
        node.value
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        else None
    )


def _is_meta(node, names: set[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in names
    if isinstance(node, ast.Subscript):
        return _str(node.slice) == "_meta"
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return (
            node.func.attr in ("setdefault", "get")
            and bool(node.args)
            and _str(node.args[0]) == "_meta"
        )
    return False


def _dict_keys(node) -> set[str]:
    if not isinstance(node, ast.Dict):
        return set()
    return {k for k in map(_str, node.keys) if k}


def _writes(walked: list) -> tuple[set[str], set[str]]:
    """(keys written to `_meta`, names of functions called) over these nodes."""
    names: set[str] = set()
    lists: set[str] = set()  # names bound to a list of `_meta` dicts
    while True:
        bound, bound_lists = set(), set()
        for n in walked:
            if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None:
                for t in n.targets if isinstance(n, ast.Assign) else [n.target]:
                    if not isinstance(t, ast.Name):
                        continue
                    if _is_meta(n.value, names):
                        bound.add(t.id)
                    if isinstance(
                        n.value, (ast.ListComp, ast.GeneratorExp)
                    ) and _is_meta(n.value.elt, names):
                        bound_lists.add(t.id)
            if isinstance(n, ast.For) and isinstance(n.target, ast.Name):
                it = n.iter
                if (isinstance(it, ast.Name) and it.id in lists) or (
                    isinstance(it, (ast.ListComp, ast.GeneratorExp))
                    and _is_meta(it.elt, names)
                ):
                    bound.add(n.target.id)
        if bound <= names and bound_lists <= lists:
            break
        names |= bound
        lists |= bound_lists
    keys: set[str] = set()
    calls: set[str] = set()
    for n in walked:
        targets = (
            n.targets
            if isinstance(n, ast.Assign)
            else [n.target]
            if isinstance(n, (ast.AugAssign, ast.AnnAssign))
            else []
        )
        for t in targets:
            if (
                isinstance(t, ast.Subscript)
                and _is_meta(t.value, names)
                and _str(t.slice)
            ):
                keys.add(_str(t.slice))
            if isinstance(t, ast.Subscript) and _str(t.slice) == "_meta":
                keys |= _dict_keys(n.value)
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                calls.add(f.id)
            if isinstance(f, ast.Attribute) and _is_meta(f.value, names):
                if f.attr == "setdefault" and n.args and _str(n.args[0]):
                    keys.add(_str(n.args[0]))
                if f.attr == "update":
                    for a in n.args:
                        keys |= _dict_keys(a)
                    keys |= {k.arg for k in n.keywords if k.arg}
    return keys, calls


def _module_functions(tree) -> dict:
    return {
        n.name: n
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _keys_after_the_filter(source: str) -> set[str]:
    tree = ast.parse(source)
    funcs = _module_functions(tree)
    impl = funcs["_call_tool_impl"]
    lines = source.splitlines()
    start = next(
        i
        for i, ln in enumerate(lines, 1)
        if 'meta_fields = config_module.get("meta_fields")' in ln
    )
    end = next(
        i
        for i, ln in enumerate(lines, 1)
        if i > start and "encode_response(name, result" in ln
    )
    # Every node that starts inside the window, so a statement that opens
    # before it or a `try` that runs on past it counts only for its lines here.
    window = [n for n in ast.walk(impl) if start <= getattr(n, "lineno", 0) < end]
    keys, calls = _writes(window)
    seen: set[str] = set()
    while calls - seen:
        name = (calls - seen).pop()
        seen.add(name)
        if name in funcs and name != "_call_tool_impl":
            k, c = _writes(list(ast.walk(funcs[name])))
            keys |= k
            calls |= c
    return keys - _WRITTEN_BY_THE_FILTER


def _server_source() -> str:
    return Path(inspect.getsourcefile(server)).read_text(encoding="utf-8")


def test_every_key_the_dispatcher_adds_after_the_filter_is_carried():
    written = _keys_after_the_filter(_server_source())
    assert {
        "secrets_redacted",
        "budget",
        "already_delivered",
        "steer_hint",
        "receipts",
        "lifetime_meter",
    } <= written
    missing = written - sd.CARRIED_META_JSON
    assert not missing, (
        f"the dispatcher adds {sorted(missing)} to _meta after the meta_fields filter; "
        "add them to schema_driven.DISPATCHER_META_JSON or a compact answer drops them"
    )


_PROBES = {
    "k1": 'meta = result.get("_meta")\n    meta.setdefault("k1", 1)',
    "k2": '_m = result.setdefault("_meta", {})\n    _m["k2"] = 1',
    "k3": 'result["_meta"].setdefault("k3", 1)',
    "k4": 'result.setdefault("_meta", {}).update({"k4": 1})',
    "k5": 'md = result["_meta"]\n    md["k5"]=1',
    "k6": 'result["_meta"] = {**result.get("_meta", {}), "k6": 1}',
    "k7": 'result.setdefault("_meta", {})["k7"] = 1',
    "k8": 'result.setdefault("_meta", {}).update(k8=1)',
    "k9": 'metas = [c["_meta"] for c in [result]]\n    for each in metas: each["k9"] = 1',
}


@pytest.mark.parametrize("key", sorted(_PROBES))
def test_the_scan_sees_a_write_in_the_window(key):
    """Non-vacuity, in each spelling a review found the first scan blind to."""
    src = _server_source()
    anchor = next(ln for ln in src.splitlines() if "encode_response(name, result" in ln)
    indent = anchor[: len(anchor) - len(anchor.lstrip())]
    probe = "\n".join(indent + ln.strip() for ln in _PROBES[key].splitlines())
    planted = src.replace(anchor, probe + "\n" + anchor, 1)
    assert key in _keys_after_the_filter(planted) - sd.CARRIED_META_JSON


def test_the_scan_follows_a_helper_the_window_calls():
    src = _server_source()
    anchor = next(ln for ln in src.splitlines() if "encode_response(name, result" in ln)
    indent = anchor[: len(anchor) - len(anchor.lstrip())]
    planted = src.replace(anchor, indent + "_l313_probe(result)\n" + anchor, 1)
    planted += '\n\ndef _l313_probe(result):\n    meta = result.setdefault("_meta", {})\n    meta["from_a_helper"] = 1\n'
    assert "from_a_helper" in _keys_after_the_filter(planted)
