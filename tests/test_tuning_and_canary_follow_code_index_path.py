"""The tuning sidecar and the embedding-drift canary live in the index store's root.

LEDGER L-285: `retrieval/tuning.py` and `retrieval/embed_drift.py` built
`Path.home() / ".code-index"` when no base path was passed, and ignored
`CODE_INDEX_PATH`. A user who set it got their indexes in one directory and
`tuning.jsonc` / the drift canary in another. The root rule has one authority,
`storage.sqlite_store._default_base_path`; both sites now ask it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jcodemunch_mcp.retrieval import embed_drift, tuning
from jcodemunch_mcp.storage import sqlite_store

SITES = [
    pytest.param(tuning._tuning_path, id="tuning"),
    pytest.param(embed_drift._canary_path, id="embed_drift"),
]


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


@pytest.mark.parametrize("site", SITES)
def test_code_index_path_is_honoured(site, tmp_path, fake_home, monkeypatch):
    store = tmp_path / "store"
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    path = site()
    assert path.parent.resolve() == store.resolve()
    assert not (fake_home / ".code-index").exists(), (
        "nothing may be created in the home default"
    )


@pytest.mark.parametrize("site", SITES)
def test_same_root_as_the_index_store(site, tmp_path, fake_home, monkeypatch):
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / "store"))
    assert site().parent.resolve() == Path(sqlite_store._default_base_path()).resolve()


@pytest.mark.parametrize("site", SITES)
def test_unset_falls_back_to_the_home_default(site, fake_home, monkeypatch):
    monkeypatch.delenv("CODE_INDEX_PATH", raising=False)
    assert site().parent.resolve() == (fake_home / ".code-index").resolve()


@pytest.mark.parametrize("site", SITES)
def test_an_explicit_base_path_still_wins(site, tmp_path, fake_home, monkeypatch):
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / "store"))
    explicit = tmp_path / "explicit"
    assert site(str(explicit)).parent.resolve() == explicit.resolve()
