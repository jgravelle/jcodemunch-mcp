"""A script under `scripts/` writes UTF-8 whatever the console's codepage (L-145).

`scripts/release_notes.py` ended with `sys.stdout.write(notes)` and set no
encoding. On Windows a redirected stdout is cp1252, so a CHANGELOG block
holding a character outside it (the 1.108.338 block holds the warning sign)
raised `UnicodeEncodeError` and wrote nothing. `release.yml` runs it on Ubuntu,
where stdout is UTF-8, so only `/release`'s local step broke.

Every script here prints text read from the repository (CHANGELOG, ledgers,
tool descriptions, check names), so the rule is for all of them, not the one
that failed: each `__main__` block sets stdout and stderr to UTF-8 before it
runs `main`.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = sorted((ROOT / "scripts").glob("*.py"))


def _main_block(tree: ast.Module):
    for node in tree.body:
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name)
            and node.test.left.id == "__name__"
        ):
            return node
    return None


def _reconfigured_streams(block: ast.If) -> set[str]:
    """Which of sys.stdout / sys.stderr the block reconfigures to UTF-8."""
    found = set()
    for node in ast.walk(block):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "reconfigure":
            continue
        target = node.func.value
        if not (
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "sys"
            and target.attr in ("stdout", "stderr")
        ):
            continue
        for kw in node.keywords:
            if (
                kw.arg == "encoding"
                and isinstance(kw.value, ast.Constant)
                and str(kw.value.value).lower().replace("-", "") == "utf8"
            ):
                found.add(target.attr)
    return found


def _missing(source: str) -> set[str]:
    block = _main_block(ast.parse(source))
    if block is None:
        return set()
    return {"stdout", "stderr"} - _reconfigured_streams(block)


def test_the_scripts_are_found():
    assert len(SCRIPTS) >= 10, [p.name for p in SCRIPTS]


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_every_script_main_block_writes_utf8(path):
    assert not _missing(path.read_text(encoding="utf-8")), (
        f"{path.name}: its __main__ block must call "
        "sys.stdout.reconfigure(encoding='utf-8') and the same on sys.stderr"
    )


def test_the_check_refuses_a_main_block_without_the_call():
    plain = 'import sys\ndef main():\n    print("x")\nif __name__ == "__main__":\n    sys.exit(main())\n'
    assert _missing(plain) == {"stdout", "stderr"}
    outside = (
        'import sys\nsys.stdout.reconfigure(encoding="utf-8")\n'
        'sys.stderr.reconfigure(encoding="utf-8")\n'
        'if __name__ == "__main__":\n    pass\n'
    )
    assert _missing(outside) == {"stdout", "stderr"}, "a call outside the block does not count"
    wrong = (
        'import sys\nif __name__ == "__main__":\n'
        '    sys.stdout.reconfigure(encoding="cp1252")\n'
        '    sys.stderr.reconfigure(errors="replace")\n'
    )
    assert _missing(wrong) == {"stdout", "stderr"}


def test_release_notes_write_a_non_cp1252_block_through_a_cp1252_stdout():
    """L-145 as reported, on any platform: PYTHONIOENCODING forces the codepage
    a Windows redirect gives, and 1.108.338's block holds the warning sign."""
    env = dict(os.environ, PYTHONIOENCODING="cp1252")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "release_notes.py"), "1.108.338", "91"],
        cwd=str(ROOT), env=env, capture_output=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")[-2000:]
    out = proc.stdout.decode("utf-8").replace("\r\n", "\n")
    assert "\u26a0" in out
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import release_notes
    finally:
        sys.path.pop(0)
    expected = release_notes.render(
        "1.108.338", "91",
        (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"),
        (ROOT / "pyproject.toml").read_text(encoding="utf-8"),
    )
    assert out == expected + "\n", "the written notes must be the rendered notes, byte for byte"
