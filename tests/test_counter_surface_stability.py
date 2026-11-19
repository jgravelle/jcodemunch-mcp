"""The published `counter` surface is a CACHED PREFIX, and nothing pinned it.

⚠⚠ `tools` is serialised AHEAD of system and messages, so every byte of the
published tool list sits in the cached prefix. Changing ANY of it invalidates
that prefix **and every turn behind it**, for every session on every install --
a cost `benchmarks/tier_switch/` priced at **174 requests** to repay a
`full`->`standard` narrowing (864 with 100k of history). A one-word edit to the
`order` description is not a docs change; it re-bills a full-rate cache write to
everyone.

⚠⚠ **The property this file pins is the one arXiv:2608.22708 (CacheRouter) is
built around: the tool catalog can GROW without moving the main model's
prefix.** The Counter already has it -- the counter branch of
`_build_tools_list` keeps a fixed whitelist (`_COUNTER_FRONT_DOOR |
_ALWAYS_PRESENT_TOOLS`), so an ordinary new tool cannot reach the surface. It
had never been stated as a guarantee and nothing failed if it broke.

⚠ That gap is the shape this repo has already shipped once: the schema budget
guardrail "only walked `tool_profile`, which does not apply to the front door at
all", so the largest lever in the project had no test under it. Same blind spot,
one axis over.

⚠ **Scope, stated so a green run is not over-read.** These tests pin the
SHIPPED definitions with description overrides forced empty. A user who sets
`descriptions` in config gets a different prefix -- stable for them as long as
their config is, and not something this repo controls. What is pinned here is
the text we ship.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from jcodemunch_mcp import config as config_module
from jcodemunch_mcp import server

# The published surface, in ORDER. Order is part of the prefix: the same six
# tools serialised in a different sequence are different bytes.
PUBLISHED_NAMES = [
    "set_tool_tier",
    "announce_model",
    "jcodemunch_guide",
    "order",
    "menu",
    "route",
]

# sha256[:16] of each tool's canonical {name, description, inputSchema}.
# ⚠⚠ A FAILURE HERE IS NOT A BROKEN TEST -- it means the cached prefix moved.
# Updating a hash is the correct fix ONLY once you have decided the edit is
# worth a full-rate cache write for every user. Keyed per tool so the failure
# NAMES the one that changed; a single blob hash would say only "something".
PUBLISHED_SURFACE_SHA = {
    "set_tool_tier":    "90b90e14d62c819b",
    "announce_model":   "2854502b9584bbd7",
    "jcodemunch_guide": "0880b731a1c8ae3c",
    "order":            "57db4c14991628bf",
    "menu":             "4f6b4506943de929",
    "route":            "8662d993995c63b3",
}

# Measured 2026-09-01. Reported on failure so the diff is legible as a cost.
PUBLISHED_TOTAL_BYTES = 4184


def _canonical(tool) -> bytes:
    """The bytes that reach the wire, normalised for comparison.

    ⚠ `sort_keys` normalises DICT key order (which the serialiser fixes anyway)
    but never list order -- see PUBLISHED_NAMES.
    """
    return json.dumps(
        {
            "name": tool.name,
            "description": tool.description or "",
            "inputSchema": tool.input_schema or {},
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


@pytest.fixture
def shipped_surface(monkeypatch):
    """The counter surface with config description overrides forced empty.

    ⚠⚠ `_apply_description_overrides` MUTATES `tool.description` in place, and
    the objects it mutates are the same ones `_RAW_CATALOG` holds. Reading
    ambient config here would make these hashes a property of the developer's
    machine -- the #437 shape, where a local non-default config made a real
    defect invisible locally and visible only to a contributor.
    """
    monkeypatch.setattr(config_module, "get_descriptions", lambda *a, **k: {})
    try:
        yield lambda **kw: server._build_tools_list(surface_override="counter", **kw)
    finally:
        # ⚠ `_build_tools_list` writes the `_RAW_CATALOG` / `_DECLARED_ARG_KEYS`
        # globals as a side effect, and `_raw_catalog_tools()` returns the
        # cached one without rebuilding. A test that grows the catalog would
        # otherwise leak a synthetic tool into every later test in the session.
        server._build_tools_list()


def test_published_names_and_order_are_pinned(shipped_surface):
    """The control, and the cheapest signal: which tools, in which sequence."""
    assert [t.name for t in shipped_surface()] == PUBLISHED_NAMES


def test_catalog_growth_does_not_move_the_prefix(shipped_surface, monkeypatch):
    """⚠⚠ THE PROPERTY. Adding tools to the catalog must not change one byte
    of what a `counter` client receives.

    Injected at the front-door append (`all_tools + _counter_front_door_tools()`)
    rather than into the big literal, because the counter branch filters
    `all_tools` by NAME membership -- a tool added anywhere in that list is the
    same input to the filter. ⚠ So this proves the FILTER is a whitelist; it
    does not prove a future edit could not add a tool to the whitelist itself,
    which is what `test_whitelist_membership_is_pinned` is for.
    """
    before = [_canonical(t) for t in shipped_surface()]

    real = server._counter_front_door_tools

    def _with_extra_tools():
        extras = [
            server.Tool(
                name=f"zz_synthetic_probe_{i}",
                description="Synthetic catalog growth. " * 40,
                inputSchema={
                    "type": "object",
                    "properties": {"q": {"type": "string", "description": "x" * 200}},
                },
            )
            for i in range(5)
        ]
        return real() + extras

    monkeypatch.setattr(server, "_counter_front_door_tools", _with_extra_tools)
    after_tools = shipped_surface()

    leaked = [t.name for t in after_tools if t.name.startswith("zz_synthetic_probe")]
    assert not leaked, (
        "catalog growth reached the published counter surface: "
        f"{leaked}. The counter branch must stay a whitelist over "
        "_COUNTER_FRONT_DOOR | _ALWAYS_PRESENT_TOOLS -- otherwise every new "
        "tool invalidates every user's cached prefix."
    )
    assert [_canonical(t) for t in after_tools] == before, (
        "the published surface changed BYTES while the tool names stayed the "
        "same -- the prefix moved without anything appearing or disappearing."
    )


def test_shipped_definitions_match_the_byte_baseline(shipped_surface):
    """⚠⚠ Catches the edit the name check cannot see: a reworded description or
    a touched inputSchema on a tool that is still called the same thing."""
    tools = shipped_surface()
    actual = {t.name: hashlib.sha256(_canonical(t)).hexdigest()[:16] for t in tools}

    drifted = sorted(
        f"{name} ({PUBLISHED_SURFACE_SHA.get(name, 'NEW')} -> {sha})"
        for name, sha in actual.items()
        if PUBLISHED_SURFACE_SHA.get(name) != sha
    )
    assert not drifted, (
        "published counter-surface bytes changed for: " + ", ".join(drifted) + ". "
        "This invalidates the cached prefix for every session on every install. "
        "If the edit is worth that, update PUBLISHED_SURFACE_SHA and say so in "
        "the CHANGELOG; do not update it to make the suite quiet."
    )
    assert set(actual) == set(PUBLISHED_SURFACE_SHA), (
        "the baseline names a tool the surface no longer publishes, or misses "
        "one it now does"
    )


def test_total_published_bytes_are_reported_and_pinned(shipped_surface):
    """The cost, in the unit the decision is made in.

    ⚠ Redundant with the per-tool hashes BY DESIGN: a hash says "different",
    a byte count says "bigger by how much", and only the second answers whether
    an edit was worth a cache write.
    """
    total = sum(len(_canonical(t)) for t in shipped_surface())
    assert total == PUBLISHED_TOTAL_BYTES, (
        f"published counter surface is {total:,} B, baseline "
        f"{PUBLISHED_TOTAL_BYTES:,} B (delta {total - PUBLISHED_TOTAL_BYTES:+,})"
    )


def test_whitelist_membership_is_pinned():
    """The other way the surface can grow: widening the whitelist itself.

    ⚠ Deliberate widening is legitimate -- this makes it VISIBLE, because the
    cost lands on every cached session and is invisible at the call site.
    """
    assert set(server._COUNTER_FRONT_DOOR) == {"order", "menu", "route"}
    assert set(server._ALWAYS_PRESENT_TOOLS) == {
        "set_tool_tier",
        "announce_model",
        "jcodemunch_guide",
    }


def test_surface_ignores_the_tier_profile(shipped_surface):
    """⚠ `counter` deliberately BYPASSES tier filtering -- the surface choice is
    itself the filter. Pinned because a future refactor that folded the tier
    filter back in would silently make the prefix depend on `tool_profile`,
    i.e. differ per install for no stated reason.
    """
    baseline = [_canonical(t) for t in shipped_surface()]
    for profile in ("core", "standard", "full"):
        assert [_canonical(t) for t in shipped_surface(profile_override=profile)] == baseline, (
            f"tool_profile={profile!r} changed the counter surface"
        )
