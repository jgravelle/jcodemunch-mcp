"""v1.108.182 — bounded provider discovery, pruned source walks, per-file parse budget.

#375 reopened: Darius Kiaulakis' re-run at 1.108.176 measured no improvement on the
subtree that stalled at 1.108.169 (268s vs 240s, both unfinished). The 1.108.171
regex fix removed one known cost centre; it could not remove the SHAPE of the
failure, which is that provider discovery and per-file parsing are both unbounded
and both run with nothing reported. These tests pin the three bounds:

1. a provider that overruns its budget is skipped and NAMED, not waited on
2. source walks prune dependency trees at the walk instead of after it
3. a single pathological file's parse is bounded and the file is named
"""

import json
import os
import time
from pathlib import Path

import pytest

from jcodemunch_mcp.parser.context import base as ctx_base
from jcodemunch_mcp.parser.context._route_utils import (
    DEFAULT_SKIP_DIRS,
    iter_source_files,
)
from jcodemunch_mcp.parser.context.base import (
    ContextProvider,
    discover_providers,
    provider_budget_seconds,
)
from jcodemunch_mcp.parser.context.express import ExpressProvider
from jcodemunch_mcp.tools._indexing_pipeline import (
    ParseBudgetExceeded,
    parse_file_budgeted,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class _StubProvider(ContextProvider):
    """Minimal provider whose load() cost and detection are test-controlled."""

    _NAME = "stub"
    _LOAD_SECONDS = 0.0
    _DETECTS = True
    _RAISES = None

    def __init__(self) -> None:
        self.loaded = False

    @property
    def name(self) -> str:
        return self._NAME

    def detect(self, folder_path: Path) -> bool:
        return self._DETECTS

    def load(self, folder_path: Path) -> None:
        if self._RAISES is not None:
            raise self._RAISES
        deadline = time.monotonic() + self._LOAD_SECONDS
        while time.monotonic() < deadline:
            time.sleep(0.01)
        self.loaded = True

    def get_file_context(self, file_path: str):
        return None

    def stats(self) -> dict:
        return {}


@pytest.fixture
def only_stub(monkeypatch):
    """Replace the provider registry with a single test provider class."""

    def _install(**attrs):
        cls = type("_Stub", (_StubProvider,), attrs)
        monkeypatch.setattr(ctx_base, "_PROVIDER_CLASSES", [cls])
        return cls

    return _install


# ---------------------------------------------------------------------------
# 1. provider budget
# ---------------------------------------------------------------------------


def test_slow_provider_is_skipped_and_named(only_stub, tmp_path):
    only_stub(_NAME="slowpoke", _LOAD_SECONDS=30.0)
    skipped: list = []

    started = time.monotonic()
    active = discover_providers(tmp_path, budget_seconds=0.2, skipped=skipped)
    elapsed = time.monotonic() - started

    assert active == [], "an overrunning provider must not be handed to the index"
    # The whole point: the caller stops WAITING. Without the budget this call
    # takes 30s; the assertion is deliberately loose to survive slow CI.
    assert elapsed < 10.0, f"discovery waited {elapsed:.1f}s on a budgeted provider"
    assert len(skipped) == 1
    assert skipped[0]["provider"] == "slowpoke"
    assert skipped[0]["reason"] == "budget_exceeded"
    assert skipped[0]["budget_seconds"] == 0.2


def test_fast_provider_still_activates(only_stub, tmp_path):
    only_stub(_NAME="quick", _LOAD_SECONDS=0.0)
    skipped: list = []

    active = discover_providers(tmp_path, budget_seconds=5.0, skipped=skipped)

    assert [p.name for p in active] == ["quick"]
    assert [p.loaded for p in active] == [True], "load() must run on the real object"
    assert skipped == []


def test_undetected_provider_is_not_a_skip(only_stub, tmp_path):
    """detect() returning False is a normal negative, not a reportable gap."""
    only_stub(_NAME="absent", _DETECTS=False)
    skipped: list = []

    assert discover_providers(tmp_path, budget_seconds=5.0, skipped=skipped) == []
    assert skipped == [], "a non-matching ecosystem is not a skipped provider"


def test_raising_provider_is_reported_not_swallowed(only_stub, tmp_path):
    only_stub(_NAME="broken", _RAISES=RuntimeError("boom"))
    skipped: list = []

    assert discover_providers(tmp_path, budget_seconds=5.0, skipped=skipped) == []
    assert skipped[0]["provider"] == "broken"
    assert skipped[0]["reason"] == "error"
    assert "boom" in skipped[0]["error"]


def test_one_slow_provider_does_not_block_the_others(only_stub, tmp_path, monkeypatch):
    """The failure mode being fixed: a single grinder taking the run down."""
    slow = type("_Slow", (_StubProvider,), {"_NAME": "slow", "_LOAD_SECONDS": 30.0})
    fast = type("_Fast", (_StubProvider,), {"_NAME": "fast", "_LOAD_SECONDS": 0.0})
    monkeypatch.setattr(ctx_base, "_PROVIDER_CLASSES", [slow, fast])
    skipped: list = []

    active = discover_providers(tmp_path, budget_seconds=0.2, skipped=skipped)

    assert [p.name for p in active] == ["fast"]
    assert [s["provider"] for s in skipped] == ["slow"]


def test_zero_budget_disables_the_ceiling(only_stub, tmp_path):
    """Opt-out must restore the old inline behaviour exactly."""
    only_stub(_NAME="unbounded", _LOAD_SECONDS=0.3)
    skipped: list = []

    active = discover_providers(tmp_path, budget_seconds=0, skipped=skipped)

    assert [p.name for p in active] == ["unbounded"]
    assert skipped == []


def test_budget_default_and_env_override(monkeypatch):
    monkeypatch.delenv("JCODEMUNCH_PROVIDER_BUDGET_SECONDS", raising=False)
    assert provider_budget_seconds() == 30.0

    monkeypatch.setenv("JCODEMUNCH_PROVIDER_BUDGET_SECONDS", "90")
    assert provider_budget_seconds() == 90.0

    # Garbage must not brick indexing — fall back, don't raise.
    monkeypatch.setenv("JCODEMUNCH_PROVIDER_BUDGET_SECONDS", "soon")
    assert provider_budget_seconds() == 30.0


def test_discover_providers_keeps_its_one_argument_signature(only_stub, tmp_path):
    """Existing callers (index_file, tests) pass a folder and nothing else."""
    only_stub(_NAME="compat", _LOAD_SECONDS=0.0)
    assert [p.name for p in discover_providers(tmp_path)] == ["compat"]


# ---------------------------------------------------------------------------
# 2. pruned walks
# ---------------------------------------------------------------------------


def _tree_with_node_modules(root: Path, n_dep_files: int = 300) -> None:
    (root / "package.json").write_text(json.dumps({"dependencies": {"express": "^4"}}))
    (root / "server.js").write_text(
        "const app = require('express')();\napp.get('/health', h);\n"
    )
    (root / "src").mkdir()
    (root / "src" / "routes.js").write_text("router.post('/users', create);\n")
    dep = root / "node_modules" / "left-pad" / "lib"
    dep.mkdir(parents=True)
    for i in range(n_dep_files):
        (dep / f"m{i}.js").write_text("app.get('/leaked', x);\n")


def test_iter_source_files_prunes_dependency_dirs(tmp_path):
    _tree_with_node_modules(tmp_path, n_dep_files=50)

    found = {rel for _abs, rel in iter_source_files(tmp_path, {".js"})}

    assert found == {"server.js", "src/routes.js"}
    assert not any("node_modules" in rel for rel in found)


def test_iter_source_files_yields_each_file_once(tmp_path):
    (tmp_path / "a.js").write_text("x")
    (tmp_path / "b.TS").write_text("x")

    rels = [rel for _abs, rel in iter_source_files(tmp_path, {".js", ".ts"})]

    assert sorted(rels) == ["a.js", "b.TS"], "suffix match is case-insensitive"
    assert len(rels) == len(set(rels)), "no file may be yielded twice"


def test_iter_source_files_uses_posix_separators(tmp_path):
    (tmp_path / "deep").mkdir()
    (tmp_path / "deep" / "x.js").write_text("x")

    rels = [rel for _abs, rel in iter_source_files(tmp_path, {".js"})]

    assert rels == ["deep/x.js"], "index paths are posix on every platform"


def test_node_modules_is_in_the_default_skip_set():
    for expected in ("node_modules", "vendor", ".git", "__pycache__"):
        assert expected in DEFAULT_SKIP_DIRS


def test_express_provider_ignores_node_modules_routes(tmp_path):
    """Behavioural proof the prune is real: leaked routes never enter context."""
    _tree_with_node_modules(tmp_path, n_dep_files=20)
    provider = ExpressProvider()

    assert provider.detect(tmp_path) is True
    provider.load(tmp_path)

    assert provider.get_file_context("server.js") is not None
    contexts = provider._file_contexts
    assert not any("node_modules" in path for path in contexts)


def test_express_provider_load_does_not_pay_for_node_modules(tmp_path):
    """The cost of a dependency tree must not scale the provider's load().

    Not a wall-clock threshold — CI timing is noise. Counts the directories the
    walk actually enters, which is the quantity the old five-glob form got
    wrong and no timing assertion could pin down.
    """
    _tree_with_node_modules(tmp_path, n_dep_files=300)
    visited: list[str] = []
    real_walk = os.walk

    def _counting_walk(top, *args, **kwargs):
        for dirpath, dirnames, filenames in real_walk(top, *args, **kwargs):
            visited.append(dirpath)
            yield dirpath, dirnames, filenames

    import jcodemunch_mcp.parser.context._route_utils as route_utils

    original = route_utils.os.walk
    route_utils.os.walk = _counting_walk
    try:
        ExpressProvider().load(tmp_path)
    finally:
        route_utils.os.walk = original

    assert not any("node_modules" in d for d in visited), (
        f"walk descended into the dependency tree: {visited}"
    )


def test_express_provider_stops_at_its_deadline(tmp_path):
    """A provider past its budget abandons the walk instead of finishing it."""
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"express": "^4"}}))
    for i in range(200):
        (tmp_path / f"r{i}.js").write_text("app.get('/x', h);\n")

    provider = ExpressProvider()
    assert provider.detect(tmp_path) is True
    provider._deadline = time.monotonic() - 1.0  # already expired
    provider.load(tmp_path)

    assert provider._file_contexts == {}, "no work should happen past the deadline"


