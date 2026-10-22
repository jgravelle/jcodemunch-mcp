"""A default install registers the server and adds only hooks the model never sees.

`init --yes` (and `install <agent>`, and the `upgrade` refresh, which runs
`init --hooks --yes`) used to write the Code Exploration Policy into the global
CLAUDE.md and AGENTS.md and install six Claude Code hooks, three of which put
text in front of the model: the PreToolUse advice on Read/Grep/Glob/Bash, the
SubagentStart briefing and the SessionStart restore after compaction. The policy
and the advice are paid for on every request they apply to, and in Claude Code
sessions we measured, the advice did not change which tools the agent used.

Now the default writes the server registration and the silent hooks
(PostToolUse reindex, PreCompact, TaskCompleted). `--guidance` adds the three
model-facing hooks and `--policy` the policy pastes. An install that already has
them keeps them; `init --remove-guidance` removes them, and `init` and
`install-status` name it without applying it.

Every test owns its settings file, home and project directory (Practice 8).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jcodemunch_mcp.cli import init as init_module
from jcodemunch_mcp.cli.policy import _CLAUDE_MD_POLICY

SILENT = {"PostToolUse", "PreCompact", "TaskCompleted"}
GUIDANCE = {"PreToolUse", "SubagentStart", "SessionStart"}


@pytest.fixture
def box(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    project = tmp_path / "project"
    project.mkdir()
    settings = home / ".claude" / "settings.json"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / "idx"))
    monkeypatch.chdir(project)
    monkeypatch.setattr(init_module, "_settings_json_path", lambda: settings)
    # conftest maps every scope to one isolated file; these tests need both scopes,
    # still inside directories this test owns.
    monkeypatch.setattr(
        init_module,
        "_claude_md_path",
        lambda scope: (home / ".claude" / "CLAUDE.md")
        if scope == "global"
        else (project / "CLAUDE.md"),
    )
    monkeypatch.setattr(init_module, "_hook_invocation", lambda: "jcodemunch-mcp")
    monkeypatch.setattr(init_module, "_detect_clients", lambda: [])
    monkeypatch.setattr(init_module, "run_index", lambda **k: "  (index skipped)")
    monkeypatch.setattr(init_module, "run_audit", lambda **k: iter(()))
    monkeypatch.setattr(init_module, "_stamp_install_version", lambda: None)
    return {"home": home, "project": project, "settings": settings}


def _events(settings: Path) -> set[str]:
    if not settings.exists():
        return set()
    data = json.loads(settings.read_text(encoding="utf-8"))
    out = set()
    for event, rules in (data.get("hooks") or {}).items():
        for rule in rules:
            if any(
                "jcodemunch-mcp" in (h.get("command") or "")
                for h in rule.get("hooks", [])
            ):
                out.add(event)
    return out


def _init(**kw):
    kw.setdefault("yes", True)
    kw.setdefault("no_backup", True)
    return init_module.run_init(**kw)


def _policy_files(box):
    return [
        box["home"] / ".claude" / "CLAUDE.md",
        box["project"] / "CLAUDE.md",
        box["project"] / "AGENTS.md",
        box["project"] / ".cursor" / "rules" / "jcodemunch.mdc",
        box["project"] / ".windsurfrules",
    ]


# --- the new default ------------------------------------------------------------------


def test_a_default_install_adds_only_the_silent_hooks_and_no_policy(box):
    assert _init() == 0
    assert _events(box["settings"]) == SILENT
    for path in _policy_files(box):
        assert not init_module._has_policy(path), path


def test_the_upgrade_refresh_on_a_fresh_home_adds_no_guidance(box):
    """`upgrade` runs `init --hooks --yes`."""
    assert _init(hooks=True, hooks_explicit=True) == 0
    assert not (_events(box["settings"]) & GUIDANCE)
    assert not init_module._has_policy(box["home"] / ".claude" / "CLAUDE.md")


def test_the_silent_and_guidance_sets_together_are_every_shipped_hook():
    """Splitting the hooks must not lose one."""
    assert (
        set(init_module._silent_hooks()) | set(init_module._guidance_hooks())
        == SILENT | GUIDANCE
    )
    assert not set(init_module._silent_hooks()) & set(init_module._guidance_hooks())


# --- opting in --------------------------------------------------------------------------


def test_guidance_adds_the_model_facing_hooks(box):
    assert _init(guidance=True) == 0
    assert _events(box["settings"]) == SILENT | GUIDANCE


def test_policy_adds_the_policy_pastes(box):
    assert _init(policy=True) == 0
    assert init_module._has_policy(box["home"] / ".claude" / "CLAUDE.md")
    assert init_module._has_policy(box["project"] / "AGENTS.md")


def test_claude_md_project_still_writes_the_project_file(box):
    assert _init(claude_md="project") == 0
    assert init_module._has_policy(box["project"] / "CLAUDE.md")
    assert not init_module._has_policy(box["home"] / ".claude" / "CLAUDE.md")


def test_strict_still_installs_the_advice_hook_it_strengthens(box):
    assert _init(strict=True) == 0
    assert "PreToolUse" in _events(box["settings"])
    data = json.loads(box["settings"].read_text(encoding="utf-8"))
    assert data["env"]["JCODEMUNCH_ENFORCE"] == "strict"


@pytest.mark.parametrize(
    "argv,expect",
    [
        (
            ["install", "claude-code"],
            {"claude_md": None, "guidance": False, "policy": False},
        ),
        (
            ["install", "claude-code", "--policy"],
            {"claude_md": "global", "policy": True},
        ),
        (["install", "claude-code", "--guidance"], {"guidance": True}),
        (
            ["init", "--yes"],
            {
                "claude_md": None,
                "guidance": False,
                "policy": False,
                "remove_guidance": False,
            },
        ),
        (["init", "--guidance", "--policy"], {"guidance": True, "policy": True}),
        (["init", "--remove-guidance"], {"remove_guidance": True}),
    ],
)
def test_the_cli_passes_the_opt_ins_through(monkeypatch, argv, expect):
    """`install <agent>` used to hardcode `claude_md="global"`."""
    from jcodemunch_mcp import server

    seen = {}

    def fake_run_init(**kw):
        seen.update(kw)
        return 0

    monkeypatch.setattr(init_module, "run_init", fake_run_init)
    with pytest.raises(SystemExit):
        server.main(argv)
    for key, value in expect.items():
        assert seen.get(key) == value, (argv, key, seen.get(key))


# --- an existing install keeps what it has ----------------------------------------------


def _existing_install(box, *, user_rule=True):
    """Today's default install, plus a user's own PreToolUse rule."""
    hooks = {**init_module._silent_hooks(), **init_module._guidance_hooks()}
    data = {"hooks": json.loads(json.dumps(hooks))}
    # A stale jcm command, to prove existing rules are still converged.
    data["hooks"]["PreToolUse"][0]["hooks"][0]["command"] = (
        "/old/path/jcodemunch-mcp hook-pretooluse"
    )
    if user_rule:
        data["hooks"]["PreToolUse"].append(
            {
                "matcher": "Bash",
                "hooks": [{"type": "command", "command": "python my_own_guard.py"}],
            }
        )
    box["settings"].write_text(json.dumps(data), encoding="utf-8")
    md = box["home"] / ".claude" / "CLAUDE.md"
    md.write_text("# My notes\n\nKeep this.\n\n" + _CLAUDE_MD_POLICY, encoding="utf-8")
    (box["project"] / "AGENTS.md").write_text(
        "# Agents\n\nMine.\n\n" + _CLAUDE_MD_POLICY + "\n", encoding="utf-8"
    )


@pytest.mark.parametrize("kw", [{}, {"hooks": True, "hooks_explicit": True}])
def test_an_existing_install_loses_nothing_on_init_or_upgrade(box, kw, capsys):
    _existing_install(box)
    assert _init(**kw) == 0
    # `--hooks` also adds the worktree hooks; the property is that nothing is lost.
    assert SILENT | GUIDANCE <= _events(box["settings"])
    data = json.loads(box["settings"].read_text(encoding="utf-8"))
    cmds = [h["command"] for r in data["hooks"]["PreToolUse"] for h in r["hooks"]]
    assert "jcodemunch-mcp hook-pretooluse" in cmds  # converged in place
    assert "python my_own_guard.py" in cmds
    assert init_module._has_policy(box["home"] / ".claude" / "CLAUDE.md")


# --- the offer and the cleanup ----------------------------------------------------------


def test_init_names_the_cleanup_without_applying_it(box, capsys):
    _existing_install(box)
    assert _init() == 0
    out = capsys.readouterr().out
    assert "--remove-guidance" in out
    assert _events(box["settings"]) == SILENT | GUIDANCE  # nothing removed


def test_a_clean_install_is_not_offered_a_cleanup(box, capsys):
    assert _init() == 0
    assert "--remove-guidance" not in capsys.readouterr().out


def test_install_status_names_what_remains_and_the_command(box):
    _existing_install(box)
    status = init_module.install_status()
    g = status["guidance"]
    assert set(g["hooks"]) == GUIDANCE
    assert "claude_md_global" in g["policies"] and "agents_md" in g["policies"]
    assert "--remove-guidance" in g["remove_with"]
    assert _events(box["settings"]) == SILENT | GUIDANCE


def test_install_status_text_names_the_cleanup(box, capsys):
    _existing_install(box)
    init_module.print_status()
    assert "--remove-guidance" in capsys.readouterr().out


def test_install_status_on_a_light_install_has_nothing_to_remove(box):
    assert _init() == 0
    g = init_module.install_status()["guidance"]
    assert g["hooks"] == [] and g["policies"] == [] and "remove_with" not in g


def test_remove_guidance_removes_exactly_the_model_facing_parts(box):
    _existing_install(box)
    assert init_module.run_init(yes=True, remove_guidance=True) == 0
    assert _events(box["settings"]) == SILENT
    data = json.loads(box["settings"].read_text(encoding="utf-8"))
    cmds = [
        h["command"] for r in data["hooks"].get("PreToolUse", []) for h in r["hooks"]
    ]
    assert cmds == ["python my_own_guard.py"]  # the user's rule stays
    md = (box["home"] / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Keep this." in md and not init_module._has_policy(
        box["home"] / ".claude" / "CLAUDE.md"
    )
    assert "Mine." in (box["project"] / "AGENTS.md").read_text(encoding="utf-8")
    assert not init_module._has_policy(box["project"] / "AGENTS.md")
    backups = [p.name for p in (box["home"] / ".claude").iterdir()]
    assert any(n != "CLAUDE.md" and n.startswith("CLAUDE.md") for n in backups), backups
    assert any(
        n != "settings.json" and n.startswith("settings.json") for n in backups
    ), backups


def test_remove_guidance_dry_run_changes_nothing(box):
    _existing_install(box)
    before = {
        p: p.read_bytes() for p in [box["settings"], *_policy_files(box)] if p.exists()
    }
    assert init_module.run_init(yes=True, remove_guidance=True, dry_run=True) == 0
    after = {
        p: p.read_bytes() for p in [box["settings"], *_policy_files(box)] if p.exists()
    }
    assert before == after


# --- review follow-ups: the cases a reader could break without a symptom -----------------


def test_a_partial_guidance_install_keeps_what_it_has_and_gains_nothing(box):
    """An install with only the advice hook keeps it (converged) and is not given
    the other two model-facing hooks by a default re-run."""
    data = {"hooks": json.loads(json.dumps(init_module._silent_hooks()))}
    data["hooks"]["PreToolUse"] = json.loads(
        json.dumps(init_module._guidance_hooks()["PreToolUse"])
    )
    box["settings"].write_text(json.dumps(data), encoding="utf-8")
    assert _init() == 0
    assert _events(box["settings"]) == SILENT | {"PreToolUse"}


def test_remove_guidance_drops_the_strict_setting_with_the_hook_it_governs(box):
    assert _init(strict=True) == 0
    data = json.loads(box["settings"].read_text(encoding="utf-8"))
    data.setdefault("env", {})["MY_VAR"] = "1"
    box["settings"].write_text(json.dumps(data), encoding="utf-8")
    assert init_module.run_init(yes=True, remove_guidance=True) == 0
    data = json.loads(box["settings"].read_text(encoding="utf-8"))
    assert "JCODEMUNCH_ENFORCE" not in data.get("env", {})
    assert data["env"]["MY_VAR"] == "1"  # the user's own setting stays


def test_remove_guidance_cleans_the_cursor_and_windsurf_rules(box, monkeypatch):
    cursor = box["project"] / ".cursor" / "rules" / "jcodemunch.mdc"
    cursor.parent.mkdir(parents=True)
    windsurf = box["project"] / ".windsurfrules"
    monkeypatch.setattr(init_module, "_cursor_rules_path", lambda: cursor)
    monkeypatch.setattr(init_module, "_windsurf_rules_path", lambda: windsurf)
    cursor.write_text("---\nalwaysApply: true\n---\n\n" + _CLAUDE_MD_POLICY, encoding="utf-8")
    windsurf = box["project"] / ".windsurfrules"
    windsurf.write_text("# My rules\n\nKeep these.\n\n" + _CLAUDE_MD_POLICY, encoding="utf-8")
    assert init_module.run_init(yes=True, remove_guidance=True) == 0
    assert not init_module._has_policy(cursor)
    assert "Keep these." in windsurf.read_text(encoding="utf-8")
    assert not init_module._has_policy(windsurf)
    names = [p.name for p in box["project"].iterdir()]
    assert any(n != ".windsurfrules" and n.startswith(".windsurfrules") for n in names), names


@pytest.mark.parametrize(
    "claude_md",
    [
        "# My notes",
        "Every Friday, write a weekly digest of merged PRs.",  # "digest" is also a tool name
        None,  # no CLAUDE.md at all
    ],
)
def test_config_check_passes_a_light_install(box, capsys, claude_md):
    """`config --check` must not fail the shipped default: a personal CLAUDE.md
    (or none) without the policy, and only the silent hooks. The exit status is
    what a client reads as a broken install, so it is asserted."""
    from jcodemunch_mcp import server

    if claude_md is not None:
        (box["home"] / ".claude" / "CLAUDE.md").write_text(claude_md, encoding="utf-8")
    assert _init() == 0
    capsys.readouterr()
    code = 0
    try:
        server.main(["config", "--check"])
    except SystemExit as exc:
        code = exc.code or 0
    out = capsys.readouterr().out
    assert code == 0, out
    assert "not mentioned in CLAUDE.md" not in out
    assert "init --policy" in out
    for sub in ("hook-pretooluse", "hook-sessionstart", "hook-subagent-start"):
        assert f"{sub} not installed (opt-in" in out, sub


def test_init_tells_the_user_to_ask_for_it_by_name(box, capsys):
    assert _init() == 0
    assert init_module.ASK_BY_NAME_LINE in capsys.readouterr().out


def test_the_upgrade_refresh_does_not_print_the_ask_by_name_line(box, capsys, monkeypatch):
    monkeypatch.setenv(init_module.UPGRADE_REFRESH_ENV, "1")
    assert _init(hooks=True, hooks_explicit=True) == 0
    assert init_module.ASK_BY_NAME_LINE not in capsys.readouterr().out
