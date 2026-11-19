"""Single-response size cap at the call_tool chokepoint (#425).

Before this existed, the largest reply the server could emit was bounded by
`max_file_size` -- an INDEXING limit, in a different subsystem, doing the job by
coincidence. Two tests here exist specifically to keep that coupling from
returning: one asserts the response limit is what bounds a response, and one
asserts that moving the indexing limit does not move the response limit.
"""

import json

import pytest
from mcp.types import CallToolResult, TextContent

from jcodemunch_mcp import server as S
from jcodemunch_mcp.security import (
    DEFAULT_MAX_RESPONSE_BYTES,
    get_max_response_bytes,
)


def _result(text: str) -> list[TextContent]:
    return [TextContent(type="text", text=text)]


def _body(result) -> dict:
    content = getattr(result, "content", result)
    return json.loads(content[0].text)


class TestResolver:
    def test_default(self):
        assert get_max_response_bytes() == DEFAULT_MAX_RESPONSE_BYTES

    def test_explicit_arg_wins(self):
        assert get_max_response_bytes(4096) == 4096

    def test_zero_is_an_explicit_opt_out(self):
        assert get_max_response_bytes(0) == 0

    def test_negative_arg_rejected(self):
        with pytest.raises(ValueError):
            get_max_response_bytes(-1)

    @pytest.mark.parametrize("bad", ["512", None, 3.5, True, -7])
    def test_garbage_config_falls_back_to_default_not_uncapped(self, bad, monkeypatch):
        """A typo must never mean 'no ceiling'. Only a literal 0 opts out."""
        monkeypatch.setattr(S, "_UNUSED", None, raising=False)
        from jcodemunch_mcp import config as c
        monkeypatch.setitem(c._GLOBAL_CONFIG, "response_max_bytes", bad)
        assert get_max_response_bytes() == DEFAULT_MAX_RESPONSE_BYTES

    def test_zero_in_config_opts_out(self, monkeypatch):
        from jcodemunch_mcp import config as c
        monkeypatch.setitem(c._GLOBAL_CONFIG, "response_max_bytes", 0)
        assert get_max_response_bytes() == 0


class TestCapBehaviour:
    def test_under_the_limit_passes_through_unchanged(self, monkeypatch):
        monkeypatch.setattr(S, "get_max_response_bytes", None, raising=False)
        from jcodemunch_mcp import config as c
        monkeypatch.setitem(c._GLOBAL_CONFIG, "response_max_bytes", 1000)
        r = _result("x" * 100)
        assert S._enforce_response_cap("search_text", r) is r

    def test_over_the_limit_refuses(self, monkeypatch):
        from jcodemunch_mcp import config as c
        monkeypatch.setitem(c._GLOBAL_CONFIG, "response_max_bytes", 500)
        out = S._enforce_response_cap("get_file_content", _result("x" * 5000))
        assert isinstance(out, CallToolResult) and out.is_error
        b = _body(out)
        assert b["response_bytes"] == 5000
        assert b["response_max_bytes"] == 500
        assert b["tool"] == "get_file_content"

    def test_refuses_rather_than_truncating(self, monkeypatch):
        """A shortened body is indistinguishable from a complete one."""
        from jcodemunch_mcp import config as c
        monkeypatch.setitem(c._GLOBAL_CONFIG, "response_max_bytes", 500)
        out = S._enforce_response_cap("get_file_content", _result("y" * 5000))
        text = getattr(out, "content", out)[0].text
        assert "yyyy" not in text, "a truncated body was served instead of a refusal"
        assert json.loads(text)["error"].startswith("Response too large")

    def test_error_names_the_key_that_moves_it(self, monkeypatch):
        from jcodemunch_mcp import config as c
        monkeypatch.setitem(c._GLOBAL_CONFIG, "response_max_bytes", 10)
        b = _body(S._enforce_response_cap("t", _result("z" * 999)))
        assert "response_max_bytes" in b["hint"]
        assert "max_file_size" in b["hint"], "must say which limit this is NOT"

    def test_zero_disables_the_cap(self, monkeypatch):
        from jcodemunch_mcp import config as c
        monkeypatch.setitem(c._GLOBAL_CONFIG, "response_max_bytes", 0)
        r = _result("x" * 10_000_000)
        assert S._enforce_response_cap("t", r) is r

    def test_existing_error_passes_through_untouched(self, monkeypatch):
        """Capping an error would replace a specific diagnosis with a generic one."""
        from jcodemunch_mcp import config as c
        monkeypatch.setitem(c._GLOBAL_CONFIG, "response_max_bytes", 10)
        err = CallToolResult(
            content=[TextContent(type="text", text=json.dumps({"error": "repo not found"}))],
            isError=True,
        )
        out = S._enforce_response_cap("t", err)
        assert out is err

    def test_measures_utf8_bytes_not_characters(self, monkeypatch):
        """A multibyte reply must be measured as it goes on the wire."""
        from jcodemunch_mcp import config as c
        monkeypatch.setitem(c._GLOBAL_CONFIG, "response_max_bytes", 150)
        text = "é" * 100  # 100 chars, 200 UTF-8 bytes
        out = S._enforce_response_cap("t", _result(text))
        assert getattr(out, "isError", False), "measured characters instead of bytes"
        assert _body(out)["response_bytes"] == 200

    def test_sums_across_multiple_content_items(self, monkeypatch):
        from jcodemunch_mcp import config as c
        monkeypatch.setitem(c._GLOBAL_CONFIG, "response_max_bytes", 150)
        out = S._enforce_response_cap("t", [
            TextContent(type="text", text="a" * 100),
            TextContent(type="text", text="b" * 100),
        ])
        assert _body(out)["response_bytes"] == 200

    def test_a_broken_cap_never_fails_closed(self, monkeypatch):
        """A cap that can take down a working call is worse than no cap."""
        import jcodemunch_mcp.security as sec
        def boom(*a, **k):
            raise RuntimeError("resolver exploded")
        monkeypatch.setattr(sec, "get_max_response_bytes", boom)
        r = _result("x" * 10)
        assert S._enforce_response_cap("t", r) is r


