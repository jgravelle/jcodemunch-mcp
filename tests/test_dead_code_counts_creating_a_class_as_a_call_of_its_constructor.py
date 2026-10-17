"""`get_dead_code_v2` counts creating a class as a call of its constructor (LEDGER L-167, item 14).

Python, JavaScript and TypeScript name a constructor with a fixed word, and
nothing ever calls it by that word: `T()` records a call of `T`, `new K()` a
call of `K`. The tool looked for a caller of the name `__init__` or
`constructor`, found none, and reported the constructor of every class with
`no_callers` however often the class was created: live code reported dead, in a
tool whose rows feed delete decisions.

A constructor now has a caller when its class has one by the rule a function
has: the class's name is called in its own file or in a file that imports it.
A subclass that is created runs the constructor it inherits, so creating a
descendant counts for every ancestor it reaches by import. Every fixture goes through
`index_folder` and the tool.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.tools import get_dead_code_v2 as v2
from jcodemunch_mcp.tools.index_folder import index_folder

# case -> ({file: source}, ids that are created somewhere, ids nothing creates)
CASES = {
    "python: created in a function of its own file": (
        {
            "a.py": "class T:\n    def __init__(self, x):\n        self.x = x\n\n"
                    "class Never:\n    def __init__(self):\n        pass\n\n"
                    "def make():\n    return T(1)\n",
            "b.py": "from a import make\n\ndef use():\n    return make()\n",
        },
        ["a.py::T.__init__#method"], ["a.py::Never.__init__#method"],
    ),
    "python: created in a file that imports it": (
        {
            "a.py": "class T:\n    def __init__(self, x):\n        self.x = x\n\n"
                    "class Never:\n    def __init__(self):\n        pass\n",
            "b.py": "import a\n\ndef make():\n    return a.T(1)\n\ndef use():\n    return make()\n",
        },
        ["a.py::T.__init__#method"], ["a.py::Never.__init__#method"],
    ),
    "python: created outside any function": (
        {
            "a.py": "class T:\n    def __init__(self, x):\n        self.x = x\n\n"
                    "class Never:\n    def __init__(self):\n        pass\n",
            "b.py": "import a\n\nprint(a.T(1))\n\ndef f():\n    return 1\n\ndef g():\n    return f()\n",
        },
        ["a.py::T.__init__#method"], ["a.py::Never.__init__#method"],
    ),
    "python: `__new__` runs when the class is created too": (
        {
            "a.py": "class T:\n    def __new__(cls):\n        return super().__new__(cls)\n\n"
                    "class Never:\n    def __new__(cls):\n        return super().__new__(cls)\n",
            "b.py": "from a import T\n\ndef make():\n    return T()\n\ndef use():\n    return make()\n",
        },
        ["a.py::T.__new__#method"], ["a.py::Never.__new__#method"],
    ),
    "python: a subclass with no constructor of its own is created": (
        {
            "a.py": "class Base:\n    def __init__(self, x):\n        self.x = x\n\n"
                    "class Kid(Base):\n    pass\n\n"
                    "class Other:\n    def __init__(self):\n        pass\n\n"
                    "class OtherKid(Other):\n    pass\n",
            "b.py": "import a\n\ndef make():\n    return a.Kid(1)\n\ndef use():\n    return make()\n",
        },
        ["a.py::Base.__init__#method"], ["a.py::Other.__init__#method"],
    ),
    "python: a subclass with its own constructor is created": (
        {
            "a.py": "class Base:\n    def __init__(self, x):\n        self.x = x\n\n"
                    "class Kid(Base):\n    def __init__(self):\n        super().__init__(1)\n\n"
                    "class Never(Base):\n    def __init__(self):\n        super().__init__(2)\n",
            "b.py": "import a\n\ndef make():\n    return a.Kid()\n\ndef use():\n    return make()\n",
        },
        ["a.py::Base.__init__#method", "a.py::Kid.__init__#method"], ["a.py::Never.__init__#method"],
    ),
    "python: a grandchild in another file is created": (
        {
            "a.py": "class Base:\n    def __init__(self, x):\n        self.x = x\n\n"
                    "class Lone:\n    def __init__(self):\n        pass\n",
            "m.py": "from a import Base\n\nclass Mid(Base):\n    pass\n",
            "k.py": "from m import Mid\n\nclass Kid(Mid):\n    pass\n\ndef make():\n    return Kid(1)\n\n"
                    "def use():\n    return make()\n",
        },
        ["a.py::Base.__init__#method"], ["a.py::Lone.__init__#method"],
    ),
    "javascript: created in a file that imports it": (
        {
            "k.js": "export class K {\n  constructor(x) { this.x = x; }\n}\n"
                    "export class Never {\n  constructor() { this.y = 1; }\n}\n",
            "u.js": "import * as k from './k.js';\n\nexport function make() { return new k.K(1); }\n\n"
                    "export function use() { return make(); }\n",
        },
        ["k.js::K.constructor#method"], ["k.js::Never.constructor#method"],
    ),
    "javascript: created outside any function": (
        {
            "k.js": "export class K {\n  constructor(x) { this.x = x; }\n}\n"
                    "export class Never {\n  constructor() { this.y = 1; }\n}\n",
            "u.js": "import * as k from './k.js';\n\nconsole.log(new k.K(1));\n\n"
                    "function f() { return 1; }\n\nexport function g() { return f(); }\n",
        },
        ["k.js::K.constructor#method"], ["k.js::Never.constructor#method"],
    ),
    "javascript: a subclass is created": (
        {
            "k.js": "export class Base {\n  constructor(x) { this.x = x; }\n}\n"
                    "export class Kid extends Base {\n  constructor() { super(1); }\n}\n"
                    "export class Never extends Base {\n  constructor() { super(2); }\n}\n",
            "u.js": "import * as k from './k.js';\n\nexport function make() { return new k.Kid(); }\n\n"
                    "export function use() { return make(); }\n",
        },
        ["k.js::Base.constructor#method", "k.js::Kid.constructor#method"], ["k.js::Never.constructor#method"],
    ),
    "typescript: created in a file that imports it": (
        {
            "t.ts": "export class Q {\n  constructor(private x: number) {}\n}\n"
                    "export class Never {\n  constructor(private y: number) {}\n}\n",
            "v.ts": "import * as t from './t';\n\nexport function make(): t.Q { return new t.Q(1); }\n\n"
                    "export function use(): t.Q { return make(); }\n",
        },
        ["t.ts::Q.constructor#method"], ["t.ts::Never.constructor#method"],
    ),
    "typescript: a subclass of an abstract class is created": (
        {
            "t.ts": "export abstract class Base {\n  constructor(protected x: number) {}\n}\n"
                    "export class Kid extends Base {\n  constructor() { super(1); }\n}\n"
                    "export abstract class Lone {\n  constructor(protected y: number) {}\n}\n",
            "v.ts": "import * as t from './t';\n\nexport function make(): t.Kid { return new t.Kid(); }\n\n"
                    "export function use(): t.Kid { return make(); }\n",
        },
        ["t.ts::Base.constructor#method", "t.ts::Kid.constructor#method"], ["t.ts::Lone.constructor#method"],
    ),
    "tsx: created in a file that imports it": (
        {
            "t.tsx": "export class Q {\n  constructor(private x: number) {}\n}\n"
                     "export class Never {\n  constructor(private y: number) {}\n}\n",
            "v.tsx": "import * as t from './t';\n\nexport function make(): t.Q { return new t.Q(1); }\n\n"
                     "export function use(): t.Q { return make(); }\n",
        },
        ["t.tsx::Q.constructor#method"], ["t.tsx::Never.constructor#method"],
    ),
}


def _run(tmp_path, files):
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
    return out


def _signals(tmp_path, files, mode="multi_signal"):
    out = _run(tmp_path, files)
    meta = out.get("_meta", {})
    assert (meta.get("mode") or meta.get("methodology")) == mode, meta
    return {row["id"]: row["signals"] for row in out["dead_symbols"]}


@pytest.mark.parametrize("case", sorted(CASES))
def test_the_constructor_of_a_class_that_is_created_is_not_reported_without_callers(tmp_path, case):
    files, created, _ = CASES[case]
    rows = _signals(tmp_path, files)
    for ctor in created:
        assert "no_callers" not in rows.get(ctor, []), f"{case}: the class of `{ctor}` is created"


@pytest.mark.parametrize("case", sorted(CASES))
def test_the_constructor_of_a_class_nothing_creates_is_still_reported(tmp_path, case):
    files, _, never = CASES[case]
    rows = _signals(tmp_path, files)
    for ctor in never:
        assert "no_callers" in rows[ctor], f"{case}: nothing creates the class of `{ctor}`: {rows}"


def test_a_same_named_class_created_in_a_file_that_does_not_import_it_counts_for_nothing(tmp_path):
    """Python must import a class to create it: `T(1)` in `other.py` is `other.py`'s own `T`."""
    files = {
        "lib.py": "class T:\n    def __init__(self, x):\n        self.x = x\n",
        "other.py": "class T:\n    def __init__(self, x):\n        self.x = x\n\n"
                    "def make():\n    return T(1)\n\ndef use():\n    return make()\n",
        "glue.py": "import other\n\ndef run():\n    return other.use()\n",
    }
    rows = _signals(tmp_path, files)
    assert "no_callers" in rows["lib.py::T.__init__#method"], rows
    assert "no_callers" not in rows.get("other.py::T.__init__#method", []), rows


