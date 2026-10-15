"""L-190: a context provider cut by its time budget is recorded in the index and
re-run by the next index pass.

Every provider runs under `JCODEMUNCH_PROVIDER_BUDGET_SECONDS`. A provider that
overran was skipped for that pass, and the Express provider, which polls
`budget_expired()`, stopped early and was kept with part of its routes. Either
way the index carried less context than an unhurried machine would build, and
provider tags and property names are search keywords, so a slow box ranked an
unchanged repository differently (the L-188 class at a higher threshold).

The ruling (jjg, 2026-10-15) keeps the 30 s guard and makes the gap heal: a
provider that overran, however it stopped, is a cut; the cut is stored in the
index's `context_metadata`; and the next explicit index pass re-parses every
file so the provider runs again (`rebuild_reason: "context_provider_cut"`). A
targeted pass (`paths=`) and a watched edit run no discovery over the whole
corpus, so they keep the stored cut rather than claim it healed.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from jcodemunch_mcp import config as _config
from jcodemunch_mcp.parser.context import base as ctx_base
from jcodemunch_mcp.parser.context.base import ContextProvider, discover_providers
from jcodemunch_mcp.storage.index_store import IndexStore
from jcodemunch_mcp.tools import index_folder as index_folder_mod
from jcodemunch_mcp.tools.index_folder import index_folder

_KEY = "providers_cut"


class _Slowable(ContextProvider):
    """Walks until it finishes or, when SLOW, until its deadline passes.

    It stops itself the way the Express provider does, by polling
    `budget_expired()`, so the cooperative path is the one under test.
    """

    SLOW = False

    @property
    def name(self) -> str:
        return "slowable"

    def detect(self, folder_path: Path) -> bool:
        return True

    def load(self, folder_path: Path) -> None:
        if not type(self).SLOW:
            return
        while not self.budget_expired():
            time.sleep(0.01)

    def get_file_context(self, file_path: str):
        return None

    def stats(self) -> dict:
        return {}


@pytest.fixture
def slowable(monkeypatch):
    cls = type("_SlowableStub", (_Slowable,), {"SLOW": False})
    monkeypatch.setattr(ctx_base, "_PROVIDER_CLASSES", [cls])
    monkeypatch.setenv("JCODEMUNCH_PROVIDER_BUDGET_SECONDS", "0.3")
    monkeypatch.setattr(index_folder_mod, "_PROVIDER_CACHE", {})
    monkeypatch.setattr(index_folder_mod, "_PROVIDER_SKIPS", {})
    return cls


@pytest.fixture
def project(tmp_path):
    src = tmp_path / "src"
    store = tmp_path / "store"
    src.mkdir()
    store.mkdir()
    (src / "a.py").write_text("def alpha():\n    return 1\n", encoding="utf-8")
    (src / "b.py").write_text("def beta():\n    return 2\n", encoding="utf-8")
    yield src, store
    _config.invalidate_project_config_cache(str(src))


def _index(src, store, **kw):
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store), **kw)
    assert r["success"] is True, r
    return r


def _stored_cut(r, store):
    owner, name = r["repo"].split("/", 1)
    idx = IndexStore(str(store)).load_index(owner, name)
    return (idx.context_metadata or {}).get(_KEY)


def test_a_provider_that_stops_itself_at_its_deadline_is_a_cut(slowable, tmp_path, monkeypatch):
    """Stopping early and returning is still an overrun: skipped and named.

    The watchdog and the provider's own poll both fire at the deadline, so in
    production either can win. Here the watchdog waits for the provider, which
    pins the interleaving where the provider returns first with part of its work.
    """

    def _wait_for_it(fn, seconds):
        fn()
        return True, None

    monkeypatch.setattr(ctx_base, "_run_with_budget", _wait_for_it)
    slowable.SLOW = True
    skipped: list = []
    active = discover_providers(tmp_path, budget_seconds=0.3, skipped=skipped)

    assert active == [], "a provider that ran out of time must not ship part of its context"
    assert [s["provider"] for s in skipped] == ["slowable"]
    assert skipped[0]["reason"] == "budget_exceeded"


def test_a_cut_is_recorded_in_the_index(slowable, project):
    src, store = project
    slowable.SLOW = True
    r = _index(src, store)

    assert [s["provider"] for s in r["providers_skipped"]] == ["slowable"]
    assert _stored_cut(r, store) == ["slowable"]


def test_the_next_index_pass_reruns_a_cut_provider(slowable, project):
    src, store = project
    slowable.SLOW = True
    _index(src, store)

    slowable.SLOW = False
    r = _index(src, store)

    assert r["performed_incremental"] is False
    assert r["rebuild_reason"] == "context_provider_cut"
    assert any("slowable" in w and "re-parsed" in w for w in r.get("warnings", [])), r.get("warnings")
    assert not _stored_cut(r, store), "a pass in which the provider finished clears the cut"

    again = _index(src, store)
    assert again.get("rebuild_reason") != "context_provider_cut", "a healed index goes back to incremental"


def test_a_provider_cut_again_stays_recorded(slowable, project):
    src, store = project
    slowable.SLOW = True
    _index(src, store)
    r = _index(src, store)

    assert r["rebuild_reason"] == "context_provider_cut"
    assert _stored_cut(r, store) == ["slowable"]


def test_an_index_with_no_cut_stays_incremental(slowable, project):
    src, store = project
    first = _index(src, store)
    assert not _stored_cut(first, store)

    (src / "a.py").write_text("def alpha():\n    return 3\n", encoding="utf-8")
    r = _index(src, store)
    assert r.get("rebuild_reason") is None
    assert r["performed_incremental"] is True


def test_a_targeted_pass_keeps_the_cut(slowable, project):
    """`paths=` re-reads named files only, so the rest still lack the context."""
    src, store = project
    slowable.SLOW = True
    _index(src, store)

    slowable.SLOW = False
    (src / "a.py").write_text("def alpha():\n    return 4\n", encoding="utf-8")
    r = _index(src, store, paths=[str(src / "a.py")])

    assert r.get("rebuild_reason") != "context_provider_cut"
    assert _stored_cut(r, store) == ["slowable"]


def test_a_watched_edit_keeps_the_cut(slowable, project):
    src, store = project
    slowable.SLOW = True
    _index(src, store)

    slowable.SLOW = False
    target = src / "b.py"
    target.write_text("def beta():\n    return 5\n", encoding="utf-8")
    r = _index(src, store, changed_paths=[("modified", str(target))])

    assert r.get("rebuild_reason") != "context_provider_cut"
    assert _stored_cut(r, store) == ["slowable"]
