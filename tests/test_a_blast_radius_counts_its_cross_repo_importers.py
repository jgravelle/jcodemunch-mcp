"""`get_blast_radius(cross_repo=True)` counts the importers it found in other
repositories (#877).

With importers only in another repository, the tool published
`_meta.verdict.state: "absent"` and `overall_risk_score: 0.0` beside
`cross_repo_confirmed_count: 1`: the verdict said nothing depends on the
symbol in the response that named something that does. `answered_nothing`
counted the cross-repo channel, but the `result_count` handed to
`blast_verdict` did not, and neither did the risk average. `absent` is the
state an agent may cite as proof (#719), and `0.0` reads as safe to change.

The property: every channel the call ran counts toward the verdict and the
risk score. A cross-repo importer is a direct dependent.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jcodemunch_mcp.tools.get_blast_radius import get_blast_radius
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.package_registry import invalidate_registry_cache


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _index(folder: Path, store: Path) -> str:
    result = index_folder(str(folder), use_ai_summaries=False, storage_path=str(store))
    assert result["success"] is True, result
    return result["repo"]


@pytest.fixture
def provider(tmp_path):
    """`mylib` declares `do_thing`; nothing in `mylib` imports it, and a
    second repository imports `mylib` and calls it."""
    provider_src, consumer_src, store = tmp_path / "provider", tmp_path / "consumer", tmp_path / "store"
    store.mkdir()
    _write(provider_src / "pyproject.toml", '[project]\nname = "mylib"\n')
    _write(provider_src / "core.py", "def do_thing(): pass\n")
    _write(consumer_src / "app.py", "import mylib\nmylib.do_thing()\n")
    provider_id = _index(provider_src, store)
    _index(consumer_src, store)
    invalidate_registry_cache()
    yield provider_id, str(store)
    invalidate_registry_cache()


def test_a_cross_repo_importer_is_not_absence(provider):
    repo, store = provider
    result = get_blast_radius(repo=repo, symbol="do_thing", storage_path=store, cross_repo=True)
    assert result["importer_count"] == 0
    assert result["cross_repo_confirmed_count"] == 1
    verdict = result["_meta"]["verdict"]
    assert verdict["state"] != "absent", verdict
    assert result["overall_risk_score"] == 1.0


def test_without_cross_repo_the_same_symbol_is_still_absent(provider):
    """The other direction: the fix counts a channel only when the call ran
    it, so `cross_repo=False` keeps the local answer."""
    repo, store = provider
    result = get_blast_radius(repo=repo, symbol="do_thing", storage_path=store, cross_repo=False)
    assert "cross_repo_confirmed" not in result
    assert result["_meta"]["verdict"]["state"] == "absent"
    assert result["overall_risk_score"] == 0.0


def test_a_local_and_a_cross_repo_importer_both_weigh_as_direct(tmp_path):
    """Both channels in one answer. Locally `use.py` imports `core.py`
    (depth 1) and `top.py` imports `use.py` (depth 2); the importer in
    another repository weighs as depth 1. Without it the average is
    (1 + 2^-0.7) / 2; with it, (1 + 2^-0.7 + 1) / 3."""
    provider_src, consumer_src, store = tmp_path / "provider", tmp_path / "consumer", tmp_path / "store"
    store.mkdir()
    _write(provider_src / "pyproject.toml", '[project]\nname = "mylib"\n')
    _write(provider_src / "mylib" / "__init__.py", "")
    _write(provider_src / "mylib" / "core.py", "def do_thing(): pass\n")
    _write(provider_src / "mylib" / "use.py", "from mylib.core import do_thing\ndo_thing()\n")
    _write(provider_src / "mylib" / "top.py", "from mylib.use import do_thing\ndo_thing()\n")
    _write(consumer_src / "app.py", "import mylib\nmylib.do_thing()\n")
    repo = _index(provider_src, store)
    _index(consumer_src, store)
    invalidate_registry_cache()
    try:
        result = get_blast_radius(repo=repo, symbol="do_thing", storage_path=str(store), cross_repo=True, depth=2)
    finally:
        invalidate_registry_cache()
    assert result["importer_count"] == 2
    assert result["cross_repo_confirmed_count"] == 1
    assert result["overall_risk_score"] == round((1 + 2 ** -0.7 + 1) / 3, 4)


def test_the_task_capsule_keeps_the_cross_repo_importers(provider):
    """`assemble_task_context` asks `get_blast_radius` for the cross-repo
    channel; its blast entry carried `confirmed_count: 0` and dropped the
    importer it had asked for (#877, the same channel one consumer on)."""
    from jcodemunch_mcp.tools.assemble_task_context import assemble_task_context

    repo, store = provider
    out = assemble_task_context(
        repo=repo, task="audit do_thing", symbols=["do_thing"],
        include=["anchor", "blast"], cross_repo=True, storage_path=store,
    )
    blast = [e for e in out["entries"] if e["stage"] == "blast"]
    assert blast, out.get("stages_run")
    assert blast[0]["cross_repo_confirmed_count"] == 1
    assert blast[0]["top_cross_repo"][0]["file"] == "app.py"


def test_endpoint_impact_keeps_the_channel_cross_repo_default_turns_on(provider, monkeypatch):
    """`get_endpoint_impact` calls `get_blast_radius` without `cross_repo`, so
    `cross_repo_default: true` runs the channel for it; it published only the
    local `affected_file_count` and dropped the rest (review of #877). The
    real producer runs; only the config default is set."""
    import jcodemunch_mcp.config as config
    from jcodemunch_mcp.tools.get_endpoint_impact import _impact_for_handler

    real_get = config.get
    monkeypatch.setattr(
        config, "get",
        lambda key, *a, **k: True if key == "cross_repo_default" else real_get(key, *a, **k),
    )
    repo, store = provider
    out = _impact_for_handler(
        repo, {"handler_id": "do_thing", "verb": "GET", "path": "/thing"}, [],
        depth=1, call_depth=0, storage_path=store,
    )
    assert out["affected_file_count"] == 0
    assert out["cross_repo_affected_count"] == 1
    assert out["cross_repo_affected_files"][0]["file"] == "app.py"
