"""A delete blocked only by a FILE-level import says so (LEDGER L-348).

`check_delete_safe` counts every file that imports the target's file as an
external use of every symbol in it. A function its module never exports, in a
file another file `require`s or imports, came back `external_uses_blocking`
with the reason "1 other file(s) in this repo import this": a claim of a use
that no file makes. Measured on main at b4184579 with an unexported CommonJS
helper and an unexported ES module function, both blocked that way.

The block stays (a wildcard import, a computed member access or a re-export
can still reach the symbol, and no text search sees those), but the verdict is
`file_import_only`, a BOUNDED verdict that names the file-level import and the
reading that would settle it, instead of asserting external uses.

NON-VACUITY: the three `file_import_only` tests fail on main, which returns
`external_uses_blocking` for each. The controls pass on both sides: a symbol
another file NAMES, by member access or in a test, stays
`external_uses_blocking`.
"""
from __future__ import annotations

import subprocess

from jcodemunch_mcp.tools.check_delete_safe import check_delete_safe
from jcodemunch_mcp.tools.index_folder import index_folder

_LIB_JS = (
    "function helperUnused() {\n  return 1;\n}\n\n"
    "function api() {\n  return 2;\n}\n\n"
    "module.exports = { api };\n"
)
_MAIN_JS = "const lib = require('./lib');\nconsole.log(lib.api());\n"


def _index(tmp_path, files: dict[str, str]):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    store = tmp_path / "store"
    out = index_folder(path=str(repo), use_ai_summaries=False, storage_path=str(store))
    assert out.get("success"), out
    return out["repo"], str(store)


def _check(repo, store, name):
    got = check_delete_safe(repo=repo, symbol=name, storage_path=store)
    assert "error" not in got, got
    return got


def test_an_unexported_commonjs_function_is_blocked_by_the_file_import_only(tmp_path):
    repo, store = _index(tmp_path, {"lib.js": _LIB_JS, "main.js": _MAIN_JS})
    got = _check(repo, store, "helperUnused")
    assert got["verdict"] == "file_import_only", got["verdict"]
    assert "main.js" in got["recommended_action"], got["recommended_action"]
    assert "names" in got["recommended_action"], got["recommended_action"]
    # Still a block: the importer is listed, and the stop rule is not terminal.
    assert any(b.get("file") == "main.js" for b in got["blockers"]), got["blockers"]
    rule = got["stop_rule"]
    assert rule["terminal"] is False, rule
    assert any("import" in g.get("action", "") for g in rule["would_change_verdict"]), rule


def test_an_unexported_es_module_function_is_blocked_by_the_file_import_only(tmp_path):
    repo, store = _index(tmp_path, {
        "esm.mjs": "function privEsm() { return 3; }\nexport function pub() { return 4; }\n",
        "use_esm.mjs": "import { pub } from './esm.mjs';\npub();\n",
    })
    assert _check(repo, store, "privEsm")["verdict"] == "file_import_only"


def test_a_python_module_import_that_never_names_the_function(tmp_path):
    # The rule is about names, not export syntax, so it is the same in Python.
    repo, store = _index(tmp_path, {
        "pkg/util.py": "def helper():\n    return 1\n\n\ndef used():\n    return 2\n",
        "app.py": "from pkg import util\n\nprint(util.used())\n",
    })
    assert _check(repo, store, "helper")["verdict"] == "file_import_only"


# ── controls: pass on both sides ─────────────────────────────────────────────

def test_a_function_another_file_names_is_still_an_external_use(tmp_path):
    repo, store = _index(tmp_path, {"lib.js": _LIB_JS, "main.js": _MAIN_JS})
    got = _check(repo, store, "api")
    assert got["verdict"] == "external_uses_blocking", got["verdict"]
    assert got["stop_rule"]["terminal"] is True, got["stop_rule"]


def test_a_name_in_another_file_test_included_keeps_the_external_verdict(tmp_path):
    repo, store = _index(tmp_path, {
        "lib.js": _LIB_JS,
        "main.js": _MAIN_JS,
        "test/lib.test.js": "// helperUnused is covered elsewhere\nrequire('../lib');\n",
    })
    assert _check(repo, store, "helperUnused")["verdict"] == "external_uses_blocking"
