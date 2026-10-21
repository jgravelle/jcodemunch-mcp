"""The startup note says the repo IS indexed when the server can see that it is.

The MCP `instructions` string is often the only jcodemunch text an agent reads
(a client that defers tools sends names and this string, nothing else). It
opened with "This repo can be indexed", which reads as work still to do even
when an index is already there. On the stdio transport the server's working
directory is the user's project, so at startup it asks `resolve_repo` whether
that directory is indexed and, if so, says "This repo is indexed by jcodemunch
(N files)". The file count lets the agent judge "small repository" for itself.

Anything it cannot establish keeps the old wording: not indexed, a lookup
error, or a lookup that takes longer than its time limit (`resolve_repo` has a
documented hang, #303, and startup must not wait on it). SSE and
streamable-http keep the old wording too, because there the server's working
directory is not the caller's.
"""

from __future__ import annotations

import ast
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from jcodemunch_mcp import server


@pytest.mark.parametrize("surface", ["counter", "full"])
def test_an_indexed_repo_is_named_as_indexed_with_its_size(surface):
    text = server._mcp_instructions(surface, indexed_files=1406)
    assert "This repo is indexed by jcodemunch (1,406 files)" in text
    assert "can be indexed" not in text
    assert len(text) < server._MCP_INSTRUCTIONS_MAX_CHARS


@pytest.mark.parametrize("surface", ["counter", "full"])
def test_without_a_count_the_wording_is_unchanged(surface):
    assert server._mcp_instructions(surface, indexed_files=None) == server._mcp_instructions(surface)
    assert "This repo can be indexed by jcodemunch" in server._mcp_instructions(surface)


@pytest.mark.parametrize("surface", ["counter", "full"])
def test_the_small_repository_guidance_stays(surface):
    """The 2026-10-14 wording on small repositories is not changed here."""
    for count in (None, 12, 50_000):
        assert "On a small repository" in server._mcp_instructions(surface, indexed_files=count)


def test_one_file_is_singular():
    assert "(1 file)" in server._mcp_instructions("counter", indexed_files=1)


def _fake_resolve(result=None, exc=None, delay=0.0):
    def fake(path, storage_path=None):
        if delay:
            time.sleep(delay)
        if exc:
            raise exc
        return result

    return fake


def test_indexed_here_returns_the_file_count(monkeypatch):
    seen = {}

    def fake(path, storage_path=None):
        seen["path"] = path
        return {"indexed": True, "file_count": 2950, "source_root": str(Path.cwd())}

    monkeypatch.setattr("jcodemunch_mcp.tools.resolve_repo.resolve_repo", fake)
    assert server._indexed_here() == 2950
    assert Path(seen["path"]).is_absolute()


@pytest.mark.parametrize(
    "result",
    [
        {"indexed": False},
        {"indexed": True},
        {"indexed": True, "file_count": "many"},
        {"indexed": True, "file_count": -1},
        None,
    ],
)
def test_indexed_here_is_none_when_it_cannot_tell(monkeypatch, result):
    monkeypatch.setattr("jcodemunch_mcp.tools.resolve_repo.resolve_repo", _fake_resolve(result))
    assert server._indexed_here() is None


@pytest.mark.parametrize(
    "extra",
    [
        {},  # no source_root: unknown, not a match
        {"source_root": "Z:/somewhere/else/entirely"},
        {"working_tree_mismatch": True},
    ],
)
def test_indexed_here_is_none_for_an_index_of_another_tree(monkeypatch, extra):
    result = {"indexed": True, "file_count": 10, **extra}
    if "source_root" not in extra and "working_tree_mismatch" in extra:
        result["source_root"] = str(Path.cwd())
    monkeypatch.setattr("jcodemunch_mcp.tools.resolve_repo.resolve_repo", _fake_resolve(result))
    assert server._indexed_here() is None


# --- Unmocked: a real index under a private CODE_INDEX_PATH ------------------


