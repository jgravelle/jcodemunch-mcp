"""The folder-prefix rule lives in `path_prefix.dir_prefix` and nowhere else.

`folder + os.sep` doubles the separator for a filesystem root, so nothing under
the root matches. That spelling was fixed three times in one day, each time at
the sites someone had looked at: the hooks (L-195), the indexer's path filter
(L-197), then, from the L-197 review, two `.gitignore` prefixes in the same
indexer file, where it failed open (a drive root's `.gitignore` stopped
applying). This scans `src/` for the spelling so a fourth site fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pathspec

from jcodemunch_mcp.path_prefix import dir_prefix
from jcodemunch_mcp.tools.index_folder import _is_gitignored_fast

SRC = Path(__file__).resolve().parents[1] / "src" / "jcodemunch_mcp"
OLD_SPELLING = re.compile(r"\+\s*os\.sep\b|\{os\.sep\}")


def test_no_module_but_the_rule_appends_os_sep():
    hits = []
    for path in SRC.rglob("*.py"):
        if path.name == "path_prefix.py":
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if OLD_SPELLING.search(code):
                hits.append(f"{path.relative_to(SRC)}:{n}: {line.strip()}")
    assert not hits, "use path_prefix.dir_prefix:\n" + "\n".join(hits)


def test_the_scan_sees_the_spelling_it_is_for():
    for bad in ("p = root + os.sep", "x=(str(d.resolve())+os.sep, spec)", 'p = f"{root}{os.sep}"'):
        assert OLD_SPELLING.search(bad), bad
    assert not OLD_SPELLING.search("p = dir_prefix(root)")


def test_a_gitignore_at_a_filesystem_root_applies_to_files_under_it(tmp_path):
    root = Path(tmp_path.resolve().anchor)
    target = str((tmp_path / "secret.log").resolve())
    spec = pathspec.PathSpec.from_lines("gitignore", ["*.log"])

    assert _is_gitignored_fast(target, [(dir_prefix(str(root)), spec)]) is True
