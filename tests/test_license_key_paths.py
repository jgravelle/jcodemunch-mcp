"""A license key reaches every consumer from every place a customer puts it.

Three of four licensing issues were customers who had just paid: #364 (a key
in config.jsonc not read, then "evaluation" with a valid key), #418 (a valid
key refused for a pack download) and #58 (a placeholder buy link).

⚠⚠ The existing gate tests (`test_org_license.py`) patch `_license_key`, the
function that decides WHERE the key comes from. #364 was a defect in exactly
that resolution, so a test that replaces the resolver cannot see it again.
This file patches only the two network edges, validate.php
(`org.license._check_server`) and the pack API (`install_pack.httpx.get`), and
drives the real CLI entry point for each consumer, so key resolution, the
cache and the gate all run as a customer's install runs them.

The key is made up. The fake server answers valid for it and invalid for
anything else, so no real key exists anywhere in this file.
"""

from __future__ import annotations

import json

import pytest

import jcodemunch_mcp.config as config_module
from jcodemunch_mcp.cli import install_pack
from jcodemunch_mcp.org import license as lic

TEST_KEY = "JCM-TEST-0000-PATHS"
WRONG_KEY = "JCM-TEST-9999-WRONG"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """A fake home and index store; no key anywhere until a test puts one."""
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("JCODEMUNCH_LICENSE_KEY", raising=False)
    return store


@pytest.fixture
def server(monkeypatch):
    """validate.php, recording every key it is asked about."""
    asked: list[str] = []

    def _check(key):
        asked.append(key)
        if key == TEST_KEY:
            return {"valid": True, "tier": "studio", "error": None}
        return {"valid": False, "tier": None, "error": "invalid license key"}

    monkeypatch.setattr(lic, "_check_server", _check)
    return asked


@pytest.fixture
def pack_api(monkeypatch):
    """The pack download endpoint, recording the headers of every request."""
    sent: list[dict] = []

    class _Resp:
        status_code = 403
        headers = {"content-type": "application/json"}

        def json(self):
            return {"error": "stopped by the test after the request was recorded"}

    def _get(url, **kwargs):
        sent.append(dict(kwargs.get("headers") or {}))
        return _Resp()

    monkeypatch.setattr(install_pack, "_catalog_version", lambda pack_id: None)
    monkeypatch.setattr(install_pack.httpx, "get", _get)
    return sent


def _key_in_config(store, key, *, load=False):
    """Write the key where a customer would. The CLI entry points must load it
    THEMSELVES (#364 was a handler that returned before loading config), so
    only the library-level presence checks, which run inside a process that
    has already loaded its config, ask for `load=True`."""
    (store / "config.jsonc").write_text(json.dumps({"license_key": key}), encoding="utf-8")
    if load:
        config_module.load_config(storage_path=str(store), create_missing=False)


def _key_in_env(monkeypatch, key):
    monkeypatch.setenv("JCODEMUNCH_LICENSE_KEY", key)


def _run(capsys, argv):
    from jcodemunch_mcp.server import main

    try:
        main(argv)
    except SystemExit:
        pass  # install-pack exits 1 on the refusal the fake pack API returns
    return capsys.readouterr()


# --------------------------------------------------------------------------- #
# `license`: the key a customer set is the key validate.php is asked about
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("source", ["env", "config"])
def test_license_command_reads_the_key_from(source, server, capsys, monkeypatch, _isolated):
    if source == "env":
        _key_in_env(monkeypatch, TEST_KEY)
    else:
        _key_in_config(_isolated, TEST_KEY)
    payload = json.loads(_run(capsys, ["license", "--json"]).out)
    assert server == [TEST_KEY]
    assert payload["key_status"] == "valid"
    assert payload["mode"] == "licensed"
    assert payload["get_license"] is None  # a key holder is never shown a sales link


def test_license_key_flag_beats_config(server, capsys, _isolated):
    _key_in_config(_isolated, WRONG_KEY)
    payload = json.loads(_run(capsys, ["license", "--key", TEST_KEY, "--json"]).out)
    assert server == [TEST_KEY]
    assert payload["key_status"] == "valid"


