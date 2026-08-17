"""The dispatcher must not eat the caller's `arguments` dict (#482).

`call_tool` extracts `format` because it belongs to no tool's schema. It used
to do that with `arguments.pop("format")` — on the caller's own object. A
caller that reuses one args dict across two calls therefore got its `format`
stripped by the first call, and every call after it fell back to whatever
`server_output` resolves to.

⚠⚠ **The first call proving the argument works is what makes this expensive.**
Nothing errors, nothing warns, and the response is still valid — just encoded
differently from what was asked for.

⚠ Over the wire this is invisible: every MCP request arrives as a freshly
parsed dict. The exposed callers are in-process ones, which includes the
Counter front door re-dispatching into `call_tool` and the whole test suite.

⚠ These assertions are deliberately about the ARGUMENT DICT, not about the
response encoding. The observed failure was a `json.loads` error on a MUNCH
payload, but that only happens when the 15% encoding gate fires, which depends
on the exact response bytes — it showed on 3 of 8 CI legs and on neither local
platform. Asserting the mutation itself is deterministic everywhere.
"""

import pytest

from jcodemunch_mcp import server


@pytest.mark.asyncio
async def test_call_tool_does_not_strip_format_from_the_caller_dict():
    args = {"repo": "no-such-repo", "query": "anything", "format": "json"}

    # The repo does not resolve; that is fine. The extraction happens before
    # any routing, so an in-band error response still exercises the path.
    await server.call_tool("search_symbols", args)

    assert args["format"] == "json", (
        "call_tool popped `format` off the caller's dict; a caller reusing one "
        "args object silently loses its requested encoding on the second call"
    )


@pytest.mark.asyncio
async def test_repeated_calls_with_one_args_dict_keep_the_requested_format():
    """The end-to-end shape of the defect: same dict, two calls."""
    args = {"repo": "no-such-repo", "query": "anything", "format": "json"}

    await server.call_tool("search_symbols", args)
    second = await server.call_tool("search_symbols", args)

    # Whatever the second call returns, it must have been asked for JSON --
    # i.e. the argument survived to be read a second time.
    assert args.get("format") == "json"
    assert second is not None


@pytest.mark.asyncio
async def test_the_dispatcher_still_honours_format_when_it_is_present():
    """Non-vacuity: copying the dict must not stop `format` being read.

    A fix that simply stopped popping would leave `format` in `arguments` and
    hand an unexpected keyword to the tool, so this pins that the extraction
    still happens on the dispatcher's own copy.
    """
    import json

    args = {"repo": "no-such-repo", "query": "anything", "format": "json"}
    res = await server.call_tool("search_symbols", args)

    content = res.content if hasattr(res, "content") else res
    # JSON was requested, so the payload must parse as JSON rather than MUNCH.
    json.loads(content[0].text)