def test_a_subclass_that_is_only_declared_creates_nothing(tmp_path):
    """`class Kid(a.Base)` names `Base` and runs no constructor.

    `from a import Base` would count: a name written outside every symbol of a
    file that imports it is taken as a use, for a class as for a function.
    """
    files = {
        "a.py": "class Base:\n    def __init__(self, x):\n        self.x = x\n",
        "b.py": "import a\n\nclass Kid(a.Base):\n    pass\n\ndef f():\n    return 1\n\ndef g():\n    return f()\n",
    }
    rows = _signals(tmp_path, files)
    assert "no_callers" in rows["a.py::Base.__init__#method"], rows


def test_a_method_that_only_shares_a_created_class_name_is_not_cleared(tmp_path):
    """Creating `T` is a call of `T`'s constructor, not of `T`'s other methods."""
    files = {
        "a.py": "class T:\n    def __init__(self, x):\n        self.x = x\n\n    def idle(self):\n        return 2\n",
        "b.py": "import a\n\ndef make():\n    return a.T(1)\n\ndef use():\n    return make()\n",
    }
    rows = _signals(tmp_path, files)
    assert "no_callers" in rows["a.py::T.idle#method"], rows
    assert "no_callers" not in rows.get("a.py::T.__init__#method", []), rows


