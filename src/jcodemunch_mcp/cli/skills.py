"""Claude Agent Skill emission for jCodemunch tool-usage guidance.

The Claude Agent Skill format (`.claude/skills/<name>/SKILL.md` with YAML
frontmatter) is Anthropic's structured way to bundle on-demand expertise
that Claude loads per task rather than carrying in baseline context.

This module emits a `jcodemunch` skill whose body is a tool-usage decision
tree: which jcm tool to reach for given the task at hand. Content is
tier-aware via the same `_filter_policy_for_tools` filter the
CLAUDE.md preamble uses — so a `core`-tier user gets a skill body that
only references tools they actually have.

Lives at:
- Global: ``~/.claude/skills/jcodemunch/SKILL.md``
- Project: ``./.claude/skills/jcodemunch/SKILL.md``

Composes cleanly with the existing CLAUDE.md preamble: preamble is
always-on policy; skill is on-demand procedural expertise. Users who
prefer one over the other can disable either via flags on
``jcm install``.
"""
from __future__ import annotations

import shutil
from pathlib import Path


# Marker is on the first non-frontmatter heading line so we can detect an
# existing jcodemunch skill (vs. a user-authored one in the same directory)
# during install/uninstall/status.
_SKILL_MARKER = "# jCodemunch Code Exploration Skill"

_SKILL_NAME = "jcodemunch"


def _skill_dir(scope: str) -> Path:
    """Return the directory holding the jcodemunch skill bundle."""
    if scope == "global":
        return Path.home() / ".claude" / "skills" / _SKILL_NAME
    return Path.cwd() / ".claude" / "skills" / _SKILL_NAME


def _skill_path(scope: str) -> Path:
    """Return the SKILL.md file path for the given scope."""
    return _skill_dir(scope) / "SKILL.md"


def _has_skill(path: Path) -> bool:
    """True iff a jcodemunch skill is already installed at this path."""
    if not path.exists():
        return False
    try:
        return _SKILL_MARKER in path.read_text(encoding="utf-8")
    except OSError:
        return False


def _build_skill_content() -> str:
    """Compose SKILL.md content with YAML frontmatter + tool-usage body.

    Body is tier-filtered via the same _filter_policy_for_tools machinery
    the CLAUDE.md preamble uses, so disabled tools don't appear in the
    skill text.
    """
    # Import inside the function to avoid a circular import at module
    # load: init.py imports from .. and from .skills (eventually).
    from .policy import _CLAUDE_MD_POLICY, _filter_policy_for_tools, _get_active_tools

    body_lines = [
        "---",
        f"name: {_SKILL_NAME}",
        "description: Use jCodemunch MCP tools for symbol-level code navigation "
        "instead of raw Read/Grep/Glob/Bash. Loads on demand; complements the "
        "always-on policy block.",
        "---",
        "",
        _SKILL_MARKER,
        "",
        "Load this skill when navigating code, investigating dependencies, "
        "planning refactors, or assembling task context. The jcodemunch MCP "
        "server provides symbol-aware retrieval and graph relationships that "
        "regex and full-file reads cannot reproduce.",
        "",
        "## When to load this skill",
        "",
        "- Opening a session in an unfamiliar repo",
        "- Tracing what depends on a symbol before changing it",
        "- Finding similar implementations to consolidate",
        "- Producing a context capsule for a multi-step task",
        "- Auditing dead code, churn hotspots, or architectural health",
        "",
        "## When NOT to load this skill",
        "",
        "- You already know the exact file and line — just `Read` it (the "
        "  policy block notes the `Read`-before-`Edit` exception).",
        "- The task is purely conversational with no code context.",
        "- The repo is unindexed and you don't intend to index it.",
        "",
        "## Tool taxonomy",
        "",
        "The policy block already enumerates the canonical decision tree. "
        "What follows is procedural advice for *how* to use those tools, not "
        "a duplicate listing.",
        "",
        "### Opening move",
        "",
        "Before any code exploration:",
        "",
        "1. `resolve_repo({\"path\": \".\"})` — confirms the repo is indexed "
        "and queryable. If `indexed: false`, run `index_folder`.",
        "2. `plan_turn({\"repo\": \"...\", \"query\": \"...\", "
        "\"model\": \"<your-model-id>\"})` — gets a confidence verdict + "
        "recommended files. Obey the confidence:",
        "   - `high` → go directly to the recommended symbols.",
        "   - `medium` → explore the recommended files, cap supplementary reads.",
        "   - `low` → the capability likely doesn't exist; report the gap, do "
        "     not keep searching hoping it appears.",
        "",
        "### Reading code",
        "",
        "Never `Read` a full file as a first move. The chain is:",
        "",
        "- `get_file_outline(file_path)` → see the symbol layout first.",
        "- `get_symbol_source(symbol_id)` → fetch just the function body you need.",
        "- `get_context_bundle(symbol_id)` → if you also need its imports/types.",
        "- `get_file_content(file_path, start_line, end_line)` → last resort, "
        "  only for ranges that aren't symbol-shaped.",
        "",
        "### Relationships",
        "",
        "Each does something distinct; pick by question:",
        "",
        "- \"What imports this file?\" → `find_importers`",
        "- \"Who imports this identifier?\" → `find_references` (import graph only)",
        "- \"Where is this identifier used?\" → `check_references` (imports + every content match, is_referenced yes/no)",
        "- \"What breaks if I change X?\" → `get_blast_radius`",
        "- \"Who calls this / what does this call?\" → `get_call_hierarchy`",
        "- \"Is this safe to rename?\" → `check_rename_safe`",
        "- \"Is this safe to delete?\" → `check_delete_safe` (8 verdict tiers)",
        "- \"What's similar to this in the codebase?\" → `find_similar_symbols`",
        "",
        "### Task orchestration",
        "",
        "When a task spans multiple tools, prefer the orchestrator:",
        "",
        "- `assemble_task_context({\"task\": \"natural-language task\"})` "
        "  auto-classifies into one of six intents (explore / debug / refactor "
        "  / extend / audit / review) and packs a token-budgeted, "
        "  source-attributed capsule. Each entry carries `stage` + `source_tool` "
        "  so you can see provenance.",
        "",
        "## Anti-patterns",
        "",
        "Avoid:",
        "",
        "- `Read`-then-`Grep`-then-`Glob` chains on indexed repos. The index "
        "  already knows the answer; the chain wastes tokens.",
        "- Searching with different keywords after `negative_evidence: "
        "  \"no_implementation_found\"`. The capability likely doesn't exist — "
        "  report the gap.",
        "- Reading full files when `get_file_outline` would do.",
        "- Calling primitives (`find_references` + `get_symbol_source` + ...) "
        "  separately when `assemble_task_context` or `get_context_bundle` "
        "  produces the whole capsule in one call.",
        "",
        "## After editing",
        "",
        "- If PostToolUse hooks are installed (Claude Code), edits are "
        "  auto-reindexed.",
        "- Otherwise: `register_edit({\"paths\": [...]})` invalidates caches "
        "  and refreshes the index for those files.",
        "",
        "## Multi-process awareness (v1.106.0)",
        "",
        "If multiple agent sessions share this repo, `get_watch_status` "
        "surfaces `watcher_holder` per repo (pid, client_id, started_at, "
        "age_seconds). When `watched_by_another_process: true`, our watcher "
        "is intentionally idle — another process is keeping the index fresh.",
        "",
        "## Tier model",
        "",
        "The server narrows the exposed tool list based on the model you "
        "report via `plan_turn`'s `model` parameter. To get the right tier:",
        "",
        "- Claude Opus → `claude-opus-4-7`",
        "- Claude Sonnet → `claude-sonnet-4-6`",
        "- Claude Haiku → `claude-haiku-4-5`",
        "- Other models → the model id as your runner prints it",
        "",
        "If `plan_turn` doesn't fit a given task, call "
        "`announce_model({\"model\": \"...\"})` once and proceed.",
        "",
        "## Tool reference",
        "",
        "The full canonical reference (with decision-tree bullet points "
        "matching the always-on preamble) follows. This restatement is "
        "intentional — when the skill is loaded the agent shouldn't need "
        "to also have the preamble in context.",
        "",
    ]

    policy = _filter_policy_for_tools(_CLAUDE_MD_POLICY, _get_active_tools())
    body_lines.append(policy.rstrip())
    body_lines.append("")
    return "\n".join(body_lines)


