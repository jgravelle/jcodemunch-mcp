"""A delete preflight excludes the TARGET's own lines, not every same-named definition (LEDGER L-91).

`check_references` hides the line span of every definition spelled exactly like
the identifier, so a declaration is not counted as a use of itself (#406, L-88).
For the public tool, which knows only a name, that is the best it can do. A
preflight knows WHICH symbol it is asking about, and hiding every same-named
definition there hides real callers: Java `B.file()` whose body calls
`new A().file()` lost that call when `check_delete_safe` asked about `A.file`,
and the used method graded as an absence verdict.

The preflights now exclude the target's own span whole and, of every other
same-named definition, only its declaration: from its first line through the
first line that names the identifier, so an annotation above the name
(`@Override`, `[Attr]`) hides the name line too. A call inside a sibling's
body counts. The rule only adds matches, so it can hold a delete back and
never licenses one.
"""
from __future__ import annotations

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.tools.check_delete_safe import check_delete_safe
from jcodemunch_mcp.tools.check_edit_safe import check_edit_safe
from jcodemunch_mcp.tools.check_references import check_references
from jcodemunch_mcp.tools.index_folder import index_folder

_ABSENCE = ("safe_to_delete", "internal_only", "test_coverage_only")

_A = (
    "public class A {\n"
    "    public int file() {\n"
    "        return 1;\n"
    "    }\n"
    "}\n"
)
_B = (
    "public class B {\n"
    "    public int file() {\n"
    "        return new A().file();\n"
    "    }\n"
    "}\n"
)


def _repo(tmp_path, files):
    for rel, text in files.items():
        (tmp_path / rel).write_text(text, encoding="utf-8")
    storage = str(tmp_path / "idx")
    repo = index_folder(path=str(tmp_path), use_ai_summaries=False, storage_path=storage)["repo"]
    return repo, storage


def _symbol_id(repo, storage, file, name):
    owner, name_ = repo.split("/", 1)
    index = IndexStore(base_path=storage).load_index(owner, name_)
    ids = [s["id"] for s in index.symbols if s.get("file") == file and s.get("name") == name]
    assert len(ids) == 1, ids
    return ids[0]


def test_a_call_inside_a_same_named_method_elsewhere_blocks_the_delete(tmp_path):
    repo, sp = _repo(tmp_path, {"A.java": _A, "B.java": _B})
    target = _symbol_id(repo, sp, "A.java", "file")
    got = check_delete_safe(repo, target, storage_path=sp)
    assert got["verdict"] not in _ABSENCE, (got["verdict"], got.get("blockers"))
    files = {b.get("file") for b in got.get("blockers", []) if b.get("kind") == "internal_reference"}
    assert "B.java" in files, got.get("blockers")


def test_the_edit_preflight_sees_the_same_caller(tmp_path):
    repo, sp = _repo(tmp_path, {"A.java": _A, "B.java": _B})
    target = _symbol_id(repo, sp, "A.java", "file")
    got = check_edit_safe(repo, target, storage_path=sp)
    files = {b.get("file") for b in got.get("blockers", []) if b.get("kind") == "internal_reference"}
    assert "B.java" in files, got.get("blockers")


def test_the_call_line_itself_is_a_match_given_the_target_span(tmp_path):
    """The reference the preflights read carries the call on line 3, and its
    blocker names a real line, never 0 (every blocker read 0 before: the
    file-level entry has `matches`, not a `line`)."""
    from jcodemunch_mcp.tools.check_references import _check_single, target_span_of
    import time
    repo, sp = _repo(tmp_path, {"A.java": _A, "B.java": _B})
    owner, name = repo.split("/", 1)
    store = IndexStore(base_path=sp)
    index = store.load_index(owner, name)
    target = next(s for s in index.symbols if s["id"] == _symbol_id(repo, sp, "A.java", "file"))
    got = _check_single("file", index, True, 20, owner, name, store, time.perf_counter(),
                        target_span=target_span_of(target))
    lines = {(f["file"], m["line"]) for f in got["content_references"] for m in f["matches"]}
    assert ("B.java", 3) in lines, lines
    blockers = check_delete_safe(repo, target["id"], storage_path=sp)["blockers"]
    assert all(b.get("line") for b in blockers if b.get("kind") == "internal_reference"), blockers


def test_control_the_targets_own_lines_stay_excluded(tmp_path):
    repo, sp = _repo(tmp_path, {"A.java": _A, "B.java": _B})
    target = _symbol_id(repo, sp, "A.java", "file")
    got = check_delete_safe(repo, target, storage_path=sp)
    lines = {(b.get("file"), b.get("line")) for b in got.get("blockers", [])}
    assert not {("A.java", n) for n in (2, 3, 4)} & lines, got.get("blockers")