def test_a_function_that_only_has_the_constructor_word_for_a_name_is_not_a_constructor(tmp_path):
    """`constructor` outside a class body is an ordinary function nothing calls."""
    files = {
        "k.js": "export class K {\n  constructor(x) { this.x = x; }\n}\n\nfunction constructor() { return 1; }\n",
        "u.js": "import * as k from './k.js';\n\nexport function make() { return new k.K(1); }\n\n"
                "export function use() { return make(); }\n",
    }
    rows = _signals(tmp_path, files)
    assert "no_callers" in rows["k.js::constructor#function"], rows
    assert "no_callers" not in rows.get("k.js::K.constructor#method", []), rows


def test_the_mode_with_no_import_graph_counts_it_too(tmp_path):
    """One file, no import: the call-graph-only path."""
    files = {
        "a.py": "class T:\n    def __init__(self, x):\n        self.x = x\n\n"
                "class Never:\n    def __init__(self):\n        pass\n\n"
                "class Base:\n    def __init__(self):\n        pass\n\n"
                "class Kid(Base):\n    pass\n\n"
                "def make():\n    return T(1), Kid()\n\ndef use():\n    return make()\n",
    }
    rows = _signals(tmp_path, files, mode="call_graph_only")
    assert "a.py::T.__init__#method" not in rows, rows
    assert "a.py::Base.__init__#method" not in rows, rows
    assert "a.py::Never.__init__#method" in rows, rows


def test_an_index_with_no_call_reference_counts_it_from_the_text(tmp_path):
    """No function calls another here, so every file is read as text."""
    files = {
        "a.py": "class T:\n    def __init__(self, x):\n        self.x = x\n",
        "c.py": "class Never:\n    def __init__(self):\n        pass\n",
        "b.py": "import a\nimport c\n\nONE = a.T\n",
    }
    out = _run(tmp_path, files)
    rows = {row["id"]: row["signals"] for row in out["dead_symbols"]}
    assert "no_callers" not in rows.get("a.py::T.__init__#method", []), rows
    assert "no_callers" in rows["c.py::Never.__init__#method"], rows


def test_a_typescript_class_that_extends_a_javascript_one_counts(tmp_path):
    files = {
        "k.js": "export class Base {\n  constructor(x) { this.x = x; }\n}\n",
        "t.ts": "import * as k from './k.js';\n\nexport class Kid extends Base {\n  constructor() { super(1); }\n}\n\n"
                "export function make(): Kid { return new Kid(); }\n\nexport function use(): Kid { return make(); }\n",
    }
    rows = _signals(tmp_path, files)
    assert "no_callers" not in rows.get("k.js::Base.constructor#method", []), rows