def test_budget_expired_is_false_without_a_deadline(tmp_path):
    """The default path must never think it is out of time."""
    provider = ExpressProvider()
    assert provider.budget_expired() is False


# ---------------------------------------------------------------------------
# 3. per-file parse budget
# ---------------------------------------------------------------------------


def test_small_files_bypass_the_watchdog(monkeypatch):
    """The common path must stay inline — no thread, no behaviour change."""
    calls: dict = {}

    def _fake_parse(content, path, language, repo=None):
        calls["thread"] = None
        return ["sym"]

    monkeypatch.setattr(
        "jcodemunch_mcp.tools._indexing_pipeline.parse_file", _fake_parse
    )
    assert parse_file_budgeted("x = 1\n", "a.py", "python") == ["sym"]
    assert "thread" in calls


def test_large_file_under_budget_still_parses(monkeypatch):
    monkeypatch.setenv("JCODEMUNCH_PARSE_BUDGET_SECONDS", "10")
    monkeypatch.setattr(
        "jcodemunch_mcp.tools._indexing_pipeline.parse_file",
        lambda content, path, language, repo=None: ["big-sym"],
    )
    assert parse_file_budgeted("a" * 200_000, "big.js", "javascript") == ["big-sym"]


def test_parse_errors_propagate_unchanged(monkeypatch):
    """The watchdog must not convert a real parse failure into a timeout."""
    monkeypatch.setenv("JCODEMUNCH_PARSE_BUDGET_SECONDS", "10")

    def _boom(content, path, language, repo=None):
        raise ValueError("bad grammar")

    monkeypatch.setattr(
        "jcodemunch_mcp.tools._indexing_pipeline.parse_file", _boom
    )
    with pytest.raises(ValueError, match="bad grammar"):
        parse_file_budgeted("a" * 200_000, "big.js", "javascript")


