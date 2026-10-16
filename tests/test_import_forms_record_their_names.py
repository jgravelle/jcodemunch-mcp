"""Import forms that recorded no names, or no edge, record them (LEDGER L-177).

Probed on 1.108.369 with `extract_imports` on one statement each:

- Python `import os, \\` continued on the next line: `sys` was lost and the
  backslash itself became a specifier.
- Scala `import a.b.{C, D}`: the edge `a.b` with no names, on one line or several.
- PHP group use `use App\\Models\\{User, Post};`: no edge at all.
- Haskell `import A.B (c, d)`: the edge with no names, on one line or several.
- Dart `import 'a.dart' show C, D;`: the edge with no names.
- JavaScript `const {a, b} = require("./m")`: the edge with no names.
- Kotlin `import a.b.D as E`: no edge, wherever it sat in the file.

A name nobody records is a name `find_references` answers 0 for and
`check_references` counts no import of. Each reader believes a list only when
the statement is whole (it closes, and every item has the language's shape);
otherwise it records what it recorded before.
"""

from __future__ import annotations

import pytest

from jcodemunch_mcp.parser.imports import extract_imports
from jcodemunch_mcp.tools.check_references import check_references
from jcodemunch_mcp.tools.find_references import find_references
from jcodemunch_mcp.tools.index_folder import index_folder

BS = chr(92)

LANG = {
    "python": "m.py",
    "scala": "M.scala",
    "php": "m.php",
    "haskell": "M.hs",
    "dart": "m.dart",
    "javascript": "m.js",
    "typescript": "m.ts",
    "kotlin": "M.kt",
}


def _edges(src: str, language: str) -> dict[str, list[str]]:
    return {e["specifier"]: e["names"] for e in extract_imports(src, LANG[language], language)}