def test_a_subclass_in_another_language_counts_for_nothing(tmp_path):
    """A JavaScript `class Kid extends Base` does not extend the Python `Base`."""
    files = {
        "a.py": "class Base:\n    def __init__(self, x):\n        self.x = x\n\ndef f():\n    return 1\n\ndef g():\n    return f()\n",
        "b.py": "import a\n\ndef h():\n    return a.g()\n",
        "k.js": "class Kid extends Base {\n  constructor() { super(1); }\n}\n\nexport function make() { return new Kid(); }\n\n"
                "export function use() { return make(); }\n",
    }
    rows = _signals(tmp_path, files)
    assert "no_callers" in rows["a.py::Base.__init__#method"], rows
    assert "no_callers" not in rows.get("k.js::Kid.constructor#method", []), rows


def test_a_created_subclass_of_a_same_named_base_it_does_not_import_counts_for_nothing(tmp_path):
    """`app.py` extends its own `Base`; `lib.py`'s `Base` is another class (review of L-167)."""
    files = {
        "lib.py": "class Base:\n    def __init__(self, x):\n        self.x = x\n",
        "app.py": "class Base:\n    def __init__(self, x):\n        self.x = x\n\nclass Kid(Base):\n    pass\n\n"
                  "def make():\n    return Kid(1)\n",
        "glue.py": "import app\n\ndef run():\n    return app.make()\n",
    }
    rows = _signals(tmp_path, files)
    assert "no_callers" in rows["lib.py::Base.__init__#method"], rows
    assert "no_callers" not in rows.get("app.py::Base.__init__#method", []), rows


def test_a_class_that_implements_another_runs_no_constructor_of_it(tmp_path):
    """TypeScript lets a class implement a class; that creates nothing of it."""
    files = {
        "h.ts": "export class Handler {\n  constructor(private x: number) {}\n}\n",
        "i.ts": "import * as h from './h';\n\nexport class Impl implements Handler {\n  constructor() {}\n}\n\n"
                "export function make(): Impl { return new Impl(); }\n\nexport function use(): Impl { return make(); }\n",
    }
    rows = _signals(tmp_path, files)
    assert "no_callers" in rows["h.ts::Handler.constructor#method"], rows
    assert "no_callers" not in rows.get("i.ts::Impl.constructor#method", []), rows


def test_a_name_many_bases_share_costs_one_row_per_class(tmp_path):
    """N files, each with its own `Base` and five subclasses: 6 rows a file, not 5N + 1.

    Joined by name across the index this was N squared rows, twenty million at
    2,000 files (review of L-167).
    """
    from jcodemunch_mcp.tools._call_graph import constructors_as_their_classes

    symbols = []
    for n in range(300):
        file = f"m{n}.py"
        symbols.append({"id": f"{file}::Base#class", "kind": "class", "name": "Base", "file": file,
                        "language": "python", "signature": "class Base"})
        symbols.append({"id": f"{file}::Base.__init__#method", "kind": "method", "name": "__init__", "file": file,
                        "language": "python", "parent": f"{file}::Base#class"})
        for k in range(5):
            symbols.append({"id": f"{file}::Kid{k}#class", "kind": "class", "name": f"Kid{k}", "file": file,
                            "language": "python", "signature": f"class Kid{k}(Base)"})
    rows = constructors_as_their_classes(symbols, {})
    assert len(rows) == 300 * 6
    assert {row["file"] for row in rows if row["id"] == "m7.py::Base.__init__#method"} == {"m7.py"}


def test_two_classes_that_name_each_other_as_base_end(tmp_path):
    """Not valid Python, and still parsed: the walk over descendants must stop."""
    files = {
        "a.py": "class A(B):\n    def __init__(self):\n        pass\n\nclass B(A):\n    def __init__(self):\n        pass\n\n"
                "class Lone:\n    def __init__(self):\n        pass\n",
        "b.py": "import a\n\ndef make():\n    return a.B()\n\ndef use():\n    return make()\n",
    }
    rows = _signals(tmp_path, files)
    assert "no_callers" not in rows.get("a.py::A.__init__#method", []), rows
    assert "no_callers" not in rows.get("a.py::B.__init__#method", []), rows
    assert "no_callers" in rows["a.py::Lone.__init__#method"], rows


def test_the_constructor_words_are_pinned():
    """A language is added only when its constructor has a fixed name nothing calls.

    Ruby's `initialize` is one, and is not here: `T.new(1)` records `new` and
    not `T`, so there is no recorded call of the class to count (LEDGER L-169).
    """
    from jcodemunch_mcp.tools._call_graph import CONSTRUCTOR_WORDS

    assert CONSTRUCTOR_WORDS == {
        "python": frozenset({"__init__", "__new__"}),
        "javascript": frozenset({"constructor"}),
        "typescript": frozenset({"constructor"}),
        "tsx": frozenset({"constructor"}),
    }
