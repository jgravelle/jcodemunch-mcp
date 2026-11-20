"""A same-named sibling's name line is found by its exact spelling (LEDGER L-331).

The delete and edit preflights leave out, of every other definition spelled
like the target, its declaration: its first line through the first line that
names the identifier. That line was found by a case-folded SUBSTRING, so an
annotation such as `@FileAnno` or `@Profile` above `public int file()` was
taken as the name line, and the sibling's real declaration line below it
counted as a reference to the target: an unused `C.file` graded
`internal_uses_blocking` with a blocker on that line.

The name line is now the first line holding the exact declared spelling as a
whole token (the word rule), after decoding Unicode escapes. Excluding text
REMOVES matches, so the exclusion is never folded (the `_fold` docstring's
rule); the declared name is the written spelling, so nothing needs folding to
find it.
"""
from __future__ import annotations

import pytest

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.tools.check_delete_safe import check_delete_safe
from jcodemunch_mcp.tools.check_edit_safe import check_edit_safe
from jcodemunch_mcp.tools.check_references import _name_through

NL = chr(10)

_TARGET = ("C.java", "public class C {\n    public int file() {\n        return 1;\n    }\n}\n")


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


def _refs_in(got, file):
    return {b.get("line") for b in got.get("blockers", [])
            if b.get("kind") == "internal_reference" and b.get("file") == file}


# An annotation above the sibling whose text holds the name in another case or
# inside a longer word. The sibling never calls the target.
_ANNOTATIONS = {
    "other_case_prefix": "@FileAnno",
    "inside_a_word": "@Profile",
    "other_case_whole": "@File",
}


def _sibling(annotation):
    return ("D.java", "public class D {\n    " + annotation + "\n    public int file() {\n"
            "        return 2;\n    }\n}\n")


@pytest.mark.parametrize("case", sorted(_ANNOTATIONS))
def test_an_annotation_spelling_the_name_does_not_make_the_declaration_a_use(tmp_path, case):
    d_name, d_src = _sibling(_ANNOTATIONS[case])
    repo, sp = _repo(tmp_path, {_TARGET[0]: _TARGET[1], d_name: d_src})
    got = check_delete_safe(repo, _symbol_id(repo, sp, "C.java", "file"), storage_path=sp)
    assert _refs_in(got, "D.java") == set(), got.get("blockers")


@pytest.mark.parametrize("case", sorted(_ANNOTATIONS))
def test_the_edit_preflight_agrees(tmp_path, case):
    d_name, d_src = _sibling(_ANNOTATIONS[case])
    repo, sp = _repo(tmp_path, {_TARGET[0]: _TARGET[1], d_name: d_src})
    got = check_edit_safe(repo, _symbol_id(repo, sp, "C.java", "file"), storage_path=sp)
    found = [b for b in got.get("blockers", [])
             if b.get("file") == "D.java" and b.get("kind") == "internal_reference"]
    assert found == [], got.get("blockers")


def test_a_body_call_under_such_an_annotation_still_blocks(tmp_path):
    # The fix moves the name line LATER, which hides more; the call in the
    # body below the real name line must still count.
    d_src = ("public class D {\n    @FileAnno\n    public int file() {\n"
             "        return new C().file();\n    }\n}\n")
    repo, sp = _repo(tmp_path, {_TARGET[0]: _TARGET[1], "D.java": d_src})
    got = check_delete_safe(repo, _symbol_id(repo, sp, "C.java", "file"), storage_path=sp)
    assert 4 in _refs_in(got, "D.java"), got.get("blockers")
    assert got.get("verdict") not in ("safe_to_delete", "internal_only", "test_coverage_only")


# _name_through(lines, lo, hi, ident_lower, ident_exact, signature) -> (lo, name_line)
_LINES = [
    "    @FileAnno",                      # 1
    "    @Profile(" + '"x"' + ")",                 # 2
    "    public int file() {",            # 3
    "        return new C().file();",     # 4
    "    }",                              # 5
]
_SIG = "@FileAnno" + NL + "    @Profile(" + '"x"' + ")" + NL + "    public int file()"


def test_the_name_line_is_the_first_exact_token_in_the_signature():
    assert _name_through(_LINES, 1, 5, "file", "file", _SIG) == (1, 3)


def test_without_a_signature_the_folded_rule_is_kept():
    assert _name_through(_LINES, 1, 5, "file", "file", "") == (1, 1)


def test_the_search_never_runs_past_the_signature():
    # A signature one line long that does not hold the exact token on that
    # line: the body call on line 4 must not become the name line.
    assert _name_through(_LINES, 1, 5, "file", "file", "@FileAnno file") == (1, 1)


def test_a_signature_without_the_exact_name_keeps_the_folded_rule():
    # A name derived from the file name (Razor's `view Counter`) where the
    # signature is synthetic: the folded rule decides, as on main.
    lines = ['@page "/counter"', "@code {", "    int n = App.Counter.Next();", "}"]
    assert _name_through(lines, 1, 4, "counter", "Counter", "view Counter") == (1, 1)


def test_an_escaped_name_under_an_annotation_is_found():
    # Without decoding the line, the exact search misses line 2 and the folded
    # rule takes the annotation on line 1.
    lines = ["    @FileAnno", "    public int \\u0066ile() {", "        return 1;", "    }"]
    sig = "@FileAnno" + NL + "    public int \\u0066ile()"
    assert _name_through(lines, 1, 4, "file", "file", sig) == (1, 2)


def test_a_name_ending_in_a_mark_is_matched_as_a_token():
    lines = ["  # valid?x", "  def valid?", "  end"]
    assert _name_through(lines, 1, 3, "valid?", "valid?", "# valid?x" + NL + "  def valid?") == (1, 2)


def test_a_razor_component_named_like_a_class_does_not_hide_a_use(tmp_path):
    # The component's name comes from its file name and its span is the whole
    # file, so it has no declaration line spelling `Counter`. A search not
    # bounded by the signature stopped at the call on line 3 and graded the
    # used class `safe_to_delete` (L-331 review).
    files = {
        "Widget.cs": NL.join(["namespace App {", " public static class Counter {",
                              "  public static int Next() { return 1; }", " }", "}", ""]),
        "Counter.razor": NL.join(['@page "/counter"', "@code {", "    int n = App.Counter.Next();", "}", ""]),
    }
    repo, sp = _repo(tmp_path, files)
    got = check_delete_safe(repo, _symbol_id(repo, sp, "Widget.cs", "Counter"), storage_path=sp)
    assert got.get("verdict") not in ("safe_to_delete", "internal_only", "test_coverage_only"), got
    assert _refs_in(got, "Counter.razor"), got.get("blockers")
