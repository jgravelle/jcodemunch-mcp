"""A call on a same-named sibling's own NAME line counts as a reference (LEDGER L-329).

L-91 made the delete and edit preflights leave out, of every other definition
spelled like the target, only its declaration: its first line through the
first line naming the identifier. That name line was left out whole, so a
one-line method that calls the target hid the call:
`@Override public int file() { return new C().file(); }` left an unused-looking
`C.file` graded `safe_to_delete` with no blockers.

The name line now counts when it calls the exact declared name (the name,
then `(`) beyond the declarations whose name line it is. A parameter, a type,
a mention without `(`, or a second declaration sharing the line does not make
the declaration a use; a declaration that is not call-shaped (a field, a
Kotlin property) does not swallow the call after it.
"""
from __future__ import annotations

import pytest

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.tools.check_delete_safe import check_delete_safe
from jcodemunch_mcp.tools.check_edit_safe import check_edit_safe
from jcodemunch_mcp.tools.check_references import check_references

_ABSENCE = ("safe_to_delete", "internal_only", "test_coverage_only")


def _repo(tmp_path, files):
    from jcodemunch_mcp.tools.index_folder import index_folder

    for rel, text in files.items():
        (tmp_path / rel).write_text(text, encoding="utf-8")
    storage = str(tmp_path / "idx")
    repo = index_folder(path=str(tmp_path), use_ai_summaries=False, storage_path=storage)["repo"]
    return repo, storage


def _symbol_id(repo, storage, file, name):
    owner, name_ = repo.split("/", 1)
    index = IndexStore(base_path=storage).load_index(owner, name_)
    ids = [s["id"] for s in index.symbols if s.get("file") == file and s.get("name") == name]
    assert len(ids) == 1, (file, name, [(s.get("file"), s.get("name")) for s in index.symbols])
    return ids[0]


def _refs(got):
    return {(b.get("file"), b.get("line")) for b in got.get("blockers", [])
            if b.get("kind") == "internal_reference"}


# (target file, target source, sibling file, sibling source, the call's line)
_ONE_LINE_CALLERS = {
    "java": (
        "C.java", "public class C {\n    public int file() {\n        return 1;\n    }\n}\n",
        "D.java", "public class D extends Base {\n    @Override public int file() { return new C().file(); }\n}\n",
        2,
    ),
    "kotlin": (
        "C.kt", "class C {\n    fun file(): Int {\n        return 1\n    }\n}\n",
        "D.kt", "class D {\n    fun file() = C().file()\n}\n",
        2,
    ),
    "python": (
        "c.py", "class C:\n    def file(self):\n        return 1\n",
        "d.py", "class D:\n    def file(self): return C().file()\n",
        2,
    ),
    "java_annotation_above": (
        "C.java", "public class C {\n    public int file() {\n        return 1;\n    }\n}\n",
        "D.java", "public class D extends Base {\n    @Override\n    public int file() { return new C().file(); }\n}\n",
        3,
    ),
    "java_field": (
        "C.java", "public class C {\n    public int file() {\n        return 1;\n    }\n}\n",
        "D.java", "public class D {\n    int file = new C().file();\n}\n",
        2,
    ),
    "kotlin_property": (
        "C.kt", "class C {\n    fun file(): Int {\n        return 1\n    }\n}\n",
        "D.kt", "class D {\n    val file get() = C().file()\n}\n",
        2,
    ),
}


@pytest.mark.parametrize("lang", sorted(_ONE_LINE_CALLERS))
def test_a_one_line_sibling_calling_the_target_blocks_the_delete(tmp_path, lang):
    c_name, c_src, d_name, d_src, line = _ONE_LINE_CALLERS[lang]
    repo, sp = _repo(tmp_path, {c_name: c_src, d_name: d_src})
    got = check_delete_safe(repo, _symbol_id(repo, sp, c_name, "file"), storage_path=sp)
    assert got["verdict"] not in _ABSENCE, (lang, got["verdict"], got.get("blockers"))
    assert (d_name, line) in _refs(got), (lang, got.get("blockers"))


