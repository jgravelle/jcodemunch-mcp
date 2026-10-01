"""A Unicode escape at a call site names the identifier it spells (LEDGER L-86).

Java translates Unicode escapes (a backslash, one or more `u`, four hex
digits) before it lexes (JLS 3.3), so `A.` + escape(`f`) + `ile()` is
`A.file()`. C# accepts the same escape inside an identifier, and its
eight-digit `U` form too (ECMA-334 6.4.3). The reference search compared the
raw line, never saw the call, and with no importer edge `check_delete_safe`
read that as no reference: a used method graded `safe_to_delete` at 1.0.

The fix decodes escapes on both sides of the comparison L-84 folds, which only
adds matches, the conservative direction for every consumer. The fixture is
compiled and run with `javac` when one is on PATH, so each pair is proven to
name one method rather than assumed to.

⚠⚠ Every escape below is BUILT from `chr(92)` and never written literally.
A literal escape in this file was decoded before it reached disk -- by the
shell's `printf` in one draft and by the editing tool in the next -- and the
"escaped" call became a plain `A.file()` that main already finds. `_written`
asserts the backslash survived into the fixture, so that cannot pass silently
again.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from jcodemunch_mcp.tools.check_delete_safe import check_delete_safe
from jcodemunch_mcp.tools.check_references import check_references
from jcodemunch_mcp.tools.index_folder import index_folder

_ABSENCE = ("safe_to_delete", "internal_only", "test_coverage_only")
BS = chr(92)


def esc(ch: str, *, us: int = 1) -> str:
    """The Java/C# short escape for one character: backslash, `us` u's, 4 hex."""
    return BS + "u" * us + format(ord(ch), "04x")


def long_esc(ch: str) -> str:
    """The C# long escape: backslash, U, 8 hex."""
    return BS + "U" + format(ord(ch), "08x")


_A = "class A { static int file() { return 1; } }\n"

# Call spellings written in B.java, each naming A.file to javac.
_CALLS = [
    pytest.param("A." + esc("f") + "ile()", id="first-letter"),
    pytest.param("A." + "".join(esc(c) for c in "file") + "()", id="every-letter"),
    pytest.param("A." + esc("f", us=3) + "ile()", id="repeated-u"),
    pytest.param("A.fil" + esc("e") + "()", id="last-letter"),
    # `l` is 6c; Java and C# accept the hex digit in either case.
    pytest.param("A.fi" + esc("l").upper().replace(BS + "U", BS + "u") + "e()", id="upper-hex"),
]


def _b(call: str) -> str:
    return "class B { public static void main(String[] a) { System.out.println(" + call + "); } }\n"


def _written(tmp_path, files):
    for rel, text in files.items():
        (tmp_path / rel).write_text(text, encoding="utf-8")
    on_disk = (tmp_path / "B.java").read_text(encoding="utf-8") if "B.java" in files else ""
    if "B.java" in files:
        assert BS in on_disk, "the escape was decoded before it reached the file"
    storage = str(tmp_path / "idx")
    out = index_folder(path=str(tmp_path), use_ai_summaries=False, storage_path=storage)
    return out["repo"], storage


def _javac_runs(tmp_path) -> None:
    """Compile OUTSIDE the indexed tree: a build dir written into it makes the
    index stale, and the preflight then refuses for that reason instead."""
    if not shutil.which("javac") or not shutil.which("java"):
        return  # the JLS rule stands without it; the compile is the proof when present
    build = tmp_path.parent / (tmp_path.name + "-javac")
    build.mkdir()
    subprocess.run(
        ["javac", "-d", str(build), str(tmp_path / "A.java"), str(tmp_path / "B.java")],
        check=True,
        capture_output=True,
    )
    out = subprocess.run(["java", "-cp", str(build), "B"], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "1", "the pair must name one method, or the fixture proves nothing"


@pytest.mark.parametrize("call", _CALLS)
def test_an_escaped_call_is_a_reference(tmp_path, call):
    repo, sp = _written(tmp_path, {"A.java": _A, "B.java": _b(call)})
    _javac_runs(tmp_path)
    got = check_references(repo, identifier="file", storage_path=sp)
    assert "B.java" in {f["file"] for f in got.get("content_references", [])}, got


def test_the_escaped_caller_blocks_the_delete(tmp_path):
    repo, sp = _written(tmp_path, {"A.java": _A, "B.java": _b("A." + esc("f") + "ile()")})
    _javac_runs(tmp_path)
    got = check_delete_safe(repo, "file", storage_path=sp)
    assert got["verdict"] not in _ABSENCE, (got["verdict"], got.get("confidence"))


def test_a_csharp_long_escape_is_a_reference(tmp_path):
    src = "class B { int Go() { return A." + long_esc("f") + "ile(); } }\n"
    assert BS in src
    repo, sp = _written(
        tmp_path, {"A.cs": "class A { public static int file() { return 1; } }\n", "B.cs": src}
    )
    got = check_references(repo, identifier="file", storage_path=sp)
    assert "B.cs" in {f["file"] for f in got.get("content_references", [])}, got


def _node_runs(tmp_path, rel: str) -> None:
    """Run the JS fixture OUTSIDE the indexed tree, so the index stays fresh."""
    if not shutil.which("node"):
        return
    copy = tmp_path.parent / (tmp_path.name + "-node")
    copy.mkdir()
    (copy / rel).write_text((tmp_path / rel).read_text(encoding="utf-8"), encoding="utf-8")
    out = subprocess.run(["node", str(copy / rel)], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "1", "the pair must name one function, or the fixture proves nothing"


def test_a_javascript_braced_escape_blocks_the_delete(tmp_path):
    """Review: ECMAScript's braced escape also names the identifier, and the
    first fix left `check_delete_safe` at `safe_to_delete` 1.0 for it."""
    call = BS + "u{66}ile()"
    src = "function file() { return 1; }\nfunction main() { return " + call + "; }\nconsole.log(main());\n"
    repo, sp = _written(tmp_path, {"a.js": src})
    assert BS in (tmp_path / "a.js").read_text(encoding="utf-8")
    _node_runs(tmp_path, "a.js")
    refs = check_references(repo, identifier="file", storage_path=sp)
    lines = [m["line"] for f in refs.get("content_references", []) for m in f["matches"]]
    assert 2 in lines, refs
    got = check_delete_safe(repo, "file", storage_path=sp)
    assert got["verdict"] not in _ABSENCE, (got["verdict"], got.get("confidence"))


def test_two_escaped_surrogates_are_one_character():
    """Java spells an astral character as two escaped surrogates. Joined, the
    mathematical bold `f` folds (NFKC) to `f`; left apart, nothing matches."""
    from jcodemunch_mcp.tools.check_references import _fold

    pair = BS + "u" + "d835" + BS + "u" + "dc1f"
    assert "file" in _fold(pair + "ile()")


def test_a_lone_escaped_surrogate_does_not_eat_its_neighbour():
    from jcodemunch_mcp.tools.check_references import _fold

    assert "file" in _fold(BS + "u" + "d800" + "file()")


def test_an_escape_of_another_letter_is_not_a_reference(tmp_path):
    """Control: decoding must not invent a match. escape(`g`) + `ile` is `gile`."""
    repo, sp = _written(tmp_path, {"A.java": _A, "B.java": _b("A." + esc("g") + "ile()")})
    got = check_references(repo, identifier="file", storage_path=sp)
    assert "B.java" not in {f["file"] for f in got.get("content_references", [])}, got
