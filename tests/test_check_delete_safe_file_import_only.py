"""A delete blocked only by a FILE-level import says so (LEDGER L-348).

`check_delete_safe` counted every file that imports the target's file as an
external use of every symbol in it. A function its module never exports, in a
file another file `require`s or imports, came back `external_uses_blocking`
with the reason "1 other file(s) in this repo import this": a claim of a use
that no file makes. Measured on main at b4184579 with an unexported CommonJS
helper and an unexported ES module function, both blocked that way.

The block stays (a wildcard import, a computed member access or a re-export
can still reach the symbol, and no text search sees those), but the verdict is
`file_import_only`, a BOUNDED verdict that names the file-level import and the
reading that would settle it, instead of asserting external uses.

The properties, not the mechanism:
- it never moves a symbol toward safe: its confidence is never above the
  `external_uses_blocking` it replaces;
- it is an absence claim, so it needs evidence of absence: a same-file use, a
  reference search that raised, or an importer or the symbol's own file the
  search could not read all keep `external_uses_blocking`;
- on a corpus that cannot back an absence claim it gains the re-index gap.

NON-VACUITY: the four `file_import_only` tests fail on main, which returns
`external_uses_blocking` for each. The rest pass on both sides, and three of
them are the witnesses for guards a deletion would otherwise leave unnoticed:
the unreadable importer and the unreadable own file (the content check) and
the raised search (`refs_searched`). Each was confirmed to fail with its guard
removed.
"""
from __future__ import annotations

import subprocess

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.tools import check_references
from jcodemunch_mcp.tools.check_delete_safe import check_delete_safe
from jcodemunch_mcp.tools.index_folder import index_folder

_LIB_JS = (
    "function helperUnused() {\n  return 1;\n}\n\n"
    "function api() {\n  return 2;\n}\n\n"
    "module.exports = { api };\n"
)
_MAIN_JS = "const lib = require('./lib');\nconsole.log(lib.api());\n"
_GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]


def _index(tmp_path, files: dict[str, str], *, commit: bool = True):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    if commit:
        subprocess.run([*_GIT, "-C", str(repo), "add", "-A"], check=True)
        subprocess.run([*_GIT, "-C", str(repo), "commit", "-q", "-m", "x"], check=True)
    store = tmp_path / "store"
    out = index_folder(path=str(repo), use_ai_summaries=False, storage_path=str(store))
    assert out.get("success"), out
    return out["repo"], str(store)


def _check(repo, store, name):
    got = check_delete_safe(repo=repo, symbol=name, storage_path=store)
    assert "error" not in got, got
    return got


def _actions(got):
    return [g.get("action", "") for g in got["stop_rule"]["would_change_verdict"]]


def test_an_unexported_commonjs_function_is_blocked_by_the_file_import_only(tmp_path):
    repo, store = _index(tmp_path, {"lib.js": _LIB_JS, "main.js": _MAIN_JS})
    got = _check(repo, store, "helperUnused")
    assert got["verdict"] == "file_import_only", got["verdict"]
    # Still a block: the importer is listed, and the stop rule is not terminal.
    assert any(b.get("file") == "main.js" for b in got["blockers"]), got["blockers"]
    rule = got["stop_rule"]
    assert rule["terminal"] is False, rule
    gap = [g for g in rule["would_change_verdict"] if "main.js" in g.get("why", "")]
    assert gap and "helperUnused" in gap[0]["why"], rule
    # An adequate corpus adds no re-index gap.
    assert got["corpus_adequacy"]["adequate"] is True, got["corpus_adequacy"]
    assert "re-index this repo" not in _actions(got), rule


def test_it_never_moves_a_symbol_toward_safe(tmp_path):
    # The confidence it replaces is `external_uses_blocking`'s; the symbol with
    # no importer at all is the other bound. Adding an importer must not raise it.
    repo, store = _index(tmp_path, {"lib.js": _LIB_JS, "main.js": _MAIN_JS})
    named = _check(repo, store, "api")
    assert named["verdict"] == "external_uses_blocking"
    got = _check(repo, store, "helperUnused")
    assert got["verdict"] == "file_import_only"
    assert got["confidence"] <= named["confidence"], (got["confidence"], named["confidence"])
    alone = tmp_path / "alone"
    alone.mkdir()
    repo2, store2 = _index(alone, {"lib.js": _LIB_JS})
    assert got["confidence"] <= _check(repo2, store2, "helperUnused")["confidence"]


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


