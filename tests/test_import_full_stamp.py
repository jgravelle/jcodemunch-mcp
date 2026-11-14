"""A full tier run on another machine counts here only for THIS tree.

Heavy full tiers run on a second machine while this one runs a benchmark, and
pre_pr reads only this checkout's `.claude/state/full-tier.json`. Copying the
other machine's files over by hand would also work, and would also install a
stamp for some other tree, a red one, or a summary from another run, without a
word. The import refuses all three and marks where an imported stamp came from
so a refusal later names it.

The premise is that `tree_id` hashes git blob content, so one commit has one
id whatever a checkout's line-ending setting. That is pinned below over
core.autocrlf true and false, with a check that the two checkouts' bytes on
disk really differ.

The summary fixtures follow the real `harness full --summary` layout: a
5-column threshold table, a 3-column check table, and the `xdist workers:`
line run_full appends. Floor and observed values are placeholders; the
verdict reads only the id and the verdict column.
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


def _summary(verdict: str, rows: list[tuple[str, str]], pytest_detail: str, pytest_verdict: str, workers: str = "4") -> str:
    lines = [
        f"## harness full: {verdict}",
        "",
        "| threshold | criterion | floor | observed | verdict |",
        "|---|---|---|---|---|",
    ]
    lines += [f"| `{tid}` | N1 | x | y | {v} |" for tid, v in rows]
    lines += [
        "",
        "| check | detail | verdict |",
        "|---|---|---|",
        "| `check.assets_warm` | tiktoken asset | PASS |",
        f"| `check.pytest_exit` | {pytest_detail} | {pytest_verdict} |",
        "",
        "```",
        "```",
        "",
        f"xdist workers: {workers}",
        "",
    ]
    return "\n".join(lines)


PASS_MD = _summary("PASS", [("coverage.min", "PASS")], "exit 0: 10 passed, 2 skipped", "PASS")
TIMING_ONLY_MD = _summary(
    "FAIL", [("coverage.min", "PASS"), ("suite.full_seconds", "**FAIL**")], "exit 0: 10 passed, 2 skipped", "PASS"
)
RED_MD = _summary("FAIL", [("coverage.min", "PASS")], "exit 1: 2 failed, 8 passed, 2 skipped", "**FAIL**")
FAIL_NO_ROW_MD = _summary("FAIL", [("coverage.min", "PASS")], "exit 0: 10 passed", "PASS")


def _clone(dst: Path, autocrlf: str | None = None, *, with_these_hooks: bool = True) -> Path:
    cfg = ["-c", f"core.autocrlf={autocrlf}"] if autocrlf else []
    subprocess.run(["git", "clone", "-q", "--no-hardlinks", *cfg, str(ROOT), str(dst)], check=True)
    subprocess.run(["git", "checkout", "-q", "-b", "feat/x"], cwd=dst, check=True)
    if not with_these_hooks:
        return dst
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


def _green(tree: str, **extra) -> dict:
    return {"tree": tree, "ok": True, "date": "d", "commit": "c", "workers": "4", **extra}


def test_one_commit_has_one_tree_id_whatever_the_line_ending_setting(tmp_path):
    # The clones keep their own committed hooks: copying this checkout's files
    # in would add an uncommitted change, and an uncommitted CRLF file IS
    # different content under autocrlf=false. The property is about a commit.
    crlf = _clone(tmp_path / "crlf", autocrlf="true", with_these_hooks=False)
    lf = _clone(tmp_path / "lf", autocrlf="false", with_these_hooks=False)
    # Non-vacuity: the two checkouts really hold different bytes on disk.
    assert b"\r\n" in (crlf / "pyproject.toml").read_bytes()
    assert b"\r\n" not in (lf / "pyproject.toml").read_bytes()
    assert _tree(crlf) == _tree(lf)


def test_a_green_stamp_for_this_tree_is_installed_and_marked(checkout, tmp_path):
    co, tree = checkout
    _clear(co)
    stale = co / ".claude" / "state" / "evidence" / "full-failures.txt"
    stale.parent.mkdir(parents=True)
    stale.write_text("tracebacks from an older red run", encoding="utf-8")
    s, m = _remote(tmp_path, _green(tree, platform="darwin"), PASS_MD)
    r = _import(co, s, m)
    assert r.returncode == 0, r.stderr
    got = json.loads(_local_stamp(co).read_text(encoding="utf-8"))
    assert got["tree"] == tree and got["ok"] is True
    assert got["imported_from"]["platform"] == "darwin"
    assert (co / ".claude" / "state" / "evidence" / "full.md").read_text(encoding="utf-8") == PASS_MD
    assert not stale.exists()


def test_timing_only_failures_are_accepted(checkout, tmp_path):
    # CI judges timing rows (jjg, 2026-10-16), the same rule run_full applies.
    co, tree = checkout
    _clear(co)
    s, m = _remote(tmp_path, _green(tree, timing_deferred_to_ci=["suite.full_seconds"]), TIMING_ONLY_MD)
    r = _import(co, s, m)
    assert r.returncode == 0, r.stderr
    assert json.loads(_local_stamp(co).read_text(encoding="utf-8"))["imported_from"]["platform"] == "unknown"


@pytest.mark.parametrize(
    "stamp_of, full_md, why",
    [
        (lambda t: _green("0" * 40), PASS_MD, "different tree"),
        (lambda t: {**_green(t), "ok": False}, PASS_MD, "not green"),
        (lambda t: _green("unreadable-x"), PASS_MD, "could not be read"),
        (lambda t: _green(t), None, "no full.md"),
        (lambda t: _green(t), "some other file\n", "not a full-tier summary"),
        (lambda t: _green(t), RED_MD, "non-timing failure"),
        (lambda t: _green(t), FAIL_NO_ROW_MD, "FAIL with no failing row"),
        # The summary of another run: deferred rows or worker cap disagree.
        (lambda t: _green(t), TIMING_ONLY_MD, "does not belong to the stamp"),
        (lambda t: _green(t, workers="8"), PASS_MD, "does not belong to the stamp"),
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
    assert not (co / ".claude" / "state" / "evidence" / "full.md").exists()


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
