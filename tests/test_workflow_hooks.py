"""The workflow hooks refuse what they exist to refuse (docs/workflows/DESIGN.md section 4).

Each hook is driven with a hook-JSON payload built in code and run as a
subprocess, the way Claude Code runs it. The payloads never travel through a
shell line, because the hooks are LIVE in this repo and a shell command that
contains `gh pr create` would be blocked by the hook under test (FINDINGS
W-8). Every case has a red arm: the same hook with the harmless input passes.
Nothing here runs the harness; H1's fast-tier path is exercised in
VERIFICATION.md, not in the suite (it would run pytest inside pytest).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / ".claude" / "hooks"


def run_hook(name: str, payload: dict, cwd: Path = ROOT) -> tuple[int, str, str]:
    r = subprocess.run(
        [sys.executable, str(HOOKS / name)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=cwd,
        timeout=60,
    )
    return r.returncode, r.stdout, r.stderr


def _working_tree_hooks(clone: Path) -> Path:
    """The clone is of HEAD; the hooks under test are the WORKING TREE's (an uncommitted fix must be what runs)."""
    dst = clone / ".claude" / "hooks"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(HOOKS, dst, ignore=shutil.ignore_patterns("__pycache__"))
    return dst


def bash(command: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


def edit(path: Path) -> dict:
    return {"tool_name": "Edit", "tool_input": {"file_path": str(path)}}


@pytest.mark.parametrize(
    "command,expect_block",
    [
        ("git " + "tag v9.9.9", True),
        ("git " + "tag --list", False),
        ("git push --force origin main", True),
        ("git push origin feat/x", False),
        ("gh " + "release create v9", True),
        ("gh " + "workflow run release.yml -f version=1", True),
        ("gh pr " + "merge 1", True),
        ("gh pr view 1", False),
        ("gh issue " + "comment 1 --body x", True),
        ("gh issue view 1", False),
        ("gh api --method POST repos/x/y", True),
        ("gh api repos/x/y", False),
        ("uvx --from " + "twine twine upload dist/*", True),
        ('"C:\\Users\\j\\mcp-' + 'publisher.exe" publish', True),
    ],
)
def test_deny_guard_refuses_exactly_the_forbidden_verbs(command, expect_block):
    rc, _, err = run_hook("deny_guard.py", bash(command))
    assert (rc == 2) is expect_block, (command, rc, err)
    if expect_block:
        assert "deny_guard: refused" in err and "cmd.exe" in err


def test_pre_pr_refuses_without_a_stamp_and_passes_unrelated_commands(
    tmp_path, monkeypatch
):
    """H4 keys on the tree id (D5): no stamp, wrong tree, failed run, or an unmet row each refuse."""
    rc, _, _ = run_hook("pre_pr.py", bash("git status"))
    assert rc == 0
    # Drive the hook against a scratch clone so the real .claude/state is untouched.
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", "--no-hardlinks", str(ROOT), str(clone)], check=True
    )
    subprocess.run(["git", "checkout", "-q", "-b", "feat/probe"], cwd=clone, check=True)
    hooks = _working_tree_hooks(clone)
    state = clone / ".claude" / "state"
    evidence = state / "evidence"
    evidence.mkdir(parents=True)
    create = bash('GITHUB_TOKEN="" gh pr ' + "create --title x")

    def run():
        r = subprocess.run(
            [sys.executable, str(hooks / "pre_pr.py")],
            input=json.dumps(create),
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=clone,
        )
        return r.returncode, r.stderr

    rc, err = run()
    assert rc == 2 and "no full-tier stamp" in err
    tree = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, r'%s'); import _common; print(_common.tree_id())"
            % hooks,
        ],
        capture_output=True,
        text=True,
        cwd=clone,
    ).stdout.strip()
    (state / "full-tier.json").write_text(
        json.dumps({"tree": "not-this-tree", "ok": True, "date": "x"}), encoding="utf-8"
    )
    rc, err = run()
    assert rc == 2 and "DIFFERENT tree" in err
    (state / "full-tier.json").write_text(
        json.dumps({"tree": tree, "ok": False, "date": "x"}), encoding="utf-8"
    )
    rc, err = run()
    assert rc == 2 and "FAILED" in err
    (state / "full-tier.json").write_text(
        json.dumps({"tree": tree, "ok": True, "date": "x"}), encoding="utf-8"
    )
    rc, err = run()
    assert rc == 2 and "checklist" in err
    (evidence / "checklist.md").write_text("| 1 | x | unmet | y |\n", encoding="utf-8")
    rc, err = run()
    assert rc == 2 and "unmet" in err
    (evidence / "checklist.md").write_text("| 1 | x | met | y |\n", encoding="utf-8")
    rc, err = run()
    assert rc == 0, err
    # Rename the probe branch to `main` (a checkout of the real main would drop the hooks on a branch that predates them).
    subprocess.run(["git", "branch", "-M", "main"], cwd=clone, check=True)
    rc, err = run()
    assert rc == 2 and "main" in err