def test_env_beats_config(server, capsys, monkeypatch, _isolated):
    _key_in_config(_isolated, WRONG_KEY)
    _key_in_env(monkeypatch, TEST_KEY)
    payload = json.loads(_run(capsys, ["license", "--json"]).out)
    assert server == [TEST_KEY]
    assert payload["key_status"] == "valid"


def test_a_wrong_key_in_config_is_rejected_not_silently_evaluation(server, capsys, _isolated):
    """#364's second half: a configured key must be named as the reason, so a
    customer sees that their key was read and refused, not a trial message."""
    _key_in_config(_isolated, WRONG_KEY)
    payload = json.loads(_run(capsys, ["license", "--json"]).out)
    assert server == [WRONG_KEY]
    assert payload["key_status"] == "rejected"


def test_no_key_anywhere_is_absent_and_asks_nothing(server, capsys):
    payload = json.loads(_run(capsys, ["license", "--json"]).out)
    assert server == []
    assert payload["key_status"] == "absent"
    assert payload["get_license"].startswith("https://jcodemunch.com/")  # #58: never a placeholder


# --------------------------------------------------------------------------- #
# `org-rollup`: the one paid feature gets past its gate
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("source", ["env", "config"])
def test_org_rollup_is_unlocked_by_a_key_from(source, server, capsys, monkeypatch, _isolated):
    import jcodemunch_mcp.org.store as store_mod

    monkeypatch.setattr(store_mod, "org_rollup", lambda org: {
        "totals": {"seat_count": 0, "tokens_saved": 0, "usd": 0.0, "calls": 0}, "seats": [],
    })
    if source == "env":
        _key_in_env(monkeypatch, TEST_KEY)
    else:
        _key_in_config(_isolated, TEST_KEY)
    out = _run(capsys, ["org-rollup", "--org", "acme", "--json"])
    payload = json.loads(out.out)
    assert server == [TEST_KEY]
    assert payload["_license"]["mode"] == "licensed"
    assert "unavailable" not in out.err


# --------------------------------------------------------------------------- #
# `install-pack`: the key travels in the header (#418)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("source", ["flag", "env", "config"])
def test_install_pack_sends_the_key_from(source, pack_api, capsys, monkeypatch, _isolated):
    argv = ["install-pack", "fastapi"]
    if source == "flag":
        argv += ["--license", TEST_KEY]
    elif source == "env":
        _key_in_env(monkeypatch, TEST_KEY)
    else:
        _key_in_config(_isolated, TEST_KEY)
    _run(capsys, argv)
    assert [h.get("X-JCM-License") for h in pack_api] == [TEST_KEY]


def test_install_pack_flag_beats_env(pack_api, capsys, monkeypatch):
    _key_in_env(monkeypatch, WRONG_KEY)
    _run(capsys, ["install-pack", "fastapi", "--license", TEST_KEY])
    assert [h.get("X-JCM-License") for h in pack_api] == [TEST_KEY]


def test_install_pack_without_a_key_sends_none(pack_api, capsys):
    _run(capsys, ["install-pack", "fastapi"])
    assert [h.get("X-JCM-License") for h in pack_api] == [None]


# --------------------------------------------------------------------------- #
# The three sales surfaces: a configured key silences them, and none of them
# sends the key anywhere (presence is read locally, never validated)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("source", ["env", "config"])
def test_sales_surfaces_stay_quiet_for_a_key_from(source, server, monkeypatch, _isolated):
    from jcodemunch_mcp import license_notice
    from jcodemunch_mcp.cli import init

    if source == "env":
        _key_in_env(monkeypatch, TEST_KEY)
    else:
        _key_in_config(_isolated, TEST_KEY, load=True)
    assert init._licence_key_configured() is True
    assert lic.licence_key_configured() is True
    assert license_notice.skip_reason(env={}) == "licensed"
    assert server == []  # presence checks never call validate.php


def test_sales_surfaces_speak_with_no_key(monkeypatch):
    from jcodemunch_mcp import license_notice
    from jcodemunch_mcp.cli import init

    assert init._licence_key_configured() is False
    assert license_notice.skip_reason(env={}) in (None, "already_shown")
