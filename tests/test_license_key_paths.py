"""A license key reaches every consumer from every place a customer puts it.

Several licensing issues came from customers who had just paid: #364 (a key
in config.jsonc not read, then "evaluation" with a valid key), #418 (a valid
key refused for a pack download) and #58 (a placeholder buy link).

⚠⚠ The existing gate tests (`test_org_license.py`) patch `_license_key`, the
function that decides WHERE the key comes from. #364 was a defect in exactly
that resolution, so a test that replaces the resolver cannot see it again.
This file patches only network edges, validate.php
(`org.license._check_server`) and the pack API (`install_pack.httpx.get`,
`install_pack.httpx.stream` and `install_pack._catalog_version`), plus the file writers of a real `init` run,
and drives the real entry point for each consumer, so key resolution, the
config load, the cache and the gate all run as a customer's install runs them.

The key is made up. The fake server answers valid for it and invalid for
anything else, so no real key exists anywhere in this file.
"""

from __future__ import annotations

import json
import re

import pytest

import jcodemunch_mcp.config as config_module
from jcodemunch_mcp.cli import install_pack
from jcodemunch_mcp.org import license as lic

TEST_KEY = "JCM-TEST-0000-PATHS"
WRONG_KEY = "JCM-TEST-9999-WRONG"


def _is_our_url(url: str) -> bool:
    """An https link whose host is exactly jcodemunch.com (#58: never a placeholder)."""
    from urllib.parse import urlparse

    parts = urlparse(url)
    return parts.scheme == "https" and parts.hostname == "jcodemunch.com"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """A fake home and index store; no key anywhere until a test puts one."""
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    # `license --key` writes os.environ itself. delenv on an absent variable
    # records no undo, so set it first: teardown then restores absence.
    monkeypatch.setenv("JCODEMUNCH_LICENSE_KEY", "unset-by-teardown")
    monkeypatch.delenv("JCODEMUNCH_LICENSE_KEY")
    # The reminder's off switches, CI runners included, silence the same lines
    # a key does; with one set, a "key silences it" cell passes for the wrong
    # reason and the no-key control fails.
    from jcodemunch_mcp.license_reminder import CI_MARKERS, ENV_VAR, NOTICE_ENV_VAR

    for name in (*CI_MARKERS, ENV_VAR, NOTICE_ENV_VAR):
        monkeypatch.delenv(name, raising=False)
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
    """The pack download endpoint, recording the headers of every request.

    The download streams (`with httpx.stream("GET", ...)`, #1346). `get` is
    served too, so the cells keep recording if the download path changes
    shape again; conftest's network guard fails any request neither catches.
    """
    from contextlib import contextmanager

    sent: list[dict] = []

    class _Resp:
        status_code = 403
        headers = {"content-type": "application/json"}

        def json(self):
            return {"error": "stopped by the test after the request was recorded"}

        def read(self):
            return b""

    def _get(url, **kwargs):
        sent.append(dict(kwargs.get("headers") or {}))
        return _Resp()

    @contextmanager
    def _stream(method, url, **kwargs):
        yield _get(url, **kwargs)

    monkeypatch.setattr(install_pack, "_catalog_version", lambda pack_id: None)
    monkeypatch.setattr(install_pack.httpx, "get", _get)
    monkeypatch.setattr(install_pack.httpx, "stream", _stream)
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
    """The console script itself (`pyproject.toml`'s `jcodemunch-mcp`), so the
    command runs as a customer's does: through `cli_entry.cli`, then
    `server.cli` and its after-command license reminder, then `server.main`."""
    from jcodemunch_mcp.cli_entry import cli

    try:
        cli(argv)
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
    assert payload["tier"] == "studio"
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
    assert _is_our_url(payload["get_license"])  # #58: never a placeholder


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
# A project's `.jcodemunch.jsonc` is not a key source
# --------------------------------------------------------------------------- #

