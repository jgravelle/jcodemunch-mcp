"""L-197: a filesystem root is a folder like any other to the indexing filter.

`_build_index_filters` built its containment prefix as `str(root) + os.sep`.
A filesystem root already ends in a separator (`C:\\`, `/`), so the prefix
came out doubled and every file under the root failed the path-traversal
check: indexing a drive root kept nothing. L-195 fixed the same spelling in the
hooks' `_path_overlaps`; this pins it for the indexer.
"""

from __future__ import annotations

import os
from pathlib import Path

from jcodemunch_mcp.tools.index_folder import _build_index_filters, _should_index_file


def test_a_root_prefix_has_one_separator():
    from jcodemunch_mcp.tools.index_folder import _dir_prefix

    root = Path(Path.cwd().resolve().anchor)
    assert _dir_prefix(str(root)) == str(root)
    assert _dir_prefix(str(root)).endswith(os.sep)
    assert not _dir_prefix(str(root)).endswith(os.sep * 2)


def test_an_ordinary_folder_prefix_gains_one_separator(tmp_path):
    from jcodemunch_mcp.tools.index_folder import _dir_prefix

    assert _dir_prefix(str(tmp_path)) == str(tmp_path) + os.sep


def test_a_file_under_a_filesystem_root_is_inside_it(tmp_path):
    target = tmp_path / "pkg" / "mod.py"
    target.parent.mkdir()
    target.write_text("def f():\n    return 1\n", encoding="utf-8")
    root = Path(tmp_path.resolve().anchor)

    cfg = _build_index_filters(root=root, max_size=512_000)
    ok, reason, rel_path, _warning = _should_index_file(target, cfg)

    assert reason != "path_traversal", "a file on the drive is inside the drive's root"
    expected = str(target.resolve())[len(str(root)):].replace("\\", "/")
    assert rel_path == expected
    assert not rel_path.startswith("/"), rel_path
