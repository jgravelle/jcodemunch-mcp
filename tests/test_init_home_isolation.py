"""A test must never write the developer's real ~/.claude/settings.json (W-34).

Practice 8 (CLAUDE.md) says a test must never read or write the developer's
real global config, and `tests/test_config_isolation_guard.py` guards the
`load_config()` half. The Claude Code settings half had no guard: five tests
in `tests/test_init.py` call `run_init(yes=True, no_backup=True)` without
redirecting `_settings_json_path`, so the full tier ran
`install_enforcement_hooks` against the real file and `_converge_rule`
rewrote every jcm hook command to whatever `shutil.which` found -- in a git
worktree, that worktree's `.venv`, which was then deleted, and the next
Claude Code session start failed on every product hook
(`docs/workflows/FINDINGS.md` W-34).

The guard runs the offending tests in a SUBPROCESS whose home is a directory
this test owns (Practice 8: a destructive defect is executed on the
non-vacuity pass, against a target the test controls), then asserts the
sentinel settings file is byte-identical and no CLAUDE.md appeared.
The second half of the fix, the conftest tripwire that fails ANY test which
changes the real file, is exercised by the same subprocess: with the redirect
removed it is the tripwire that reports the writer by name.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

OFFENDERS = (
    "test_run_init_explicit_client_none",
    "test_run_init_yes_cursor_writes_rules",
    "test_run_init_yes_windsurf_writes_rules",
    "test_run_init_yes_includes_audit",
)

SENTINEL = (
    '{"hooks": {"PreToolUse": [{"matcher": "Read", "hooks": [{"type": "command", '
    '"command": "/owned/by/this/test/jcodemunch-mcp hook-pretooluse"}]}]}}\n'
)


def test_run_init_tests_leave_the_real_settings_untouched(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    settings = home / ".claude" / "settings.json"
    settings.write_text(SENTINEL, encoding="utf-8")
    env = dict(os.environ)
    env.update({
        "HOME": str(home),
        "USERPROFILE": str(home),
        "CODE_INDEX_PATH": str(tmp_path / "idx"),
        "PYTHONPATH": str(ROOT / "src"),
    })
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "-p", "no:xdist", str(ROOT / "tests" / "test_init.py"),
         "-k", " or ".join(OFFENDERS)],
        cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=600,
    )
    ran = proc.stdout.strip().splitlines()[-1:] if proc.stdout else []
    assert proc.returncode == 0, f"offending tests did not pass: {ran}\n{proc.stdout[-2000:]}"
    assert settings.read_text(encoding="utf-8") == SENTINEL, (
        "a run_init test rewrote the home settings.json (W-34):\n"
        + settings.read_text(encoding="utf-8")[:600]
    )
    assert not (home / ".claude" / "CLAUDE.md").exists(), "a run_init test wrote ~/.claude/CLAUDE.md"
    assert not (home / ".claude" / "settings.json.bak").exists()