def test_zero_parse_budget_disables_the_watchdog(monkeypatch):
    monkeypatch.setenv("JCODEMUNCH_PARSE_BUDGET_SECONDS", "0")
    monkeypatch.setattr(
        "jcodemunch_mcp.tools._indexing_pipeline.parse_file",
        lambda content, path, language, repo=None: ["sym"],
    )
    assert parse_file_budgeted("a" * 200_000, "big.js", "javascript") == ["sym"]


# ---------------------------------------------------------------------------
# 4. the gap is disclosed on the index result
# ---------------------------------------------------------------------------


def test_skipped_providers_surface_as_warnings():
    from jcodemunch_mcp.tools.index_folder import (
        _PROVIDER_SKIPS,
        _attach_provider_skips,
    )

    folder = "/tmp/repo-under-test"
    _PROVIDER_SKIPS[folder] = [
        {"provider": "express", "reason": "budget_exceeded",
         "seconds": 30.4, "budget_seconds": 30.0}
    ]
    result: dict = {"success": True}
    try:
        _attach_provider_skips(result, folder)
    finally:
        _PROVIDER_SKIPS.pop(folder, None)

    assert result["providers_skipped"][0]["provider"] == "express"
    warning = result["warnings"][0]
    assert "express" in warning
    # An index that quietly drops route context reads identical to a complete
    # one unless the consequence is spelled out.
    assert "import edges" in warning
    assert "JCODEMUNCH_PROVIDER_BUDGET_SECONDS" in warning


def test_no_skips_leaves_the_result_untouched():
    from jcodemunch_mcp.tools.index_folder import _attach_provider_skips

    result: dict = {"success": True}
    _attach_provider_skips(result, "/tmp/never-indexed")
    assert result == {"success": True}, "a healthy index must gain no noise"
