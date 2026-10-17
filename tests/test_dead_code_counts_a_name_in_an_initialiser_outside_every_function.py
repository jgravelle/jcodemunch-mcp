"""`get_dead_code_v2` reads an initialiser written outside every function (LEDGER L-169, item 15).

A call written outside every function belongs to no function, so no call
reference records it; the tool reads that code as text instead: each file with
its symbols' lines taken out. It took out EVERY symbol's lines, and a constant,
a variable and a class field are symbols. So the one place a module-level call
is usually written, the right-hand side of an assignment, was removed before
it was read: a function used only as `ONE = helper()`, or named only in a
dispatch table, read `no_callers` in Python, JavaScript, TypeScript, Go, Java,
C# and Rust. Live code reported dead, in a tool whose rows feed delete
decisions.

The lines of a constant, variable, field or property that is not inside a
function are read now. Every fixture goes through `index_folder` and the tool.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.tools import get_dead_code_v2 as v2
from jcodemunch_mcp.tools.index_folder import index_folder

# Two Python files joined by an import, so the index has an import graph and
# the tool runs its three-signal path.
PYTHON_PAIR = {
    "g1.py": "def gf():\n    return 1\n",
    "g2.py": "from g1 import gf\n\ndef gg():\n    return gf()\n",
}

# case -> ({file: source}, (file, name) used only in an initialiser, (file, name) nothing uses)
CASES = {
    "python: a constant": (
        {"a.py": "def helper():\n    return 1\n\ndef unused():\n    return 2\n\nONE = helper()\n"},
        ("a.py", "helper"), ("a.py", "unused"),
    ),
    "python: a constant in a file that imports it": (
        {
            "a.py": "def helper():\n    return 1\n\ndef unused():\n    return 2\n",
            "b.py": "import a\n\nONE = a.helper()\n",
        },
        ("a.py", "helper"), ("a.py", "unused"),
    ),
    "python: a dispatch table over several lines": (
        {"a.py": "def helper():\n    return 1\n\ndef unused():\n    return 2\n\nTABLE = {\n    'h': helper,\n}\n"},
        ("a.py", "helper"), ("a.py", "unused"),
    ),
    "python: an annotated constant": (
        {"a.py": "def helper():\n    return 1\n\ndef unused():\n    return 2\n\nONE: int = helper()\n"},
        ("a.py", "helper"), ("a.py", "unused"),
    ),
    "python: a class attribute": (
        {"a.py": "def helper():\n    return 1\n\ndef unused():\n    return 2\n\nclass Holder:\n    one = helper()\n"},
        ("a.py", "helper"), ("a.py", "unused"),
    ),
    "python: a class attribute table over several lines": (
        {"a.py": "def helper():\n    return 1\n\ndef unused():\n    return 2\n\n"
                 "class Holder:\n    table = {\n        'h': helper,\n    }\n"},
        ("a.py", "helper"), ("a.py", "unused"),
    ),
    "python: a class created in a constant": (
        {
            "a.py": "class T:\n    def __init__(self):\n        self.x = 1\n\n"
                    "class Never:\n    def __init__(self):\n        self.y = 1\n\nONE = T()\n",
        },
        ("a.py", "T.__init__"), ("a.py", "Never.__init__"),
    ),
    "javascript: a const": (
        {"k.js": "function helper() { return 1; }\nfunction unused() { return 2; }\nconst ONE = helper();\nfunction f() { return 1; }\nfunction g() { return f(); }\ng();\n"},
        ("k.js", "helper"), ("k.js", "unused"),
    ),
    "javascript: a table over several lines": (
        {"k.js": "function helper() { return 1; }\nfunction unused() { return 2; }\nconst TABLE = {\n  h: helper,\n};\nfunction f() { return 1; }\nfunction g() { return f(); }\ng();\n"},
        ("k.js", "helper"), ("k.js", "unused"),
    ),
    "typescript: a const": (
        {"k.ts": "function helper(): number { return 1; }\nfunction unused(): number { return 2; }\n"
                 "export const ONE: number = helper();\nfunction f(): number { return 1; }\nexport function g(): number { return f(); }\ng();\n"},
        ("k.ts", "helper"), ("k.ts", "unused"),
    ),
    "typescript: a class field and a static one": (
        {"k.ts": "function helper(): number { return 1; }\nfunction other(): number { return 3; }\n"
                 "function unused(): number { return 2; }\n"
                 "export class Holder {\n  one: number = helper();\n  static two = other();\n}\nfunction f(): number { return 1; }\nexport function g(): number { return f(); }\ng();\n"},
        ("k.ts", "helper"), ("k.ts", "unused"),
    ),
    "go: a package variable": (
        {"a.go": "package p\n\nfunc helper() int { return 1 }\n\nfunc unused() int { return 2 }\n\nvar One = helper()\n\nfunc f() int { return 1 }\n\nfunc init() { f() }\n"},
        ("a.go", "helper"), ("a.go", "unused"),
    ),
    "java: a static field": (
        {"A.java": "package p;\n\nclass A {\n  static int helper() { return 1; }\n  static int unused() { return 2; }\n"
                   "  static int ONE = helper();\n  static int f() { return 1; }\n  static int g() { return f(); }\n  static int TWO = g();\n}\n"},
        ("A.java", "A.helper"), ("A.java", "A.unused"),
    ),
    "csharp: a static field": (
        {"A.cs": "namespace P {\n  class A {\n    static int Helper() { return 1; }\n    static int Unused() { return 2; }\n"
                 "    static int One = Helper();\n    static int F() { return 1; }\n    static int G() { return F(); }\n    static int Two = G();\n  }\n}\n"},
        ("A.cs", "A.Helper"), ("A.cs", "A.Unused"),
    ),
    "rust: a static": (
        {"a.rs": "const fn helper() -> i32 { 1 }\n\nfn unused() -> i32 { 2 }\n\nstatic ONE: i32 = helper();\n\nconst fn f() -> i32 { 1 }\n\nconst fn g() -> i32 { f() }\n\nstatic TWO: i32 = g();\n"},
        ("a.rs", "helper"), ("a.rs", "unused"),
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
    rows = {}
    for row in out["dead_symbols"]:
        qualified = row["id"].split("::", 1)[1].rsplit("#", 1)[0]
        rows[(row["file"], qualified)] = row["signals"]
    return rows


@pytest.mark.parametrize("case", sorted(CASES))
def test_a_function_named_only_in_an_initialiser_is_not_reported_without_callers(tmp_path, case):
    files, used, _ = CASES[case]
    rows = _signals(tmp_path, {**PYTHON_PAIR, **files})
    assert "no_callers" not in rows.get(used, []), f"{case}: `{used[1]}` is used in an initialiser: {rows}"


@pytest.mark.parametrize("case", sorted(CASES))
def test_a_function_nothing_names_is_still_reported(tmp_path, case):
    files, _, unused = CASES[case]
    rows = _signals(tmp_path, {**PYTHON_PAIR, **files})
    assert "no_callers" in rows[unused], f"{case}: {rows}"


def test_the_second_function_of_a_typescript_class_is_counted_through_its_static_field(tmp_path):
    files, _, _ = CASES["typescript: a class field and a static one"]
    rows = _signals(tmp_path, {**PYTHON_PAIR, **files})
    assert "no_callers" not in rows.get(("k.ts", "other"), []), rows


def test_a_field_does_not_count_as_a_use_of_the_method_that_shares_its_name(tmp_path):
    """`int size;` declares a field. It is not a call of `size()`."""
    files = {
        "A.java": "package p;\n\nclass A {\n  int size;\n  int size() { return 1; }\n  int other() { return 2; }\n"
                  "  static int ONE = new A().other();\n  static int f() { return 1; }\n"
                  "  static int g() { return f(); }\n}\n",
    }
    rows = _signals(tmp_path, {**PYTHON_PAIR, **files})
    assert "no_callers" in rows[("A.java", "A.size")], rows
    assert "no_callers" not in rows.get(("A.java", "A.other"), []), rows


def test_a_constant_does_not_count_as_a_use_of_the_function_that_shares_its_name(tmp_path):
    """A Go variable and a Python constant named like a function in another file it imports."""
    files = {
        "a.py": "def LIMIT():\n    return 1\n",
        "b.py": "import a\n\nLIMIT = 5\n",
    }
    rows = _signals(tmp_path, {**PYTHON_PAIR, **files})
    assert "no_callers" in rows[("a.py", "LIMIT")], rows


def test_a_word_inside_a_quoted_string_is_not_a_use(tmp_path):
    """`NAMES = ["helper"]` holds text. A table that holds the function itself is a use."""
    files = {
        "a.py": "def helper():\n    return 1\n\ndef listed():\n    return 2\n\n"
                "NAMES = [\"helper\", 'helper']\nTABLE = {\"listed\": listed}\n",
    }
    rows = _signals(tmp_path, {**PYTHON_PAIR, **files})
    assert "no_callers" in rows[("a.py", "helper")], rows
    assert "no_callers" not in rows.get(("a.py", "listed"), []), rows


def test_a_csharp_property_initialiser_is_read(tmp_path):
    files = {
        "A.cs": "namespace P {\n  class A {\n    static int Helper() { return 1; }\n    static int Unused() { return 2; }\n"
                "    static int One { get; } = Helper();\n    static int F() { return 1; }\n"
                "    static int G() { return F(); }\n    static int Two = G();\n  }\n}\n",
    }
    rows = _signals(tmp_path, {**PYTHON_PAIR, **files})
    assert "no_callers" not in rows.get(("A.cs", "A.Helper"), []), rows
    assert "no_callers" in rows[("A.cs", "A.Unused")], rows


def test_a_field_declared_on_a_line_a_method_holds_is_not_read_as_text(tmp_path):
    """`this.size` reads a property. Read as text it would count as a call of every method named `size`.

    A TypeScript parameter property is a field whose line the constructor
    holds. A method's lines are answered by its call references, whatever is
    declared on them.
    """
    files = {
        "k.ts": "export class Box {\n  constructor(private n: number = (this as any).size) {}\n"
                "  size(): number { return 1; }\n}\n"
                "function helper(): number { return 1; }\n"
                "export const HANDLERS = {\n  other: helper,\n};\n"
                "function f(): number { return 1; }\nexport function g(): number { return f(); }\ng();\n",
    }
    rows = _signals(tmp_path, {**PYTHON_PAIR, **files})
    assert "no_callers" in rows[("k.ts", "Box.size")], rows
    assert "no_callers" not in rows.get(("k.ts", "helper"), []), rows


def test_a_method_written_inside_a_constant_is_not_read_as_text(tmp_path):
    """The constant's lines are read AROUND the method it holds (review of L-169).

    `b.size` reads a property. With the method's lines read as text, zod lost
    107 more rows (`evidence/guard_l169.txt`).
    """
    files = {
        "k.js": "class Box {\n  size() { return 1; }\n}\n"
                "function helper() { return 1; }\n"
                "const T = {\n  run(b) {\n    return b.size;\n  },\n  other: helper,\n};\n"
                "function f() { return 1; }\nfunction g() { return f(); }\ng();\n",
    }
    rows = _signals(tmp_path, {**PYTHON_PAIR, **files})
    assert "no_callers" in rows[("k.js", "Box.size")], rows
    assert "no_callers" not in rows.get(("k.js", "helper"), []), rows


def test_a_ruby_class_variable_does_not_count_as_a_use_of_the_method_behind_its_sigil(tmp_path):
    """`@@after_reload = []` declares `@@after_reload`. It is not a call of `after_reload`."""
    files = {
        "r.rb": "class Reloader\n  @@after_reload = []\n\n  def after_reload\n    1\n  end\n\n"
                "  def f\n    1\n  end\n\n  def g\n    f()\n  end\nend\n",
    }
    rows = _signals(tmp_path, {**PYTHON_PAIR, **files})
    assert "no_callers" in rows[("r.rb", "Reloader.after_reload")], rows


def test_the_declared_name_is_blanked_once(tmp_path):
    """In `static int size = size();` the second `size` is a call."""
    files = {
        "A.java": "package p;\n\nclass A {\n  static int size() { return 1; }\n  static int size = size();\n"
                  "  static int f() { return 1; }\n  static int g() { return f(); }\n}\n",
    }
    rows = _signals(tmp_path, {**PYTHON_PAIR, **files})
    assert "no_callers" not in rows.get(("A.java", "A.size"), []), rows


def test_a_quoted_word_on_a_later_line_of_a_table_is_not_a_use(tmp_path):
    files = {
        "a.py": "def helper():\n    return 1\n\ndef listed():\n    return 2\n\n"
                "NAMES = [\n    \"helper\",\n    listed,\n]\n",
    }
    rows = _signals(tmp_path, {**PYTHON_PAIR, **files})
    assert "no_callers" in rows[("a.py", "helper")], rows
    assert "no_callers" not in rows.get(("a.py", "listed"), []), rows


def test_a_rust_apostrophe_is_not_a_string(tmp_path):
    """A lifetime and a character on one line are not a pair of quotes around the call between them."""
    files = {
        "a.rs": "const fn helper(_c: char) -> &'static str { \"x\" }\n\nfn unused() -> i32 { 2 }\n\n"
                "static ONE: &'static str = helper('a');\n\n"
                "const fn f() -> i32 { 1 }\n\nconst fn g() -> i32 { f() }\n\nstatic TWO: i32 = g();\n",
    }
    rows = _signals(tmp_path, {**PYTHON_PAIR, **files})
    assert "no_callers" not in rows.get(("a.rs", "helper"), []), rows
    assert "no_callers" in rows[("a.rs", "unused")], rows


def test_a_name_inside_another_function_is_still_not_read_as_code_outside_every_function(tmp_path):
    """The text read is what lies outside functions; a function body is answered by its call references."""
    files = {
        "a.py": "def helper():\n    return 1\n",
        "b.py": "import a\n\ndef run():\n    note = 'helper'\n    return note\n",
    }
    rows = _signals(tmp_path, {**PYTHON_PAIR, **files})
    assert "no_callers" in rows[("a.py", "helper")], rows
