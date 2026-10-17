"""install-pack honours every savings opt-out (ledger L-212, jjg 2026-10-17).

`install-pack` posted an install event (pack id, version, platform) unless
`JCODEMUNCH_SHARE_SAVINGS` was exactly "0", a second parser beside `config`. A
user who opted out with `false`, `off` or `no`, or with `share_savings: false`
in the config file, still sent it. jjg ruled the fix with one constraint:
install-pack telemetry may only ever send LESS. The new condition is the old
one AND the config's answer, so `test_it_never_sends_where_the_old_rule_did_not`
is the load-bearing test here.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from jcodemunch_mcp import config as config_module
from jcodemunch_mcp.cli import install_pack


def _allowed(monkeypatch, env: str | None, file_value: bool | None) -> bool:
    if env is None:
        monkeypatch.delenv("JCODEMUNCH_SHARE_SAVINGS", raising=False)
    else:
        monkeypatch.setenv("JCODEMUNCH_SHARE_SAVINGS", env)
    # load_config() is the module's own call; stand it in so the test reads
    # neither the developer's real config nor the env twice (Practice 8).
    resolved = True if file_value is None else file_value
    if env is not None and env.lower() in ("false", "0", "no", "off"):
        resolved = False  # what config's env fallback reads for these spellings
    monkeypatch.setattr(config_module, "load_config", lambda *a, **k: None)
    monkeypatch.setattr(config_module, "get", lambda key, default=None, repo=None: resolved if key == "share_savings" else default)
    return install_pack._install_telemetry_allowed()


@pytest.mark.parametrize("env", ["0", "false", "False", "off", "OFF", "no"])
def test_every_env_opt_out_spelling_stops_the_post(monkeypatch, env):
    assert _allowed(monkeypatch, env, None) is False


def test_a_config_file_opt_out_stops_the_post(monkeypatch):
    assert _allowed(monkeypatch, None, False) is False


def test_nothing_set_still_posts(monkeypatch):
    assert _allowed(monkeypatch, None, None) is True


def test_an_unreadable_config_does_not_post(monkeypatch):
    monkeypatch.delenv("JCODEMUNCH_SHARE_SAVINGS", raising=False)

    def boom(*a, **k):
        raise OSError("config unreadable")

    monkeypatch.setattr(config_module, "load_config", boom)
    assert install_pack._install_telemetry_allowed() is False


@pytest.mark.parametrize("env", [None, "0", "1", "true", "false", "off", "on", "garbage", ""])
@pytest.mark.parametrize("file_value", [None, True, False])
def test_it_never_sends_where_the_old_rule_did_not(monkeypatch, env, file_value):
    """jjg: install-pack telemetry may only ever send less."""
    old_rule = (env if env is not None else "1") != "0"
    if _allowed(monkeypatch, env, file_value):
        assert old_rule, (env, file_value)


def test_the_telemetry_post_is_guarded_by_the_helper():
    """The one `action=telemetry` post in the module sits under
    `if _install_telemetry_allowed():`, not under a second env parser."""
    tree = ast.parse(inspect.getsource(install_pack))
    guarded = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test_src = ast.unparse(node.test)
            body_src = "\n".join(ast.unparse(s) for s in node.body)
            if "action=telemetry" in body_src:
                guarded.append(test_src)
    assert guarded == ["_install_telemetry_allowed()"], guarded
