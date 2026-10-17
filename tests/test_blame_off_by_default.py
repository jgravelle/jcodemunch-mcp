"""Git blame is off by default, and an opted-in blame adds no "N properties" to a file summary (LEDGER L-189).

After L-188 took blame's property names out of search ranking, its output
reached two places: the AI summarizer's prompt, and a "2 properties" suffix on
the heuristic summary of every file it covered. The suffix names no property,
and both depended on whether a timed `git log` finished. A `git log` of up to
10 s ran on every index for that. jjg ruled 2026-10-15: off by default, still
available with `git_blame_enabled: true`, and no "N properties" for it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jcodemunch_mcp import config as config_module
from jcodemunch_mcp.parser.context.git_blame import GitBlameProvider


@pytest.fixture(autouse=True)
def _blame_cannot_run_out_of_time(monkeypatch):
    """When a test opts blame in, its completion is setup, not an assumption (L-208).

    Under load the 10 s `git log` budget or the 30 s provider budget cut blame, a
    symbol carried no `ecosystem_context`, and a test failed on wall clock rather
    than on what it checks. Both are read at call time. Harmless where blame is
    off, which is the default this file also pins.
    """
    from jcodemunch_mcp.parser.context import git_blame

    monkeypatch.setenv("JCODEMUNCH_PROVIDER_BUDGET_SECONDS", "0")
    monkeypatch.setattr(git_blame, "GIT_BLAME_TIMEOUT_S", 600.0)


def _git_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for args in (
        ["init", "-q"],
        ["config", "user.email", "t@example.invalid"],
        ["config", "user.name", "Tester"],
        ["config", "commit.gpgsign", "false"],
    ):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, stdin=subprocess.DEVNULL)
    (root / "mod.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True, stdin=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True, capture_output=True, stdin=subprocess.DEVNULL)
    return root


def test_the_default_is_off():
    assert config_module.DEFAULTS["git_blame_enabled"] is False


def test_blame_does_not_run_on_a_git_repository_by_default(tmp_path):
    repo = _git_repo(tmp_path / "repo")
    assert GitBlameProvider().detect(repo) is False


def test_an_index_with_default_config_runs_no_blame(tmp_path, monkeypatch):
    """At the entry point: the indexer never calls the provider's `git log`."""
    from jcodemunch_mcp.tools.index_folder import index_folder

    repo = _git_repo(tmp_path / "repo")
    loads = []
    real_load = GitBlameProvider.load
    monkeypatch.setattr(GitBlameProvider, "load", lambda self, p: (loads.append(p), real_load(self, p))[1])
    res = index_folder(str(repo), use_ai_summaries=False, storage_path=str(tmp_path / "store"), identity_mode="local")
    assert res.get("success") is True
    assert loads == []


def test_opting_in_still_runs_blame(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path / "repo")
    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "git_blame_enabled", True)  # type: ignore[attr-defined]
    prov = GitBlameProvider()
    assert prov.detect(repo) is True
    prov.load(repo)
    ctx = prov.get_file_context("mod.py")
    assert ctx is not None and ctx.properties.get("last_author") == "Tester"


def test_an_opted_in_blame_adds_no_property_count_to_a_file_summary(tmp_path, monkeypatch):
    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools.index_folder import index_folder

    repo = _git_repo(tmp_path / "repo")
    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "git_blame_enabled", True)  # type: ignore[attr-defined]
    store = tmp_path / "store"
    res = index_folder(str(repo), use_ai_summaries=False, storage_path=str(store), identity_mode="local")
    assert res.get("success") is True
    owner, name = res["repo"].split("/", 1)
    idx = IndexStore(base_path=str(store)).load_index(owner, name)
    sym = next(s for s in idx.symbols if s["name"] == "alpha")
    assert "git_blame" in sym["ecosystem_context"], "blame must have run for this to test anything"
    summary = idx.file_summaries.get("mod.py", "")
    assert "properties" not in summary, summary


@pytest.mark.parametrize("value", ["1", "true"])
def test_the_environment_variable_still_opts_in(tmp_path, monkeypatch, value):
    repo = _git_repo(tmp_path / "repo")
    monkeypatch.setenv("JCODEMUNCH_GIT_BLAME_ENABLED", value)
    config_module._apply_env_var_fallback(set())
    try:
        assert GitBlameProvider().detect(repo) is True
    finally:
        config_module._GLOBAL_CONFIG["git_blame_enabled"] = config_module.DEFAULTS["git_blame_enabled"]  # type: ignore[attr-defined]


def test_a_full_reindex_clears_the_suffix_an_older_index_kept(tmp_path, monkeypatch):
    """What the CHANGELOG tells an existing user: no generation bump, so the
    suffix stays on an unchanged file until a full re-index (incremental=False)."""
    from jcodemunch_mcp.storage import IndexStore
    from jcodemunch_mcp.tools.index_folder import index_folder

    repo = _git_repo(tmp_path / "repo")
    store = tmp_path / "store"
    with monkeypatch.context() as old_release:
        old_release.setitem(config_module._GLOBAL_CONFIG, "git_blame_enabled", True)  # type: ignore[attr-defined]
        old_release.setattr(GitBlameProvider, "properties_are_search_terms", True)
        res = index_folder(str(repo), use_ai_summaries=False, storage_path=str(store), identity_mode="local")
    owner, name = res["repo"].split("/", 1)

    def summary():
        return IndexStore(base_path=str(store)).load_index(owner, name).file_summaries.get("mod.py", "")

    assert "2 properties" in summary(), "the old index must carry the suffix"
    index_folder(str(repo), use_ai_summaries=False, storage_path=str(store), identity_mode="local", incremental=False)
    assert "properties" not in summary()


def test_every_file_summary_caller_passes_the_providers_flag():
    """`file_summary()` defaults to counting properties, which is right for a
    provider whose properties are vocabulary. A caller that omits the flag
    brings "2 properties" back for blame, so every call in src/ must pass it."""
    import ast

    src = Path(__file__).resolve().parent.parent / "src" / "jcodemunch_mcp"
    calls, missing = 0, []
    for path in src.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "file_summary":
                calls += 1
                if not any(k.arg == "include_properties" for k in node.keywords):
                    missing.append(f"{path.relative_to(src)}:{node.lineno}")
    assert calls >= 2, "the scan found no callers; it is measuring nothing"
    assert missing == [], f"file_summary() called without include_properties: {missing}"
