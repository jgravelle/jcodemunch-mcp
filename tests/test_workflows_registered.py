"""The workflow layer cannot drift from its own design (docs/workflows/DESIGN.md section 8).

Every command under `.claude/commands/` is named in CLAUDE.md and in
DESIGN.md; every command, hook and agent carries the four-line header
(purpose / invokes / produces / refuses); no command or hook restates a
Floor value from `harness/thresholds.json`; every hook `settings.json`
wires exists; and H1's format check is the one `pr-gate.yml` runs, read
from the workflow file rather than copied (the `test_ci_env_reproduce_command`
shape). Each assertion has a red arm in its own docstring: the thing it
would miss if deleted.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CLAUDE_DIR = ROOT / ".claude"
COMMANDS = sorted((CLAUDE_DIR / "commands").glob("*.md"))
HOOKS = sorted(
    p for p in (CLAUDE_DIR / "hooks").glob("*.py") if not p.name.startswith("_")
)
AGENTS = sorted((CLAUDE_DIR / "agents").glob("*.md"))
HEADER_KEYS = ("purpose:", "invokes:", "produces:", "refuses:")


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_the_layer_exists():
    """A tracked `.claude/` is the whole point of DESIGN D1; an empty glob passes every parametrize vacuously."""
    assert len(COMMANDS) >= 6, [p.name for p in COMMANDS]
    assert len(HOOKS) >= 5, [p.name for p in HOOKS]
    assert (CLAUDE_DIR / "agents" / "reviewer.md").exists()
    assert (CLAUDE_DIR / "settings.json").exists()


@pytest.mark.parametrize("path", COMMANDS + HOOKS + AGENTS, ids=lambda p: p.name)
def test_every_workflow_file_carries_the_header(path: Path):
    """Build rule: purpose, invokes, produces, refuses. A file without one is undocumented process."""
    text = _read(path)
    missing = [k for k in HEADER_KEYS if k not in text]
    assert not missing, f"{path.name} lacks header keys {missing}"


@pytest.mark.parametrize("path", COMMANDS, ids=lambda p: p.name)
def test_every_command_is_named_in_claude_md_and_design(path: Path):
    """A command absent from CLAUDE.md is one a session improvises around; absent from DESIGN.md it has no spec."""
    name = "/" + path.stem
    assert name in _read(ROOT / "CLAUDE.md"), f"{name} not listed in CLAUDE.md"
    assert name in _read(ROOT / "docs" / "workflows" / "DESIGN.md"), (
        f"{name} not specified in DESIGN.md"
    )


def _floor_literals() -> list[tuple[str, str]]:
    data = json.loads(_read(ROOT / "harness" / "thresholds.json"))["thresholds"]
    out = []
    for e in data:
        f = e.get("floor")
        if isinstance(f, (int, float)) and f not in (0, 1):
            out.append((e["id"], str(f)))
    return out


@pytest.mark.parametrize("path", COMMANDS + HOOKS, ids=lambda p: p.name)
def test_no_command_or_hook_restates_a_floor(path: Path):
    """Principle 1: a Floor lives only in thresholds.json. A literal here would drift the day the Floor moves."""
    text = _read(path)
    hits = []
    for tid, value in _floor_literals():
        if re.search(rf"{re.escape(tid)}\D{{0,40}}{re.escape(value)}\b", text):
            hits.append((tid, value))
    assert not hits, f"{path.name} restates Floor value(s): {hits}"


def test_settings_hooks_point_at_files_that_exist():
    """A hook wired to a missing file is a hook that never runs and never says so."""
    settings = json.loads(_read(CLAUDE_DIR / "settings.json"))
    wired = []
    for event, entries in settings["hooks"].items():
        for entry in entries:
            for h in entry["hooks"]:
                m = re.search(r"\.claude/hooks/(\w+\.py)", h["command"])
                assert m, (event, h["command"])
                wired.append(m.group(1))
                assert (CLAUDE_DIR / "hooks" / m.group(1)).exists(), m.group(1)
                assert h.get("timeout"), (
                    f"{m.group(1)} has no timeout backstop (DESIGN section 4)"
                )
    for hook in HOOKS:
        if hook.name in ("run_full.py", "dod_checklist.py"):
            continue
        assert hook.name in wired, f"{hook.name} exists but no event runs it"


def test_settings_deny_covers_every_verb_the_brief_forbids():
    """Publish, tag, force-push, merge, post: each must be denied on Bash. Losing one reopens a path the layer promises is closed."""
    deny = json.loads(_read(CLAUDE_DIR / "settings.json"))["permissions"]["deny"]
    joined = "\n".join(deny)
    for verb in (
        "git tag",
        "git push --force",
        "gh release",
        "gh workflow run",
        "gh pr merge",
        "gh pr comment",
        "gh issue comment",
        "twine",
        "mcp-publisher",
    ):
        assert verb in joined, f"deny list lost `{verb}`"


def test_h1_format_check_is_read_from_the_workflow_not_copied():
    """`fast: format`'s scope is pr-gate.yml's decision (C-1). pre_commit.py must find it there; a copied command drifts."""
    src = _read(CLAUDE_DIR / "hooks" / "pre_commit.py")
    assert "pr-gate.yml" in src and "format --check" in src
    assert not re.search(r"format --check\s+harness/\s+scripts/", src), (
        "the scope is copied into the hook; read it from pr-gate.yml"
    )
    wf = _read(ROOT / ".github" / "workflows" / "pr-gate.yml")
    assert re.search(r"^\s*uvx ruff\S* format --check", wf, re.M), (
        "pr-gate.yml no longer has the step the hook reads"
    )


def test_sdist_still_excludes_all_of_claude_dir():
    """D1 narrowed .gitignore, not the sdist exclusion. The v0.2.6 leak vector was the sdist."""
    pyproject = _read(ROOT / "pyproject.toml")
    sdist = pyproject.split("[tool.hatch.build.targets.sdist]", 1)[1].split("\n[", 1)[0]
    assert '".claude/"' in sdist
    gitignore = _read(ROOT / ".gitignore")
    assert ".claude/settings.local.json" in gitignore and ".claude/state/" in gitignore


@pytest.mark.parametrize(
    "path",
    COMMANDS + AGENTS + sorted((CLAUDE_DIR / "skills").glob("*/SKILL.md")),
    ids=lambda p: p.parent.name if p.name == "SKILL.md" else p.name,
)
def test_front_matter_is_valid_yaml(path: Path):
    """`/review`'s `argument-hint: [pr-number | ref] [--merge-check]` parsed as a flow sequence plus junk, so Claude Code showed `<!--` as its description; a broken header is a command nobody can find."""
    import yaml  # a runtime dependency (pyproject `pyyaml>=6.0`), so no skip

    text = _read(path)
    assert text.startswith("---\n"), f"{path.name} has no front matter"
    front = text.split("---", 2)[1]
    data = yaml.safe_load(front)
    assert isinstance(data, dict) and data.get("description"), (
        f"{path.name}: description missing or unparsed"
    )
