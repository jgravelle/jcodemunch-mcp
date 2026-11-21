"""Runtime tier state + tools/list_changed emission tests."""

import asyncio
import pytest

from jcodemunch_mcp import server as server_mod
from jcodemunch_mcp import config as config_mod


def _default_override():
    """Read the default-session override bucket directly."""
    return server_mod._session_tier_overrides.get(server_mod._SESSION_TIER_DEFAULT_KEY)


def _rtext(result):
    """Read content[0].text whether `result` is a plain content list (success)
    or a CallToolResult (error path now carries isError; v1.108.74 / F-P01)."""
    from mcp.types import CallToolResult
    content = result.content if isinstance(result, CallToolResult) else result
    return content[0].text


class TestSessionTierState:
    def setup_method(self):
        server_mod._reset_session_tiers()

    def test_default_is_empty(self):
        assert _default_override() is None

    def test_set_get_tier(self):
        server_mod._set_session_tier("core")
        assert _default_override() == "core"

    def test_set_none_evicts_entry(self):
        server_mod._set_session_tier("core")
        server_mod._set_session_tier(None)
        assert server_mod._SESSION_TIER_DEFAULT_KEY not in server_mod._session_tier_overrides

    def test_effective_profile_prefers_session(self, monkeypatch):
        """When session tier is set, it overrides config tool_profile."""
        monkeypatch.setattr(config_mod, "get", lambda k, *a, **kw: "full" if k == "tool_profile" else {})
        server_mod._set_session_tier("core")
        assert server_mod._effective_profile() == "core"

    def test_effective_profile_falls_back_to_config(self, monkeypatch):
        monkeypatch.setattr(
            config_mod, "get",
            lambda k, *a, **kw: "standard" if k == "tool_profile" else {}
        )
        server_mod._reset_session_tiers()
        assert server_mod._effective_profile() == "standard"


class TestPerSessionIsolation:
    """Two concurrent MCP sessions on the same process must not leak tier state."""

    def setup_method(self):
        server_mod._reset_session_tiers()

    def teardown_method(self):
        server_mod._reset_session_tiers()

    def _install_session(self, monkeypatch, session_obj):
        class _FakeRequestContext:
            def __init__(self, session):
                self.session = session

        class _FakeServer:
            def __init__(self, request_context):
                self.request_context = request_context

        monkeypatch.setattr(server_mod, "server", _FakeServer(_FakeRequestContext(session_obj)))

    def test_sessions_hold_independent_overrides(self, monkeypatch):
        session_a = object()
        session_b = object()

        self._install_session(monkeypatch, session_a)
        server_mod._set_session_tier("core")
        assert server_mod._effective_profile() == "core"

        self._install_session(monkeypatch, session_b)
        assert server_mod._effective_profile() != "core" or len(server_mod._session_tier_overrides) == 1
        server_mod._set_session_tier("standard")
        assert server_mod._effective_profile() == "standard"

        self._install_session(monkeypatch, session_a)
        assert server_mod._effective_profile() == "core"

    def test_session_id_attribute_is_preferred_over_identity(self, monkeypatch):
        class _Session:
            session_id = "stable-sid-xyz"

        s1 = _Session()
        s2 = _Session()  # different object identity, same session_id
        self._install_session(monkeypatch, s1)
        server_mod._set_session_tier("core")

        self._install_session(monkeypatch, s2)
        assert server_mod._effective_profile() == "core"

    def test_many_sessions_do_not_exceed_memory(self, monkeypatch):
        """Pre-v1.62 this test exercised LRU-cap eviction. After audit
        findings F2 + F3 we dropped the cap entirely and switched to
        weak-reference tracking: entries disappear when the session object
        is collected, so there's no need to evict live ones."""
        class _S:
            pass

        for i in range(500):
            s = _S()
            s.session_id = f"s-{i}"
            self._install_session(monkeypatch, s)
            server_mod._set_session_tier("core")

        # Every unique session_id stayed (no silent eviction of live state).
        assert len(server_mod._session_tier_overrides) == 500
        assert "s-0" in server_mod._session_tier_overrides
        assert "s-499" in server_mod._session_tier_overrides

    def test_gc_does_not_leak_tier_to_reused_id(self, monkeypatch):
        """Audit F2: `id()` can be reused after GC. A freed session's tier
        override must NOT be inherited by a later session allocated at the
        same address."""
        import gc

        class _S:
            pass

        s1 = _S()
        self._install_session(monkeypatch, s1)
        server_mod._set_session_tier("core")
        assert server_mod._effective_profile() == "core"

        # Release the first session and force collection so its weakref
        # finalizer runs and drops the tier override.
        del s1
        gc.collect()

        s2 = _S()
        self._install_session(monkeypatch, s2)
        # Fresh session must land on config default, not the carryover tier.
        assert server_mod._effective_profile() != "core"