def test_a_key_in_project_config_is_not_read(server, capsys, monkeypatch, tmp_path, caplog):
    """Only the env var and the GLOBAL config.jsonc supply a key. Since
    PRIVATE-LEDGER 2026-11-09 the project loader refuses a `license_key` and
    warns, naming both places (it used to accept it into the overlay silently,
    so a customer who put the key there read as unlicensed with no clue why).
    CONFIGURATION.md says so. If project config should become a source, change
    the code, CONFIGURATION.md and this cell together.

    The project file is loaded the way the server loads it when the repo is
    indexed, so the cell holds in a process that HAS read the file, not only in
    a `license` run that never looks at it."""
    import logging

    project = tmp_path / "project"
    project.mkdir()
    (project / ".jcodemunch.jsonc").write_text(json.dumps({"license_key": TEST_KEY}), encoding="utf-8")
    monkeypatch.chdir(project)
    with caplog.at_level(logging.WARNING, logger="jcodemunch_mcp.config"):
        config_module.load_project_config(str(project))
    assert config_module.get("license_key", repo=str(project)) == ""  # refused at load
    assert lic.licence_key_configured() is False
    warned = [r.getMessage() for r in caplog.records if "license_key" in r.getMessage()]
    assert warned and "JCODEMUNCH_LICENSE_KEY" in warned[0]
    assert TEST_KEY not in warned[0]
    payload = json.loads(_run(capsys, ["license", "--json"]).out)
    assert server == []
    assert payload["key_status"] == "absent"


# --------------------------------------------------------------------------- #
# The sales surfaces, through their entry points: a key in config.jsonc
# silences each one, and none of them sends the key anywhere
# --------------------------------------------------------------------------- #

_INIT_MARKERS = ("descriptions.php", "guarantee.php", "Using jCodeMunch at work?")


@pytest.fixture
def quiet_init(monkeypatch):
    """A real, non-dry-run `init` with every file writer replaced by a spy
    (the same shape as `test_init_licence_line.py`). The config load is NOT
    replaced: that load is the thing under test."""
    from unittest.mock import MagicMock

    from jcodemunch_mcp.cli import init as init_module

    for name in ("install_claude_md", "install_cursor_rules", "install_windsurf_rules", "install_agents_md",
                 "install_hooks", "install_enforcement_hooks", "install_copilot_hooks", "run_index"):
        monkeypatch.setattr(init_module, name, MagicMock(return_value="  (spy)"))
    monkeypatch.setattr(init_module, "run_audit", MagicMock(return_value=iter([])))
    client = MagicMock()
    client.name = "Claude Code"
    client.config_path = None
    monkeypatch.setattr(init_module, "_detect_clients", lambda: [client])
    monkeypatch.setattr(init_module, "configure_client", MagicMock(return_value="  (spy)"))

    def _run_init(capsys):
        rc = init_module.run_init(clients=None, hooks=False, index=False, audit=False, yes=True,
                                  minimal=True, dry_run=False)
        assert rc == 0
        return capsys.readouterr().out

    return _run_init


@pytest.mark.parametrize("source", ["env", "config"])
def test_init_prints_no_license_line_for_a_key_from(source, quiet_init, server, capsys, monkeypatch, _isolated):
    if source == "env":
        _key_in_env(monkeypatch, TEST_KEY)
    else:
        _key_in_config(_isolated, TEST_KEY)
    out = quiet_init(capsys)
    assert "Done." in out
    assert not any(m in out for m in _INIT_MARKERS), out
    assert server == []  # presence is read locally, never validated


def test_init_prints_the_license_line_with_no_key(quiet_init, capsys):
    """The control: without it, the cell above passes on an `init` that never
    prints the line at all."""
    out = quiet_init(capsys)
    assert "Using jCodeMunch at work?" in out


@pytest.fixture
def catalog(monkeypatch):
    """The pack catalog with one licensed pack, so `--list` reaches its footer."""
    class _Resp:
        status_code = 200
        headers = {"content-type": "application/json"}

        def json(self):
            return {"packs": [{"id": "fastapi", "name": "FastAPI", "free": False}]}

    monkeypatch.setattr(install_pack.httpx, "get", lambda url, **kw: _Resp())


