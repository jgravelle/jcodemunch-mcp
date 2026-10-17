"""Tests for CLAUDE.md policy update (Feature 9: Session-Aware Routing)."""
import pytest


# All required terms that must be present in _CLAUDE_MD_POLICY
REQUIRED_POLICY_TERMS = [
    ("plan_turn", "plan_turn tool for routing"),
    ("assemble_task_context", "assemble_task_context single-call orchestrator"),
    ("negative_evidence", "negative_evidence for empty results"),
    ("high", "high confidence level"),
    ("medium", "medium confidence level"),
    ("low", "low confidence level"),
    ("register_edit", "register_edit for bulk cache invalidation"),
    ("Session-Aware Routing", "Session-Aware Routing section header"),
    ("budget_warning", "budget_warning handling"),
    ("No existing implementation", "no implementation guidance"),
    # Was "Opening move": the policy demanded `plan_turn` before any task. It
    # is offered for a broad task now (tests/test_the_policy_makes_no_absolute_claim.py).
    ("For a broad task", "plan_turn offered for a broad task"),
]


class TestClaudeMdPolicyContent:
    """Tests for updated _CLAUDE_MD_POLICY constant."""

    @pytest.mark.parametrize("term,description", REQUIRED_POLICY_TERMS)
    def test_policy_contains_required_term(self, term, description):
        """_CLAUDE_MD_POLICY must contain all required terms."""
        from jcodemunch_mcp.cli.init import _CLAUDE_MD_POLICY
        assert term in _CLAUDE_MD_POLICY, f"Missing {description}"


class TestCursorRulesContent:
    """Tests for updated _CURSOR_RULES_CONTENT."""

    def test_cursor_rules_inherits_policy(self):
        """_CURSOR_RULES_CONTENT must include the updated policy."""
        from jcodemunch_mcp.cli.init import _CURSOR_RULES_CONTENT, _CLAUDE_MD_POLICY
        # Cursor rules should contain the policy text
        assert _CLAUDE_MD_POLICY in _CURSOR_RULES_CONTENT or "plan_turn" in _CURSOR_RULES_CONTENT

    def test_cursor_rules_has_frontmatter(self):
        """_CURSOR_RULES_CONTENT must have MDC frontmatter."""
        from jcodemunch_mcp.cli.init import _CURSOR_RULES_CONTENT
        assert "---" in _CURSOR_RULES_CONTENT
        assert "description:" in _CURSOR_RULES_CONTENT


class TestWindsurfRulesContent:
    """Tests for updated _WINDSURF_RULES_CONTENT."""

    def test_windsurf_rules_equals_policy(self):
        """_WINDSURF_RULES_CONTENT should equal _CLAUDE_MD_POLICY."""
        from jcodemunch_mcp.cli.init import _WINDSURF_RULES_CONTENT, _CLAUDE_MD_POLICY
        assert _WINDSURF_RULES_CONTENT == _CLAUDE_MD_POLICY


class TestInstallClaudeMdIntegration:
    """Integration tests for install_claude_md with updated policy."""

    def test_install_claude_md_contains_new_policy(self, tmp_path, monkeypatch):
        """Installed CLAUDE.md must contain Session-Aware Routing section and plan_turn."""
        from jcodemunch_mcp.cli.init import install_claude_md
        monkeypatch.setattr(
            "jcodemunch_mcp.cli.init._claude_md_path",
            lambda scope: tmp_path / "CLAUDE.md"
        )
        install_claude_md("project", backup=False)
        content = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        assert "Session-Aware Routing" in content
        assert "plan_turn" in content


