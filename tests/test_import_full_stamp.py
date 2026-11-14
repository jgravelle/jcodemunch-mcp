"""A full tier run on another machine counts here only for THIS tree.

Heavy full tiers run on a second machine while this one runs a benchmark, and
pre_pr reads only this checkout's `.claude/state/full-tier.json`. Copying the
other machine's files over by hand would also work, and would also install a
stamp for some other tree or a red one without a word. The import refuses both,
and marks where an imported stamp came from so a refusal later names it.

`tree_id` hashes git blob content, so the same commit has the same id on any
machine whose checkout normalises line endings the same way.
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

PASS_MD = "## harness full: PASS\n\nHARNESS PASS\n"
TIMING_ONLY_MD = (
    "## harness full: FAIL\n\n"
    "| `suite.full_seconds` | N1 | <= 360 | 636.12 | **FAIL** |\n"
)
REAL_FAIL_MD = (
    "## harness full: FAIL\n\n"
    "| `check.pytest_exit` | 1 | == 0 | 1 | **FAIL** |\n"
)


def _clone(dst: Path) -> Path:
    subprocess.run(["git", "clone", "-q", "--no-hardlinks", str(ROOT), str(dst)], check=True)
    subprocess.run(["git", "checkout", "-q", "-b", "feat/x"], cwd=dst, check=True)
    hooks = dst / ".claude" / "hooks"
    if hooks.exists():
        shutil.rmtree(hooks)
    shutil.copytree(HOOKS, hooks, ignore=shutil.ignore_patterns("__pycache__"))
    return dst


def _tree(checkout: Path) -> str:
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, r'%s'); import _common; print(_common.tree_id())"
            % (checkout / ".claude" / "hooks"),
        ],
        capture_output=True,
        text=True,
        cwd=checkout,
        check=True,
    ).stdout.strip()


@pytest.fixture(scope="module")
def checkout(tmp_path_factory):
    co = _clone(tmp_path_factory.mktemp("imp") / "co")
    return co, _tree(co)


def _remote(tmp_path: Path, stamp: dict, full_md: str | None) -> tuple[Path, Path]:
    d = tmp_path / "from-mac"
    d.mkdir()
    s = d / "full-tier.json"
    s.write_text(json.dumps(stamp), encoding="utf-8")
    m = d / "full.md"
    if full_md is not None:
        m.write_text(full_md, encoding="utf-8")
    return s, m


def _import(checkout: Path, stamp: Path, full_md: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(checkout / ".claude" / "hooks" / "import_full_stamp.py"), str(stamp), str(full_md)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=checkout,
    )


def _clear(checkout: Path) -> None:
    state = checkout / ".claude" / "state"
    if state.exists():
        shutil.rmtree(state)


def _local_stamp(checkout: Path) -> Path:
    return checkout / ".claude" / "state" / "full-tier.json"


def test_a_green_stamp_for_this_tree_is_installed_and_marked(checkout, tmp_path):
    co, tree = checkout
    _clear(co)
    s, m = _remote(tmp_path, {"tree": tree, "ok": True, "date": "d", "platform": "darwin"}, PASS_MD)
    r = _import(co, s, m)
    assert r.returncode == 0, r.stderr
    got = json.loads(_local_stamp(co).read_text(encoding="utf-8"))
    assert got["tree"] == tree and got["ok"] is True
    assert got["imported_from"]["platform"] == "darwin"
    assert (co / ".claude" / "state" / "evidence" / "full.md").read_text(encoding="utf-8") == PASS_MD


def test_timing_only_failures_are_accepted(checkout, tmp_path):
    # CI judges timing rows (jjg, 2026-10-16), the same rule run_full applies.
    co, tree = checkout
    _clear(co)
    s, m = _remote(tmp_path, {"tree": tree, "ok": True, "date": "d"}, TIMING_ONLY_MD)
    r = _import(co, s, m)
    assert r.returncode == 0, r.stderr
    assert json.loads(_local_stamp(co).read_text(encoding="utf-8"))["imported_from"]["platform"] == "unknown"


@pytest.mark.parametrize(
    "stamp_of, full_md, why",
    [
        (lambda t: {"tree": "0" * 40, "ok": True}, PASS_MD, "different tree"),
        (lambda t: {"tree": t, "ok": False}, PASS_MD, "not green"),
        (lambda t: {"tree": "unreadable-x", "ok": True}, PASS_MD, "could not be read"),
        (lambda t: {"tree": t, "ok": True}, None, "no full.md"),
        (lambda t: {"tree": t, "ok": True}, "some other file\n", "not a full-tier summary"),
        (lambda t: {"tree": t, "ok": True}, REAL_FAIL_MD, "non-timing failure"),
    ],
)
def test_a_stamp_that_cannot_count_is_refused_and_the_local_one_is_kept(
    checkout, tmp_path, stamp_of, full_md, why
):
    co, tree = checkout
    _clear(co)
    local = _local_stamp(co)
    local.parent.mkdir(parents=True, exist_ok=True)
    before = json.dumps({"tree": "local", "ok": False, "date": "keep"})
    local.write_text(before, encoding="utf-8")
    s, m = _remote(tmp_path, stamp_of(tree), full_md)
    r = _import(co, s, m)
    assert r.returncode != 0, (why, r.stdout)
    assert why in r.stderr, (why, r.stderr)
    assert local.read_text(encoding="utf-8") == before


def test_pre_pr_names_an_imported_stamp_when_it_refuses(checkout):
    co, _ = checkout
    _clear(co)
    ev = co / ".claude" / "state" / "evidence"
    ev.mkdir(parents=True)
    _local_stamp(co).write_text(
        json.dumps({"tree": "0" * 40, "ok": True, "date": "d", "imported_from": {"platform": "darwin"}}),
        encoding="utf-8",
    )
    payload = {"tool_name": "Bash", "tool_input": {"command": "gh pr create --title x --body-file b.md"}, "cwd": str(co)}
    r = subprocess.run(
        [sys.executable, str(co / ".claude" / "hooks" / "pre_pr.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=co,
    )
    assert r.returncode == 2 and "DIFFERENT tree" in r.stderr and "imported from darwin" in r.stderr, r.stderr


def test_run_full_records_the_platform_it_ran_on():
    sys.path.insert(0, str(HOOKS))
    try:
        import run_full
    finally:
        sys.path.remove(str(HOOKS))
    head = run_full.stamp_head("t", "c", "4")
    assert head["platform"] == sys.platform
    assert head["ok"] is False and head["tree"] == "t" and head["workers"] == "4"
