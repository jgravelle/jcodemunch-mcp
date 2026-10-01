"""The deletion investigator reads the import match `check_references` made (LEDGER L-90).

`check_references` compares names through `_fold`, so `from shapes import
<fi-ligature>le` is an import of `file` (L-84) and an import spelled with a
Unicode escape is an import of the plain name (L-86). The investigator then
re-filtered those rows with a raw `target_name in names`, dropped both, and
answered `export_not_imported` SATISFIED for a name a live file imports.

The filter it needed was only "named, not specifier-stem", which every row
already states as `match_type`. The last test pins that half: a file imported
by its stem alone is not an import of a name.

Each importer here has an importer of its own. When this was written the
investigator counted a file nothing imports as unreachable, entry points
included (LEDGER L-94, fixed since); the fixtures stay as they are so the two
properties are tested apart.
"""

from __future__ import annotations

from pathlib import Path

from jcodemunch_mcp.investigator import REFUTED, SATISFIED, investigate_deletion_safety
from jcodemunch_mcp.tools.index_folder import index_folder

LIGATURE = chr(0xFB01)  # one code point, NFKC-normalises to "fi"
BS = chr(92)  # a literal escape typed here would reach disk decoded
ESCAPED = BS + "u0066ile"  # `file`, first letter written as an escape


def _index(root: Path, files: dict[str, str]) -> tuple[str, str]:
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    storage = str(root / ".index")
    res = index_folder(str(root), use_ai_summaries=False, storage_path=storage)
    return res.get("repo", str(root)), storage


def _ob(result: dict, name: str) -> dict:
    for o in result["obligations"]:
        if o["obligation"] == name:
            return o
    raise AssertionError(f"obligation {name!r} missing from {result['obligations']}")


def test_an_import_in_another_unicode_form_refutes(tmp_path):
    files = {
        "shapes.py": "def file():\n    return 1\n",
        "user.py": f"from shapes import {LIGATURE}le\n\n\ndef use():\n    return {LIGATURE}le()\n",
        "main.py": "from user import use\n\nprint(use())\n",
    }
    assert LIGATURE in files["user.py"] and "import file" not in files["user.py"]
    repo, storage = _index(tmp_path, files)
    r = investigate_deletion_safety(repo, "file", storage_path=storage)
    ob = _ob(r, "export_not_imported")
    assert ob["status"] == REFUTED, ob
    assert any("user.py" in e for e in ob["evidence"]), ob


def test_an_import_written_with_an_escape_refutes(tmp_path):
    files = {
        "package.json": '{"name":"fx","version":"1.0.0","type":"module","main":"src/main.js"}',
        "src/shapes.js": "export function file() { return 1; }\n",
        "src/user.js": (
            "import { " + ESCAPED + " } from './shapes.js';\n"
            "export function use() { return file(); }\n"
        ),
        "src/main.js": "import { use } from './user.js';\nexport function boot() { return use(); }\n",
    }
    assert BS + "u0066" in files["src/user.js"]
    repo, storage = _index(tmp_path, files)
    r = investigate_deletion_safety(repo, "file", storage_path=storage)
    ob = _ob(r, "export_not_imported")
    assert ob["status"] == REFUTED, ob
    assert any("user.js" in e for e in ob["evidence"]), ob


def test_a_specifier_stem_match_is_not_an_import_of_the_name(tmp_path):
    """`import { other } from './file.js'` names the FILE `file`, not the export."""
    files = {
        "package.json": '{"name":"fx","version":"1.0.0","type":"module","main":"src/main.js"}',
        "src/shapes.js": "export function file() { return 1; }\n",
        "src/file.js": "export function other() { return 2; }\n",
        "src/main.js": "import { other } from './file.js';\nexport function boot() { return other(); }\n",
        # Without an importer of its own, main.js reads unreachable and the
        # obligation is SATISFIED whether or not the stem match is filtered.
        "src/app.js": "import { boot } from './main.js';\nboot();\n",
    }
    repo, storage = _index(tmp_path, files)
    r = investigate_deletion_safety(repo, "src/shapes.js::file#function", storage_path=storage)
    assert "error" not in r, r
    assert _ob(r, "export_not_imported")["status"] == SATISFIED
