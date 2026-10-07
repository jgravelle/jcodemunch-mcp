"""`init`'s index line reports what the indexer returned.

`run_index` read `files_indexed` and `symbols_indexed` from `index_folder`'s
result. The indexer has never returned either key on a successful run, so the
line read `(? files, ? symbols)` for every index `init` ever ran, and it read
`indexed` for a run the indexer had refused (reported by Dave, 2026-10-07).

Every case here calls the real indexer on a project the test owns. A mock
would supply whichever keys the test's author expected, which is how the
line stayed wrong: `test_init_minimal.py` replaces `run_index` whole.
"""

import pytest

from jcodemunch_mcp.cli import init as init_mod

NL = chr(10)


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / "store"))
    for key in ("ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "OPENAI_API_BASE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(root)
    return root


def _write(root, name, functions):
    body = NL.join("def %s():%s    return 1%s" % (f, NL, NL) for f in functions)
    (root / name).write_text(body, encoding="utf-8", newline=NL)


def test_a_first_index_reports_its_files_and_symbols(project):
    _write(project, "one.py", ["a"])
    _write(project, "two.py", ["b", "c"])
    line = init_mod.run_index()
    assert "?" not in line, line
    assert "2 files" in line and "3 symbols" in line, line


def test_a_second_run_with_nothing_changed_says_so(project):
    _write(project, "one.py", ["a"])
    init_mod.run_index()
    line = init_mod.run_index()
    assert "?" not in line, line
    assert "up to date" in line, line


def test_a_run_that_re_indexes_some_files_counts_them(project):
    _write(project, "one.py", ["a"])
    _write(project, "two.py", ["b"])
    init_mod.run_index()
    _write(project, "one.py", ["a", "a2"])
    _write(project, "three.py", ["d"])
    (project / "two.py").unlink()
    line = init_mod.run_index()
    assert "?" not in line, line
    assert "1 changed, 1 new, 1 deleted" in line, line
    assert "3 symbols" in line, line


def test_a_refused_run_is_not_reported_as_indexed(project):
    # No source file: the indexer answers success False with an error.
    line = init_mod.run_index()
    assert "?" not in line, line
    assert "indexing failed" in line and "No source files found" in line, line
    assert "indexed " + str(project) not in line, line


@pytest.mark.parametrize("result, expected", [
    ({"success": True, "file_count": 4, "symbol_count": 9}, "  indexed X (4 files, 9 symbols)"),
    ({"success": True, "changed": 2, "new": 0, "deleted": 1, "symbol_count": 7},
     "  indexed X (2 changed, 0 new, 1 deleted, 7 symbols)"),
    ({"success": True, "changed": 0, "new": 0, "deleted": 0},
     "  X is up to date (nothing changed since the last index)"),
    # A shape this line has not met carries no invented count.
    ({"success": True}, "  indexed X"),
    ({"success": True, "symbol_count": 5}, "  indexed X (5 symbols)"),
    ({"success": False, "error": "Folder not found: X"}, "  indexing failed: Folder not found: X"),
    ({"success": False}, "  indexing failed: the indexer gave no reason"),
    ({}, "  indexing failed: the indexer gave no reason"),
])
def test_each_shape_of_result_has_its_line(result, expected):
    assert init_mod._index_summary("X", result) == expected


def test_the_dry_run_line_is_unchanged(project):
    assert init_mod.run_index(dry_run=True) == "  would index %s" % project
