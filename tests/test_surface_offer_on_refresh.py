"""The surface offer reaches an existing install through the commands it runs.

An install that predates the `counter` default stays on its surface on purpose,
and `surface_offer.py` prices the move for it. The offer was shown only by
`install-status`, `jcodemunch-mcp surface`, a log line at server start and a
notice on the first `resolve_repo`, so an install on the full tool list could
go a long time without seeing it. `upgrade` re-runs `init --hooks --yes`, and a
user refreshing hooks types the same, so `init` with hooks prints the offer
too.

It stays a message: `init` prints it and changes nothing, and the text is
`render_offer_lines`, the one renderer every other human surface uses.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from jcodemunch_mcp.cli import init as init_module
from jcodemunch_mcp.surface_offer import build_offer, render_offer_lines


def _offer() -> dict:
    offer = build_offer(
        current_surface="full",
        current_tools=91,
        current_schema_tokens=24000,
        offer_tools=6,
        offer_schema_tokens=1000,
        catalog_tools=94,
        seen=False,
    )
    assert offer is not None
    return offer


@pytest.fixture()
def spy_init(monkeypatch, tmp_path):
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    for name in (
        "install_claude_md",
        "install_cursor_rules",
        "install_windsurf_rules",
        "install_agents_md",
        "install_hooks",
        "install_enforcement_hooks",
        "install_copilot_hooks",
        "run_index",
    ):
        monkeypatch.setattr(init_module, name, MagicMock(return_value="  (spy)"))
    monkeypatch.setattr(init_module, "run_audit", MagicMock(return_value=iter([])))
    monkeypatch.setattr(init_module, "_detect_clients", lambda: [])
    monkeypatch.setattr(init_module, "_licence_key_configured", lambda: True)
    monkeypatch.delenv(init_module.UPGRADE_REFRESH_ENV, raising=False)
    return init_module


def _run_refresh(module, *, hooks_explicit: bool = True) -> None:
    module.run_init(
        clients=None,
        hooks=True,
        hooks_explicit=hooks_explicit,
        index=False,
        audit=False,
        yes=True,
    )


def _assert_offer_printed(out: str, offer: dict) -> None:
    lines = render_offer_lines(offer)
    for line in lines:
        assert line in out, line
    # The renderer's lines arrive together and in order, not scattered.
    assert "\n".join(lines) in out


def test_the_upgrade_refresh_prints_the_offer(spy_init, monkeypatch, capsys):
    offer = _offer()
    monkeypatch.setattr(spy_init, "_surface_offer_block", lambda: offer)
    monkeypatch.setenv(spy_init.UPGRADE_REFRESH_ENV, "1")
    _run_refresh(spy_init, hooks_explicit=False)
    _assert_offer_printed(capsys.readouterr().out, offer)


def test_a_typed_hooks_refresh_prints_the_offer(spy_init, monkeypatch, capsys):
    offer = _offer()
    monkeypatch.setattr(spy_init, "_surface_offer_block", lambda: offer)
    _run_refresh(spy_init)
    _assert_offer_printed(capsys.readouterr().out, offer)


def test_no_offer_prints_nothing(spy_init, monkeypatch, capsys):
    """On the default surface, or once silenced, the block is None and init says nothing."""
    monkeypatch.setattr(spy_init, "_surface_offer_block", lambda: None)
    _run_refresh(spy_init)
    assert "Tool surface:" not in capsys.readouterr().out


def test_a_failing_offer_does_not_fail_init(spy_init, monkeypatch, capsys):
    def boom():
        raise RuntimeError("no server")

    monkeypatch.setattr(spy_init, "_surface_offer_block", boom)
    assert (
        spy_init.run_init(
            clients=None,
            hooks=True,
            hooks_explicit=True,
            index=False,
            audit=False,
            yes=True,
        )
        == 0
    )
    assert "Tool surface:" not in capsys.readouterr().out


def test_install_agent_does_not_print_it(spy_init, monkeypatch, capsys):
    """`install <agent>` passes hooks=True as its own default; the offer is for a refresh."""
    monkeypatch.setattr(spy_init, "_surface_offer_block", _offer)
    _run_refresh(spy_init, hooks_explicit=False)
    assert "Tool surface:" not in capsys.readouterr().out


def test_a_full_surface_install_gets_its_own_offer_unmocked(
    spy_init, monkeypatch, capsys
):
    """No mock of the producer: a `full` install prints what `install-status` would."""
    monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", "full")
    expected = spy_init._surface_offer_block()
    assert expected is not None, "a full install has an offer to print"
    _run_refresh(spy_init)
    out = capsys.readouterr().out
    _assert_offer_printed(out, expected)
    assert out.count("Tool surface: full") == 1
