"""The tool-surface offer reaches the user once, in the first `resolve_repo` answer.

An install created before the front-door default serves the full tool list for
ever: `tool_surface` is written once and no upgrade revisits it. The offer to
switch was one WARNING line in the server log and two status commands. A user
inside an MCP client reads neither, so the offer was delivered and never seen.

The first successful `resolve_repo` answer an install makes now carries the
offer, priced for that install, with a line asking the agent to relay it. It
is in the response body (the default `meta_fields: []` strips `_meta`), it is
said once per install, and it changes no setting.
"""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path

import pytest

from jcodemunch_mcp import server, surface_offer

MARKER = "surface_notice_state.json"


@pytest.fixture()
def install(tmp_path, monkeypatch):
    """A full-surface install with its own storage directory and no notice said yet."""
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    # No variable: the surface comes from the config default, as on an install that never chose.
    monkeypatch.delenv("JCODEMUNCH_TOOL_SURFACE", raising=False)
    monkeypatch.setitem(surface_offer.NOTICE_STATE, "checked", False)
    work = tmp_path / "work"
    work.mkdir()
    return store, work


def _call(tool: str, args: dict) -> dict:
    out = asyncio.run(server.call_tool(tool, args))
    content = out.content if hasattr(out, "content") else out  # an error answer is a CallToolResult
    return json.loads(content[0].text)


def _new_process(monkeypatch):
    monkeypatch.setitem(surface_offer.NOTICE_STATE, "checked", False)


def test_the_first_resolve_repo_answer_carries_the_offer(install):
    store, work = install
    answer = _call("resolve_repo", {"path": str(work)})
    notice = answer.get("surface_notice")
    assert notice, answer
    assert "error" not in answer


def test_the_figures_are_this_installs_own(install):
    """Priced by the same function the status commands use, never a literal."""
    store, work = install
    offer = server._tool_surface_stats()["surface_offer"]
    notice = _call("resolve_repo", {"path": str(work)})["surface_notice"]
    assert notice["schema_tokens_delta"] == offer["schema_tokens_delta"] > 0
    assert notice["schema_tokens_basis"] == offer["schema_tokens_basis"]
    text = notice["message"]
    for figure in ("current_tools", "offer_tools", "schema_tokens_delta"):
        assert f"{offer[figure]:,}" in text, (figure, text)


def test_a_narrower_install_is_told_a_smaller_figure(install, monkeypatch):
    store, work = install
    wide = _call("resolve_repo", {"path": str(work)})["surface_notice"]["schema_tokens_delta"]
    (store / MARKER).unlink()
    _new_process(monkeypatch)
    monkeypatch.setattr(server, "_effective_profile", lambda: "core")
    narrow = _call("resolve_repo", {"path": str(work)})["surface_notice"]["schema_tokens_delta"]
    assert 0 < narrow < wide


def test_it_says_every_turn_and_says_the_basis(install):
    store, work = install
    text = _call("resolve_repo", {"path": str(work)})["surface_notice"]["message"]
    assert "every turn" in text
    assert "cache" in text, "a count read as a full-rate saving overstates it; the basis is part of the claim"
    assert "predates" not in text and "left as it was" not in text, "the code does not know the install's history"
    assert "order/menu/route" in text, "the tools stay callable, and a reader who thinks otherwise declines"


def test_it_asks_the_agent_to_relay_it_and_not_to_act(install):
    store, work = install
    notice = _call("resolve_repo", {"path": str(work)})["surface_notice"]
    relay = notice["relay_to_user"]
    assert "user" in relay and "once" in relay
    assert "cache" in relay and "smaller than the count" in relay, "the likely relay is the bare count"
    assert "not" in relay and "yourself" in relay
    assert notice["switch_command"] == "jcodemunch-mcp config set tool_surface counter"
    assert notice["undo_command"] == "jcodemunch-mcp config set tool_surface full"
    assert notice["silence_command"] == surface_offer.SILENCE_COMMAND


def test_it_carries_the_basis_note_its_sibling_renderers_carry(install):
    """The status rows and the log line are bound to say it is not a per-request saving; so is this."""
    from jcodemunch_mcp.tier_switch_cost import SCHEMA_TOKENS_BASIS_NOTE

    store, work = install
    notice = _call("resolve_repo", {"path": str(work)})["surface_notice"]
    assert notice["schema_tokens_basis_note"] == SCHEMA_TOKENS_BASIS_NOTE
    assert "NOT a per-request saving" in notice["schema_tokens_basis_note"]
    assert "schema_tokens_basis_note" in notice["message"]


def test_a_surface_set_by_the_environment_is_not_told_to_run_the_config_command(install, monkeypatch):
    """The variable wins over the config file, so the config command would change nothing."""
    store, work = install
    monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", "full")
    notice = _call("resolve_repo", {"path": str(work)})["surface_notice"]
    for key, surface in (("switch_command", "counter"), ("undo_command", "full")):
        assert f"JCODEMUNCH_TOOL_SURFACE={surface}" in notice[key], notice[key]
        assert "config set" not in notice[key], notice[key]
    assert notice["silence_command"] == surface_offer.SILENCE_COMMAND


