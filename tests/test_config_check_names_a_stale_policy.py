"""`config --check` says when the installed agent policy is not the one this version writes (#871).

Found while fixing #719. `install_claude_md` returns "policy already present"
and writes nothing whenever the marker exists, and `config --check` compared
only which TOOLS a CLAUDE.md names. So a correction to the policy's wording --
#719 changed what every agent is told about proving absence -- reached new
installs only, and nothing told an existing one its text was out of date.

The fix is a message, never a rewrite (the `surface_offer` rule): the check
compares the installed block with `active_policy()` and, when they differ,
says so, names the command that prints the current text, and says it cannot
tell an outdated block from one its owner edited on purpose.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jcodemunch_mcp.cli import policy as P


def _claude_md(tmp_path: Path, monkeypatch, text: str) -> Path:
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    path = home / ".claude" / "CLAUDE.md"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return path


def test_an_installed_policy_equal_to_the_current_one_is_current():
    text = "# Mine\n\nsome notes\n\n" + P.active_policy() + "\n\n## My own section\n\nkeep this\n"
    assert P.installed_policy_drift(text) == {"state": "current"}


def test_an_installed_policy_with_old_wording_differs():
    current = P.active_policy()
    lines = current.splitlines()
    # An older install: one policy line said something else.
    old = "\n".join(lines[:3] + ["- an empty result proves the target is absent"] + lines[4:])
    drift = P.installed_policy_drift("# Mine\n\n" + old + "\n")
    assert drift == {"state": "differs", "lines_differing": 1}, (
        "one substituted line is one line that differs, not a removal plus an addition"
    )


def test_the_users_own_sections_after_the_block_are_not_compared():
    text = P.active_policy() + "\n\n## Deploy notes\n\n- ship on Fridays\n"
    assert P.installed_policy_drift(text) == {"state": "current"}


def test_whitespace_and_blank_lines_are_not_a_difference():
    current = P.active_policy()
    reflowed = "\n\n".join(line.rstrip() + "   " for line in current.splitlines())
    assert P.installed_policy_drift(reflowed) == {"state": "current"}


def test_no_installed_block_has_nothing_to_compare():
    assert P.installed_policy_drift("# Mine\n\nCall the jcodemunch_guide tool.\n") is None


def _this_install(monkeypatch, tmp_path, surface: str | None = None) -> None:
    """Load the config `config --check` will load, so `active_policy()` is the same one.

    The policy depends on the served surface, which comes from config; computing
    it before the check loads a fresh config compares against another install.
    """
    from jcodemunch_mcp.config import _GLOBAL_CONFIG, load_config

    store = tmp_path / "store"
    store.mkdir(exist_ok=True)
    if surface is not None:
        (store / "config.jsonc").write_text('{"tool_surface": "%s"}' % surface, encoding="utf-8")
        monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", surface)
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    _GLOBAL_CONFIG.clear()
    load_config(str(store))


def _run_check(capsys, monkeypatch, tmp_path) -> str:
    from jcodemunch_mcp.server import _run_config

    try:
        _run_config(check=True)
    except SystemExit:
        pass  # other prerequisites on this box may fail; only the output is read
    return capsys.readouterr().out


def test_config_check_names_an_outdated_or_edited_policy(tmp_path, monkeypatch, capsys):
    """The reported shape, through the command a user runs."""
    _this_install(monkeypatch, tmp_path)
    lines = P.active_policy().splitlines()
    old = "\n".join(lines[:3] + ["- an empty result proves the target is absent"] + lines[4:])
    path = _claude_md(tmp_path, monkeypatch, old + "\n")

    out = _run_check(capsys, monkeypatch, tmp_path)
    assert "differs from the policy this version installs" in out, out[-1500:]
    assert "claude-md --generate --format policy" in out
    assert "cannot tell" in out, "an edited block and an outdated one must not be told apart falsely"
    assert path.read_text(encoding="utf-8") == old + "\n", "the check must never rewrite the file"


def test_config_check_says_nothing_about_a_current_policy(tmp_path, monkeypatch, capsys):
    _this_install(monkeypatch, tmp_path)
    _claude_md(tmp_path, monkeypatch, P.active_policy() + "\n")
    out = _run_check(capsys, monkeypatch, tmp_path)
    assert "differs from the policy this version installs" not in out
    assert "matches the policy this version installs" in out


@pytest.mark.parametrize("variant", ["_CLAUDE_MD_POLICY", "_CLAUDE_MD_POLICY_COUNTER"])
def test_every_policy_variant_starts_with_the_marker(variant):
    """The block is found by its marker; a variant without it could never be compared."""
    assert getattr(P, variant).lstrip().startswith("## Code Exploration Policy")


def test_init_says_a_present_policy_differs_and_does_not_rewrite_it(tmp_path, monkeypatch):
    """`init` is where a user meets "already present"; it must not hide the drift."""
    from jcodemunch_mcp.cli.init import install_claude_md

    _this_install(monkeypatch, tmp_path)
    lines = P.active_policy().splitlines()
    old = "\n".join(lines[:3] + ["- an empty result proves the target is absent"] + lines[4:]) + "\n"
    path = _claude_md(tmp_path, monkeypatch, old)
    # conftest's `_isolate_claude_home` redirects `_claude_md_path`; point it here.
    from jcodemunch_mcp.cli import init as _init

    monkeypatch.setattr(_init, "_claude_md_path", lambda scope: path)
    msg = install_claude_md("global", backup=False)
    assert "differs from the policy this version installs" in msg
    assert path.read_text(encoding="utf-8") == old


@pytest.mark.parametrize("surface", ["full", "counter"])
def test_the_command_the_message_names_prints_the_text_it_compares_against(tmp_path, monkeypatch, capsys, surface):
    """Review: `claude-md --generate` (full) is another generator's text; replacing a
    block with it would make the drift permanent. `--format policy` must round-trip.
    ⚠ Both surfaces: on `counter` the old snippet happened to equal the policy, so an
    arm run only there passed against the defect."""
    from jcodemunch_mcp.server import _run_claude_md

    _this_install(monkeypatch, tmp_path, surface)
    capsys.readouterr()
    _run_claude_md(generate=True, fmt="policy")
    printed = capsys.readouterr().out
    assert P.installed_policy_drift("# Mine\n\n" + printed) == {"state": "current"}


def _issue_count(out: str) -> int:
    import re

    m = re.search(r"(\d+) issue\(s\) found", out)
    return int(m.group(1)) if m else 0


def test_a_differing_policy_warns_but_is_not_an_issue(tmp_path, monkeypatch, capsys):
    """Review: an owner's deliberate edit must not fail the health check's exit status."""
    _this_install(monkeypatch, tmp_path)
    _claude_md(tmp_path, monkeypatch, P.active_policy() + "\n")
    baseline = _issue_count(_run_check(capsys, monkeypatch, tmp_path))

    lines = P.active_policy().splitlines()
    _claude_md(tmp_path / "second", monkeypatch, "\n".join(lines[:3] + ["- mine, on purpose"] + lines[4:]) + "\n")
    out = _run_check(capsys, monkeypatch, tmp_path)
    assert "differs from the policy this version installs" in out
    assert _issue_count(out) == baseline, "a warning the check cannot justify was counted as an issue"


def test_the_marker_is_found_the_way_init_finds_it():
    """Review: `init` matches the marker as a substring; the drift check must see the same block."""
    from jcodemunch_mcp.cli import init as _init

    assert _init._CLAUDE_MD_MARKER is P.POLICY_MARKER
    text = P.active_policy().replace(P.POLICY_MARKER, P.POLICY_MARKER + " (mine)", 1)
    assert P.installed_policy_drift(text) is not None
