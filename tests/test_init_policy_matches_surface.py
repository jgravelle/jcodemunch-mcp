"""The policy `init` writes must describe tools the server actually advertises.

#397 (@n-able-consulting): on a first-ever install `init` wrote a policy naming
~25 tools by name while the server served the 3-tool front door, so the guidance
described calls the client never offered the model. Two independent causes, both
covered here:

* `init` never called ``load_config``, so every config read returned a built-in
  default. A user with ``tool_profile: "core"`` still got the full policy.
* Nothing consulted ``tool_surface`` at all, and on a fresh install the config
  did not exist yet -- the server created it later and chose ``counter``.

⚠ The failure was SILENT because hidden tools stay callable by name. Nothing
errored; the guidance was merely unreachable through the tool list. So the test
that matters is not "does a call succeed" but "is every tool the policy names
present in the advertised surface".
"""
from __future__ import annotations

import re

import pytest

from jcodemunch_mcp.cli import init as I
import jcodemunch_mcp.server as server_module

# Matches the ways the policies name a tool: `name`, `name {`, `name(`.
_NAMED = re.compile(r"`([a-z_][a-z0-9_]*)[`(\s{]")


def _tools_named_in(policy: str) -> set[str]:
    from jcodemunch_mcp.server import _CANONICAL_TOOL_NAMES
    known = set(_CANONICAL_TOOL_NAMES) | {"order", "menu", "route"}
    return {n for n in _NAMED.findall(policy) if n in known}


@pytest.fixture
def surface(monkeypatch):
    """Force an effective tool surface without touching a real config."""
    def _set(value):
        monkeypatch.setattr(I, "_effective_tool_surface", lambda: value)
    return _set


# ── the invariant ─────────────────────────────────────────────────────────

def test_counter_policy_names_only_front_door_tools(surface):
    """The bug, stated as a property.

    Under the front door the advertised surface is the front door, so a policy
    naming anything else is describing a call the client cannot offer.
    """
    surface("counter")
    named = _tools_named_in(I.active_policy())
    reachable = I._front_door_tool_names()
    assert named, "the counter policy must still name the tools it wants used"
    assert named <= reachable, (
        f"policy names tools absent from the front door: {sorted(named - reachable)}"
    )


def test_counter_policy_actually_teaches_the_front_door(surface):
    """Filtering the direct-tool policy down would leave no workflow at all.

    A policy that merely omits unreachable names is not equivalent to one that
    explains how to reach them, which is why `counter` gets its own text rather
    than a filtered copy.
    """
    surface("counter")
    policy = I.active_policy()
    for entry in ("order", "menu", "route"):
        assert f"`{entry}" in policy, f"front-door policy never mentions {entry}"


def test_full_surface_keeps_the_direct_tool_policy(surface):
    """The common path is unchanged.

    Without this, a bug that returned the counter policy unconditionally would
    satisfy every assertion above.
    """
    surface("full")
    policy = I.active_policy()
    assert policy is not I._CLAUDE_MD_POLICY_COUNTER
    named = _tools_named_in(policy)
    assert "search_symbols" in named and "get_symbol_source" in named


# ── surface awareness in the tool filter ──────────────────────────────────

def test_active_tools_collapses_to_the_front_door_under_counter(surface):
    surface("counter")
    assert I._get_active_tools() == I._front_door_tool_names() or (
        I._get_active_tools() <= I._front_door_tool_names()
    )


def test_active_tools_ignores_profile_when_the_surface_is_the_front_door(surface, monkeypatch):
    """Surface wins over profile: `core` still cannot advertise a direct tool."""
    surface("counter")
    monkeypatch.setattr(I, "_get_active_tools", I._get_active_tools)
    from jcodemunch_mcp import config as cfg
    monkeypatch.setattr(cfg, "get", lambda k, d=None: "core" if k == "tool_profile" else d)
    active = I._get_active_tools()
    assert "search_symbols" not in active


def test_full_surface_still_honours_profile(surface, monkeypatch):
    """A non-full profile must still filter, and to what `tools/list` carries.

    ⚠ #507: this asserted ``active == set(_PROFILE_TIERS["core"])`` — the baked
    tier table. That was never what the server advertises: `_ALWAYS_PRESENT_TOOLS`
    survives every tier, and `tool_tier_bundles` can redefine a tier outright.
    **The test encoded the premise of the defect**, so it could only pass while
    the helper reconstructed the answer instead of asking for it.

    It also monkeypatched `cfg.get` with a two-argument signature, which the real
    resolver does not have; setting the config exercises the actual path.
    """
    surface("full")
    from jcodemunch_mcp import config as cfg
    from jcodemunch_mcp.server import _build_tools_list

    monkeypatch.setitem(cfg._GLOBAL_CONFIG, "tool_profile", "core")
    monkeypatch.setitem(cfg._GLOBAL_CONFIG, "disabled_tools", [])

    active = I._get_active_tools()

    assert active is not None, "a non-full profile must still filter"
    assert active == {t.name for t in _build_tools_list()}
    assert len(active) < len(server_module._CANONICAL_TOOL_NAMES), (
        "the profile did not narrow anything"
    )


