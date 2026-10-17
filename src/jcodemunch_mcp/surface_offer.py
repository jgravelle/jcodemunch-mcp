"""A priced, opt-in offer to move an EXISTING install onto today's default surface.

``tool_surface`` is written into a config exactly once, by ``_fresh_config_content``
on a genuinely first-ever install, and is deliberately absent from
``generate_template`` so ``upgrade_config`` can never back-inject it. That is what
stops a package update silently collapsing a user's tool surface -- and it is also
why every seat created before the ``counter`` default shipped is on ``full``
permanently, with no path off it.

⚠⚠ **This module is a MESSAGE, never a migration.** Nothing here writes config,
and ``upgrade_config`` is untouched. The only thing that can move the key is a
command the user types. An offer that could apply itself would be the exact
failure mode the freeze exists to prevent.

⚠⚠ **It is fed plain data and computes nothing about visibility itself** (the
``counter.py`` rule). Both sides of the comparison are priced by the caller
through ``_build_tools_list``, which is what ``list_tools`` publishes -- a second
copy of the visibility rules would miss the counter branch's deliberate
``disabled_tools`` bypass and price a surface no client receives. That is
``_schema_tokens_for_profile``'s lesson, one axis over.

⚠ **Omit-when-clean.** ``build_offer`` returns ``None`` whenever there is nothing
to sell -- already on the target surface, silenced, or a non-positive delta. An
offer that renders with nothing behind it trains people to skip the row that
matters.
"""

from __future__ import annotations

import os
from typing import Optional

from .tier_switch_cost import SCHEMA_TOKENS_BASIS, SCHEMA_TOKENS_BASIS_NOTE

# The surface a genuinely first-ever install receives today, per
# `config._fresh_config_content`. ⚠ If that default ever changes, this constant
# moves with it -- `tests/test_surface_offer.py` binds the two so they cannot
# drift into offering a surface new installs no longer get.
CURRENT_DEFAULT_SURFACE = "counter"

SWITCH_COMMAND = "jcodemunch-mcp config set tool_surface {surface}"
UNDO_COMMAND = "jcodemunch-mcp config set tool_surface {surface}"
SILENCE_COMMAND = "jcodemunch-mcp config set surface_offer_seen true"

# ⚠⚠ The disclosure that separates this from a silent migration. The switch is
# not free at the moment it happens: `tools` is serialised AHEAD of system and
# messages, so republishing the block invalidates it and the new one must be
# cache-WRITTEN before it reads cheaply again. For this narrowing the payback is
# fast, but stating the cost is the point -- see benchmarks/tier_switch/.
SWITCH_COST_NOTE = (
    "Switching republishes the tool-schema block, so it is cache-written once "
    "at your next session start before it reads cheaply again. It does not "
    "change anything in the session you are in now."
)


def build_offer(
    *,
    current_surface: str,
    current_tools: int,
    current_schema_tokens: int,
    offer_tools: int,
    offer_schema_tokens: int,
    catalog_tools: int,
    seen: bool,
    offer_surface: str = CURRENT_DEFAULT_SURFACE,
) -> Optional[dict]:
    """Price the move from ``current_surface`` to ``offer_surface``, or return None.

    Every count is measured on the caller's own install and passed in. ⚠ Nothing
    in this module may hand-type a token figure: a seat with ``disabled_tools``
    set, or on a narrower ``tool_profile``, has a different pair, and a shipped
    literal would be wrong for most of them. Same rule as
    ``benchmarks/schema_baseline.json`` -- compute, never quote.

    Returns ``None`` when there is nothing to offer.
    """
    if seen:
        return None
    if (current_surface or "").strip().lower() == offer_surface:
        return None

    delta = current_schema_tokens - offer_schema_tokens
    if delta <= 0:
        # Nothing to sell. Reachable when `disabled_tools` has already trimmed
        # the visible surface below the front door's own weight.
        return None

    # Tools that stop being ADVERTISED. ⚠ They do not stop being callable --
    # the front door dispatches the whole catalog on demand, and saying so is
    # load-bearing: a reader who thinks the offer removes capability declines an
    # offer that removes none.
    unadvertised = max(0, current_tools - offer_tools)

    return {
        "current_surface": current_surface,
        "offer_surface": offer_surface,
        "current_tools": current_tools,
        "offer_tools": offer_tools,
        "catalog_tools": catalog_tools,
        "current_schema_tokens": current_schema_tokens,
        "offer_schema_tokens": offer_schema_tokens,
        "schema_tokens_delta": delta,
        "schema_tokens_basis": SCHEMA_TOKENS_BASIS,
        "schema_tokens_basis_note": SCHEMA_TOKENS_BASIS_NOTE,
        "tools_no_longer_advertised": unadvertised,
        "capability_preserved": True,
        "switch_command": SWITCH_COMMAND.format(surface=offer_surface),
        "undo_command": UNDO_COMMAND.format(surface=current_surface),
        "silence_command": SILENCE_COMMAND,
        "switch_cost_note": SWITCH_COST_NOTE,
    }