class TestEmitToolsListChanged:
    @pytest.mark.asyncio
    async def test_emit_does_not_raise(self):
        """Helper must be a no-op when MCP session isn't available."""
        await server_mod._emit_tools_list_changed()  # must not raise

    @pytest.mark.asyncio
    async def test_emit_calls_session_send_tool_list_changed(self, monkeypatch):
        """Integration-style check: use module server.request_context, not _get_mcp_session mocking."""

        class _FakeSession:
            def __init__(self):
                self.called = False

            async def send_tool_list_changed(self):
                self.called = True

        class _FakeRequestContext:
            def __init__(self, session):
                self.session = session

        class _FakeServer:
            def __init__(self, request_context):
                self.request_context = request_context

        fake_session = _FakeSession()
        fake_server = _FakeServer(_FakeRequestContext(fake_session))
        monkeypatch.setattr(server_mod, "server", fake_server)

        await server_mod._emit_tools_list_changed()
        assert fake_session.called is True

    @pytest.mark.asyncio
    async def test_emit_warns_when_session_has_no_send_method(self, caplog, monkeypatch):
        class _FakeSession:
            pass

        class _FakeRequestContext:
            def __init__(self, session):
                self.session = session

        class _FakeServer:
            def __init__(self, request_context):
                self.request_context = request_context

        monkeypatch.setattr(server_mod, "server", _FakeServer(_FakeRequestContext(_FakeSession())))
        caplog.set_level("WARNING")

        await server_mod._emit_tools_list_changed()

        msgs = [r.message for r in caplog.records]
        assert any("send_tool_list_changed" in m for m in msgs)


def test_startup_logs_bundle_disabled_overlap(caplog, monkeypatch):
    monkeypatch.setattr(
        config_mod, "get",
        lambda k, *a, **kw: {
            "tool_tier_bundles": {"core": ["search_symbols"]},
            "disabled_tools": ["search_symbols"],
        }.get(k, (a[0] if a else None)),
    )
    caplog.set_level("WARNING")
    server_mod._log_startup_validation_warnings()
    msgs = [r.message for r in caplog.records]
    assert any("search_symbols" in m and "disabled_tools" in m for m in msgs)


def test_note_adaptive_tiering_logs_info_when_enabled(caplog, monkeypatch):
    real_get = config_mod.get

    def _fake_get(key, *a, **kw):
        if key == "adaptive_tiering":
            return True
        return real_get(key, *a, **kw)

    monkeypatch.setattr(config_mod, "get", _fake_get)
    caplog.set_level("INFO")
    server_mod._note_adaptive_tiering_transport("sse")  # must not raise
    msgs = [r.message for r in caplog.records]
    assert any("adaptive_tiering" in m and "transport=sse" in m for m in msgs)


def test_note_adaptive_tiering_noop_when_disabled(monkeypatch):
    real_get = config_mod.get

    def _fake_get(key, *a, **kw):
        if key == "adaptive_tiering":
            return False
        return real_get(key, *a, **kw)

    monkeypatch.setattr(config_mod, "get", _fake_get)
    server_mod._note_adaptive_tiering_transport("sse")
    server_mod._note_adaptive_tiering_transport("streamable-http")


# --------------------------------------------------------------------------- #
# set_tool_tier tool                                                           #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_set_tool_tier_switches_session_tier():
    from copy import deepcopy
    from jcodemunch_mcp.server import call_tool

    orig_config = config_mod._GLOBAL_CONFIG.copy()
    config_mod._GLOBAL_CONFIG.clear()
    config_mod._GLOBAL_CONFIG.update(deepcopy(config_mod.DEFAULTS))
    server_mod._reset_session_tiers()

    try:
        result = await call_tool("set_tool_tier", {"tier": "core"})
        text = _rtext(result)
        import json
        data = json.loads(text)
        assert data.get("ok") is True
        assert data.get("tier") == "core"
        assert _default_override() == "core"
    finally:
        config_mod._GLOBAL_CONFIG.clear()
        config_mod._GLOBAL_CONFIG.update(orig_config)
        server_mod._reset_session_tiers()


