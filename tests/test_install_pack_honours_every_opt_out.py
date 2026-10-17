"""install-pack honours every savings opt-out (ledger L-212, jjg 2026-10-17).

`install-pack` posted an install event (pack id, version, platform) unless
`JCODEMUNCH_SHARE_SAVINGS` was exactly "0", a second parser beside `config`. A
user who opted out with `false`, `off` or `no`, or with `share_savings: false`
in the config file, still sent it. jjg ruled the fix with one constraint:
install-pack telemetry may only ever send LESS.

⚠⚠ The real config runs here, unmocked (review of L-212: a first draft stubbed
`config.get` and re-implemented its env parser, so it supplied the contract it
was meant to check). Each case points `CODE_INDEX_PATH` at its own empty store
and resets the loaded config. `test_reading_the_setting_creates_no_config_file`
pins the review's other finding: the first draft called `load_config()`, which
WRITES `config.jsonc` when absent and can pin a new user to the `full` surface.
"""

from __future__ import annotations

import ast
import inspect
import json

import pytest

from jcodemunch_mcp import config as config_module
from jcodemunch_mcp.cli import install_pack

_ENV_VALUES = [None, "0", "0 ", " 0", "1", "true", "false", "FALSE", "off", "no", "on", "yes", "garbage", ""]
_FILE_VALUES = [None, True, False]


def _allowed(monkeypatch, tmp_path, env, file_value) -> bool:
    store = tmp_path / "store"
    store.mkdir(exist_ok=True)
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    if env is None:
        monkeypatch.delenv("JCODEMUNCH_SHARE_SAVINGS", raising=False)
    else:
        monkeypatch.setenv("JCODEMUNCH_SHARE_SAVINGS", env)
    if file_value is not None:
        (store / "config.jsonc").write_text(json.dumps({"share_savings": file_value}), encoding="utf-8")
    monkeypatch.setattr(config_module, "_GLOBAL_CONFIG", {})
    monkeypatch.setattr(config_module, "_CONFIG_LOADED", False)
    return install_pack._install_telemetry_allowed()


@pytest.mark.parametrize("env", ["0", "false", "False", "off", "OFF", "no"])
def test_every_env_opt_out_spelling_stops_the_post(monkeypatch, tmp_path, env):
    assert _allowed(monkeypatch, tmp_path, env, None) is False


def test_a_config_file_opt_out_stops_the_post(monkeypatch, tmp_path):
    assert _allowed(monkeypatch, tmp_path, None, False) is False


def test_nothing_set_still_posts(monkeypatch, tmp_path):
    assert _allowed(monkeypatch, tmp_path, None, None) is True


def test_env_zero_wins_over_a_config_file_saying_true(monkeypatch, tmp_path):
    """The old rule's `=0` is kept as the first test, so it always stops the post."""
    assert _allowed(monkeypatch, tmp_path, "0", True) is False


@pytest.mark.parametrize("env", _ENV_VALUES)
@pytest.mark.parametrize("file_value", _FILE_VALUES)
def test_it_never_sends_where_the_old_rule_did_not(monkeypatch, tmp_path, env, file_value):
    """jjg: install-pack telemetry may only ever send less."""
    old_rule = (env if env is not None else "1") != "0"
    if _allowed(monkeypatch, tmp_path, env, file_value):
        assert old_rule, (env, file_value)


def test_reading_the_setting_creates_no_config_file(monkeypatch, tmp_path):
    """Practice 8: a read must never write `config.jsonc` (review of L-212)."""
    _allowed(monkeypatch, tmp_path, None, None)
    assert not (tmp_path / "store" / "config.jsonc").exists()


_BAD_FILES = ['{"share_savings": false,, }', '{"share_savings": "no"}', "not json at all"]


@pytest.mark.parametrize("text", _BAD_FILES)
def test_an_unparseable_config_reads_as_the_default_and_never_sends_more(monkeypatch, tmp_path, text):
    """The real loader maps a parse error or a wrong-typed value to the default
    (on), so the answer is the old rule's, never more (review round 2 of L-212:
    a stub that raised pinned a branch the real `config.get` never reaches)."""
    store = tmp_path / "store"
    store.mkdir()
    (store / "config.jsonc").write_text(text, encoding="utf-8")
    for env, expected in ((None, True), ("0", False), ("off", False)):
        monkeypatch.setenv("CODE_INDEX_PATH", str(store))
        if env is None:
            monkeypatch.delenv("JCODEMUNCH_SHARE_SAVINGS", raising=False)
        else:
            monkeypatch.setenv("JCODEMUNCH_SHARE_SAVINGS", env)
        monkeypatch.setattr(config_module, "_GLOBAL_CONFIG", {})
        monkeypatch.setattr(config_module, "_CONFIG_LOADED", False)
        assert install_pack._install_telemetry_allowed() is expected, (text, env)


def test_the_telemetry_post_is_guarded_by_the_helper():
    """The one `action=telemetry` post in the module sits under
    `if _install_telemetry_allowed():`, not under a second env parser."""
    tree = ast.parse(inspect.getsource(install_pack))
    guarded = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            body_src = "\n".join(ast.unparse(s) for s in node.body)
            if "action=telemetry" in body_src:
                guarded.append(ast.unparse(node.test))
    assert guarded == ["_install_telemetry_allowed()"], guarded
