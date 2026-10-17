"""A typo in a protective boolean env var keeps the default (ledger L-207).

`_parse_env_value` read a boolean as `value.lower() in ("true", "1", "yes",
"on")`, so any other value, a typo included, read as False. For a default-on
switch that turned it OFF: a mistyped `JCODEMUNCH_SKIP_VIRTUALENVS` re-admitted
installed packages, `JCODEMUNCH_TRUSTED_FOLDERS_WHITELIST_MODE` dropped the
allowlist, and `JCODEMUNCH_RUNTIME_REDACT` dropped PII redaction. The skip
getters and CLI-AND-ENV.md promised "only an explicit false disables it"; the
config file kept that promise and the env var did not.

⚠⚠ The rule is a SET of protective keys, not "garbage means default" for every
boolean. `JCODEMUNCH_SHARE_SAVINGS` is the savings-telemetry opt-out, and a typo
there reads as OFF today, the privacy-safe side. A blanket rule would flip it
ON. `test_a_typo_in_the_savings_opt_out_still_reads_as_off` pins that, and it is
the load-bearing test in this file.
"""

from __future__ import annotations

import logging

import pytest

from jcodemunch_mcp import config as config_module
from jcodemunch_mcp.config import (
    CONFIG_TYPES,
    DEFAULTS,
    ENV_VAR_MAPPING,
    _parse_env_value,
)

_ENV_FOR = {key: env for env, key in ENV_VAR_MAPPING.items()}
# Listed here, not read from the module, so the behavioural tests below run (and
# fail on their assertions) against code that predates the set.
_PROTECTIVE = sorted({
    "respect_cachedir_tag",
    "skip_msbuild_output",
    "skip_nuget_packages",
    "skip_virtualenvs",
    "trusted_folders_whitelist_mode",
    "runtime_redact_enabled",
})
_TYPOS = ["ture", "flase", "enabled", "disabled", "2", "on-ish", " "]


@pytest.fixture
def restore(monkeypatch):
    """Put every touched key back to its default after the env fallback runs."""
    touched: list[str] = []
    yield touched
    for key in touched:
        config_module._GLOBAL_CONFIG[key] = DEFAULTS[key]  # type: ignore[attr-defined]


def _apply(monkeypatch, restore, key: str, value: str):
    monkeypatch.setenv(_ENV_FOR[key], value)
    config_module._GLOBAL_CONFIG[key] = DEFAULTS[key]  # type: ignore[attr-defined]
    restore.append(key)
    config_module._apply_env_var_fallback(set())
    return config_module._GLOBAL_CONFIG[key]  # type: ignore[attr-defined]


def test_the_module_names_exactly_the_protective_set():
    assert set(config_module.BOOL_ENV_TYPO_KEEPS_DEFAULT) == set(_PROTECTIVE)


@pytest.mark.parametrize("key", _PROTECTIVE)
def test_every_protective_key_is_a_default_on_boolean_with_an_env_var(key):
    """The set cannot drift from the config tables it describes."""
    assert CONFIG_TYPES[key] is bool
    assert DEFAULTS[key] is True
    assert key in _ENV_FOR


@pytest.mark.parametrize("key", _PROTECTIVE)
@pytest.mark.parametrize("typo", _TYPOS)
def test_a_typo_keeps_a_protective_switch_on(monkeypatch, restore, key, typo):
    assert _apply(monkeypatch, restore, key, typo) is True


@pytest.mark.parametrize("key", _PROTECTIVE)
@pytest.mark.parametrize("value", ["false", "0", "no", "off", "FALSE", " Off "])
def test_an_explicit_false_still_disables_a_protective_switch(monkeypatch, restore, key, value):
    assert _apply(monkeypatch, restore, key, value) is False


def test_a_typo_in_the_savings_opt_out_still_reads_as_off(monkeypatch, restore):
    """The load-bearing guard: the opt-out keeps failing toward NOT sharing."""
    assert "share_savings" not in getattr(config_module, "BOOL_ENV_TYPO_KEEPS_DEFAULT", ())
    for typo in _TYPOS:
        assert _apply(monkeypatch, restore, "share_savings", typo) is False


@pytest.mark.parametrize("value", ["true", "1", "yes", "on", "TRUE", " On "])
def test_recognised_true_spellings_read_true_for_a_protective_key(value):
    assert _parse_env_value(value, bool, key="skip_virtualenvs") is True


@pytest.mark.parametrize("value", ["false", "0", "no", "off", "False", " OFF "])
def test_recognised_false_spellings_read_false_for_a_protective_key(value):
    assert _parse_env_value(value, bool, key="skip_virtualenvs") is False


_OTHER_BOOL_KEYS = sorted(
    k for k in _ENV_FOR if CONFIG_TYPES.get(k) is bool and k not in set(_PROTECTIVE)
)
_BATTERY = ["1", "1 ", " 1", "true", "true ", " yes", "On\n", "\ttrue", "TRUE", "0", "off", "", " ", "garbage", "enabled"]


@pytest.mark.parametrize("key", _OTHER_BOOL_KEYS)
def test_every_other_boolean_reads_exactly_as_before(key):
    """Zero change outside the protective set (review of L-207). Stripping alone
    would turn `JCODEMUNCH_SHARE_SAVINGS=1 ` (cmd.exe's `set X=1 && cmd`) from off
    to on, and the same for default-off network and billing switches."""
    for value in _BATTERY:
        before = value.lower() in ("true", "1", "yes", "on")
        assert _parse_env_value(value, bool, key=key) is before, (key, value)


def test_the_guard_covers_the_telemetry_and_network_switches():
    """Non-vacuity: the battery above reaches the keys it exists to protect."""
    assert {"share_savings", "perf_telemetry_enabled", "allow_paid_summaries", "runtime_ingest_enabled"} <= set(_OTHER_BOOL_KEYS)


def test_a_typo_is_named_in_a_warning(monkeypatch, restore, caplog):
    with caplog.at_level(logging.WARNING, logger=config_module.logger.name):
        _apply(monkeypatch, restore, "skip_virtualenvs", "ture")
    text = caplog.text
    assert "JCODEMUNCH_SKIP_VIRTUALENVS" in text and "ture" in text
