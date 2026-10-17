"""One licence line in the human-read output of `init` and `install` (jjg, DECISIONS 2026-10-17).

jjg approved Marketing's licence line "only as a line in the install/init
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


def _run(capsys, **kw) -> str:
    rc = init_module.run_init(clients=None, hooks=False, index=False, audit=False, yes=True, minimal=True, **kw)
    assert rc == 0
    return capsys.readouterr().out


def test_a_real_run_ends_with_the_licence_line_after_done(quiet_init, capsys):
    out = _run(capsys, dry_run=False)
    lines = [ln for ln in out.splitlines() if ln.strip()]
    done = next(i for i, ln in enumerate(lines) if ln.startswith("Done."))
    tail = "\n".join(lines[done + 1:])
    assert "Using jCodeMunch at work? A commercial licence is $79, one time:" in tail
    assert "https://jcodemunch.com/descriptions.php" in tail
    assert "(30-day money-back guarantee; terms: https://jcodemunch.com/guarantee.php)" in tail
    assert out.count("descriptions.php") == 1


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