def test_test_edit_guard_blocks_a_skip_on_a_load_bearing_test(tmp_path):
    """H2: a skip mark on a LOAD-BEARING file with no retired.json change blocks; a src/ edit is ignored; the red arm is the same file unweakened."""
    rc, _, _ = run_hook("test_edit_guard.py", edit(ROOT / "src" / "x.py"))
    assert rc == 0
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", "--no-hardlinks", str(ROOT), str(clone)], check=True
    )
    hooks = _working_tree_hooks(clone)
    target = clone / "tests" / "test_result_cache.py"
    original = target.read_text(encoding="utf-8")
    payload = edit(target)

    def run():
        r = subprocess.run(
            [sys.executable, str(hooks / "test_edit_guard.py")],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=clone,
        )
        return r.returncode, r.stdout, r.stderr

    rc, out, err = run()
    assert rc == 0 and not out, "unweakened file must pass silently"
    target.write_text(
        "import pytest\npytestmark = pytest.mark.skip(reason='probe')\n" + original,
        encoding="utf-8",
    )
    rc, out, err = run()
    assert rc == 2 and "LOAD-BEARING" in err and "retired.json" in err, err
    (clone / "harness" / "retired.json").write_text(
        (clone / "harness" / "retired.json").read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    rc, out, err = run()
    assert rc == 0 and "skip/xfail" in out, (
        "with the ledger touched it warns instead of blocking"
    )


def test_surface_guard_is_silent_when_the_surface_did_not_move():
    """H3 on an untouched server.py: no surface change, no output. (A moved surface is exercised in VERIFICATION.md.)"""
    rc, out, err = run_hook(
        "surface_guard.py", edit(ROOT / "src" / "jcodemunch_mcp" / "server.py")
    )
    assert rc == 0, err
    assert "differs" not in out, out


def test_hooks_follow_the_session_cwd_into_a_worktree(tmp_path):
    """W-30: the MAIN checkout's hook, told the session is in another checkout, reads THAT checkout's state."""
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", "--no-hardlinks", str(ROOT), str(clone)], check=True
    )
    subprocess.run(["git", "checkout", "-q", "-b", "feat/wt"], cwd=clone, check=True)
    _working_tree_hooks(clone)
    # The main checkout may carry a valid stamp; the clone has none.
    payload = bash('GITHUB_TOKEN="" gh pr ' + "create --title x")
    payload["cwd"] = str(clone)
    r = subprocess.run(
        [sys.executable, str(HOOKS / "pre_pr.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=clone,
    )
    assert r.returncode == 2 and "no full-tier stamp" in r.stderr, r.stderr
    # Red arm: with the clone's stamp present and matching, the same hook passes on the clone's checklist.
    state = clone / ".claude" / "state"
    (state / "evidence").mkdir(parents=True)
    tree = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, r'%s'); import _common; print(_common.tree_id())"
            % (clone / ".claude" / "hooks"),
        ],
        capture_output=True,
        text=True,
        cwd=clone,
    ).stdout.strip()
    (state / "full-tier.json").write_text(
        json.dumps({"tree": tree, "ok": True, "date": "x"}), encoding="utf-8"
    )
    (state / "evidence" / "checklist.md").write_text(
        "| 1 | x | met | y |\n", encoding="utf-8"
    )
    r = subprocess.run(
        [sys.executable, str(HOOKS / "pre_pr.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=clone,
    )
    assert r.returncode == 0, r.stderr
