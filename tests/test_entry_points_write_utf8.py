"""A script run on Windows writes UTF-8, not the console code page (L-145, L-281).

On Windows a redirected stdout is cp1252, so a script that prints text it read
from CHANGELOG.md or LEDGER.md raises UnicodeEncodeError on the first
character outside cp1252 (a warning sign, an arrow) and prints nothing.
`scripts/release_notes.py` did that on 1.108.338's block, and the harness's
fast tier lost the tail of a failing run the same way and then wrote an empty
summary. Every entry point under scripts/ and harness/ now sets stdout and
stderr to UTF-8 when it runs as a script, before it runs anything else.

The harness half had a second trigger: any exception in a tier left `rc`
unbound, so the summary write raised UnboundLocalError over the real error
and fast.md stayed empty. `main` now starts from a failing `rc`.

The same rule reaches `.claude/hooks/` and `benchmarks/` (L-320): a benchmark
prints corpus text and a hook prints repository paths and CHANGELOG lines.
"""

import ast
import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _entry_points() -> list[Path]:
    """Every file under scripts/, harness/, .claude/hooks/ and benchmarks/ with a `__main__` guard."""
    # Tracked files only: a gitignored corpus checked out under benchmarks/
    # is someone else's code. An sdist is not a git tree: skip there.
    if not (ROOT / ".git").exists():
        pytest.skip("not a git work tree; the tracked-file list is unknown")
    r = subprocess.run(
        ["git", "ls-files", "-z", "--", "scripts", "harness", ".claude/hooks", "benchmarks"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
    )
    if r.returncode != 0:
        pytest.skip(f"git ls-files failed: {r.stderr.strip()}")
    tracked = [t for t in r.stdout.split("\0") if t]
    out = []
    for rel in sorted(tracked):
        p = ROOT / rel
        if (
            p.suffix == ".py"
            and "__name__" in p.read_text(encoding="utf-8")
            and _main_guard(p) is not None
        ):
            out.append(p)
    return out


def _main_guard(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name)
            and node.test.left.id == "__name__"
        ):
            return node
    return None


def _reconfigured_stream(stmt: ast.stmt) -> "str | None":
    """'stdout' or 'stderr' when stmt is sys.<stream>.reconfigure(encoding="utf-8")."""
    if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)):
        return None
    call = stmt.value
    f = call.func
    if not (
        isinstance(f, ast.Attribute)
        and f.attr == "reconfigure"
        and isinstance(f.value, ast.Attribute)
        and f.value.attr in ("stdout", "stderr")
        and isinstance(f.value.value, ast.Name)
        and f.value.value.id == "sys"
    ):
        return None
    for kw in call.keywords:
        if (
            kw.arg == "encoding"
            and isinstance(kw.value, ast.Constant)
            and kw.value.value == "utf-8"
        ):
            return f.value.attr
    return None


def _reconfigured_first(guard: ast.If) -> bool:
    """True when both streams are set to UTF-8 before the guard runs anything else.

    Order matters: a guard that calls `sys.exit(main())` first and reconfigures
    after it has already printed everything it will print. A guard whose
    whole body is one `try` (deny_guard: a crash must refuse, not allow) may
    make the two calls as the first statements of that `try`.
    """
    body = guard.body
    if len(body) == 1 and isinstance(body[0], ast.Try):
        body = body[0].body
    seen = set()
    for stmt in body:
        stream = _reconfigured_stream(stmt)
        if stream is None:
            break
        seen.add(stream)
    return seen == {"stdout", "stderr"}


def test_the_entry_point_list_is_not_empty():
    # The ratchet below iterates this list; an empty list would pass vacuously.
    names = {p.name for p in _entry_points()}
    assert {"release_notes.py", "__main__.py", "pre_pr.py", "report.py"} <= names, names


def test_every_entry_point_writes_utf8():
    missing = [
        str(p.relative_to(ROOT))
        for p in _entry_points()
        if not _reconfigured_first(_main_guard(p))
    ]
    assert not missing, (
        "set sys.stdout and sys.stderr to UTF-8 as the first statements of the "
        f"__main__ guard of: {missing}"
    )


