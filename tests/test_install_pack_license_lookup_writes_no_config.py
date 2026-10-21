"""install-pack's license lookup writes no config file (ledger L-215).

`resolve_effective_license_key` called `load_config()` with the default
`create_missing=True`, and `run_install_pack` calls it before every install
that has no `--license`. So `install-pack <id>` could create `config.jsonc`
when it was absent, and once a pack's `.db` sits in the store that new file
lacks `tool_surface` and pins a first-time user to the `full` surface
(Practice 8). L-212 fixed the same shape one function later on this path.

The real config runs here, unmocked, against an isolated `CODE_INDEX_PATH`
per case, as in `test_install_pack_honours_every_opt_out.py`.
"""

from __future__ import annotations

import json

import pytest

from jcodemunch_mcp import config as config_module
from jcodemunch_mcp.cli import install_pack


@pytest.fixture
def store(monkeypatch, tmp_path):
    path = tmp_path / "store"
    path.mkdir()
    monkeypatch.setenv("CODE_INDEX_PATH", str(path))
    monkeypatch.delenv("JCODEMUNCH_LICENSE_KEY", raising=False)
    monkeypatch.setattr(config_module, "_GLOBAL_CONFIG", {})
    monkeypatch.setattr(config_module, "_CONFIG_LOADED", False)
    return path


def _write_config(store, key):
    (store / "config.jsonc").write_text(json.dumps({"license_key": key}), encoding="utf-8")


def test_a_config_license_key_is_still_found(store):
    _write_config(store, "CFG-KEY-1234")
    assert install_pack.resolve_effective_license_key(None) == "CFG-KEY-1234"


def test_a_config_key_is_found_when_config_was_already_loaded(store, monkeypatch):
    """`config.get`'s lazy load fires only when nothing was ever loaded, so
    leaning on it alone would hide the file's key behind any earlier load
    (the first draft of L-215 did, and `test_v1_108_122` caught it)."""
    from copy import deepcopy

    monkeypatch.setattr(config_module, "_GLOBAL_CONFIG", deepcopy(config_module.DEFAULTS))
    monkeypatch.setattr(config_module, "_CONFIG_LOADED", True)
    _write_config(store, "CFG-KEY-1234")
    assert install_pack.resolve_effective_license_key(None) == "CFG-KEY-1234"


def test_the_env_var_wins_over_the_config_key(store, monkeypatch):
    _write_config(store, "CFG-KEY-1234")
    monkeypatch.setenv("JCODEMUNCH_LICENSE_KEY", "ENV-KEY-5678")
    assert install_pack.resolve_effective_license_key(None) == "ENV-KEY-5678"


def test_the_env_var_is_used_with_no_config_file(store, monkeypatch):
    monkeypatch.setenv("JCODEMUNCH_LICENSE_KEY", "ENV-KEY-5678")
    assert install_pack.resolve_effective_license_key(None) == "ENV-KEY-5678"


def test_an_explicit_license_wins_over_everything(store, monkeypatch):
    _write_config(store, "CFG-KEY-1234")
    monkeypatch.setenv("JCODEMUNCH_LICENSE_KEY", "ENV-KEY-5678")
    assert install_pack.resolve_effective_license_key("CLI-KEY-0000") == "CLI-KEY-0000"


def test_no_key_anywhere_resolves_to_none(store):
    assert install_pack.resolve_effective_license_key(None) is None


def test_resolving_the_key_creates_no_config_file(store):
    """Practice 8: the lookup must never write `config.jsonc`."""
    install_pack.resolve_effective_license_key(None)
    assert not (store / "config.jsonc").exists()


def test_run_install_pack_creates_no_config_file(store, monkeypatch):
    """The command path: `install-pack <id>` with no `--license`. The download
    is replaced so nothing touches the network; the lookup ahead of it runs."""
    seen = {}

    def fake_install(pack_id, license_key=None, force=False):
        seen["key"] = license_key
        return 0

    monkeypatch.setattr(install_pack, "_install_pack", fake_install)
    assert install_pack.run_install_pack("some-pack") == 0
    assert seen == {"key": None}
    assert not (store / "config.jsonc").exists()


def test_run_install_pack_passes_the_config_key_on(store, monkeypatch):
    _write_config(store, "CFG-KEY-1234")
    seen = {}

    def fake_install(pack_id, license_key=None, force=False):
        seen["key"] = license_key
        return 0

    monkeypatch.setattr(install_pack, "_install_pack", fake_install)
    assert install_pack.run_install_pack("some-pack") == 0
    assert seen == {"key": "CFG-KEY-1234"}