@pytest.mark.asyncio
async def test_set_tool_tier_rejects_invalid_tier():
    from copy import deepcopy
    from jcodemunch_mcp.server import call_tool

    orig_config = config_mod._GLOBAL_CONFIG.copy()
    config_mod._GLOBAL_CONFIG.clear()
    config_mod._GLOBAL_CONFIG.update(deepcopy(config_mod.DEFAULTS))

    try:
        result = await call_tool("set_tool_tier", {"tier": "enormous"})
        from mcp.types import CallToolResult
        assert isinstance(result, CallToolResult)  # errors now carry isError (F-P01)
        assert result.isError is True
        import json
        data = json.loads(result.content[0].text)
        assert "error" in data
    finally:
        config_mod._GLOBAL_CONFIG.clear()
        config_mod._GLOBAL_CONFIG.update(orig_config)


# --------------------------------------------------------------------------- #
# announce_model tool                                                          #
# --------------------------------------------------------------------------- #

@pytest.fixture
def adaptive_on(monkeypatch):
    """Enable adaptive_tiering for the duration of a test."""
    real_get = config_mod.get

    def fake_get(key, *a, **kw):
        if key == "adaptive_tiering":
            return True
        return real_get(key, *a, **kw)

    monkeypatch.setattr(config_mod, "get", fake_get)
    yield


@pytest.mark.asyncio
async def test_announce_model_resolves_tier(adaptive_on):
    from copy import deepcopy
    from jcodemunch_mcp.server import call_tool
    import json

    orig_config = config_mod._GLOBAL_CONFIG.copy()
    config_mod._GLOBAL_CONFIG.clear()
    config_mod._GLOBAL_CONFIG.update(deepcopy(config_mod.DEFAULTS))
    server_mod._reset_session_tiers()

    try:
        result = await call_tool("announce_model", {"model": "claude-haiku-4-5"})
        text = _rtext(result)
        data = json.loads(text)
        assert data["ok"] is True
        assert data["tier"] == "core"
        assert data["changed"] is True
        assert data["match_reason"].startswith("substring:")
        assert _default_override() == "core"
    finally:
        config_mod._GLOBAL_CONFIG.clear()
        config_mod._GLOBAL_CONFIG.update(orig_config)
        server_mod._reset_session_tiers()


@pytest.mark.asyncio
async def test_announce_model_idempotent(adaptive_on):
    from copy import deepcopy
    from jcodemunch_mcp.server import call_tool
    import json

    orig_config = config_mod._GLOBAL_CONFIG.copy()
    config_mod._GLOBAL_CONFIG.clear()
    config_mod._GLOBAL_CONFIG.update(deepcopy(config_mod.DEFAULTS))
    server_mod._reset_session_tiers()

    try:
        await call_tool("announce_model", {"model": "claude-haiku-4-5"})
        second = await call_tool("announce_model", {"model": "claude-haiku-4-5"})
        text = _rtext(second)
        data = json.loads(text)
        assert data["changed"] is False
    finally:
        config_mod._GLOBAL_CONFIG.clear()
        config_mod._GLOBAL_CONFIG.update(orig_config)
        server_mod._reset_session_tiers()


@pytest.mark.asyncio
async def test_announce_model_unknown_falls_back_full(adaptive_on):
    from copy import deepcopy
    from jcodemunch_mcp.server import call_tool
    import json

    orig_config = config_mod._GLOBAL_CONFIG.copy()
    config_mod._GLOBAL_CONFIG.clear()
    config_mod._GLOBAL_CONFIG.update(deepcopy(config_mod.DEFAULTS))
    server_mod._reset_session_tiers()

    try:
        result = await call_tool("announce_model", {"model": "totally-new-model-2030"})
        text = _rtext(result)
        data = json.loads(text)
        assert data["tier"] == "full"
        assert data["match_reason"] == "wildcard"
    finally:
        config_mod._GLOBAL_CONFIG.clear()
        config_mod._GLOBAL_CONFIG.update(orig_config)
        server_mod._reset_session_tiers()


