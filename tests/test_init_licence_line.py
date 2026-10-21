"""One license line in the human-read output of `init` and `install` (jjg, DECISIONS 2026-10-17).

jjg approved Marketing's license line "only as a line in the install/init
output a person reads, not in the agent's answers". `install <agent>` is
sugar for `run_init`, so the print at the end of `run_init`'s real-run branch
covers both. ⚠⚠ The load-bearing half is the absence: the text must never
reach an MCP tool description, the `instructions` string or the policy text an
agent reads, and no `src/` module other than `cli/init.py` may carry it.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jcodemunch_mcp.cli import init as init_module

_SRC = Path(init_module.__file__).resolve().parents[1]
_MARKERS = ("descriptions.php", "guarantee.php", "Using jCodeMunch at work?")


@pytest.fixture
def quiet_init(monkeypatch):
    """A real (non-dry-run) init that touches nothing: every writer is a spy."""
    for name in ("install_claude_md", "install_cursor_rules", "install_windsurf_rules", "install_agents_md",
                 "install_hooks", "install_enforcement_hooks", "install_copilot_hooks", "run_index"):
        monkeypatch.setattr(init_module, name, MagicMock(return_value="  (spy)"))
    monkeypatch.setattr(init_module, "run_audit", MagicMock(return_value=iter([])))
    client = MagicMock()
    client.name = "Claude Code"
    client.config_path = None
    monkeypatch.setattr(init_module, "_detect_clients", lambda: [client])
    monkeypatch.setattr(init_module, "configure_client", MagicMock(return_value="  (spy)"))
    # An unlicensed install unless a test says otherwise: a key on the box
    # running the suite must not decide what the default case prints.
    monkeypatch.delenv("JCODEMUNCH_LICENSE_KEY", raising=False)


def _run(capsys, **kw) -> str:
    rc = init_module.run_init(clients=None, hooks=False, index=False, audit=False, yes=True, minimal=True, **kw)
    assert rc == 0
    return capsys.readouterr().out


def test_a_real_run_ends_with_the_licence_line_after_done(quiet_init, capsys):
    out = _run(capsys, dry_run=False)
    lines = [ln for ln in out.splitlines() if ln.strip()]
    done = next(i for i, ln in enumerate(lines) if ln.startswith("Done."))
    tail = "\n".join(lines[done + 1:])
    assert "Using jCodeMunch at work? A commercial license is $79, one time:" in tail
    assert "https://jcodemunch.com/descriptions.php" in tail
    assert "(30-day money-back guarantee; terms: https://jcodemunch.com/guarantee.php)" in tail
    assert out.count("descriptions.php") == 1


def test_a_licensed_install_by_env_prints_no_licence_line(quiet_init, capsys, monkeypatch):
    """jjg's promise: paid clients are never marketed to (DECISIONS 2026-10-20).
    The $79 line is a sales line, so a configured key suppresses it."""
    monkeypatch.setenv("JCODEMUNCH_LICENSE_KEY", "jcm-test-key-not-real")
    out = _run(capsys, dry_run=False)
    assert "Done." in out
    assert not any(m in out for m in _MARKERS), out


def test_a_licensed_install_by_config_prints_no_licence_line(quiet_init, capsys, monkeypatch):
    from jcodemunch_mcp import config

    real_get = config.get
    monkeypatch.setattr(
        config, "get", lambda key, *a, **k: "jcm-test-key-not-real" if key == "license_key" else real_get(key, *a, **k)
    )
    out = _run(capsys, dry_run=False)
    assert "Done." in out
    assert not any(m in out for m in _MARKERS), out


def test_a_blank_key_is_no_licence(quiet_init, capsys, monkeypatch):
    """Whitespace is not a key: the line still prints."""
    monkeypatch.setenv("JCODEMUNCH_LICENSE_KEY", "   ")
    out = _run(capsys, dry_run=False)
    assert out.count("descriptions.php") == 1


def test_the_licence_check_makes_no_network_call(quiet_init, capsys, monkeypatch):
    """The gate reads the configured key only. Validating it would send it to
    validate.php from every `init`, a network call `init` has never made.

    ⚠ Calls are RECORDED, never refused with a raise: `_check_server` wraps its
    request in `except Exception: return None`, so a raise from a patched
    socket would be swallowed and a validating gate would stay green (review of
    this test, the 08-13 #439/#453 shape)."""
    import socket

    import httpx

    from jcodemunch_mcp.org import license as lic

    calls = []
    real_check = lic._check_server
    monkeypatch.setattr(lic, "_check_server", lambda *a, **k: calls.append("_check_server") or real_check(*a, **k))
    for verb in ("get", "post", "request"):
        real = getattr(httpx, verb)
        monkeypatch.setattr(httpx, verb, lambda *a, _v=verb, _r=real, **k: calls.append("httpx." + _v) or _r(*a, **k))
    real_connect = socket.socket.connect
    monkeypatch.setattr(socket.socket, "connect", lambda self, *a, **k: calls.append("connect") or real_connect(self, *a, **k))
    monkeypatch.setenv("JCODEMUNCH_LICENSE_KEY", "jcm-test-key-not-real")
    out = _run(capsys, dry_run=False)
    assert not any(m in out for m in _MARKERS), out
    assert calls == [], calls


@pytest.mark.parametrize("mode", [{"dry_run": True}, {"demo": True}])
def test_a_dry_run_or_demo_prints_no_licence_line(quiet_init, capsys, mode):
    out = _run(capsys, **mode)
    assert not any(m in out for m in _MARKERS), out


def test_upgrade_prints_no_licence_line(quiet_init, capsys, monkeypatch):
    """The line is shown once per install, never on every upgrade (Chief of Staff, 2026-10-17).

    `upgrade` re-runs `init --hooks` as a child process; the child here is run
    in-process with exactly the environment `upgrade` hands it."""
    from jcodemunch_mcp.cli import upgrade

    envs = []

    class _R:
        returncode = 0

    def fake_run(cmd, check=False, env=None, **kw):
        envs.append(env)
        return _R()

    monkeypatch.setattr(upgrade.subprocess, "run", fake_run)
    assert upgrade._refresh_hooks(yes=True) == 0
    capsys.readouterr()
    (env,) = envs
    assert env is not None, "upgrade must hand its init child an explicit environment"
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    out = _run(capsys, dry_run=False)
    assert "Done." in out
    assert not any(m in out for m in _MARKERS), out


def test_the_text_lives_only_in_cli_init():
    """No tool description, instructions string, policy text or other module carries it.

    Every file under `src/`, not only `.py`: a template shipped as data would carry text too."""
    carriers = sorted(
        str(p.relative_to(_SRC)).replace("\\", "/")
        for p in _SRC.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
        and any(m in p.read_text(encoding="utf-8", errors="replace") for m in _MARKERS)
    )
    assert carriers == ["cli/init.py"], carriers


def test_the_policy_and_the_tool_surface_never_carry_it():
    from jcodemunch_mcp import server
    from jcodemunch_mcp.cli import policy

    texts = [policy.active_policy(), server._mcp_instructions()]
    for tool in server._build_tools_list():
        texts.append(tool.description or "")
        texts.append(str(tool.inputSchema))
    for text in texts:
        assert not any(m in text for m in _MARKERS)