# ── config is loaded before anything is generated ─────────────────────────

def test_init_materialises_the_config(tmp_path, monkeypatch):
    """`init` must take the surface decision, not inherit it later.

    Before this, `init` wrote guidance and the SERVER created the config on
    first start, so the two artifacts were produced by code paths that never
    met and `init` ran first.
    """
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path))
    I.ensure_config_loaded()
    assert (tmp_path / "config.jsonc").exists()


def test_run_init_leaves_the_process_config_as_it_found_it(
    tmp_path, monkeypatch, spies, clean_global_config
):
    """Loading the config must not swap it out from under the caller.

    `init` is a CLI, but it is also an importable function, and replacing a host
    process's configuration as a side effect of writing a policy file is not
    something a caller can anticipate. Caught the cheap way first: adding the
    load made `test_json` fail whenever an init test ran earlier in the same
    process, because the config init materialised outlived the call.
    """
    cfg = clean_global_config

    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path))
    cfg._GLOBAL_CONFIG.clear()
    cfg._GLOBAL_CONFIG.update({"sentinel": "untouched", "languages": ["python"]})
    before = dict(cfg._GLOBAL_CONFIG)

    I.run_init(clients=None, claude_md=None, dry_run=True, yes=True, minimal=True)

    assert cfg._GLOBAL_CONFIG == before, "run_init mutated the caller's config"


def test_scoped_config_restores_even_when_the_body_raises(
    tmp_path, monkeypatch, clean_global_config
):
    """The restore has to survive the error paths, not just the happy one."""
    cfg = clean_global_config

    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path))
    cfg._GLOBAL_CONFIG.clear()
    cfg._GLOBAL_CONFIG.update({"sentinel": "untouched"})
    before = dict(cfg._GLOBAL_CONFIG)

    @I.with_scoped_config
    def boom():
        raise RuntimeError("mid-init failure")

    with pytest.raises(RuntimeError):
        boom()
    assert cfg._GLOBAL_CONFIG == before


def test_ensure_config_loaded_never_raises(monkeypatch):
    """Guidance generation must not become a hard failure of `init`."""
    import jcodemunch_mcp.config as cfg

    def boom(*a, **k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(cfg, "load_config", boom)
    I.ensure_config_loaded()  # must not raise


# ── flag precedence ───────────────────────────────────────────────────────

@pytest.fixture
def clean_global_config():
    """Snapshot and restore the process config around a test that writes it.

    `monkeypatch` cannot help here: these tests mutate the module's dict in
    place rather than rebinding an attribute, so nothing would put it back and
    every later test in the process would inherit the sentinel.
    """
    from jcodemunch_mcp import config as cfg

    saved = dict(cfg._GLOBAL_CONFIG)
    saved_projects = dict(cfg._PROJECT_CONFIGS)
    try:
        yield cfg
    finally:
        cfg._GLOBAL_CONFIG.clear()
        cfg._GLOBAL_CONFIG.update(saved)
        cfg._PROJECT_CONFIGS.clear()
        cfg._PROJECT_CONFIGS.update(saved_projects)


@pytest.fixture
def spies(monkeypatch):
    """Stub every channel writer and pretend one client is present."""
    from unittest.mock import MagicMock

    made = {}
    for name in ("install_claude_md", "install_cursor_rules", "install_windsurf_rules",
                 "install_agents_md", "install_hooks", "install_enforcement_hooks",
                 "install_copilot_hooks", "run_index"):
        made[name] = MagicMock(return_value="  (would install)")
        monkeypatch.setattr(I, name, made[name])
    made["run_audit"] = MagicMock(return_value=iter(["  (would audit)"]))
    monkeypatch.setattr(I, "run_audit", made["run_audit"])

    client = MagicMock()
    client.name = "Claude Code"
    client.config_path = None
    monkeypatch.setattr(I, "_detect_clients", lambda: [client])
    monkeypatch.setattr(I, "configure_client", MagicMock(return_value="  configured"))
    return made


def test_minimal_honours_a_typed_hooks_flag(spies):
    """`init --minimal --hooks` installed nothing (#397).

    A hardened install wanting hooks but not the policy paste had to let init
    write the sections and delete them afterwards.
    """
    rc = I.run_init(clients=None, claude_md=None, hooks=True, hooks_explicit=True,
                    dry_run=True, yes=True, minimal=True)
    assert rc == 0
    assert spies["install_hooks"].called, "a typed --hooks must survive --minimal"
    assert not spies["install_claude_md"].called, "--minimal still skips the policy"


def test_minimal_still_suppresses_a_callers_default(spies):
    """The opposite contract, which pulls the other way.

    `install <agent>` hardcodes ``hooks=True`` as its own default and
    `--minimal` must still install nothing. A plain bool cannot separate that
    from a typed flag, which is why `hooks_explicit` exists rather than a
    reordering.
    """
    rc = I.run_init(clients=None, claude_md="global", hooks=True, hooks_explicit=False,
                    dry_run=True, yes=True, minimal=True)
    assert rc == 0
    assert not spies["install_hooks"].called
    assert not spies["install_claude_md"].called
    assert not spies["run_index"].called