def render_offer_lines(offer: dict, *, indent: str = "  ") -> list[str]:
    """Human rendering of an offer, as lines without trailing newlines.

    ⚠ ONE producer for every human surface (`surface`, `install-status`). A
    second renderer is how the basis line goes missing from one of them, which
    is the .312 defect -- a machine-readable field the CLI does not print leaves
    the human surface carrying the fault, and a human is exactly who supplies
    the wrong basis.
    """
    i = indent
    lines = [
        f"Tool surface: {offer['current_surface']} "
        f"({offer['current_tools']} tools, {offer['current_schema_tokens']:,} schema tokens)",
        f"{i}A new install today would default to '{offer['offer_surface']}' "
        f"({offer['offer_tools']} tools, {offer['offer_schema_tokens']:,} schema tokens).",
        f"{i}Your install predates that default and was left as-is on purpose.",
        "",
        f"{i}Switching would remove {offer['schema_tokens_delta']:,} tokens from your "
        f"tool-schema block.",
        f"{i}  basis: {offer['schema_tokens_basis']}",
        f"{i}  {offer['schema_tokens_basis_note']}",
        f"{i}  {offer['switch_cost_note']}",
        "",
        f"{i}{offer['tools_no_longer_advertised']} tools would stop being advertised. "
        f"They stay callable through order/menu/route.",
        "",
        f"{i}Switch:  {offer['switch_command']}",
        f"{i}Undo:    {offer['undo_command']}",
        f"{i}Silence: {offer['silence_command']}",
    ]
    return lines


# Whether THIS PROCESS has already decided about the in-answer notice. Held
# here, in the leaf, so the test suite can switch the notice off for every test
# without importing the server (tests/conftest.py); the server owns the marker
# file and every write.
NOTICE_STATE = {"checked": False}


def notice_marker_path(filename: str):
    """Where a once-per-install notice keeps its marker: the store, `CODE_INDEX_PATH` or `~/.code-index`.

    THE one rule for every in-answer notice's marker (the surface notice and the
    policy refresh notice), so a third notice inherits it instead of copying it.
    """
    from pathlib import Path

    base = os.environ.get("CODE_INDEX_PATH") or str(Path.home() / ".code-index")
    return Path(base) / filename

RELAY_LINE = (
    "Tell the user this once, in a sentence or two, then carry on with their task. "
    "Say that the tokens are read from cache after the first turn, so the cost saving "
    "is much smaller than the count. Do not change the setting yourself: it is theirs to decide."
)

# When the surface comes from the environment the config command changes nothing
# (the variable wins), so the notice must not hand that command over.
SURFACE_ENV_VAR = "JCODEMUNCH_TOOL_SURFACE"
ENV_SWITCH = "set {var}={surface} where this MCP server is launched (the variable overrides the config file)"