def test_it_is_said_once_per_install(install, monkeypatch):
    store, work = install
    assert "surface_notice" in _call("resolve_repo", {"path": str(work)})
    marker = json.loads((store / MARKER).read_text(encoding="utf-8"))
    assert marker["shown_at"] and marker["tool"] == "resolve_repo" and marker["surface"] == "full"
    assert set(marker) == {"shown_at", "tool", "surface", "version"}
    assert "surface_notice" not in _call("resolve_repo", {"path": str(work)})
    _new_process(monkeypatch)
    assert "surface_notice" not in _call("resolve_repo", {"path": str(work)})


def test_a_process_prices_the_offer_at_most_once(install, monkeypatch):
    store, work = install
    calls = []
    real = server._tool_surface_stats
    monkeypatch.setattr(server, "_tool_surface_stats", lambda *a, **k: calls.append(1) or real(*a, **k))
    for _ in range(3):
        _call("resolve_repo", {"path": str(work)})
    assert len(calls) == 1


@pytest.mark.parametrize("why", ["front door", "silenced", "another tool", "an error answer"])
def test_nothing_is_said_and_no_marker_is_written_when_there_is_nothing_to_offer(install, monkeypatch, why):
    store, work = install
    tool, args = "resolve_repo", {"path": str(work)}
    if why == "front door":
        monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", "counter")
    elif why == "silenced":
        from jcodemunch_mcp import config

        real = config.get
        monkeypatch.setattr(config, "get", lambda k, d=None, *a, **kw: True if k == "surface_offer_seen" else real(k, d, *a, **kw))
    elif why == "another tool":
        tool, args = "list_repos", {}
    else:
        tool, args = "get_file_outline", {"repo": "no/such-repo", "file_path": "x.py"}
    answer = _call(tool, args)
    assert "surface_notice" not in answer
    assert not (store / MARKER).exists()


def test_an_answer_that_is_not_the_notices_does_not_use_it_up(install):
    """An error answer or another tool first, then `resolve_repo`: the notice is still owed."""
    store, work = install
    _call("list_repos", {})
    _call("get_file_outline", {"repo": "no/such-repo", "file_path": "x.py"})
    assert "surface_notice" in _call("resolve_repo", {"path": str(work)})


@pytest.mark.parametrize("result", [{"error": "no such path"}, "text", None, ["a"]], ids=["error", "str", "none", "list"])
def test_an_answer_that_cannot_carry_it_is_left_alone_and_does_not_use_it_up(install, result):
    store, work = install
    assert server._surface_notice_for("resolve_repo", result) is None
    assert surface_offer.NOTICE_STATE["checked"] is False
    assert not (store / MARKER).exists()
    assert server._surface_notice_for("resolve_repo", {"indexed": False})


def test_a_pricing_failure_leaves_the_answer_alone(install, monkeypatch):
    store, work = install
    plain = _call("list_repos", {})
    assert "error" not in plain

    def boom(*a, **k):
        raise RuntimeError("no")

    monkeypatch.setattr(server, "_tool_surface_stats", boom)
    assert server._surface_notice_for("resolve_repo", {"indexed": False}) is None  # it does not raise
    _new_process(monkeypatch)
    answer = _call("resolve_repo", {"path": str(work)})
    assert "surface_notice" not in answer and "error" not in answer
    assert not (store / MARKER).exists()


def test_an_unwritable_marker_still_says_it_and_only_once_in_the_process(install, monkeypatch):
    """Fails in the recoverable direction: said again next session, never suppressed for good."""
    store, work = install
    real = Path.write_text

    def refuse(self, *a, **k):
        if self.name.startswith("surface_notice_state"):
            raise OSError("read-only")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "write_text", refuse)
    assert "surface_notice" in _call("resolve_repo", {"path": str(work)})
    assert "surface_notice" not in _call("resolve_repo", {"path": str(work)})
    assert not (store / MARKER).exists()


def test_the_answer_is_json_even_when_compact_output_is_asked_for(install):
    """A compact encoding declares its columns; an undeclared key is dropped, and the marker would be spent."""
    store, work = install
    out = asyncio.run(server.call_tool("resolve_repo", {"path": str(work), "format": "compact"}))
    assert "surface_notice" in json.loads(out[0].text)


def test_the_config_file_is_never_written(install):
    store, work = install
    before = sorted(p.name for p in store.iterdir())
    _call("resolve_repo", {"path": str(work)})
    after = sorted(p.name for p in store.iterdir())
    assert "config.jsonc" not in set(after) - set(before)
    assert MARKER in after


def test_the_offer_module_still_writes_nothing_and_reads_no_config():
    tree = ast.parse(Path(surface_offer.__file__).read_text(encoding="utf-8"))
    imported = {
        (n.module or "") if isinstance(n, ast.ImportFrom) else a.name
        for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in n.names
    }
    assert not any("config" in m for m in imported), imported
    called = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not called & {"write_text", "write_bytes", "open", "replace", "mkdir", "unlink"}, called


def test_the_notice_and_its_marker_are_disclosed():
    """SECURITY.md holds the background-behaviour disclosures (the README points to it)."""
    text = (Path(__file__).resolve().parent.parent / "SECURITY.md").read_text(encoding="utf-8")
    para = next(p for p in text.split("\n- ") if MARKER in p)
    for word in ("resolve_repo", "surface_notice", "once per install", "surface_offer_seen", "Nothing is sent"):
        assert word in para, word