@pytest.mark.asyncio
async def test_announce_model_noop_when_adaptive_tiering_disabled():
    """When adaptive_tiering is false (default), announce_model does not switch tier."""
    from copy import deepcopy
    from jcodemunch_mcp.server import call_tool
    import json

    orig_config = config_mod._GLOBAL_CONFIG.copy()
    config_mod._GLOBAL_CONFIG.clear()
    config_mod._GLOBAL_CONFIG.update(deepcopy(config_mod.DEFAULTS))
    server_mod._reset_session_tiers()

    try:
        result = await call_tool("announce_model", {"model": "claude-haiku-4-5"})
        text = _rtext(result)
        data = json.loads(text)
        assert data["ok"] is True
        assert data["changed"] is False
        assert data.get("adaptive_tiering") is False
        assert _default_override() is None
    finally:
        config_mod._GLOBAL_CONFIG.clear()
        config_mod._GLOBAL_CONFIG.update(orig_config)
        server_mod._reset_session_tiers()


# --------------------------------------------------------------------------- #
# Force-include runtime tools in every tier                                    #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_switch_tools_always_present_on_core():
    """set_tool_tier + announce_model are in tools/list regardless of tier (announce_model with adaptive_tiering on)."""
    from copy import deepcopy

    orig_config = config_mod._GLOBAL_CONFIG.copy()
    config_mod._GLOBAL_CONFIG.clear()
    config_mod._GLOBAL_CONFIG.update(deepcopy(config_mod.DEFAULTS))
    config_mod._GLOBAL_CONFIG["tool_profile"] = "core"
    config_mod._GLOBAL_CONFIG["disabled_tools"] = []
    config_mod._GLOBAL_CONFIG["adaptive_tiering"] = True  # announce_model is published only then
    server_mod._set_session_tier("core")

    try:
        from jcodemunch_mcp.server import list_tools
        tools = await list_tools()
        names = {t.name for t in tools}
        assert "set_tool_tier" in names
        assert "announce_model" in names
    finally:
        config_mod._GLOBAL_CONFIG.clear()
        config_mod._GLOBAL_CONFIG.update(orig_config)
        server_mod._reset_session_tiers()


@pytest.mark.asyncio
async def test_switch_tools_not_filterable_by_disabled():
    """Even if user disables them via disabled_tools, they stay exposed (announce_model with adaptive_tiering on)."""
    from copy import deepcopy

    orig_config = config_mod._GLOBAL_CONFIG.copy()
    orig_disabled = list(config_mod._GLOBAL_CONFIG.get("disabled_tools", []))
    config_mod._GLOBAL_CONFIG.clear()
    config_mod._GLOBAL_CONFIG.update(deepcopy(config_mod.DEFAULTS))
    config_mod._GLOBAL_CONFIG["disabled_tools"] = list(orig_disabled) + ["set_tool_tier", "announce_model"]
    config_mod._GLOBAL_CONFIG["adaptive_tiering"] = True  # announce_model is published only then

    try:
        from jcodemunch_mcp.server import list_tools
        tools = await list_tools()
        names = {t.name for t in tools}
        assert "set_tool_tier" in names
        assert "announce_model" in names
    finally:
        config_mod._GLOBAL_CONFIG.clear()
        config_mod._GLOBAL_CONFIG.update(orig_config)


@pytest.mark.asyncio
async def test_switch_tools_callable_even_when_is_tool_disabled_true(monkeypatch):
    """Runtime switch tools are exempt from disabled gate at call-time."""
    from jcodemunch_mcp.server import call_tool
    import json

    monkeypatch.setattr(
        config_mod,
        "is_tool_disabled",
        lambda name, repo=None: name in {"set_tool_tier", "announce_model"},
    )
    server_mod._reset_session_tiers()

    try:
        r1 = await call_tool("set_tool_tier", {"tier": "core"})
        d1 = json.loads(r1[0].text if hasattr(r1[0], "text") else r1[0])
        assert d1.get("ok") is True
        assert d1.get("tier") == "core"

        r2 = await call_tool("announce_model", {"model": "claude-haiku-4-5"})
        d2 = json.loads(r2[0].text if hasattr(r2[0], "text") else r2[0])
        assert "error" not in d2
    finally:
        server_mod._reset_session_tiers()


