"""`CODE_INDEX_PATH` must move the storage classes, not just the config.

⚠⚠ It did not. `config.py`, `process_registry.py`, `install_pack.py`,
`receipt.py` and two `server.py` sites all read `CODE_INDEX_PATH`, while
`IndexStore` and `SQLiteIndexStore` hardcoded `Path.home() / ".code-index"`
and ignored it. A user who set the variable therefore got their CONFIG from
one directory and their INDEXES in another — split state produced by a knob
the env table documents as "Index storage location".

⚠ Nothing errored, which is why it survived: an index written to the wrong
root is a successful write. Same shape as #428, where a declared
`constant_patterns` entry with no branch behind it was indistinguishable from
a language that has no constants.

⚠ The fingerprint was in the tree the whole time — two `server.py` call sites
pass `os.environ.get("CODE_INDEX_PATH")` by hand. Someone hit this and patched
their own call site rather than the default.

⚠ Found because the test suite could not be isolated from the developer's real
`~/.code-index` without it: pinning the env var moved the WRITE and not the
READ, so indexes vanished between the two.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from jcodemunch_mcp.storage.index_store import IndexStore
from jcodemunch_mcp.storage.sqlite_store import SQLiteIndexStore, _default_base_path


@pytest.mark.parametrize("cls", [IndexStore, SQLiteIndexStore])
def test_env_var_moves_the_default_store(cls, tmp_path, monkeypatch):
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / "store"))
    assert cls().base_path == (tmp_path / "store").resolve()


@pytest.mark.parametrize("cls", [IndexStore, SQLiteIndexStore])
def test_explicit_base_path_still_wins_over_the_env_var(cls, tmp_path, monkeypatch):
    """An argument the caller named must never be overridden by the environment."""
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / "env"))
    explicit = tmp_path / "explicit"
    assert cls(base_path=str(explicit)).base_path == explicit.resolve()


def test_falls_back_to_home_when_unset(monkeypatch):
    monkeypatch.delenv("CODE_INDEX_PATH", raising=False)
    assert _default_base_path() == Path.home() / ".code-index"


def test_empty_env_var_is_treated_as_unset(monkeypatch):
    """`CODE_INDEX_PATH=` must not resolve to the process working directory.

    `Path("")` is `.`, so a bare `export CODE_INDEX_PATH=` would otherwise put
    every index in whatever directory the server happened to start in — the
    same class of CWD-dependence v1.108.280 removed from the perf-db cache key.
    """
    monkeypatch.setenv("CODE_INDEX_PATH", "")
    assert _default_base_path() == Path.home() / ".code-index"


def test_the_two_classes_cannot_disagree(tmp_path, monkeypatch):
    """One spelling, one directory — the v1.108.280 rule, applied here.

    `IndexStore` constructs a `SQLiteIndexStore` internally, so a divergence
    between their defaults would put the metadata and the rows in different
    roots.
    """
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / "shared"))
    assert IndexStore().base_path == SQLiteIndexStore().base_path


def test_env_var_is_resolved_not_taken_literally(tmp_path, monkeypatch):
    """A relative spelling must not depend on the caller's working directory."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "rel").mkdir()
    monkeypatch.setenv("CODE_INDEX_PATH", "rel")
    resolved = _default_base_path()
    assert resolved.is_absolute()
    assert resolved == (tmp_path / "rel").resolve()


def test_conftest_pins_the_env_var_for_the_whole_run():
    """The suite must never be able to reach the developer's real store.

    ⚠ This is the ratchet. Without the session fixture in conftest, any test
    calling `index_folder()` or constructing a store without an explicit path
    writes `~/.code-index` — tolerable serially, and under `pytest-xdist` four
    workers contend on one store and its `indexwrite` process-lock scopes.
    `test_v1_108_2.py::test_probe_runs_when_identity_true` failed once on a
    Linux CI leg that way, passed on a re-run of the identical tree, and
    survived all 14 bisect pairings.
    """
    pinned = os.environ.get("CODE_INDEX_PATH")
    assert pinned, "conftest must pin CODE_INDEX_PATH for the session"
    assert Path(pinned).resolve() != (Path.home() / ".code-index").resolve(), (
        "the suite is pointed at the developer's real index store"
    )
