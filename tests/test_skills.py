"""Tests for the Claude Agent Skill bundle install/uninstall/status flow (v1.107.0)."""
import json
from pathlib import Path

import pytest

from jcodemunch_mcp.cli.skills import (
    _SKILL_MARKER,
    _build_skill_content,
    _has_skill,
    _skill_dir,
    _skill_path,
    install_claude_skill,
    skill_status,
    uninstall_claude_skill,
)


# ---------------------------------------------------------------------------
# Content builder
# ---------------------------------------------------------------------------

class TestBuildSkillContent:
    def test_has_yaml_frontmatter(self):
        content = _build_skill_content()
        assert content.startswith("---\n")
        assert "name: jcodemunch" in content
        assert "description:" in content
        # Frontmatter ends with the second `---` followed by a blank line.
        head = content.split("\n\n", 1)[0]
        assert head.count("---") == 2

    def test_contains_marker(self):
        assert _SKILL_MARKER in _build_skill_content()

    def test_includes_tool_decision_tree(self):
        content = _build_skill_content()
        # Spot-check the procedural body has the expected sections
        assert "When to load this skill" in content
        # Was "Opening move": two calls demanded before any exploration.
        assert "Before the first jCodemunch call" in content
        assert "Anti-patterns" in content
        assert "resolve_repo" in content
        assert "plan_turn" in content


# ---------------------------------------------------------------------------
# install_claude_skill
# ---------------------------------------------------------------------------

class TestInstallSkill:
    def test_creates_skill_in_global_scope(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        msg = install_claude_skill(scope="global", backup=False)
        path = tmp_path / ".claude" / "skills" / "jcodemunch" / "SKILL.md"
        assert path.exists()
        assert _SKILL_MARKER in path.read_text(encoding="utf-8")
        assert "wrote" in msg

    def test_creates_skill_in_project_scope(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        install_claude_skill(scope="project", backup=False)
        path = tmp_path / ".claude" / "skills" / "jcodemunch" / "SKILL.md"
        assert path.exists()

    def test_dry_run_does_not_write(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        msg = install_claude_skill(scope="global", dry_run=True, backup=False)
        path = tmp_path / ".claude" / "skills" / "jcodemunch" / "SKILL.md"
        assert not path.exists()
        assert "would write" in msg

    def test_idempotent_when_already_present(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        install_claude_skill(scope="global", backup=False)
        msg = install_claude_skill(scope="global", backup=False)
        assert "already present" in msg


# ---------------------------------------------------------------------------
# uninstall_claude_skill
# ---------------------------------------------------------------------------

class TestUninstallSkill:
    def test_removes_file_and_empty_dirs(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        install_claude_skill(scope="global", backup=False)
        path = tmp_path / ".claude" / "skills" / "jcodemunch" / "SKILL.md"
        assert path.exists()

        msg = uninstall_claude_skill(scope="global", backup=False)
        assert not path.exists()
        # Parent jcodemunch/ should be removed because it's now empty
        assert not path.parent.exists()
        # skills/ dir should also be removed since it has no other skills
        assert not path.parent.parent.exists()
        assert "removed" in msg

    def test_preserves_user_authored_skill_md(self, tmp_path, monkeypatch):
        """If a SKILL.md exists at the target but isn't our skill, leave it alone."""
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        path = tmp_path / ".claude" / "skills" / "jcodemunch" / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# A different skill written by the user\n", encoding="utf-8")

        msg = uninstall_claude_skill(scope="global", backup=False)
        # File should still exist
        assert path.exists()
        assert "user" in path.read_text(encoding="utf-8")
        assert "not a jcodemunch skill" in msg

    def test_no_op_when_no_skill_present(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        msg = uninstall_claude_skill(scope="global", backup=False)
        assert "no skill" in msg

    def test_dry_run_does_not_remove(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        install_claude_skill(scope="global", backup=False)
        path = tmp_path / ".claude" / "skills" / "jcodemunch" / "SKILL.md"
        msg = uninstall_claude_skill(scope="global", dry_run=True, backup=False)
        assert path.exists()
        assert "would remove" in msg

    def test_preserves_other_skills_in_same_dir(self, tmp_path, monkeypatch):
        """If sibling skills exist under skills/, only jcodemunch/ is removed."""
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        install_claude_skill(scope="global", backup=False)
        # Pretend the user has another skill
        other = tmp_path / ".claude" / "skills" / "their-skill" / "SKILL.md"
        other.parent.mkdir(parents=True, exist_ok=True)
        other.write_text("# Their skill", encoding="utf-8")

        uninstall_claude_skill(scope="global", backup=False)
        # Our skill gone
        assert not (tmp_path / ".claude" / "skills" / "jcodemunch").exists()
        # Theirs survives
        assert other.exists()


# ---------------------------------------------------------------------------
# skill_status
# ---------------------------------------------------------------------------

class TestSkillStatus:
    def test_reports_absent_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        status = skill_status("global")
        assert status["present"] is False
        assert ".claude/skills/jcodemunch/SKILL.md" in status["path"].replace("\\", "/")

    def test_reports_present_after_install(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        install_claude_skill(scope="global", backup=False)
        status = skill_status("global")
        assert status["present"] is True

    def test_does_not_report_user_authored_skill_as_ours(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        path = tmp_path / ".claude" / "skills" / "jcodemunch" / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# someone else's skill at the same path\n", encoding="utf-8")
        assert skill_status("global")["present"] is False


# ---------------------------------------------------------------------------
# Round-trip via run_init + run_uninstall
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_install_skill_via_run_init_then_uninstall(self, tmp_path, monkeypatch):
        from jcodemunch_mcp.cli.init import run_init, run_uninstall
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        monkeypatch.setattr(
            "jcodemunch_mcp.cli.init._claude_md_path",
            lambda scope: tmp_path / f"CLAUDE-{scope}.md",
        )
        monkeypatch.setattr("jcodemunch_mcp.cli.init._detect_clients", lambda: [])
        monkeypatch.chdir(tmp_path)

        # Install with skills
        rc = run_init(
            clients=["none"], claude_md="global", hooks=False, copilot_hooks=False,
            index=False, audit=False, yes=True, no_backup=True,
            skills=True, skills_scope="global",
        )
        assert rc == 0
        skill_path = tmp_path / ".claude" / "skills" / "jcodemunch" / "SKILL.md"
        assert skill_path.exists(), "Skill should be installed"

        # Uninstall scrubs it
        rc = run_uninstall(
            claude_md=False, cursor_rules=False, windsurf_rules=False,
            agents_md=False, hooks=False, copilot_hooks=False,
            skills=True, yes=True, no_backup=True,
        )
        assert rc == 0
        assert not skill_path.exists(), "Skill should be removed"
