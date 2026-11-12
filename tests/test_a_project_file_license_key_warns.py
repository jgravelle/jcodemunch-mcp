"""A license key in a project `.jcodemunch.jsonc` warns and is not read (PRIVATE-LEDGER 2026-11-09).

The license check reads `license_key` with no repo, so a key a customer put in
a project file never applied, and nothing said so: a paying customer read as
unlicensed with no clue why. The project loader accepted the key into the
overlay and `config` reported it as the project's.

A license key is read only from the user's own config.jsonc or
JCODEMUNCH_LICENSE_KEY. A project file can be committed to a repository, so
the key is never read from one; the loader now warns and names both places,
as it already does for the trusted-folders keys.
"""

from __future__ import annotations

import logging

import pytest

from jcodemunch_mcp import config as config_module
from jcodemunch_mcp.org import license as lic

# A made-up key; never a real customer's.
KEY = "JCM-TEST-0000-NOT-A-REAL-KEY"


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / "storage"))
    monkeypatch.delenv("JCODEMUNCH_LICENSE_KEY", raising=False)
    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "license_key", "")
    monkeypatch.setattr(config_module, "_PROJECT_KEYS_WARNED", set())
    root = tmp_path / "repo"
    root.mkdir()
    return root


def _project_file(root, text):
    (root / ".jcodemunch.jsonc").write_text(text, encoding="utf-8")
    config_module.invalidate_project_config_cache(str(root))
    config_module.load_project_config(str(root))


def test_a_project_file_license_key_warns_and_names_where_it_belongs(project, caplog):
    with caplog.at_level(logging.WARNING, logger="jcodemunch_mcp.config"):
        _project_file(project, '{"license_key": "%s"}' % KEY)
    msgs = [r.getMessage() for r in caplog.records if "'license_key'" in r.getMessage()]
    assert msgs, "a project-file license key was dropped with no warning"
    assert "JCODEMUNCH_LICENSE_KEY" in msgs[0]
    assert "config.jsonc" in msgs[0]
    assert "trusted" not in msgs[0], (
        "the trust keys' reason was printed for a license key"
    )
    assert KEY not in msgs[0], "the warning printed the key"


def test_a_project_file_license_key_is_not_read_and_not_reported_as_in_effect(project):
    _project_file(project, '{"license_key": "%s"}' % KEY)
    assert lic._license_key() == ""
    assert lic.licence_key_configured() is False
    assert config_module.get("license_key", repo=str(project)) == ""
    in_effect, ignored = config_module.project_keys_in_effect(str(project))
    assert "license_key" in ignored
    assert "license_key" not in in_effect


def test_the_trusted_folders_keys_keep_their_own_reason(project, caplog):
    with caplog.at_level(logging.WARNING, logger="jcodemunch_mcp.config"):
        _project_file(project, '{"trusted_folders": ["."]}')
    msgs = [
        r.getMessage() for r in caplog.records if "'trusted_folders'" in r.getMessage()
    ]
    assert msgs and "trusted" in msgs[0]
    assert "JCODEMUNCH_LICENSE_KEY" not in msgs[0]


def test_the_users_own_config_and_env_still_carry_the_key(project, monkeypatch):
    _project_file(project, '{"license_key": "%s"}' % KEY)
    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "license_key", KEY)
    assert lic._license_key() == KEY
    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "license_key", "")
    monkeypatch.setenv("JCODEMUNCH_LICENSE_KEY", KEY)
    assert lic._license_key() == KEY