@pytest.mark.parametrize("lang", ["java", "kotlin", "python"])
def test_the_edit_preflight_sees_the_one_line_caller(tmp_path, lang):
    c_name, c_src, d_name, d_src, line = _ONE_LINE_CALLERS[lang]
    repo, sp = _repo(tmp_path, {c_name: c_src, d_name: d_src})
    got = check_edit_safe(repo, _symbol_id(repo, sp, c_name, "file"), storage_path=sp)
    assert (d_name, line) in _refs(got), got.get("blockers")


# A sibling whose name line spells the name again without calling it: none of
# these is a use of the target, and each stayed deletable before this change.
_DECLARATION_ONLY_LINES = {
    "python_param": ("d.py", "class D:\n    def file(self, file): return file\n"),
    "python_attr": ("d.py", "class D:\n    def file(self): return self.file_\n"),
    "java_type": ("D.java", "public class D {\n    public File file() { return null; }\n}\n"),
    "java_other_case": ("D.java", "public class D {\n    public File file() { return new File(\"x\"); }\n}\n"),
    "java_comment_mention": ("D.java", "public class D {\n    public int file() { return 2; } // file\n}\n"),
    "java_overloads_on_one_line": ("D.java", "interface D { int file(); int file(int x); }\n"),
    "js_two_classes_on_one_line": (
        "d.js", "class A { file() { return 2; } } class B { file() { return 3; } }\n"),
}


@pytest.mark.parametrize("case", sorted(_DECLARATION_ONLY_LINES))
def test_control_a_name_line_without_a_second_call_stays_excluded(tmp_path, case):
    d_name, d_src = _DECLARATION_ONLY_LINES[case]
    if d_name.endswith(".py"):
        c_name, c_src = "c.py", "class C:\n    def file(self):\n        return 1\n"
    elif d_name.endswith(".js"):
        c_name, c_src = "c.js", "class C {\n  file() {\n    return 1;\n  }\n}\n"
    else:
        c_name, c_src = "C.java", "public class C {\n    public int file() {\n        return 1;\n    }\n}\n"
    repo, sp = _repo(tmp_path, {c_name: c_src, d_name: d_src})
    got = check_delete_safe(repo, _symbol_id(repo, sp, c_name, "file"), storage_path=sp)
    assert not _refs(got), (case, got["verdict"], got.get("blockers"))


def test_control_the_targets_own_one_line_self_call_stays_excluded(tmp_path):
    """The target's own span is still left out whole, so a one-line method
    calling itself is not evidence anyone else uses it."""
    c = "public class C {\n    public int file() { return file(); }\n}\n"
    repo, sp = _repo(tmp_path, {"C.java": c})
    got = check_delete_safe(repo, _symbol_id(repo, sp, "C.java", "file"), storage_path=sp)
    assert not _refs(got), got.get("blockers")


def test_control_the_public_tool_still_hides_every_same_named_definition(tmp_path):
    """`check_references` knows only a name and keeps excluding every
    definition so spelled, the one-line caller's line included."""
    c_name, c_src, d_name, d_src, line = _ONE_LINE_CALLERS["java"]
    repo, sp = _repo(tmp_path, {c_name: c_src, d_name: d_src})
    got = check_references(repo, identifier="file", storage_path=sp)
    lines = {(f["file"], m["line"]) for f in got.get("content_references", []) for m in f["matches"]}
    assert (d_name, line) not in lines, lines


def test_a_one_line_sibling_that_calls_itself_counts_like_a_body_line(tmp_path):
    """Name-based, not resolved: a one-line sibling calling its own name is
    counted, as a call in a sibling's body already is (L-91)."""
    c = "class C:\n    def file(self):\n        return 1\n"
    d = "class D:\n    def file(self, n): return self.file(n - 1) if n else 0\n"
    repo, sp = _repo(tmp_path, {"c.py": c, "d.py": d})
    got = check_delete_safe(repo, _symbol_id(repo, sp, "c.py", "file"), storage_path=sp)
    assert ("d.py", 2) in _refs(got), got.get("blockers")
