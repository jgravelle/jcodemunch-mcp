"""`get_changed_symbols` reads a renamed file's before side at its OLD path (LEDGER L-67).

The before side of a renamed file was read at its new path, which does not
exist at `since_sha`. So every symbol of the file was published as `added`,
none was removed at the old path, and `symbol_diff_complete` read true: a file
moved without a single edit reported its whole contents as new code.

The diff's `--name-status -z` output already carries the old path (#874) and
discarded it. Now the before side is read and parsed at the old path, so a
moved symbol whose body did not change is not a change, an edited one is
`modified`, and one deleted in the move is `removed` at the old path. The move
itself is named in `renamed_files`. Rename detection is asked for explicitly
(`-M`), so a repository with `diff.renames=false` gets the same answer.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from jcodemunch_mcp.tools import get_changed_symbols as mod
from jcodemunch_mcp.tools.get_changed_symbols import get_changed_symbols
from jcodemunch_mcp.tools.index_folder import index_folder

PADDING = "# padding line\n" * 30
BODY = (
    "def kept():\n    return 0\n\n\n"
    "def edited():\n    return 0\n\n\n"
    "def dropped():\n    return 0\n\n\n"
    + PADDING
)


def _git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True,
        encoding="utf-8", stdin=subprocess.DEVNULL,
    ).stdout.strip()


def _repo(tmp_path: Path, files: dict[str, str]) -> tuple[Path, str, str, str]:
    root = tmp_path / "r"
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    _git(["init", "-q"], root)
    _git(["config", "user.email", "t@t"], root)
    _git(["config", "user.name", "t"], root)
    _git(["add", "-A"], root)
    _git(["commit", "-qm", "c0"], root)
    storage = str(tmp_path / "store")
    res = index_folder(str(root), use_ai_summaries=False, storage_path=storage, identity_mode="local")
    return root, res["repo"], storage, _git(["rev-parse", "HEAD"], root)


def _move(root: Path, old: str, new: str, text: str | None = None) -> None:
    (root / new).parent.mkdir(parents=True, exist_ok=True)
    (root / old).rename(root / new)
    if text is not None:
        (root / new).write_text(text, encoding="utf-8")
    _git(["add", "-A"], root)
    _git(["commit", "-qm", "c1"], root)


def _by_name(entries: list[dict]) -> dict[str, dict]:
    return {e["name"]: e for e in entries}


def test_a_file_moved_without_an_edit_reports_no_symbol_changes(tmp_path):
    root, repo, storage, base = _repo(tmp_path, {"app/old_name.py": BODY})
    _move(root, "app/old_name.py", "app/new_name.py")

    result = get_changed_symbols(repo, since_sha=base, storage_path=storage)
    assert "error" not in result, result
    assert result["changed_files"] == ["app/new_name.py"]
    assert result["added_symbols"] == [], "a moved file published its whole contents as added"
    assert result["removed_symbols"] == []
    assert result["changed_symbols"] == []
    assert result["renamed_files"] == [{"from": "app/old_name.py", "to": "app/new_name.py"}]
    assert result["symbol_diff_complete"] is True


def test_a_moved_and_edited_file_diffs_against_its_old_contents(tmp_path):
    root, repo, storage, base = _repo(tmp_path, {"app/old_name.py": BODY})
    after = BODY.replace("def edited():\n    return 0", "def edited():\n    return 1")
    after = after.replace("def dropped():\n    return 0\n\n\n", "def fresh():\n    return 2\n\n\n")
    _move(root, "app/old_name.py", "app/new_name.py", after)

    result = get_changed_symbols(repo, since_sha=base, storage_path=storage)
    assert set(_by_name(result["added_symbols"])) == {"fresh"}
    removed = _by_name(result["removed_symbols"])
    assert set(removed) == {"dropped"}
    assert removed["dropped"]["file"] == "app/old_name.py", "a removed symbol is named where it was"
    changed = _by_name(result["changed_symbols"])
    assert set(changed) == {"edited"}
    assert changed["edited"]["change_type"] == "modified"
    assert changed["edited"]["file"] == "app/new_name.py"
    assert result["renamed_files"] == [{"from": "app/old_name.py", "to": "app/new_name.py"}]


def test_a_rename_is_detected_when_the_repo_turns_rename_detection_off(tmp_path):
    root, repo, storage, base = _repo(tmp_path, {"app/old_name.py": BODY})
    _git(["config", "diff.renames", "false"], root)
    _move(root, "app/old_name.py", "app/new_name.py")

    result = get_changed_symbols(repo, since_sha=base, storage_path=storage)
    assert result["added_symbols"] == [] and result["removed_symbols"] == []
    assert result["renamed_files"] == [{"from": "app/old_name.py", "to": "app/new_name.py"}]


def test_a_renamed_file_whose_old_side_cannot_be_read_is_unreadable(tmp_path, monkeypatch):
    """The old side exists at `since_sha`; failing to read it is not an added file."""
    root, repo, storage, base = _repo(tmp_path, {"app/old_name.py": BODY})
    _move(root, "app/old_name.py", "app/new_name.py")

    real = mod._get_file_content_at

    def _read(sha, file_path, cwd):
        if sha.startswith(base[:12]):
            return None
        return real(sha, file_path, cwd)

    monkeypatch.setattr(mod, "_get_file_content_at", _read)
    result = get_changed_symbols(repo, since_sha=base, storage_path=storage)
    assert result["added_symbols"] == []
    assert result["unparsed_changed_files"] == [{"file": "app/new_name.py", "reason": "unreadable"}]
    assert result["symbol_diff_complete"] is False


def test_control_an_edit_in_place_names_no_rename(tmp_path):
    root, repo, storage, base = _repo(tmp_path, {"app/engine.py": BODY})
    (root / "app" / "engine.py").write_text(BODY.replace("return 0", "return 9", 1), encoding="utf-8")
    _git(["commit", "-qam", "c1"], root)

    result = get_changed_symbols(repo, since_sha=base, storage_path=storage)
    assert result["renamed_files"] == []
    assert set(_by_name(result["changed_symbols"])) == {"kept"}


def test_renamed_files_survives_the_compact_encoding(tmp_path, monkeypatch):
    import asyncio

    from mcp.types import CallToolResult

    from jcodemunch_mcp import server

    root, repo, storage, base = _repo(tmp_path, {"app/old_name.py": BODY})
    _move(root, "app/old_name.py", "app/new_name.py")
    monkeypatch.setenv("CODE_INDEX_PATH", storage)

    res = asyncio.run(server.call_tool("get_changed_symbols", {"repo": repo, "since_sha": base, "format": "compact"}))
    text = (res.content if isinstance(res, CallToolResult) else res)[0].text
    assert "renamed_files" in text and "app/old_name.py" in text, text[:600]