@pytest.fixture
def fresh_process(monkeypatch):
    """Config as a new CLI process has it: not loaded yet.

    conftest marks config loaded (defaults only) before every test. The
    `--list` path reads the key through `config.get`'s lazy load, which fires
    only when nothing was loaded, so under conftest's state the list path never
    sees config.jsonc. A real `install-pack --list` starts unloaded and does
    see it. The download path loads
    explicitly instead (`resolve_effective_license_key`); the list path's
    reliance on the lazy load is LEDGER L-267.
    """
    monkeypatch.setattr(config_module, "_CONFIG_LOADED", False)
    monkeypatch.setattr(config_module, "_GLOBAL_CONFIG", {})  # `_ensure_loaded` needs both


@pytest.mark.parametrize("source", ["flag", "env", "config"])
def test_install_pack_list_hides_the_sales_line_for_a_key_from(
    source, catalog, fresh_process, server, capsys, monkeypatch, _isolated
):
    args = ["install-pack", "--list"]
    if source == "flag":
        args += ["--license", TEST_KEY]
    elif source == "env":
        _key_in_env(monkeypatch, TEST_KEY)
    else:
        _key_in_config(_isolated, TEST_KEY)
    out = _run(capsys, args).out
    assert "fastapi" in out
    assert "Get a license" not in out
    assert server == []


def test_install_pack_list_shows_a_real_sales_link_with_no_key(catalog, fresh_process, capsys):
    out = _run(capsys, ["install-pack", "--list"]).out
    links = [ln for ln in out.splitlines() if "Get a license" in ln]
    assert len(links) == 1
    urls = re.findall(r"https?://[^\s\x1b]+", links[0])
    assert len(urls) == 1 and _is_our_url(urls[0]), links  # #58: never a placeholder


def _receipt(capsys, tmp_path, monkeypatch):
    """`receipt` over an empty transcript tree, so it scans nothing real."""
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    root = tmp_path / "transcripts"
    root.mkdir(exist_ok=True)
    return _run(capsys, ["receipt", "--projects-root", str(root)]).out


@pytest.mark.parametrize("source", ["env", "config"])
def test_receipt_ends_without_the_license_line_for_a_key_from(
    source, fresh_process, server, capsys, monkeypatch, tmp_path, _isolated
):
    """`receipt` is dispatched above `main`'s shared config load, so a key in
    config.jsonc reaches its license line only through `config.get`'s lazy
    load, the same reliance as `install-pack --list` (hence `fresh_process`)."""
    from jcodemunch_mcp.license_reminder import BUY_URL, RECEIPT_TEXT

    if source == "env":
        _key_in_env(monkeypatch, TEST_KEY)
    else:
        _key_in_config(_isolated, TEST_KEY)
    from jcodemunch_mcp.license_reminder import switch_reason

    out = _receipt(capsys, tmp_path, monkeypatch)
    assert out.strip(), "receipt printed nothing, so the cell checks nothing"
    assert switch_reason() == "licensed"  # silenced by the key, not another switch
    assert RECEIPT_TEXT not in out
    assert BUY_URL not in out
    assert server == []


def test_receipt_ends_with_a_real_license_link_with_no_key(fresh_process, capsys, monkeypatch, tmp_path):
    """The control for the cell above, and #58's shape for this surface."""
    from jcodemunch_mcp.license_reminder import BUY_URL, RECEIPT_TEXT

    out = _receipt(capsys, tmp_path, monkeypatch)
    assert out.rstrip().endswith(RECEIPT_TEXT)
    assert _is_our_url(BUY_URL)  # #58: never a placeholder
    assert BUY_URL in out


@pytest.mark.parametrize("source", ["env", "config"])
def test_the_first_answer_notice_is_skipped_for_a_key_from(source, server, monkeypatch, _isolated):
    """The server loads config.jsonc at startup, before any tool answer, so
    this cell loads it the same way. It is the one library-level cell here: a
    test that starts the MCP server for one notice would cost more than the
    notice. The two cells above drive real entry points for the same rule."""
    from jcodemunch_mcp import license_notice

    if source == "env":
        _key_in_env(monkeypatch, TEST_KEY)
    else:
        _key_in_config(_isolated, TEST_KEY, load=True)
    assert license_notice.skip_reason(env={}) == "licensed"
    assert server == []


def test_with_no_key_nothing_counts_as_licensed():
    from jcodemunch_mcp import license_notice

    assert lic.licence_key_configured() is False
    assert license_notice.skip_reason(env={}) is None