def _project(root: Path, files: int = 3) -> Path:
    root.mkdir(parents=True)
    for i in range(files):
        (root / f"mod{i}.py").write_text(f"def f{i}():\n    return {i}\n", encoding="utf-8")
    return root


def test_a_real_index_of_this_directory_is_found(tmp_path, monkeypatch):
    from jcodemunch_mcp.tools.index_folder import index_folder

    store = tmp_path / "store"
    project = _project(tmp_path / "proj")
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    out = index_folder(str(project), use_ai_summaries=False, storage_path=str(store), identity_mode="local")
    assert out.get("success"), out
    monkeypatch.chdir(project)
    assert server._indexed_here(timeout=10) == out["file_count"] == 3
    sub = project / "pkg"
    sub.mkdir()
    monkeypatch.chdir(sub)
    assert server._indexed_here(timeout=10) == 3  # a directory inside the indexed tree


def test_an_unindexed_directory_is_not_claimed(tmp_path, monkeypatch):
    store = tmp_path / "store"
    other = _project(tmp_path / "other")
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    monkeypatch.chdir(other)
    assert server._indexed_here(timeout=10) is None


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_a_second_checkout_of_an_indexed_project_is_not_claimed(tmp_path, monkeypatch):
    """resolve_repo answers indexed: true here (same project identity, other
    working tree); the startup note must not."""
    from jcodemunch_mcp.tools.index_folder import index_folder
    from jcodemunch_mcp.tools.resolve_repo import resolve_repo

    def git(*args, cwd):
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, stdin=subprocess.DEVNULL)

    store = tmp_path / "store"
    a = _project(tmp_path / "a")
    git("init", "-q", cwd=a)
    git("remote", "add", "origin", "https://github.com/example/same-project.git", cwd=a)
    b = tmp_path / "b"
    shutil.copytree(a, b)
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    out = index_folder(str(a), use_ai_summaries=False, storage_path=str(store))
    assert out.get("success"), out

    r = resolve_repo(str(b.resolve()))
    if not (r.get("indexed") and r.get("working_tree_mismatch")):
        pytest.fail(f"fixture no longer produces the sibling-checkout shape: {r}")
    monkeypatch.chdir(b)
    assert server._indexed_here(timeout=10) is None
    monkeypatch.chdir(a)
    assert server._indexed_here(timeout=10) == 3


def test_indexed_here_is_none_without_a_working_directory(monkeypatch):
    """A deleted working directory makes Path.cwd() raise; startup must not."""
    def gone():
        raise FileNotFoundError("cwd removed")

    monkeypatch.setattr(server.Path, "cwd", staticmethod(gone))
    assert server._indexed_here() is None


def test_indexed_here_is_none_on_an_error(monkeypatch):
    monkeypatch.setattr("jcodemunch_mcp.tools.resolve_repo.resolve_repo", _fake_resolve(exc=OSError("boom")))
    assert server._indexed_here() is None


def test_indexed_here_does_not_wait_past_its_limit(monkeypatch):
    release = threading.Event()

    def stuck(path, storage_path=None):
        release.wait(5)
        return {"indexed": True, "file_count": 10}

    monkeypatch.setattr("jcodemunch_mcp.tools.resolve_repo.resolve_repo", stuck)
    started = time.monotonic()
    try:
        assert server._indexed_here(timeout=0.2) is None
        assert time.monotonic() - started < 2.0
    finally:
        release.set()


def _init_option_calls() -> list[ast.Call]:
    tree = ast.parse(Path(server.__file__).read_text(encoding="utf-8"))
    return [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_initialization_options"
    ]


def test_only_the_stdio_transport_passes_a_count():
    calls = _init_option_calls()
    assert len(calls) == 3
    with_count = [c for c in calls if c.keywords and any(k.arg == "indexed_files" for k in c.keywords)]
    assert len(with_count) == 1
    src = Path(server.__file__).read_text(encoding="utf-8").splitlines()
    window = "\n".join(src[max(0, with_count[0].lineno - 30): with_count[0].lineno])
    assert "stdio_server(" in window