def test_an_inadequate_corpus_keeps_the_verdict_and_adds_the_reindex_gap(tmp_path):
    # No commit: the index records no revision, so an absence cannot be backed.
    repo, store = _index(tmp_path, {"lib.js": _LIB_JS, "main.js": _MAIN_JS}, commit=False)
    got = _check(repo, store, "helperUnused")
    if got["corpus_adequacy"]["adequate"]:
        raise AssertionError("fixture must be an inadequate corpus", got["corpus_adequacy"])
    assert got["verdict"] == "file_import_only", got["verdict"]
    assert "re-index this repo" in _actions(got), got["stop_rule"]


def test_a_dynamic_loader_keeps_the_verdict_and_adds_the_loader_gap(tmp_path):
    # A loader is one more way to reach the file, so the dynamic gate adds its
    # gap and blocker and keeps the narrower verdict, as the corpus gate does.
    repo, store = _index(tmp_path, {
        "adapters/__init__.py": "",
        "adapters/alpha.py": "def helper():\n    return 1\n\n\ndef used():\n    return 2\n",
        "app.py": "from adapters import alpha\n\nprint(alpha.used())\n",
        "run.py": (
            "import importlib\n\n\ndef make(name):\n"
            "    return importlib.import_module(f'adapters.{name}')\n"
        ),
    })
    got = _check(repo, store, "helper")
    assert got["verdict"] == "file_import_only", got["verdict"]
    assert got["confidence"] <= 0.25, got["confidence"]
    assert "read the named loaders for the module names they can produce" in _actions(got)
    assert any(b.get("kind") == "dynamic_import_boundary" for b in got["blockers"]), got["blockers"]


# ── controls and guard witnesses: pass on both sides ─────────────────────────

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


def test_a_same_file_use_is_a_found_use_and_keeps_the_external_verdict(tmp_path):
    lib = (
        "function helper() {\n  return 1;\n}\n\n"
        "function api() {\n  return helper();\n}\n\n"
        "module.exports = { api };\n"
    )
    repo, store = _index(tmp_path, {"lib.js": lib, "main.js": _MAIN_JS})
    assert _check(repo, store, "helper")["verdict"] == "external_uses_blocking"


def test_an_importer_the_search_could_not_read_keeps_the_external_verdict(tmp_path):
    # GUARD WITNESS (the content check): `pub` appears only at its definition
    # and in the importer. With the importer's cached content gone the search
    # skips it and finds no use, though `import { pub }` names it.
    repo, store = _index(tmp_path, {
        "esm.mjs": "export function pub() { return 4; }\n",
        "use_esm.mjs": "import { pub } from './esm.mjs';\npub();\n",
    })
    assert _check(repo, store, "pub")["verdict"] == "external_uses_blocking"
    owner, name = repo.split("/", 1)
    st = IndexStore(base_path=store)
    cached = st._safe_content_path(st._content_dir(owner, name), "use_esm.mjs")
    assert cached and cached.exists(), cached
    cached.unlink()
    assert _check(repo, store, "pub")["verdict"] == "external_uses_blocking"


def test_the_symbols_own_file_unread_keeps_the_external_verdict(tmp_path):
    # GUARD WITNESS (the target file in the read check): `priv` is used only
    # inside its own file. With that file's cached content gone the search
    # cannot see the use, so the absence is not established.
    repo, store = _index(tmp_path, {
        "esm.mjs": "function priv() { return 3; }\nexport function pub() { return priv(); }\n",
        "use_esm.mjs": "import { pub } from './esm.mjs';\npub();\n",
    })
    assert _check(repo, store, "priv")["verdict"] == "external_uses_blocking"
    owner, name = repo.split("/", 1)
    st = IndexStore(base_path=store)
    cached = st._safe_content_path(st._content_dir(owner, name), "esm.mjs")
    assert cached and cached.exists(), cached
    cached.unlink()
    assert _check(repo, store, "priv")["verdict"] == "external_uses_blocking"


def test_a_reference_search_that_raised_keeps_the_external_verdict(tmp_path, monkeypatch):
    # GUARD WITNESS (`refs_searched`): a search that raised found nothing.
    repo, store = _index(tmp_path, {"lib.js": _LIB_JS, "main.js": _MAIN_JS})

    def boom(*a, **k):
        raise RuntimeError("search failed")

    monkeypatch.setattr(check_references, "_check_single", boom)
    assert _check(repo, store, "helperUnused")["verdict"] == "external_uses_blocking"