def _guard_of(src: str) -> ast.If:
    tree = ast.parse(src)
    return next(n for n in tree.body if isinstance(n, ast.If))


def test_the_check_refuses_a_guard_that_reconfigures_after_main():
    late = (
        'if __name__ == "__main__":\n'
        "    sys.exit(main())\n"
        '    sys.stdout.reconfigure(encoding="utf-8")\n'
        '    sys.stderr.reconfigure(encoding="utf-8")\n'
    )
    one = (
        'if __name__ == "__main__":\n'
        '    sys.stdout.reconfigure(encoding="utf-8")\n'
        "    sys.exit(main())\n"
    )
    first = (
        'if __name__ == "__main__":\n'
        '    sys.stdout.reconfigure(encoding="utf-8")\n'
        '    sys.stderr.reconfigure(encoding="utf-8")\n'
        "    sys.exit(main())\n"
    )
    in_try = (
        'if __name__ == "__main__":\n'
        "    try:\n"
        '        sys.stdout.reconfigure(encoding="utf-8")\n'
        '        sys.stderr.reconfigure(encoding="utf-8")\n'
        "        main()\n"
        "    except BaseException:\n"
        "        block()\n"
    )
    split_by_try = (
        'if __name__ == "__main__":\n'
        '    sys.stdout.reconfigure(encoding="utf-8")\n'
        "    try:\n"
        '        sys.stderr.reconfigure(encoding="utf-8")\n'
        "        main()\n"
        "    except BaseException:\n"
        "        block()\n"
    )
    assert not _reconfigured_first(_guard_of(late))
    assert not _reconfigured_first(_guard_of(one))
    assert _reconfigured_first(_guard_of(first))
    assert _reconfigured_first(_guard_of(in_try))
    assert not _reconfigured_first(_guard_of(split_by_try))


def test_a_tier_that_raises_still_writes_a_failing_summary(monkeypatch, tmp_path):
    # L-281's second trigger: `rc` was unbound when the tier raised, so the
    # summary write raised UnboundLocalError over the real error.
    monkeypatch.syspath_prepend(str(ROOT))
    hm = importlib.import_module("harness.__main__")

    def boom(_a):
        raise RuntimeError("the tier's own error")

    monkeypatch.setattr(hm, "_dispatch", boom)
    summary = tmp_path / "fast.md"
    with pytest.raises(RuntimeError, match="the tier's own error"):
        hm.main(["fast", "--summary", str(summary)])
    text = summary.read_text(encoding="utf-8")
    assert "## harness fast: FAIL" in text


def test_a_hook_reads_its_utf8_payload_through_a_cp1252_stdin():
    # Claude Code sends the hook payload as UTF-8. Read through a cp1252 stdin,
    # an arrow arrives as three wrong characters, and 0x81/0x8D/0x8F/0x90/0x9D
    # raise, which every hook but deny_guard turns into exit 1: allow.
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); import _common; "
        "print(ascii(_common.read_hook_input().get('x')))"
    )
    env = dict(os.environ, PYTHONIOENCODING="cp1252")
    payload = '{"x": "a → b Α"}'.encode("utf-8")
    r = subprocess.run(
        [sys.executable, "-c", code, str(ROOT / ".claude" / "hooks")],
        input=payload,
        capture_output=True,
        env=env,
        cwd=ROOT,
        timeout=60,
    )
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")[-2000:]
    assert r.stdout.decode("ascii").strip() == ascii("a → b Α")


def test_release_notes_prints_a_warning_sign_through_a_cp1252_stdout():
    # The 1.108.338 block holds the warning sign that broke the script on 2026-10-09.
    env = dict(os.environ, PYTHONIOENCODING="cp1252")
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "release_notes.py"), "1.108.338", "91"],
        capture_output=True,
        env=env,
        cwd=ROOT,
        timeout=120,
    )
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")[-2000:]
    out = r.stdout.decode("utf-8")
    assert "⚠" in out
