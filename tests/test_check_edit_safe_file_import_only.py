"""An edit flagged only by a FILE-level import says so (LEDGER L-349).

`check_edit_safe` built `signature_impact` from file-level importers, so a
function its module never exports, in a file another file `require`s or
imports, read "1 external/cross-repo caller(s) depend on this" with the
importer listed as an `external_import` blocker: a caller no file is. Same
mechanism as L-348 in `check_delete_safe`, one tool over; that fix did not
reach this one. Reproduced on main at b4184579 with the L-348 fixture.

The verdict is `file_import_only`, a BOUNDED verdict that names the file-level
import and the reading that would settle it. The properties:
- it never reads as safer than `signature_impact`: an import can still reach
  the symbol through a wildcard import, a computed member access or a
  re-export, and no text search sees those;
- it is an absence claim, so it needs evidence of absence: a use in any file
  (its own included), a reference search that raised, and an importer or own
  file the search could not read all keep `signature_impact`;
- a cross-repo importer or a compiler-verified caller keeps `signature_impact`.

NON-VACUITY: the four `file_import_only` tests fail on main, which returns
`signature_impact` for each.
"""
from __future__ import annotations

import subprocess

from jcodemunch_mcp.storage import IndexStore
from jcodemunch_mcp.tools import check_references
from jcodemunch_mcp.tools.check_edit_safe import check_edit_safe
from jcodemunch_mcp.tools.index_folder import index_folder

_LIB_JS = (
    "function helperUnused() {\n  return 1;\n}\n\n"
    "function api() {\n  return 2;\n}\n\n"
    "module.exports = { api };\n"
)
_MAIN_JS = "const lib = require('./lib');\nconsole.log(lib.api());\n"
_GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid"]


def _index(tmp_path, files: dict[str, str]):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    subprocess.run([*_GIT, "-C", str(repo), "add", "-A"], check=True)
    subprocess.run([*_GIT, "-C", str(repo), "commit", "-q", "-m", "x"], check=True)
    store = tmp_path / "store"
    out = index_folder(path=str(repo), use_ai_summaries=False, storage_path=str(store))
    assert out.get("success"), out
    return out["repo"], str(store)


def _check(repo, store, name):
    got = check_edit_safe(repo=repo, symbol=name, storage_path=store)
    assert "error" not in got, got
    return got


def _actions(got):
    return [g.get("action", "") for g in got["stop_rule"]["would_change_verdict"]]


def test_an_unexported_commonjs_function_is_flagged_by_the_file_import_only(tmp_path):
    repo, store = _index(tmp_path, {"lib.js": _LIB_JS, "main.js": _MAIN_JS})
    got = _check(repo, store, "helperUnused")
    assert got["verdict"] == "file_import_only", got["verdict"]
    # The importer is still listed, but not as a caller.
    imp = [b for b in got["blockers"] if b.get("file") == "main.js"]
    assert imp and "caller" not in imp[0]["info"], got["blockers"]
    assert "caller(s) depend on this" not in got["recommended_action"], got["recommended_action"]
    rule = got["stop_rule"]
    assert rule["terminal"] is False, rule
    gap = [g for g in rule["would_change_verdict"] if "main.js" in g.get("why", "")]
    assert gap and "helperUnused" in gap[0]["why"], rule


def test_it_never_reads_as_safer_than_signature_impact(tmp_path):
    repo, store = _index(tmp_path, {"lib.js": _LIB_JS, "main.js": _MAIN_JS})
    named = _check(repo, store, "api")
    assert named["verdict"] == "signature_impact"
    got = _check(repo, store, "helperUnused")
    assert got["verdict"] == "file_import_only"
    assert got["confidence"] <= named["confidence"], (got["confidence"], named["confidence"])


def test_an_unexported_es_module_function_is_flagged_by_the_file_import_only(tmp_path):
    repo, store = _index(tmp_path, {
        "esm.mjs": "function privEsm() { return 3; }\nexport function pub() { return 4; }\n",
        "use_esm.mjs": "import { pub } from './esm.mjs';\npub();\n",
    })
    assert _check(repo, store, "privEsm")["verdict"] == "file_import_only"