# (language, source, the edges it must record)
RECORDS = {
    # Python: a backslash continuation of a plain `import`.
    "python continuation": ("python", "import os, " + BS + "\n    sys\n", {"os": [], "sys": []}),
    "python continuation, dotted": ("python", "import os.path, " + BS + "\n    a.b as c\n", {"os.path": [], "a.b": []}),
    "python backslash first": ("python", "import " + BS + "\n    os, sys\n", {"os": [], "sys": []}),
    "python one line": ("python", "import os, sys\n", {"os": [], "sys": []}),
    # Scala: a brace selector.
    "scala one line": ("scala", "import a.b.{C, D}\n", {"a.b": ["C", "D"]}),
    "scala several lines": ("scala", "import a.b.{\n  C,\n  D\n}\n", {"a.b": ["C", "D"]}),
    "scala rename": ("scala", "import a.b.{C => X, D}\n", {"a.b": ["C", "D"]}),
    "scala 3 rename": ("scala", "import a.b.{C as X, D}\n", {"a.b": ["C", "D"]}),
    "scala hidden and wildcard": ("scala", "import a.b.{C => _, D, _}\n", {"a.b": ["D"]}),
    "scala given and a name that starts with it": ("scala", "import a.b.{given, given Ord[Int], givenName, C}\n",
                                                   {"a.b": ["givenName", "C"]}),
    "scala plain, as before":("scala", "import a.b.C\n", {"a.b.C": []}),
    # PHP: a group use is one edge per member, as `use A\B\C;` is.
    "php group one line": ("php", "<?php\nuse App" + BS + "Models" + BS + "{User, Post};\n",
                           {"App\\Models\\User": ["User"], "App\\Models\\Post": ["Post"]}),
    "php group several lines": ("php", "<?php\nuse App" + BS + "Models" + BS + "{\n    User,\n    Post,\n};\n",
                                {"App\\Models\\User": ["User"], "App\\Models\\Post": ["Post"]}),
    "php group nested and aliased": ("php", "<?php\nuse App" + BS + "{Models" + BS + "User, Http" + BS + "Kernel as K};\n",
                                     {"App\\Models\\User": ["User"], "App\\Http\\Kernel": ["Kernel"]}),
    "php plain, as before": ("php", "<?php\nuse App" + BS + "Models" + BS + "User;\n", {"App\\Models\\User": ["User"]}),
    # Haskell: an import list.
    "haskell one line": ("haskell", "module M where\nimport A.B (c, d)\n", {"A.B": ["c", "d"]}),
    "haskell several lines": ("haskell", "module M where\nimport A.B\n  ( c\n  , d\n  )\n", {"A.B": ["c", "d"]}),
    "haskell qualified as": ("haskell", "import qualified A.B as X (c, d)\n", {"A.B": ["c", "d"]}),
    "haskell constructors and operators": ("haskell", "import A.B (T(..), U(V, W), (<>), type (+), c)\n",
                                           {"A.B": ["T", "U", "<>", "+", "c"]}),
    "haskell comment in the list": ("haskell", "import A.B\n  ( c -- the first\n  , d\n  )\n", {"A.B": ["c", "d"]}),
    "haskell no space before the list": ("haskell", "import A.B(c, d)\n", {"A.B": ["c", "d"]}),
    "haskell hiding":("haskell", "import A.B hiding (c, d)\n", {"A.B": []}),
    "haskell no list, as before": ("haskell", "import A.B\nimport C\n", {"A.B": [], "C": []}),
    # Dart: a `show` combinator.
    "dart one line": ("dart", "import 'a.dart' show C, D;\n", {"a.dart": ["C", "D"]}),
    "dart show on the next line": ("dart", "import 'a.dart'\n    show C, D;\n", {"a.dart": ["C", "D"]}),
    "dart as and show": ("dart", "import 'a.dart' as p show C;\n", {"a.dart": ["C"]}),
    "dart hide": ("dart", "import 'a.dart' hide C;\n", {"a.dart": []}),
    "dart plain, as before": ("dart", "import 'a.dart';\n", {"a.dart": []}),
    # JavaScript / TypeScript: a destructuring require.
    "js one line": ("javascript", 'const {a, b} = require("./m");\n', {"./m": ["a", "b"]}),
    "js several lines": ("javascript", 'const {\n  a,\n  b\n} = require("./m");\n', {"./m": ["a", "b"]}),
    "js renamed and defaulted": ("javascript", "let { a: x, b = 1 } = require('./m');\n", {"./m": ["a", "b"]}),
    "ts destructuring require": ("typescript", 'const { a } = require("./m");\n', {"./m": ["a"]}),
    "js plain require, as before": ("javascript", 'const m = require("./m");\n', {"./m": []}),
    "js rest is everything": ("javascript", 'const { a, ...rest } = require("./m");\n', {"./m": []}),
    # A merge keeps every name: `find_references` finds a file only through them (L-201 is the whole-module half).
    "js two destructurings": ("javascript", 'const {a} = require("./m");\nconst {b} = require("./m");\n',
                              {"./m": ["a", "b"]}),
    "ts named import beside a type-position import()": (
        "typescript", "import {a} from './m';\ntype T = import('./m').Foo;\n", {"./m": ["a"]}),
    "ts named import beside a commented require": (
        "typescript", "import {a} from './m';\n// const m = require('./m');\n", {"./m": ["a"]}),
    "ts named import beside a side-effect import": ("typescript", "import './m';\nimport {a} from './m';\n", {"./m": ["a"]}),
    # Kotlin: an aliased import.
    "kotlin alias alone": ("kotlin", "import a.b.D as E\n", {"a.b.D": ["D"]}),
    "kotlin alias after another": ("kotlin", "import a.b.C\nimport a.b.D as E\n", {"a.b.C": ["C"], "a.b.D": ["D"]}),
}


@pytest.mark.parametrize("case", sorted(RECORDS))
def test_each_form_records_its_edges_and_names(case):
    language, src, want = RECORDS[case]
    assert _edges(src, language) == want


