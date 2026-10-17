"""`get_dead_code_v2` counts a call from another file where the language needs no import (LEDGER L-165, item 2).

For a function of a language that records calls, the tool looked for a caller
in two places: the function's own file, and each file the import graph says
imports that file. That is the right rule for Python and JavaScript, where a
file must import a name to call it. It is the wrong rule for Go, Java, C#, Rust
and Ruby: two files of one Go package, two classes of one Java package, two C#
files of one namespace call each other with no import at all, a Rust path call
(`crate::flags::config::args()`) needs no `use`, and a Ruby `require` is global
to the process. In those languages a function called only from another file
read `no_callers`: live code reported dead, in a tool whose rows feed delete
decisions.

A function of such a language that is still without a caller now counts a call
recorded in any other file of the SAME language. Every fixture goes through
`index_folder` and the tool.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.tools import get_dead_code_v2 as v2
from jcodemunch_mcp.tools.index_folder import index_folder

# Two Python files joined by an import, so the index has an import graph and
# the tool runs its three-signal path (the call-graph-only path already asks
# every file).
PYTHON_PAIR = {
    "util.py": "def b():\n    return 1\n",
    "py.py": "from util import b\n\ndef a():\n    return b()\n",
}

# language -> {file: source}, the function called only from the other file,
# the function nothing calls.
CASES = {
    "go": (
        {
            "a.go": "package p\n\nfunc Helper() int { return 1 }\n\nfunc Unused() int { return 2 }\n",
            "b.go": "package p\n\nfunc Run() int { return Helper() }\n",
        },
        ("a.go", "Helper"), ("a.go", "Unused"),
    ),
    "java": (
        {
            "A.java": "package p;\n\nclass A {\n  static int helper() { return 1; }\n  static int unused() { return 2; }\n}\n",
            "B.java": "package p;\n\nclass B {\n  int run() { return A.helper(); }\n}\n",
        },
        ("A.java", "helper"), ("A.java", "unused"),
    ),
    "csharp": (
        {
            "A.cs": "namespace P {\n  class A {\n    public static int Helper() { return 1; }\n"
                    "    public static int Unused() { return 2; }\n  }\n}\n",
            "B.cs": "namespace P {\n  class B {\n    int Run() { return A.Helper(); }\n  }\n}\n",
        },
        ("A.cs", "Helper"), ("A.cs", "Unused"),
    ),
    # The ledger row's shape: a path call with no `use` (ripgrep's `args`).
    "rust": (
        {
            "config.rs": "pub fn args() -> i32 { 1 }\n\npub fn unused() -> i32 { 2 }\n",
            "parse.rs": "fn run() -> i32 {\n    crate::config::args()\n}\n",
        },
        ("config.rs", "args"), ("config.rs", "unused"),
    ),
    # A method called through a receiver inside a macro, in a function of another file.
    "rust-in-a-macro": (
        {
            "util.rs": "pub struct T;\n\nimpl T {\n    pub fn cmd(&self) -> i32 { 1 }\n    pub fn unused(&self) -> i32 { 2 }\n}\n",
            "feature.rs": "fn check(t: T) {\n    assert_eq!(t.cmd(), 1);\n}\n",
        },
        ("util.rs", "cmd"), ("util.rs", "unused"),
    ),
    "ruby": (
        {
            "a.rb": "def helper_thing\n  1\nend\n\ndef unused_thing\n  2\nend\n",
            "b.rb": "def run\n  helper_thing()\nend\n",
        },
        ("a.rb", "helper_thing"), ("a.rb", "unused_thing"),
    ),
}

# Where a file must import a name to call it, a call from a file that does not
# import the function's file is another function of that name, and counts for
# nothing.
IMPORT_IS_REQUIRED = {
    "python": (
        {
            "lib.py": "def helper_thing():\n    return 1\n",
            "other.py": "def helper_thing():\n    return 2\n\ndef run():\n    return helper_thing()\n",
        },
        ("lib.py", "helper_thing"),
    ),
    "javascript": (
        {
            "lib.js": "export function helperThing() { return 1; }\n",
            "other.js": "function helperThing() { return 2; }\n\nexport function run() { return helperThing(); }\n",
        },
        ("lib.js", "helperThing"),
    ),
    "typescript": (
        {
            "lib.ts": "export function helperThing(): number { return 1; }\n",
            "other.ts": "function helperThing(): number { return 2; }\n\nexport function run(): number { return helperThing(); }\n",
        },
        ("lib.ts", "helperThing"),
    ),
}


def _signals(tmp_path, files):
    src = tmp_path / "src"
    src.mkdir()
    store = tmp_path / "store"
    store.mkdir()
    for name, body in files.items():
        (src / name).write_text(body, encoding="utf-8")
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert r["success"] is True
    out = v2.get_dead_code_v2(repo=r["repo"], min_confidence=0.0, max_results=0, storage_path=str(store))
    assert "error" not in out, out
    assert out.get("_meta", {}).get("mode") != "call_graph_only", out.get("_meta")
    return {(row["file"], row["name"]): row["signals"] for row in out["dead_symbols"]}


@pytest.mark.parametrize("language", sorted(CASES))
def test_a_function_called_only_from_another_file_is_not_reported_without_callers(tmp_path, language):
    files, used, _ = CASES[language]
    rows = _signals(tmp_path, {**PYTHON_PAIR, **files})
    assert "no_callers" not in rows.get(used, []), f"{language}: `{used[1]}` is called in the other file"


@pytest.mark.parametrize("language", sorted(CASES))
def test_a_function_nothing_calls_is_still_reported(tmp_path, language):
    files, _, unused = CASES[language]
    rows = _signals(tmp_path, {**PYTHON_PAIR, **files})
    assert "no_callers" in rows[unused], rows


@pytest.mark.parametrize("language", sorted(IMPORT_IS_REQUIRED))
def test_a_same_named_call_in_a_file_that_does_not_import_it_counts_for_nothing(tmp_path, language):
    files, shadowed = IMPORT_IS_REQUIRED[language]
    rows = _signals(tmp_path, {**PYTHON_PAIR, **files})
    assert "no_callers" in rows[shadowed], rows


def test_a_call_in_another_language_counts_for_nothing(tmp_path):
    """A Java call of `shared_name` is not a call of the Go function `shared_name`."""
    files = {
        "a.go": "package p\n\nfunc shared_name() int { return 1 }\n",
        "B.java": "package p;\n\nclass B {\n  int run() { return shared_name(); }\n  int shared_name() { return 2; }\n}\n",
    }
    rows = _signals(tmp_path, {**PYTHON_PAIR, **files})
    assert "no_callers" in rows[("a.go", "shared_name")], rows


def test_the_languages_that_need_no_import_are_pinned():
    """A language is added here only when its files call each other with no import.

    Kotlin, Swift and PHP record no call today (L-158); they are listed for the
    day they do. Python, JavaScript, TypeScript, TSX and Dart must never be.
    """
    from jcodemunch_mcp.tools._call_graph import LANGUAGES_CALLED_ACROSS_FILES_WITHOUT_AN_IMPORT

    assert LANGUAGES_CALLED_ACROSS_FILES_WITHOUT_AN_IMPORT == frozenset(
        {"go", "java", "csharp", "rust", "ruby", "kotlin", "swift", "php"}
    )