class TestChokepointCoverage:
    def test_call_tool_is_the_wrapper_not_the_dispatcher(self):
        """The cap must sit outside the dispatcher, or a new early return skips it.

        The dispatcher has more than a dozen return sites across the MUNCH,
        JSON, in-band-error and front-door paths.

        ⚠ This counted the SUBSTRING "return" in the source and asserted it
        equalled 1. That is a restatement of the mechanism, not the property:
        adding a comment containing the word "returned" turned it red while the
        wrapper still had exactly one exit (#551, 2026-08-26). Counting `Return`
        NODES asks the question the docstring actually poses, and is immune to
        prose.
        """
        import ast
        import inspect
        import textwrap
        src = inspect.getsource(S.call_tool)
        assert "_enforce_response_cap" in src
        assert "_call_tool_impl" in src
        fn = ast.parse(textwrap.dedent(src)).body[0]
        returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
        assert len(returns) == 1, (
            f"the wrapper must have exactly one exit; found {len(returns)}. A "
            f"second return skips the cap for whatever path takes it."
        )

    def test_dispatcher_itself_does_not_apply_the_cap(self):
        """Belt-and-braces inside the dispatcher would hide a missing wrapper."""
        import inspect
        assert "_enforce_response_cap" not in inspect.getsource(S._call_tool_impl)


class TestDecoupledFromIndexingLimit:
    """#425's core claim: the indexing cap was bounding responses by coincidence."""

    def test_response_limit_is_not_the_indexing_limit(self):
        from jcodemunch_mcp.security import DEFAULT_MAX_FILE_SIZE
        assert DEFAULT_MAX_RESPONSE_BYTES != DEFAULT_MAX_FILE_SIZE

    def test_moving_the_indexing_limit_does_not_move_the_response_limit(self, monkeypatch):
        """Raising max_file_size to index a large generated file must not
        silently raise how large a single reply can be."""
        from jcodemunch_mcp import config as c
        from jcodemunch_mcp.security import get_max_file_size
        monkeypatch.setitem(c._GLOBAL_CONFIG, "max_file_size", 50_000_000)
        assert get_max_file_size() == 50_000_000
        assert get_max_response_bytes() == DEFAULT_MAX_RESPONSE_BYTES

    def test_moving_the_response_limit_does_not_move_the_indexing_limit(self, monkeypatch):
        from jcodemunch_mcp import config as c
        from jcodemunch_mcp.security import get_max_file_size, DEFAULT_MAX_FILE_SIZE
        monkeypatch.setitem(c._GLOBAL_CONFIG, "response_max_bytes", 99_000_000)
        assert get_max_response_bytes() == 99_000_000
        assert get_max_file_size() == DEFAULT_MAX_FILE_SIZE


class TestConfigSurface:
    def test_env_var_is_mapped(self):
        from jcodemunch_mcp.config import ENV_VAR_MAPPING
        assert ENV_VAR_MAPPING["JCODEMUNCH_RESPONSE_MAX_BYTES"] == "response_max_bytes"

    def test_key_is_typed_and_defaulted(self):
        from jcodemunch_mcp.config import CONFIG_TYPES, DEFAULTS
        assert CONFIG_TYPES["response_max_bytes"] is int
        assert DEFAULTS["response_max_bytes"] == DEFAULT_MAX_RESPONSE_BYTES

    def test_documented_in_the_template(self):
        """An undocumented limit is the emergent property this replaces."""
        from jcodemunch_mcp.config import generate_template
        t = generate_template()
        assert "response_max_bytes" in t


class TestEndToEndThroughCallTool:
    """Source inspection proves placement; only a real call proves enforcement."""

    @pytest.mark.asyncio
    async def test_a_real_oversized_call_is_refused(self, monkeypatch):
        from jcodemunch_mcp import config as c
        monkeypatch.setitem(c._GLOBAL_CONFIG, "response_max_bytes", 200)
        out = await S.call_tool("menu", {"query": "search"})
        assert getattr(out, "isError", False), "cap did not fire through call_tool"
        b = _body(out)
        assert b["response_max_bytes"] == 200
        assert b["response_bytes"] > 200

    @pytest.mark.asyncio
    async def test_the_same_call_succeeds_under_a_generous_cap(self, monkeypatch):
        """Control: the refusal above is the cap, not a broken tool."""
        from jcodemunch_mcp import config as c
        monkeypatch.setitem(c._GLOBAL_CONFIG, "response_max_bytes", 10_000_000)
        out = await S.call_tool("menu", {"query": "search"})
        assert not getattr(out, "isError", False)
        assert "Response too large" not in getattr(out, "content", out)[0].text