# A statement that is not whole is believed no further than before.
CONSERVATIVE = {
    "scala brace never closes": ("scala", "import a.b.{C,\n  D\n\nobject X\n", {"a.b": []}),
    "haskell list never closes": ("haskell", "import A.B (c,\nd\nfoo = 1\n", {"A.B": []}),
    "haskell list on an unindented line": ("haskell", "import A.B\n(c, d)\n", {"A.B": []}),
    "dart show without a semicolon": ("dart", "import 'a.dart' show C\nvoid main() {}\n", {"a.dart": []}),
    "js nested destructuring": ("javascript", 'const { a: { b } } = require("./m");\n', {"./m": []}),
    "php group never closes": ("php", "<?php\nuse App" + BS + "{User,\nclass X {}\n", {}),
}


@pytest.mark.parametrize("case", sorted(CONSERVATIVE))
def test_a_statement_that_is_not_whole_adds_no_name(case):
    language, src, want = CONSERVATIVE[case]
    assert _edges(src, language) == want


DART_BACKTRACK = {
    # CodeQL's three witnesses for the first `_DART_CLAUSE`: no `;`, so the regex must fail, and it
    # tried every way to split each word between a name and the next keyword (2 more repeats, 4x the time).
    "as": "as " + "0deferred as " * 60 + "!",
    "show": "show " + "0deferred as 0hide " * 60 + "!",
    "show list": "show 0," + "0deferred as 0hide 0," * 60 + "!",
}


@pytest.mark.parametrize("case", sorted(DART_BACKTRACK))
def test_a_dart_clause_that_never_ends_fails_without_backtracking(case):
    """Run in a child so an exponential regex is killed, not left to hang the suite.

    At 60 repeats the old pattern needed on the order of 2**40 times its 18-repeat cost;
    the fixed one reads each word one way, so the child returns at once.
    """
    import subprocess
    import sys
    from pathlib import Path

    src_dir = Path(__file__).resolve().parents[1] / "src"
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "from jcodemunch_mcp.parser.imports import extract_imports;"
        "print(extract_imports(sys.argv[2], 'm.dart', 'dart'))"
    )
    src = "import 'a.dart' " + DART_BACKTRACK[case] + "\n"
    out = subprocess.run(
        [sys.executable, "-c", code, str(src_dir), src],
        capture_output=True, text=True, encoding="utf-8", timeout=60, stdin=subprocess.DEVNULL,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[{'specifier': 'a.dart', 'names': []}]", out.stdout


def test_the_parser_generation_moved_so_stored_indexes_re_read_these_files():
    """`files.imports` changes on unchanged content; without the bump an existing index keeps the empty lists."""
    from jcodemunch_mcp.storage.index_store import PARSER_GENERATION

    assert PARSER_GENERATION >= 25


@pytest.fixture()
def repo(tmp_path):
    src = tmp_path / "proj"
    (src / "src" / "Models").mkdir(parents=True)
    (src / "src" / "Models" / "User.php").write_text(
        "<?php\nnamespace App\\Models;\n\nclass User {}\n", encoding="utf-8"
    )
    (src / "src" / "Http.php").write_text(
        "<?php\nnamespace App;\n\nuse App\\Models\\{\n    User,\n};\n\nfunction make() { return new User(); }\n",
        encoding="utf-8",
    )
    store = tmp_path / "store"
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store), identity_mode="local")
    assert r.get("success"), r
    return r["repo"], str(store)


def test_find_references_sees_a_class_named_in_a_group_use(repo):
    name, store = repo
    out = find_references(name, "User", storage_path=store)
    files = [r["file"] for r in out["references"]]
    assert files == ["src/Http.php"], out


def test_check_references_counts_the_group_use_import(repo):
    name, store = repo
    out = check_references(name, "User", storage_path=store)
    files = [r["file"] for r in out.get("import_references", [])]
    assert "src/Http.php" in files, out