class TestFilterPolicyForTools:
    """Tests for _filter_policy_for_tools (issue #242 follow-up)."""

    def test_none_returns_unchanged(self):
        """None active_tools means full profile — no filtering."""
        from jcodemunch_mcp.cli.init import _filter_policy_for_tools, _CLAUDE_MD_POLICY
        assert _filter_policy_for_tools(_CLAUDE_MD_POLICY, None) == _CLAUDE_MD_POLICY

    def test_core_profile_excludes_standard_tools(self):
        """Core profile should drop lines referencing standard-only tools."""
        from jcodemunch_mcp.cli.init import _filter_policy_for_tools, _CLAUDE_MD_POLICY
        from jcodemunch_mcp.server import _TOOL_TIER_CORE
        result = _filter_policy_for_tools(_CLAUDE_MD_POLICY, set(_TOOL_TIER_CORE))
        assert "search_symbols" in result  # core tool — kept
        assert "get_blast_radius" not in result  # standard-only — removed
        assert "find_dead_code" not in result  # standard-only — removed
        assert "plan_turn" not in result  # full-only — removed

    def test_core_profile_keeps_core_tools(self):
        """Core profile should keep all core tool references."""
        from jcodemunch_mcp.cli.init import _filter_policy_for_tools, _CLAUDE_MD_POLICY
        from jcodemunch_mcp.server import _TOOL_TIER_CORE
        result = _filter_policy_for_tools(_CLAUDE_MD_POLICY, set(_TOOL_TIER_CORE))
        for tool in ("resolve_repo", "index_folder", "search_symbols",
                      "get_file_outline", "get_symbol_source", "get_file_content",
                      "get_context_bundle",
                      "find_importers", "find_references"):
            assert tool in result, f"core tool {tool} should be in filtered output"

    def test_standard_profile_excludes_full_only(self):
        """Standard profile should drop full-only tools like plan_turn."""
        from jcodemunch_mcp.cli.init import _filter_policy_for_tools, _CLAUDE_MD_POLICY
        from jcodemunch_mcp.server import _TOOL_TIER_STANDARD
        result = _filter_policy_for_tools(_CLAUDE_MD_POLICY, set(_TOOL_TIER_STANDARD))
        assert "get_blast_radius" in result  # standard tool — kept
        assert "plan_turn" not in result  # full-only — removed

    def test_empty_bold_sections_removed(self):
        """Bold-label sections with no surviving bullets should be pruned."""
        from jcodemunch_mcp.cli.init import _filter_policy_for_tools, _CLAUDE_MD_POLICY
        from jcodemunch_mcp.server import _TOOL_TIER_CORE
        result = _filter_policy_for_tools(_CLAUDE_MD_POLICY, set(_TOOL_TIER_CORE))
        # "Relationships & impact:" section has mostly standard-only tools
        # check_references, get_dependency_graph, get_blast_radius,
        # get_changed_symbols, find_dead_code, get_class_hierarchy are all standard
        # Only find_importers and find_references (core) should survive
        # The section header should still be present since it has surviving bullets
        assert "find_importers" in result

    def test_disabled_tools_respected(self):
        """Individually disabled tools should be filtered out."""
        from jcodemunch_mcp.cli.init import _filter_policy_for_tools, _CLAUDE_MD_POLICY
        from jcodemunch_mcp.server import _CANONICAL_TOOL_NAMES
        active = set(_CANONICAL_TOOL_NAMES) - {"search_text"}
        result = _filter_policy_for_tools(_CLAUDE_MD_POLICY, active)
        assert "search_text" not in result
        assert "search_symbols" in result  # other tools unaffected

    def test_install_claude_md_core_profile(self, tmp_path, monkeypatch):
        """install_claude_md respects tool_profile=core."""
        from jcodemunch_mcp.cli.init import install_claude_md
        from jcodemunch_mcp.server import _TOOL_TIER_CORE
        monkeypatch.setattr(
            "jcodemunch_mcp.cli.init._claude_md_path",
            lambda scope: tmp_path / "CLAUDE.md"
        )
        # ⚠ `cli.policy`, not `cli.init`. `init` re-exports this name, but
        # `active_policy` resolves it through `policy`'s globals, so patching
        # the alias is silently ineffective.
        monkeypatch.setattr(
            "jcodemunch_mcp.cli.policy._get_active_tools",
            lambda: set(_TOOL_TIER_CORE)
        )
        install_claude_md("project", backup=False)
        content = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        assert "search_symbols" in content
        assert "plan_turn" not in content
        assert "get_blast_radius" not in content