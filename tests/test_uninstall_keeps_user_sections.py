"""`uninstall` removes the policy block and nothing after it (LEDGER L-186).

`uninstall` found the block with a rule of its own, `init._strip_policy_blocks`,
beside `policy.policy_block_span`, the rule `init`, the drift check and
`claude-md --refresh` share. It ended the block only at a `## ` heading, so a
user's own `#` or `###` section directly after the policy was deleted with it
(a `.md.bak` was kept). And the Cursor, Windsurf and AGENTS.md installers
tested for the marker as a SUBSTRING, so a file that only mentions the policy
by name read as installed and got nothing.

Every target is a file the test owns under `tmp_path` (Practice 8).
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.cli import init
from jcodemunch_mcp.cli.policy import POLICY_MARKER, active_policy

AFTER = {
    "a level-1 section": "# My notes\n\nKeep this.\n",
    "a level-3 section": "### My checklist\n\n- keep this\n",
    "a level-2 section, as before": "## My section\n\nKeep this.\n",
    "a level-1 section after a blank line run": "\n\n\n# My notes\n\nKeep this.\n",
}


def _installed(after: str) -> str:
    return "# Project\n\nIntro.\n\n" + active_policy().rstrip("\n") + "\n\n" + after


@pytest.mark.parametrize("case", sorted(AFTER))
def test_strip_keeps_a_users_section_of_any_level_after_the_policy(case):
    new, changed = init._strip_policy_blocks(_installed(AFTER[case]))
    assert changed
    assert POLICY_MARKER not in new
    assert "Keep this" in new or "keep this" in new, new
    assert "Intro." in new


def test_crlf_line_endings_keep_the_users_section_and_its_endings():
    src = _installed("# My notes\n\nKeep this.\n").replace("\n", "\r\n")
    new, changed = init._strip_policy_blocks(src)
    assert changed and POLICY_MARKER not in new
    assert "# My notes\r\n\r\nKeep this.\r\n" in new, repr(new)


def test_a_heading_inside_a_users_code_fence_does_not_end_the_users_section():
    after = "# Snippets\n\n```md\n## Code Exploration Policy\n```\n\nKeep this.\n"
    new, changed = init._strip_policy_blocks(_installed(after))
    assert changed
    assert "# Snippets" in new and "Keep this." in new, new


@pytest.mark.parametrize("which", ["claude_md", "windsurf", "agents_md"])
def test_uninstall_keeps_a_level_1_section_after_the_policy(which, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = {
        "claude_md": tmp_path / "CLAUDE.md",
        "windsurf": tmp_path / ".windsurfrules",
        "agents_md": tmp_path / "AGENTS.md",
    }[which]
    monkeypatch.setattr(init, "_claude_md_path", lambda scope: tmp_path / "CLAUDE.md")
    monkeypatch.setattr(init, "_windsurf_rules_path", lambda: tmp_path / ".windsurfrules")
    target.write_text(_installed("# My notes\n\nKeep this.\n"), encoding="utf-8")
    uninstall = {
        "claude_md": lambda: init.uninstall_claude_md(backup=False),
        "windsurf": lambda: init.uninstall_windsurf_rules(backup=False),
        "agents_md": lambda: init.uninstall_agents_md(backup=False),
    }[which]
    uninstall()
    text = target.read_text(encoding="utf-8")
    assert POLICY_MARKER not in text
    assert "# My notes" in text and "Keep this." in text, text


MENTION = f"Team rule: never hand-edit the `{POLICY_MARKER}` block an installer adds.\n"


@pytest.mark.parametrize("which", ["cursor", "windsurf", "agents_md"])
def test_a_file_that_only_mentions_the_policy_still_gets_it(which, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cursor = tmp_path / ".cursor" / "rules" / "jcodemunch.mdc"
    windsurf = tmp_path / ".windsurfrules"
    monkeypatch.setattr(init, "_cursor_rules_path", lambda: cursor)
    monkeypatch.setattr(init, "_windsurf_rules_path", lambda: windsurf)
    target = {"cursor": cursor, "windsurf": windsurf, "agents_md": tmp_path / "AGENTS.md"}[which]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(MENTION, encoding="utf-8")
    install = {
        "cursor": lambda: init.install_cursor_rules(backup=False),
        "windsurf": lambda: init.install_windsurf_rules(backup=False),
        "agents_md": lambda: init.install_agents_md(backup=False),
    }[which]
    out = install()
    assert "already present" not in out, out
    lines = target.read_text(encoding="utf-8").splitlines()
    assert any(ln.strip() == POLICY_MARKER for ln in lines), lines[:5]


@pytest.mark.parametrize("which", ["cursor", "windsurf", "agents_md"])
def test_an_installed_policy_is_still_found(which, tmp_path, monkeypatch):
    """The other direction: a real install reads as present, so a second install adds no second copy."""
    monkeypatch.chdir(tmp_path)
    cursor = tmp_path / ".cursor" / "rules" / "jcodemunch.mdc"
    windsurf = tmp_path / ".windsurfrules"
    monkeypatch.setattr(init, "_cursor_rules_path", lambda: cursor)
    monkeypatch.setattr(init, "_windsurf_rules_path", lambda: windsurf)
    target = {"cursor": cursor, "windsurf": windsurf, "agents_md": tmp_path / "AGENTS.md"}[which]
    install = {
        "cursor": lambda: init.install_cursor_rules(backup=False),
        "windsurf": lambda: init.install_windsurf_rules(backup=False),
        "agents_md": lambda: init.install_agents_md(backup=False),
    }[which]
    install()
    assert "already present" in install()
    text = target.read_text(encoding="utf-8")
    assert sum(1 for ln in text.splitlines() if ln.strip() == POLICY_MARKER) == 1