def test_a_python_module_import_that_never_names_the_function(tmp_path):
    repo, store = _index(tmp_path, {
        "pkg/util.py": "def helper():\n    return 1\n\n\ndef used():\n    return 2\n",
        "app.py": "from pkg import util\n\nprint(util.used())\n",
    })
    assert _check(repo, store, "helper")["verdict"] == "file_import_only"


# ── controls and guard witnesses: pass on both sides ─────────────────────────

def test_a_function_another_file_names_keeps_signature_impact(tmp_path):
    repo, store = _index(tmp_path, {"lib.js": _LIB_JS, "main.js": _MAIN_JS})
    got = _check(repo, store, "api")
    assert got["verdict"] == "signature_impact", got["verdict"]
    assert got["stop_rule"]["terminal"] is True, got["stop_rule"]


def test_a_name_in_a_test_file_keeps_signature_impact(tmp_path):
    repo, store = _index(tmp_path, {
        "lib.js": _LIB_JS,
        "main.js": _MAIN_JS,
        "test/lib.test.js": "// helperUnused is covered elsewhere\nrequire('../lib');\n",
    })
    assert _check(repo, store, "helperUnused")["verdict"] == "signature_impact"


def test_a_same_file_use_keeps_signature_impact(tmp_path):
    lib = (
        "function helper() {\n  return 1;\n}\n\n"
        "function api() {\n  return helper();\n}\n\n"
        "module.exports = { api };\n"
    )
    repo, store = _index(tmp_path, {"lib.js": lib, "main.js": _MAIN_JS})
    assert _check(repo, store, "helper")["verdict"] == "signature_impact"


def test_an_importer_the_search_could_not_read_keeps_signature_impact(tmp_path):
    # GUARD WITNESS (the content check): `pub` appears only at its definition
    # and in the importer. With the importer's cached content gone the search
    # skips it and finds no use, though `import { pub }` names it.
    repo, store = _index(tmp_path, {
        "esm.mjs": "export function pub() { return 4; }\n",
        "use_esm.mjs": "import { pub } from './esm.mjs';\npub();\n",
    })
    assert _check(repo, store, "pub")["verdict"] == "signature_impact"
    owner, name = repo.split("/", 1)
    st = IndexStore(base_path=store)
    cached = st._safe_content_path(st._content_dir(owner, name), "use_esm.mjs")
    assert cached and cached.exists(), cached
    cached.unlink()
    assert _check(repo, store, "pub")["verdict"] == "signature_impact"


def test_the_symbols_own_file_unread_keeps_signature_impact(tmp_path):
    # GUARD WITNESS (the target file in the read check).
    repo, store = _index(tmp_path, {
        "esm.mjs": "function priv() { return 3; }\nexport function pub() { return priv(); }\n",
        "use_esm.mjs": "import { pub } from './esm.mjs';\npub();\n",
    })
    assert _check(repo, store, "priv")["verdict"] == "signature_impact"
    owner, name = repo.split("/", 1)
    st = IndexStore(base_path=store)
    cached = st._safe_content_path(st._content_dir(owner, name), "esm.mjs")
    assert cached and cached.exists(), cached
    cached.unlink()
    assert _check(repo, store, "priv")["verdict"] == "signature_impact"


def test_a_reference_search_that_raised_keeps_signature_impact(tmp_path, monkeypatch):
    # GUARD WITNESS (`refs_searched`): a search that raised found nothing.
    repo, store = _index(tmp_path, {"lib.js": _LIB_JS, "main.js": _MAIN_JS})

    def boom(*a, **k):
        raise RuntimeError("search failed")

    monkeypatch.setattr(check_references, "_check_single", boom)
    assert _check(repo, store, "helperUnused")["verdict"] == "signature_impact"


def test_both_preflights_read_importers_through_one_rule():
    # The read rule has one home; a second copy is how L-349 happened.
    from jcodemunch_mcp.tools import _importer_reads, check_delete_safe, check_edit_safe as ces

    assert check_delete_safe._content_search_read_every is _importer_reads.content_search_read_every
    assert ces._content_search_read_every is _importer_reads.content_search_read_every
