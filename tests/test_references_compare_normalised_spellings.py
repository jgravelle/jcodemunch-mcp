"""The reference search compares identifiers the way Python does (LEDGER L-84).

`check_references` found a use by testing whether the identifier appeared in a
line, case-insensitively, byte for byte. Python normalises identifiers to NFKC,
so `def file()` called as `ﬁle()` (the fi ligature) is one function called
once, and the search never saw the call. `check_delete_safe` reads that search
for its "no reference" evidence, and on main it graded the used function
`safe_to_delete` at confidence 1.0.

The fix compares NFKC-folded text on both sides of a line or import match,
which only widens what counts as a reference, the conservative direction for
every consumer. The definition-span EXCLUSION is never folded: it removes
matches, and in Java `\ufb01le()` and `file()` are two methods (review, L-84).
Every Python pair below is run under Python first, so each names one function.
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from jcodemunch_mcp.tools.check_delete_safe import check_delete_safe
from jcodemunch_mcp.tools.check_references import check_references
from jcodemunch_mcp.tools.index_folder import index_folder

_ABSENCE = ("safe_to_delete", "internal_only", "test_coverage_only")

# (declared spelling, call spelling), each naming ONE Python function.
_PAIRS = [
    pytest.param("file", "ﬁle", id="ascii-definition-ligature-call"),
    pytest.param("foo", "\U0001d41foo", id="ascii-definition-math-bold-call"),
    pytest.param("café", "café", id="composed-definition-decomposed-call"),
    pytest.param("café", "café", id="decomposed-definition-composed-call"),
    pytest.param("café", "ｃafé", id="fullwidth-call"),
]


def _repo(tmp_path, files):
    for rel, text in files.items():
        (tmp_path / rel).write_text(text, encoding="utf-8")
    storage = str(tmp_path / "idx")
    return index_folder(path=str(tmp_path), use_ai_summaries=False, storage_path=storage)["repo"], storage


def _runs(path):
    out = subprocess.run([sys.executable, str(path)], capture_output=True, text=True, encoding="utf-8")
    return out.stdout.strip()


@pytest.mark.parametrize("declared,called", _PAIRS)
def test_a_call_in_another_spelling_is_a_reference(tmp_path, declared, called):
    src = f"def {declared}():\n    return 1\n\ndef go():\n    return {called}()\n\nprint(go())\n"
    repo, sp = _repo(tmp_path, {"m.py": src})
    assert _runs(tmp_path / "m.py") == "1", "the pair must name one function, or the fixture proves nothing"
    got = check_references(repo, identifier=declared, storage_path=sp)
    assert got["is_referenced"] is True, got
    lines = [m["line"] for f in got["content_references"] for m in f["matches"]]
    assert 5 in lines, lines
    assert 1 not in lines, "the definition's own line is not a reference to itself"


def test_the_live_case_is_not_certified_deletable(tmp_path):
    """The shape L-84 measured on main: an ASCII definition, which the name gate
    admits, called under a ligature in the same file."""
    src = "def file():\n    return 1\n\ndef go():\n    return ﬁle()\n\nprint(go())\n"
    repo, sp = _repo(tmp_path, {"m.py": src})
    assert _runs(tmp_path / "m.py") == "1"
    got = check_delete_safe(repo, "file", storage_path=sp)
    assert got["verdict"] not in _ABSENCE, (got["verdict"], got["confidence"])


def test_an_import_under_another_spelling_is_a_reference(tmp_path):
    repo, sp = _repo(tmp_path, {
        "a.py": "def file():\n    return 1\n",
        "b.py": "from a import ﬁle\n\nprint(ﬁle())\n",
    })
    got = check_references(repo, identifier="file", search_content=False, storage_path=sp)
    assert got["is_referenced"] is True, got
    assert got["import_references"], got


def test_a_sibling_whose_name_folds_to_the_target_keeps_its_call(tmp_path):
    """Java does not normalise identifiers: `\ufb01le()` and `file()` are two
    methods, and the call to `file()` sits inside `\ufb01le`'s body. A folded
    exclusion skipped that body as `file`'s own definition."""
    src = (
        "public class A {\n"
        "    public int \ufb01le() {\n"
        "        return file();\n"
        "    }\n"
        "    private int file() {\n"
        "        return 1;\n"
        "    }\n"
        "}\n"
    )
    repo, sp = _repo(tmp_path, {"A.java": src})
    got = check_references(repo, identifier="file", storage_path=sp)
    lines = [m["line"] for f in got["content_references"] for m in f["matches"]]
    assert 3 in lines, got
    verdict = check_delete_safe(repo, "file", storage_path=sp)["verdict"]
    assert verdict not in _ABSENCE, verdict


def test_control_a_different_name_is_still_not_a_reference(tmp_path):
    """Folding must not make every name match: `fire` is not `file`."""
    repo, sp = _repo(tmp_path, {"m.py": "def file():\n    return 1\n\ndef fire():\n    return 2\n\nfire()\n"})
    got = check_references(repo, identifier="file", storage_path=sp)
    assert got["is_referenced"] is False, got


def test_a_full_page_of_test_mentions_does_not_hide_a_real_caller(tmp_path):
    """LEDGER L-89 (L-84 review): `check_delete_safe` read `check_references`
    capped at 20 files and classified from that page alone, so twenty test files
    that merely mention the name pushed the one real caller off the page and the
    verdict fell to `test_coverage_only`. Folding (L-84) adds a new way to fill
    the page; the raw spelling below shows the page was already the defect."""
    files = {"zz_lib.py": "def file():\n    return 1\n\nprint(file())\n"}
    (tmp_path / "tests").mkdir()
    for i in range(20):
        files[f"tests/test_{i:02d}.py"] = "# see the file helper\n"
    repo, sp = _repo(tmp_path, files)
    got = check_delete_safe(repo, "file", storage_path=sp)
    assert got["verdict"] not in _ABSENCE, (got["verdict"], [b.get("file") for b in got["blockers"]])
