"""`get_changed_symbols` says which changed files it symbol-diffed and which it did not (#874).

Split from #718 (finding 2), reported by @Torolosko. `changed_files` matched git
exactly, while a changed JSONL file produced no symbol delta and nothing said
why: "no changed symbols" could mean the change touched no symbol, or that the
file was never parsed.

The report names one way a file goes undiffed (no language). The property is
every way, so each is a test here:
- no language for the path (the reported `.jsonl`);
- the parser raised, which `_parse_symbols_from_content` swallowed into `{}`,
  indistinguishable from a file with no symbols;
- a path git C-quotes (`core.quotePath`, a non-ASCII name): without `-z` the
  diff hands back `"caf\\303\\251.py"`, every content read of that name fails,
  and the file reads as unchanged (LEDGER L-64).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from jcodemunch_mcp.tools import get_changed_symbols as mod
from jcodemunch_mcp.tools.get_changed_symbols import get_changed_symbols
from jcodemunch_mcp.tools.index_folder import index_folder


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


def _commit(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    _git(["add", "-A"], root)
    _git(["commit", "-qm", "c1"], root)


def _names(result: dict) -> set[str]:
    return {e["name"] for e in result["added_symbols"] + result["removed_symbols"] + result["changed_symbols"]}


def test_a_changed_file_with_no_language_is_named_as_not_diffed(tmp_path):
    """The reported shape: a changed JSONL evidence file."""
    root, repo, storage, base = _repo(tmp_path, {"app/engine.py": "def run():\n    return 0\n", "evidence.jsonl": '{"a": 1}\n'})
    _commit(root, {"app/engine.py": "def run():\n    return 1\n", "evidence.jsonl": '{"a": 2}\n'})

    result = get_changed_symbols(repo, since_sha=base, storage_path=storage)
    assert "error" not in result, result
    assert sorted(result["changed_files"]) == ["app/engine.py", "evidence.jsonl"]
    assert result["unparsed_changed_files"] == [{"file": "evidence.jsonl", "reason": "no_language"}]
    assert result["parsed_changed_files_count"] == 1
    assert result["symbol_diff_complete"] is False


def test_a_file_the_parser_failed_on_is_named_not_read_as_symbol_free(tmp_path, monkeypatch):
    root, repo, storage, base = _repo(tmp_path, {
        "app/engine.py": "def run():\n    return 0\n",
        "app/broken.py": "def gone():\n    return 0\n",
    })
    _commit(root, {"app/engine.py": "def run():\n    return 1\n", "app/broken.py": "def gone():\n    return 1\n"})

    real = mod.parse_file

    def _parse(content, rel_path, language, **kw):
        if rel_path == "app/broken.py":
            raise RuntimeError("grammar crashed")
        return real(content, rel_path, language, **kw)

    monkeypatch.setattr(mod, "parse_file", _parse)
    result = get_changed_symbols(repo, since_sha=base, storage_path=storage)
    assert "gone" not in _names(result), "precondition: the failing file yields no symbols"
    assert {"file": "app/broken.py", "reason": "parse_failed"} in result["unparsed_changed_files"]
    assert result["symbol_diff_complete"] is False


def test_a_non_ascii_path_is_symbol_diffed(tmp_path):
    """L-64: without `-z` the diff returns a C-quoted name every read misses."""
    root, repo, storage, base = _repo(tmp_path, {"app/café.py": "def brew():\n    return 0\n"})
    _commit(root, {"app/café.py": "def brew():\n    return 1\n"})

    result = get_changed_symbols(repo, since_sha=base, storage_path=storage)
    assert result["changed_files"] == ["app/café.py"], "the path is published unquoted"
    assert "brew" in _names(result), "a changed symbol in a non-ASCII path was never diffed"
    assert result["symbol_diff_complete"] is True


def test_control_every_file_diffed_says_so(tmp_path):
    root, repo, storage, base = _repo(tmp_path, {"app/engine.py": "def run():\n    return 0\n"})
    _commit(root, {"app/engine.py": "def run():\n    return 1\n"})

    result = get_changed_symbols(repo, since_sha=base, storage_path=storage)
    assert result["unparsed_changed_files"] == []
    assert result["parsed_changed_files_count"] == 1
    assert result["symbol_diff_complete"] is True


def test_the_unparsed_list_survives_the_compact_encoding(tmp_path, monkeypatch):
    """The generic encoder serves this tool; a list of dicts must reach the caller."""
    import asyncio

    from mcp.types import CallToolResult

    from jcodemunch_mcp import server

    root, repo, storage, base = _repo(tmp_path, {"app/engine.py": "def run():\n    return 0\n", "evidence.jsonl": '{"a": 1}\n'})
    _commit(root, {"app/engine.py": "def run():\n    return 1\n", "evidence.jsonl": '{"a": 2}\n'})
    monkeypatch.setenv("CODE_INDEX_PATH", storage)

    res = asyncio.run(server.call_tool("get_changed_symbols", {"repo": repo, "since_sha": base, "format": "compact"}))
    text = (res.content if isinstance(res, CallToolResult) else res)[0].text
    assert "evidence.jsonl" in text and "no_language" in text, text[:600]
    assert "symbol_diff_complete" in text


def test_a_modified_file_one_side_of_which_could_not_be_read_is_unreadable(tmp_path, monkeypatch):
    """Review: a one-sided read failure diffed against nothing and certified complete."""
    root, repo, storage, base = _repo(tmp_path, {"app/engine.py": "def run():\n    return 0\n"})
    _commit(root, {"app/engine.py": "def run():\n    return 1\n"})

    real = mod._get_file_content_at

    def _read(sha, file_path, cwd):
        if sha.startswith(base[:12]):
            return None  # e.g. the 15 s `git show` timeout
        return real(sha, file_path, cwd)

    monkeypatch.setattr(mod, "_get_file_content_at", _read)
    result = get_changed_symbols(repo, since_sha=base, storage_path=storage)
    assert result["added_symbols"] == [], "every symbol of a modified file was published as added"
    assert result["unparsed_changed_files"] == [{"file": "app/engine.py", "reason": "unreadable"}]
    assert result["symbol_diff_complete"] is False


def test_control_added_deleted_and_renamed_files_are_diffed(tmp_path):
    """A side a status says is absent is not a read failure."""
    root, repo, storage, base = _repo(tmp_path, {
        "app/gone.py": "def gone():\n    return 0\n",
        "app/old_name.py": "def moved():\n    return 0\n" + "# padding\n" * 20,
    })
    (root / "app" / "gone.py").unlink()
    (root / "app" / "old_name.py").rename(root / "app" / "new_name.py")
    _commit(root, {"app/fresh.py": "def fresh():\n    return 1\n"})

    result = get_changed_symbols(repo, since_sha=base, storage_path=storage)
    assert sorted(result["changed_files"]) == ["app/fresh.py", "app/gone.py", "app/new_name.py"]
    assert result["unparsed_changed_files"] == []
    assert result["symbol_diff_complete"] is True
    assert {"gone", "fresh", "moved"} <= _names(result)
