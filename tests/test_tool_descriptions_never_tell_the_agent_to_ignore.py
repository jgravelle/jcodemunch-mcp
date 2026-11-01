"""No tool or parameter description tells the agent to ignore something.

`suggest_corrections`' `all_time` flag was described as "Ignore the window and
analyze the full ledger." It described the flag, but a sentence that opens
with "Ignore" is the shape of a prompt-injection override, and a security
scanner's classifier rated the repository high on it (p=0.98). Every
description reaches the agent in the tool schema, so it describes what a tool
or flag does and never addresses the agent with an override verb.

A passive form ("ignored when token_budget is set") states behavior and is
allowed; an imperative at the start of a sentence is not.
"""
from __future__ import annotations

import re

import pytest

from jcodemunch_mcp import server

_IMPERATIVE_OVERRIDE = re.compile(
    r"(?:^|[.!?:;]\s+|\n\s*)(ignore|disregard|forget)\b", re.IGNORECASE
)


def _descriptions(obj, path=""):
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "description" and isinstance(value, str):
                yield path or "(tool)", value
            else:
                yield from _descriptions(value, f"{path}.{key}" if path else str(key))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            yield from _descriptions(value, f"{path}[{i}]")


def _offenders(tools):
    found = []
    for tool in tools:
        texts = [("(tool)", tool.description or "")]
        texts += list(_descriptions(tool.inputSchema or {}))
        for where, text in texts:
            m = _IMPERATIVE_OVERRIDE.search(text)
            if m:
                found.append(f"{tool.name} {where}: {text.strip()[:120]}")
    return found


@pytest.mark.parametrize("surface", ["full", "counter"])
def test_no_published_description_opens_a_sentence_with_an_override_verb(surface):
    tools = server._build_tools_list(surface_override=surface)
    assert tools, f"the {surface} surface published no tools"
    assert not _offenders(tools), "\n".join(_offenders(tools))


def test_the_rule_tells_an_imperative_from_a_passive():
    class _T:
        def __init__(self, description, schema=None):
            self.name = "t"
            self.description = description
            self.inputSchema = schema or {}

    assert _offenders([_T("Ignore the window and analyze the full ledger.")])
    assert _offenders([_T("Counts rows. Disregard the cache.")])
    assert _offenders([_T("ok", {"properties": {"x": {"description": "Forget prior state."}}})])
    assert not _offenders([_T("Maximum results (ignored when token_budget is set).")])
    assert not _offenders([_T("When True, sort_by is ignored.")])