def render_offer_notice(offer: dict, *, surface_from_env: bool = False) -> dict:
    """The form that rides in a tool answer, for the agent to relay (2026-10-13).

    ⚠⚠ The log line reaches nobody inside an MCP client: the server's stderr is
    not shown there, and the status commands are run by people who already
    know to ask. So the offer was delivered once per install and seen by no
    one. This form goes in the BODY of a tool answer -- `_meta` is stripped by
    the default `meta_fields: []` -- and asks the agent to pass it on.

    ⚠ It says "every turn" because the tool list is in every turn's input, and
    says in the same sentence that it is read from cache after the first turn.
    The count alone invites the full-rate reading `SCHEMA_TOKENS_BASIS` exists
    to stop; the basis key rides beside it for the same reason.

    ⚠ Every figure comes from *offer*, which the caller priced on its own
    install. Nothing here is a literal.

    ⚠ ONE more renderer of the same offer, so it carries what the other two
    are test-bound to carry: `schema_tokens_basis_note` rides in full, and the
    relay line asks for the cache caveat, because the likely relay is the
    bare count (review, 2026-10-13).

    ⚠ It states no history. The code knows the surface is not today's default,
    not why: an install may have chosen it. And when *surface_from_env* is set
    the switch and undo are the variable, since the config command would do
    nothing (Standing lesson 08-18, #488).
    """
    message = (
        f"This jcodemunch install lists {offer['current_tools']:,} tools "
        f"({offer['current_schema_tokens']:,} schema tokens), and the tool list is part of every turn's input. "
        f"The '{offer['offer_surface']}' surface lists {offer['offer_tools']:,} "
        f"({offer['offer_schema_tokens']:,}) and reaches the same actions through order/menu/route. "
        f"Switching means {offer['schema_tokens_delta']:,} fewer tool-list tokens read on every turn. "
        f"After the first turn they are read from cache, so the cost saving is much smaller than the count "
        f"(see schema_tokens_basis_note). "
        f"A new install gets '{offer['offer_surface']}' by default."
    )
    switch, undo = offer["switch_command"], offer["undo_command"]
    if surface_from_env:
        switch = ENV_SWITCH.format(var=SURFACE_ENV_VAR, surface=offer["offer_surface"])
        undo = ENV_SWITCH.format(var=SURFACE_ENV_VAR, surface=offer["current_surface"])
    return {
        "relay_to_user": RELAY_LINE,
        "message": message,
        "schema_tokens_delta": offer["schema_tokens_delta"],
        "schema_tokens_basis": offer["schema_tokens_basis"],
        "schema_tokens_basis_note": offer["schema_tokens_basis_note"],
        "switch_command": switch,
        "undo_command": undo,
        "silence_command": offer["silence_command"],
        "switch_cost_note": offer["switch_cost_note"],
        "shown": "once per install",
    }


def render_offer_log_line(offer: dict) -> str:
    """The one-line server-start form, for the WARNING log channel.

    ⚠ Emitted at WARNING because that is the default ``log_level`` -- at INFO
    nobody sees it, which is the ``HeartbeatReporter`` precedent exactly.

    ⚠⚠ **The log reaches a user who never runs a status command only if they
    read the log, and inside an MCP client nobody does.** Since 2026-10-13
    `render_offer_notice` carries the same offer once in a tool answer. The
    other channels stay closed by design: ``_meta`` is stripped by the default ``meta_fields: []``, the MCP
    ``instructions`` string is a 1,000-char budget aimed at the MODEL and is the
    only prose surviving tool deferral, and an unrequested notification is what
    ``progress.py`` holds no notify channel BY CONSTRUCTION to prevent.

    ⚠ One line, and it names the delta, the basis and the way out. A
    multi-line banner on a stdio server's stderr is chatter, and this server
    has a handshake watchdog for exactly that class of noise.
    """
    return (
        f"tool_surface is '{offer['current_surface']}' "
        f"({offer['current_tools']} tools, {offer['current_schema_tokens']:,} schema tokens); "
        f"a new install today defaults to '{offer['offer_surface']}' "
        f"({offer['offer_tools']} tools, {offer['offer_schema_tokens']:,}). "
        f"Switching removes {offer['schema_tokens_delta']:,} tokens of payload "
        f"(basis: {offer['schema_tokens_basis']}; not a per-request saving). "
        f"Capability is unchanged -- the other {offer['tools_no_longer_advertised']} "
        f"stay callable via order/menu/route. "
        f"Switch: `{offer['switch_command']}`. "
        f"Silence: `{offer['silence_command']}`. "
        f"This notice appears once."
    )