# --------------------------------------------------------------------------- #
# plan_turn(model=...) piggyback                                               #
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_plan_turn_model_piggyback_switches_tier(adaptive_on):
    """plan_turn(model=...) must flip the session tier as a side effect."""
    from copy import deepcopy
    from jcodemunch_mcp.server import call_tool
    import json

    orig_config = config_mod._GLOBAL_CONFIG.copy()
    config_mod._GLOBAL_CONFIG.clear()
    config_mod._GLOBAL_CONFIG.update(deepcopy(config_mod.DEFAULTS))
    server_mod._reset_session_tiers()

    try:
        args = {
            "repo": "local/jcodemunch-mcp-384d867b",
            "query": "anything",
            # ⚠ Was "claude-sonnet-4-6", which routed at "standard" -- a
            # narrowing the server now refuses because it cannot repay its own
            # cache invalidation. That made this test assert a switch that must
            # not happen. Haiku -> core is a narrowing that DOES pay (4
            # requests), so the property under test -- plan_turn(model=) flips
            # the session tier as a side effect -- is unchanged and still real.
            "model": "claude-haiku-4-5",
        }
        result = await call_tool("plan_turn", args)
        text = _rtext(result)
        data = json.loads(text)
        assert _default_override() == "core"
        ann = data.get("tier_announcement", {})
        assert ann.get("tier") == "core"
        assert ann.get("changed") is True
        assert "refused" not in ann
    finally:
        config_mod._GLOBAL_CONFIG.clear()
        config_mod._GLOBAL_CONFIG.update(orig_config)
        server_mod._reset_session_tiers()


@pytest.mark.asyncio
async def test_plan_turn_without_model_leaves_tier_untouched(adaptive_on):
    from copy import deepcopy
    from jcodemunch_mcp.server import call_tool
    import json

    orig_config = config_mod._GLOBAL_CONFIG.copy()
    config_mod._GLOBAL_CONFIG.clear()
    config_mod._GLOBAL_CONFIG.update(deepcopy(config_mod.DEFAULTS))
    server_mod._set_session_tier("core")

    try:
        await call_tool(
            "plan_turn",
            {"repo": "local/jcodemunch-mcp-384d867b", "query": "anything"},
        )
        assert _default_override() == "core"
    finally:
        config_mod._GLOBAL_CONFIG.clear()
        config_mod._GLOBAL_CONFIG.update(orig_config)
        server_mod._reset_session_tiers()


@pytest.mark.asyncio
async def test_plan_turn_model_noop_when_adaptive_tiering_disabled():
    """With adaptive_tiering=false (default), plan_turn's model param is
    accepted but the session tier is not switched."""
    from copy import deepcopy
    from jcodemunch_mcp.server import call_tool
    import json

    orig_config = config_mod._GLOBAL_CONFIG.copy()
    config_mod._GLOBAL_CONFIG.clear()
    config_mod._GLOBAL_CONFIG.update(deepcopy(config_mod.DEFAULTS))
    server_mod._reset_session_tiers()

    try:
        args = {
            "repo": "local/jcodemunch-mcp-384d867b",
            "query": "anything",
            "model": "claude-haiku-4-5",
        }
        result = await call_tool("plan_turn", args)
        text = _rtext(result)
        data = json.loads(text)
        assert _default_override() is None
        ann = data.get("tier_announcement", {})
        assert ann.get("changed") is False
        assert ann.get("adaptive_tiering") is False
    finally:
        config_mod._GLOBAL_CONFIG.clear()
        config_mod._GLOBAL_CONFIG.update(orig_config)
        server_mod._reset_session_tiers()


@pytest.mark.asyncio
async def test_plan_turn_model_does_not_switch_tier_when_plan_turn_errors(adaptive_on, monkeypatch):
    """Model tier switch must not persist if plan_turn handler fails."""
    from copy import deepcopy
    from jcodemunch_mcp.server import call_tool
    from jcodemunch_mcp.tools import plan_turn as plan_turn_module
    import json

    orig_config = config_mod._GLOBAL_CONFIG.copy()
    config_mod._GLOBAL_CONFIG.clear()
    config_mod._GLOBAL_CONFIG.update(deepcopy(config_mod.DEFAULTS))
    server_mod._reset_session_tiers()

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(plan_turn_module, "plan_turn", _boom)

    try:
        args = {
            "repo": "local/jcodemunch-mcp-384d867b",
            "query": "anything",
            "model": "claude-haiku-4-5",
        }
        result = await call_tool("plan_turn", args)
        text = _rtext(result)
        data = json.loads(text)
        assert "error" in data
        assert data["summary"] == "RuntimeError: boom"
        assert _default_override() is None
    finally:
        config_mod._GLOBAL_CONFIG.clear()
        config_mod._GLOBAL_CONFIG.update(orig_config)
        server_mod._reset_session_tiers()