def test_control_the_public_tool_still_hides_every_same_named_definition(tmp_path):
    """`check_references` knows only a name, so it keeps excluding every
    definition so spelled; only the preflights, which know the target, narrow."""
    repo, sp = _repo(tmp_path, {"A.java": _A, "B.java": _B})
    got = check_references(repo, identifier="file", storage_path=sp)
    lines = {(f["file"], m["line"]) for f in got.get("content_references", []) for m in f["matches"]}
    assert ("B.java", 3) not in lines, lines


def test_a_same_named_siblings_declaration_line_is_not_a_reference(tmp_path):
    """Of a same-named sibling, only its declaration is left out: `B.file`'s
    signature (line 2) is not a use of `A.file`, while the call on line 3 is."""
    repo, sp = _repo(tmp_path, {"A.java": _A, "B.java": _B})
    target = _symbol_id(repo, sp, "A.java", "file")
    got = check_delete_safe(repo, target, storage_path=sp)
    lines = {(b.get("file"), b.get("line")) for b in got.get("blockers", [])}
    assert ("B.java", 3) in lines, got.get("blockers")
    assert ("B.java", 2) not in lines, got.get("blockers")


def test_an_unused_method_beside_a_same_named_sibling_stays_deletable(tmp_path):
    """A sibling that only DECLARES the name blocks nothing: `C.file` is used by
    no one, and `D.file`'s declaration is not a reference to it."""
    c = "public class C {\n    public int file() {\n        return 1;\n    }\n}\n"
    d = "public class D {\n    public int file() {\n        return 2;\n    }\n}\n"
    repo, sp = _repo(tmp_path, {"C.java": c, "D.java": d})
    got = check_delete_safe(repo, _symbol_id(repo, sp, "C.java", "file"), storage_path=sp)
    refs = [b for b in got.get("blockers", []) if b.get("kind") == "internal_reference"]
    assert not refs, got.get("blockers")


def test_a_target_with_no_line_keeps_the_name_wide_exclusion():
    """`target_span_of` gives None when the symbol carries no usable line, which
    sends `_check_single` back to excluding every same-named definition, so a
    line-less target is never counted as a reference to itself."""
    from jcodemunch_mcp.tools.check_references import target_span_of
    assert target_span_of({"file": "a.py", "line": None}) is None
    assert target_span_of({"file": "a.py"}) is None
    assert target_span_of({"file": "", "line": 3}) is None
    assert target_span_of({"file": "a.py", "line": "x"}) is None
    assert target_span_of({"file": "a.py", "line": 5, "end_line": 2}) == ("a.py", 5, 5)
    assert target_span_of({"file": "a.py", "line": 5, "end_line": 9}) == ("a.py", 5, 9)


import pytest  # noqa: E402

_ANNOTATED = {
    "java": (
        "C.java", "public class C {\n    public int file() {\n        return 1;\n    }\n}\n",
        "D.java", "public class D extends Base {\n    @Override\n    public int file() {\n        return 2;\n    }\n}\n",
    ),
    "csharp": (
        "C.cs", "public class C {\n    public int file() {\n        return 1;\n    }\n}\n",
        "D.cs", "public class D {\n    [Obsolete]\n    public int file() {\n        return 2;\n    }\n}\n",
    ),
    "kotlin": (
        "C.kt", "class C {\n    fun file(): Int {\n        return 1\n    }\n}\n",
        "D.kt", "class D {\n    @JvmStatic\n    fun file(): Int {\n        return 2\n    }\n}\n",
    ),
}


@pytest.mark.parametrize("lang", sorted(_ANNOTATED))
def test_an_annotated_siblings_name_line_is_not_a_reference(tmp_path, lang):
    """An annotation above a same-named sibling is its first indexed line; the
    line that names the method comes after it and must not count as a use of
    the target (round 2 of the L-91 review: `@Override` reopened the
    declaration-as-use move for Java, C# and Kotlin)."""
    c_name, c_src, d_name, d_src = _ANNOTATED[lang]
    repo, sp = _repo(tmp_path, {c_name: c_src, d_name: d_src})
    got = check_delete_safe(repo, _symbol_id(repo, sp, c_name, "file"), storage_path=sp)
    refs = [b for b in got.get("blockers", []) if b.get("kind") == "internal_reference"]
    assert not refs, (lang, got["verdict"], got.get("blockers"))


def test_a_call_inside_an_annotated_sibling_still_counts(tmp_path):
    """Excluding through the name line stops there: the body of an `@Override`
    sibling that calls the target is still a call site."""
    a = "public class A {\n    public int file() {\n        return 1;\n    }\n}\n"
    b = ("public class B extends Base {\n    @Override\n    public int file() {\n"
         "        return new A().file();\n    }\n}\n")
    repo, sp = _repo(tmp_path, {"A.java": a, "B.java": b})
    got = check_delete_safe(repo, _symbol_id(repo, sp, "A.java", "file"), storage_path=sp)
    lines = {(x.get("file"), x.get("line")) for x in got.get("blockers", [])}
    assert ("B.java", 4) in lines, got.get("blockers")
    assert got["verdict"] not in _ABSENCE, got["verdict"]
