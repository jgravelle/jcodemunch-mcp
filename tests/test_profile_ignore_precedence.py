"""A project's own ignore negation wins over a framework profile's default (#1398).

`index_folder` detected a framework profile and APPENDED its ignore patterns
after the project's `.jcodemunch.jsonc` list. Ignore matching is
last-match-wins, so the express profile's `coverage/` beat the project's
explicit `!tools/coverage/**`, and a hand-written source directory the project
re-included was dropped. `index_file` merged the same way. Reported by
@dkiaulakis.

The order is now profile, then project/global config, then per-call, so a
built-in default has the lowest precedence. The profile pattern itself still
applies where the project says nothing: a plain `coverage/` report directory
stays out of the index.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jcodemunch_mcp.tools.index_file import index_file
from jcodemunch_mcp.tools.index_folder import index_folder


def _repo(root: Path, framework: bool) -> Path:
    root.mkdir()
    deps = {"express": "^4.0.0"} if framework else {}
    (root / "package.json").write_text(json.dumps({"dependencies": deps}), encoding="utf-8")
    (root / ".jcodemunch.jsonc").write_text(
        '{ "extra_ignore_patterns": ["!tools/coverage/**"] }', encoding="utf-8"
    )
    (root / "tools" / "coverage").mkdir(parents=True)
    (root / "tools" / "coverage" / "run.js").write_text(
        "function run() { return 1; }\n", encoding="utf-8"
    )
    (root / "coverage").mkdir()
    (root / "coverage" / "report.js").write_text(
        "function generated() { return 2; }\n", encoding="utf-8"
    )
    (root / "src").mkdir()
    (root / "src" / "app.js").write_text("function app() { return 3; }\n", encoding="utf-8")
    return root


def _indexed(result: dict, store: Path) -> set[str]:
    import sqlite3

    assert result.get("success"), result
    db = next(store.rglob("*.db"))
    conn = sqlite3.connect(db)
    try:
        return {p for (p,) in conn.execute("SELECT path FROM files")}
    finally:
        conn.close()


@pytest.mark.parametrize("framework", [True, False])
def test_index_folder_keeps_a_project_reinclude_over_the_profile(tmp_path, framework):
    root = _repo(tmp_path / "repo", framework)
    store = tmp_path / "store"
    files = _indexed(
        index_folder(str(root), use_ai_summaries=False, storage_path=str(store)), store
    )
    assert "tools/coverage/run.js" in files, (
        "the project's !tools/coverage/** must win over a framework default"
    )
    assert "src/app.js" in files


def test_the_profile_default_still_applies_where_the_project_says_nothing(tmp_path):
    root = _repo(tmp_path / "repo", framework=True)
    store = tmp_path / "store"
    files = _indexed(
        index_folder(str(root), use_ai_summaries=False, storage_path=str(store)), store
    )
    assert "coverage/report.js" not in files, "express's coverage/ default still excludes a report dir"


def test_index_file_keeps_a_project_reinclude_over_the_profile(tmp_path, monkeypatch):
    root = _repo(tmp_path / "repo", framework=True)
    store = tmp_path / "store"
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    assert index_folder(str(root), use_ai_summaries=False, storage_path=str(store)).get("success")
    new = root / "tools" / "coverage" / "more.js"
    new.write_text("function more() { return 4; }\n", encoding="utf-8")
    result = index_file(path=str(new), use_ai_summaries=False, storage_path=str(store))
    assert result.get("success") is True, result
    assert "tools/coverage/more.js" in _indexed({"success": True}, store)


def test_get_extra_ignore_patterns_puts_the_profile_first(tmp_path):
    from jcodemunch_mcp.security import get_extra_ignore_patterns

    out = get_extra_ignore_patterns(["call/"], repo=None, profile_patterns=["coverage/"])
    assert out[0] == "coverage/" and out[-1] == "call/"
