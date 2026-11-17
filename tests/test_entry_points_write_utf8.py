"""A script run on Windows writes UTF-8, not the console code page (L-145, L-281).

On Windows a redirected stdout is cp1252, so a script that prints text it read
from CHANGELOG.md or LEDGER.md raises UnicodeEncodeError on the first
character outside cp1252 (a warning sign, an arrow) and prints nothing.
`scripts/release_notes.py` did that on 1.108.338's block, and the harness's
fast tier lost the tail of a failing run the same way and then wrote an empty
summary. Every entry point under scripts/ and harness/ now sets stdout and
stderr to UTF-8 when it runs as a script.
"""

import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _entry_points() -> list[Path]:
    """Every file under scripts/ and harness/ with a `__main__` guard."""
    out = []
    for d in ("scripts", "harness"):
        for p in sorted((ROOT / d).rglob("*.py")):
            if (
                "__name__" in p.read_text(encoding="utf-8")
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


def _reconfigures(guard: ast.If, stream: str) -> bool:
    """True when the guard calls sys.<stream>.reconfigure(encoding="utf-8")."""
    for node in ast.walk(guard):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        f = node.func
        if not (
            f.attr == "reconfigure"
            and isinstance(f.value, ast.Attribute)
            and f.value.attr == stream
            and isinstance(f.value.value, ast.Name)
            and f.value.value.id == "sys"
        ):
            continue
        for kw in node.keywords:
            if (
                kw.arg == "encoding"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value == "utf-8"
            ):
                return True
    return False


def test_the_entry_point_list_is_not_empty():
    # The ratchet below iterates this list; an empty list would pass vacuously.
    names = {p.name for p in _entry_points()}
    assert {"release_notes.py", "__main__.py"} <= names, names


def test_every_entry_point_writes_utf8():
    missing = [
        str(p.relative_to(ROOT))
        for p in _entry_points()
        if not (
            _reconfigures(_main_guard(p), "stdout")
            and _reconfigures(_main_guard(p), "stderr")
        )
    ]
    assert not missing, (
        f"set sys.stdout and sys.stderr to UTF-8 in the __main__ guard of: {missing}"
    )


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