def install_claude_skill(
    scope: str = "global",
    *,
    dry_run: bool = False,
    backup: bool = True,
) -> str:
    """Write the jcodemunch Claude Agent Skill bundle.

    scope: "global" (~/.claude/skills/jcodemunch/) or "project"
        (./.claude/skills/jcodemunch/).
    Returns a status message.
    """
    path = _skill_path(scope)
    if _has_skill(path):
        return f"  skill already present at {path}"
    if dry_run:
        return f"  would write {path}"

    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        shutil.copy2(path, path.with_suffix(".md.bak"))

    content = _build_skill_content()
    path.write_text(content, encoding="utf-8")
    return f"  wrote {path}"


def uninstall_claude_skill(
    scope: str = "global",
    *,
    dry_run: bool = False,
    backup: bool = True,  # accepted for signature parity with other uninstall_*; not used
) -> str:
    """Remove the jcodemunch Claude Agent Skill bundle for the given scope.

    Preserves user-authored content: if the SKILL.md at the target path
    doesn't contain our marker, we leave it alone. After removing the
    SKILL.md we also rmdir the parent (`jcodemunch/`) and grandparent
    (`skills/`) directories iff they're empty — we created them; safe
    to clean up.

    Note: `backup` is intentionally a no-op here. Writing the .bak next to
    SKILL.md would defeat the empty-parent cleanup pass; the skill content
    is regenerable by re-running `jcm install ... --skills`, so the .bak
    has no real safety value on the way out.
    """
    del backup  # acknowledged unused
    path = _skill_path(scope)
    if not path.exists():
        return f"  no skill at {path}"
    if not _has_skill(path):
        return f"  file at {path} is not a jcodemunch skill — left untouched"
    if dry_run:
        return f"  would remove {path}"
    path.unlink()
    # Remove the per-skill directory if it's empty (we created it).
    parent = path.parent
    try:
        parent.rmdir()
    except OSError:
        return f"  removed {path} (parent dir kept — contains other files)"
    # Remove the `.claude/skills/` grandparent iff empty (no other skills).
    skills_parent = parent.parent
    if skills_parent.name == "skills":
        try:
            skills_parent.rmdir()
        except OSError:
            pass  # other skills present
    return f"  removed {path}"


def skill_status(scope: str) -> dict:
    """Read-only status: is the jcodemunch skill installed at this scope?"""
    path = _skill_path(scope)
    return {
        "path": str(path),
        "present": _has_skill(path),
    }
