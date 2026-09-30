"""A differently-cased sibling's body is not the target's definition (LEDGER L-88).

`check_references` excludes the definition's own lines from its content search,
so a declaration is not counted as a use of itself (#406). It chose those lines
by matching every symbol's name to the identifier CASE-INSENSITIVELY. In a
case-sensitive language that names a different symbol: Java `File()` beside
`file()` is two methods, and the call to `file()` inside `File()`'s body was
skipped as `file`'s own definition. `check_delete_safe` then graded the used
`file` `safe_to_delete` at confidence 1.0, on main.

The exclusion now matches the declared spelling exactly. In a case-insensitive
language that errs toward counting a differently-cased declaration as a
reference, which blocks a delete and never licenses one.
"""
from __future__ import annotations

from jcodemunch_mcp.tools.check_delete_safe import check_delete_safe
from jcodemunch_mcp.tools.check_references import check_references
from jcodemunch_mcp.tools.index_folder import index_folder

_ABSENCE = ("safe_to_delete", "internal_only", "test_coverage_only")

_JAVA = (
    "public class A {\n"
    "    public int File() {\n"
    "        return file();\n"
    "    }\n"
    "    private int file() {\n"
    "        return 1;\n"
    "    }\n"
    "}\n"
)


def _repo(tmp_path, files):
    for rel, text in files.items():
        (tmp_path / rel).write_text(text, encoding="utf-8")
    storage = str(tmp_path / "idx")
    return index_folder(path=str(tmp_path), use_ai_summaries=False, storage_path=storage)["repo"], storage


def _lines(result):
    return [m["line"] for f in result.get("content_references", []) for m in f["matches"]]


def test_a_call_inside_a_differently_cased_sibling_is_a_reference(tmp_path):
    repo, sp = _repo(tmp_path, {"A.java": _JAVA})
    got = check_references(repo, identifier="file", storage_path=sp)
    assert 3 in _lines(got), got


def test_the_used_method_is_not_certified_deletable(tmp_path):
    repo, sp = _repo(tmp_path, {"A.java": _JAVA})
    got = check_delete_safe(repo, "file", storage_path=sp)
    assert got["verdict"] not in _ABSENCE, (got["verdict"], got["confidence"])


def test_control_the_definitions_own_lines_are_still_excluded(tmp_path):
    """The exact-name definition keeps its exclusion: `file`'s declaration and
    body (lines 5-7) are not a reference to itself."""
    repo, sp = _repo(tmp_path, {"A.java": _JAVA})
    lines = _lines(check_references(repo, identifier="file", storage_path=sp))
    assert not set(lines) & {5, 6, 7}, lines


def test_the_accepted_cost_a_cased_siblings_declaration_reads_as_a_reference(tmp_path):
    """The direction this fix errs in, pinned so it is a decision and not an
    accident: an unused `load` beside `Load` now reads as referenced, because
    the case-insensitive substring search sees `Load`'s declaration and the
    exact exclusion no longer hides it. That blocks a delete; it never
    licenses one. The target's own lines stay excluded."""
    src = (
        "public class B {\n"
        "    public int Load() {\n"
        "        return 2;\n"
        "    }\n"
        "    private int load() {\n"
        "        return 1;\n"
        "    }\n"
        "}\n"
    )
    repo, sp = _repo(tmp_path, {"B.java": src})
    lines = _lines(check_references(repo, identifier="load", storage_path=sp))
    assert 2 in lines, lines
    assert not set(lines) & {5, 6, 7}, lines


def test_a_capitalised_targets_own_lines_are_excluded(tmp_path):
    """The key is the declared spelling as given, not a lowered copy: asking
    about `File` excludes `File`'s own lines (2-4), including the call to
    `file()` inside it, which is not a use of `File`."""
    repo, sp = _repo(tmp_path, {"A.java": _JAVA})
    lines = _lines(check_references(repo, identifier="File", storage_path=sp))
    assert not set(lines) & {2, 3, 4}, lines
