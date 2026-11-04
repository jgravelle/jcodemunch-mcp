"""The work-license reminder (J, DECISIONS item 650: CLI plus Option T, 7/30).

A license holder is never reminded (a key read locally, never sent); one command
silences it for good and every reminder names that command; it waits 7 days
after the machine's first index and then at most 30 days between reminders; it
is quiet on CI and in non-interactive runs.
"""

from __future__ import annotations

import io
import json
import socket
from datetime import datetime, timedelta, timezone

import pytest

from jcodemunch_mcp import license_reminder as lr

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.fixture
def store(tmp_path, monkeypatch):
    """A fresh store with one index, no key, no switches, no CI markers."""
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path))
    for name in ("CI", "CLAUDE_CODE_REMOTE", lr.ENV_VAR, "JCODEMUNCH_LICENSE_NOTICE",
                 "JCODEMUNCH_LICENSE_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(lr, "_licence_key_configured", lambda: False)
    monkeypatch.setattr(lr, "_config_silenced", lambda: False)
    (tmp_path / "owner-repo.db").write_bytes(b"")
    return tmp_path


def _seed(store, first_days_ago=None, last_days_ago=None, count=0):
    state = {}
    if first_days_ago is not None:
        state["first_index_at"] = (NOW - timedelta(days=first_days_ago)).isoformat()
    if last_days_ago is not None:
        state["last_shown"] = (NOW - timedelta(days=last_days_ago)).isoformat()
        state["count"] = count
    (store / lr.STATE_FILE).write_text(json.dumps(state), encoding="utf-8")


def _state(store):
    return json.loads((store / lr.STATE_FILE).read_text(encoding="utf-8"))


# --- the text -----------------------------------------------------------------

def test_every_text_names_the_silencing_command_and_the_buy_page():
    for text in (lr.CLI_TEXT, lr.TOOL_TEXT):
        assert lr.SILENCE_COMMAND in text
        assert "https://jcodemunch.com/buying.php" in text
        assert "Personal use stays free" in text


def _query(text):
    from urllib.parse import parse_qs, urlsplit

    url = next(w for w in text.split() if w.startswith("https://jcodemunch.com/buying.php"))
    return parse_qs(urlsplit(url.rstrip(".")).query)


def test_each_copy_of_the_buy_link_names_where_it_was_shown():
    """A sale that came through the reminder can be told apart from other sales.

    The terminal copy and the tool-answer copy carry different `utm_medium` values.
    The whole query is pinned, so no install id, version or count can join it: the
    link is the same on every machine."""
    base = "https://jcodemunch.com/buying.php?utm_source=cli&utm_campaign=license-reminder"
    assert lr.CLI_BUY_URL == base.replace("cli&", "cli&utm_medium=reminder&")
    assert lr.TOOL_BUY_URL == base.replace("cli&", "cli&utm_medium=tool&")
    # No punctuation follows a link into its campaign value.
    assert lr.CLI_BUY_URL + " " in lr.CLI_TEXT and lr.TOOL_TEXT.endswith(lr.TOOL_BUY_URL)
    cli, tool = _query(lr.CLI_TEXT), _query(lr.TOOL_TEXT)
    assert cli == {"utm_source": ["cli"], "utm_medium": ["reminder"], "utm_campaign": ["license-reminder"]}
    assert tool == {"utm_source": ["cli"], "utm_medium": ["tool"], "utm_campaign": ["license-reminder"]}


def test_the_tool_text_is_a_statement_not_an_instruction_to_the_model():
    lowered = lr.TOOL_TEXT.lower()
    for relay in ("tell the user", "let the user know", "inform the user", "mention this",
                  "you should", "please run", "run this"):
        assert relay not in lowered
    assert "a person can turn" in lowered


def test_the_silencing_command_is_a_real_config_set_of_a_real_key():
    from jcodemunch_mcp import config

    assert lr.SILENCE_COMMAND == "jcodemunch-mcp config set license_reminder false"
    assert config.DEFAULTS[lr.CONFIG_KEY] is True
    assert config.CONFIG_TYPES[lr.CONFIG_KEY] is bool


# --- the cadence --------------------------------------------------------------

def test_the_first_sighting_of_an_index_starts_the_clock_and_says_nothing(store):
    assert lr.skip_reason(now=NOW) == "too_early"
    assert _state(store)["first_index_at"] == NOW.isoformat()


def test_no_index_means_no_reminder_and_no_clock(store):
    (store / "owner-repo.db").unlink()
    (store / "telemetry.db").write_bytes(b"")
    (store / "org_savings.db").write_bytes(b"")
    assert lr.skip_reason(now=NOW) == "no_index"
    assert not (store / lr.STATE_FILE).exists()


def test_quiet_before_day_7_then_due(store):
    _seed(store, first_days_ago=6)
    assert lr.skip_reason(now=NOW) == "too_early"
    _seed(store, first_days_ago=7)
    assert lr.skip_reason(now=NOW) is None


def test_quiet_for_30_days_after_a_reminder_then_due_again(store):
    _seed(store, first_days_ago=90, last_days_ago=29, count=2)
    assert lr.skip_reason(now=NOW) == "recent"
    _seed(store, first_days_ago=90, last_days_ago=30, count=2)
    assert lr.skip_reason(now=NOW) is None


def test_marking_records_only_a_time_and_a_count(store):
    _seed(store, first_days_ago=10)
    lr.mark_shown(now=NOW)
    state = _state(store)
    assert set(state) == {"first_index_at", "last_shown", "count"}
    assert state["count"] == 1 and state["last_shown"] == NOW.isoformat()


# --- never pester a license holder or a silenced machine -----------------------

def test_a_license_key_silences_it_and_is_never_sent(store, monkeypatch):
    _seed(store, first_days_ago=60)
    monkeypatch.setattr(lr, "_licence_key_configured", lambda: True)

    def _no_network(*a, **k):
        raise AssertionError("the reminder made a network call")

    monkeypatch.setattr(socket, "create_connection", _no_network)
    monkeypatch.setattr(socket.socket, "connect", _no_network)
    assert lr.skip_reason(now=NOW) == "licensed"


def test_the_real_key_check_reads_config_and_env_locally(tmp_path, monkeypatch):
    """No patched key check here: the real one, fed by the env var, read locally."""
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path))
    monkeypatch.setattr(lr, "_config_silenced", lambda: False)
    for name in ("CI", "CLAUDE_CODE_REMOTE", lr.ENV_VAR, "JCODEMUNCH_LICENSE_NOTICE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("JCODEMUNCH_LICENSE_KEY", "JCM-TEST-KEY")
    (tmp_path / "owner-repo.db").write_bytes(b"")
    _seed(tmp_path, first_days_ago=60)
    assert lr.skip_reason(now=NOW) == "licensed"


def test_config_set_false_silences_it_for_good(store, monkeypatch):
    _seed(store, first_days_ago=60)
    monkeypatch.setattr(lr, "_config_silenced", lambda: True)
    assert lr.skip_reason(now=NOW) == "silenced"


def test_the_config_key_is_read_from_global_config(monkeypatch):
    """The real `_config_silenced`, over `config.get` with no repo: global config only."""
    from jcodemunch_mcp import config

    seen = []
    monkeypatch.setattr(config, "get", lambda key, default=None, repo=None:
                        seen.append(repo) or (False if key == lr.CONFIG_KEY else default))
    assert lr._config_silenced() is True
    assert seen == [None]
    monkeypatch.setattr(config, "get", lambda key, default=None, repo=None: default)
    assert lr._config_silenced() is False


@pytest.mark.parametrize("name", [lr.ENV_VAR, "JCODEMUNCH_LICENSE_NOTICE"])
@pytest.mark.parametrize("value", ["0", "false", "no", "off", "OFF"])
def test_either_env_switch_silences_it(store, monkeypatch, name, value):
    _seed(store, first_days_ago=60)
    monkeypatch.setenv(name, value)
    assert lr.skip_reason(now=NOW) == "disabled"


@pytest.mark.parametrize("name", ["CI", "CLAUDE_CODE_REMOTE"])
def test_quiet_on_ci_and_remote_runners(store, monkeypatch, name):
    _seed(store, first_days_ago=60)
    monkeypatch.setenv(name, "true")
    assert lr.skip_reason(now=NOW) == "ci"


def test_claudecode_is_not_a_ci_marker(store, monkeypatch):
    _seed(store, first_days_ago=60)
    monkeypatch.setenv("CLAUDECODE", "1")
    assert lr.skip_reason(now=NOW) is None


# --- the CLI surface --------------------------------------------------------------

def test_cli_says_it_once_on_stderr_after_a_listed_command(store):
    _seed(store, first_days_ago=60)
    out, err = _Tty(), _Tty()
    assert lr.cli_reminder(["receipt"], stdout=out, stderr=err, now=NOW) == lr.CLI_TEXT
    assert out.getvalue() == ""
    assert err.getvalue().strip() == lr.CLI_TEXT
    assert lr.cli_reminder(["receipt"], stdout=out, stderr=err, now=NOW) is None


@pytest.mark.parametrize("argv", [
    ["serve"], ["hook-pretooluse"], ["watch-all"], ["index-file", "x.py"], ["init"],
    ["config", "set", "license_reminder", "false"], ["license"], [],
    ["receipt", "--json"], ["health", "--json"],
])
def test_cli_never_speaks_after_an_unlisted_command_or_json(store, argv):
    _seed(store, first_days_ago=60)
    err = _Tty()
    assert lr.cli_reminder(argv, stdout=_Tty(), stderr=err, now=NOW) is None
    assert err.getvalue() == ""


@pytest.mark.parametrize("which", ["stdout", "stderr"])
def test_cli_is_quiet_when_either_stream_is_not_a_terminal(store, which):
    _seed(store, first_days_ago=60)
    streams = {"stdout": _Tty(), "stderr": _Tty()}
    streams[which] = io.StringIO()
    assert lr.cli_reminder(["digest"], now=NOW, **streams) is None


def test_cli_reminds_only_after_a_clean_exit(store, monkeypatch):
    from jcodemunch_mcp import server

    calls = []
    monkeypatch.setattr(server, "_cli_license_reminder", lambda argv: calls.append(argv))
    monkeypatch.setattr(server, "main", lambda argv=None: 0)
    server.cli(["receipt"])
    assert calls == [["receipt"]]

    def _fails(argv=None):
        raise SystemExit(1)

    monkeypatch.setattr(server, "main", _fails)
    with pytest.raises(SystemExit):
        server.cli(["receipt"])
    assert calls == [["receipt"]]

    def _boom(argv=None):
        raise RuntimeError("x")

    monkeypatch.setattr(server, "main", _boom)
    with pytest.raises(RuntimeError):
        server.cli(["receipt"])
    assert calls == [["receipt"]]


def test_the_console_script_and_python_m_both_go_through_cli():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    assert 'jcodemunch-mcp = "jcodemunch_mcp.server:cli"' in (root / "pyproject.toml").read_text(encoding="utf-8")
    main_py = (root / "src" / "jcodemunch_mcp" / "__main__.py").read_text(encoding="utf-8")
    assert "cli()" in main_py and "main()" not in main_py


# --- Option T: the tool answer ----------------------------------------------------------

@pytest.fixture
def fresh_process(monkeypatch):
    monkeypatch.setitem(lr.REMINDER_STATE, "checked", False)


def test_tool_answer_carries_it_once_per_process_when_due(store, fresh_process):
    _seed(store, first_days_ago=60)
    assert lr.tool_reminder_for({"ok": 1}, now=NOW) == lr.TOOL_TEXT
    assert lr.tool_reminder_for({"ok": 1}, now=NOW) is None
    assert _state(store)["count"] == 1


def test_tool_answer_is_untouched_on_routine_calls(store, fresh_process):
    _seed(store, first_days_ago=60, last_days_ago=3, count=1)
    assert lr.tool_reminder_for({"ok": 1}, now=NOW) is None
    assert lr.tool_reminder_for({"ok": 1}, now=NOW) is None


def test_an_error_answer_never_carries_it_and_does_not_use_up_the_check(store, fresh_process):
    _seed(store, first_days_ago=60)
    assert lr.tool_reminder_for({"error": "x"}, now=NOW) is None
    assert lr.tool_reminder_for("not a dict", now=NOW) is None
    assert lr.tool_reminder_for({"ok": 1}, now=NOW) == lr.TOOL_TEXT


def test_the_dispatcher_puts_it_in_the_license_notice_field(store, fresh_process, monkeypatch):
    import asyncio

    from jcodemunch_mcp import server

    _seed(store, first_days_ago=60)
    monkeypatch.setattr(lr, "_now", lambda: NOW)
    def _call():
        out = asyncio.run(server.call_tool("list_repos", {}))
        content = out.content if hasattr(out, "content") else out
        return json.loads(content[0].text)

    assert _call().get("license_notice") == lr.TOOL_TEXT
    assert "license_notice" not in _call()


def test_config_set_false_also_silences_the_one_time_notice(tmp_path, monkeypatch):
    """End to end: config.get answers false, nothing in between is patched."""
    from jcodemunch_mcp import config, license_notice

    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path))
    # The key check reads this env var before config, so a host that sets it
    # would answer "licensed" below whatever config says.
    monkeypatch.delenv("JCODEMUNCH_LICENSE_KEY", raising=False)
    monkeypatch.setattr(config, "get", lambda key, default=None, repo=None:
                        False if key == lr.CONFIG_KEY else default)
    assert license_notice.skip_reason(env={}) == "silenced"
    monkeypatch.setattr(config, "get", lambda key, default=None, repo=None:
                        "" if key == "license_key" else default)
    assert license_notice.skip_reason(env={}) is None


def test_the_reminder_is_disclosed_with_every_switch():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    security = (root / "SECURITY.md").read_text(encoding="utf-8")
    para = next(p for p in security.split("\n- ") if lr.STATE_FILE in p)
    for word in ("license_reminder", lr.SILENCE_COMMAND, lr.ENV_VAR, "JCODEMUNCH_LICENSE_NOTICE",
                 "CI=true", "license key", "7 days", "30 days", "Nothing is sent"):
        assert word in para, word
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert lr.SILENCE_COMMAND in readme
    assert lr.ENV_VAR in (root / "CLI-AND-ENV.md").read_text(encoding="utf-8")
    assert "license_reminder" in (root / "CONFIGURATION.md").read_text(encoding="utf-8")


# --- the site's terms, and the line at the end of `receipt` -----------------

SITE_TERMS = "Work use at a for-profit company, or for client or paid work, needs a license"


def test_both_reminder_copies_state_the_site_terms():
    for text in (lr.CLI_TEXT, lr.TOOL_TEXT):
        assert SITE_TERMS in text
        assert "for work needs a license" not in text


def test_the_security_disclosure_quotes_the_reminder_as_it_reads():
    from pathlib import Path

    security = (Path(__file__).resolve().parent.parent / "SECURITY.md").read_text(encoding="utf-8")
    assert f"Personal use stays free. {lr.WORK_TERMS}: {lr.CLI_BUY_URL}" in security
    assert lr.RECEIPT_TEXT in security


def test_the_receipt_line_names_where_it_was_shown():
    from urllib.parse import parse_qs, urlsplit

    assert lr.RECEIPT_TEXT.endswith(lr.RECEIPT_BUY_URL)
    assert parse_qs(urlsplit(lr.RECEIPT_BUY_URL).query) == {
        "utm_source": ["cli"],
        "utm_medium": ["receipt"],
        "utm_campaign": ["savings-receipt"],
    }


def test_the_receipt_line_is_said_on_every_receipt_without_a_key(store):
    """No clock: the line does not wait 7 days and writes no state."""
    assert lr.receipt_line(env={}) == lr.RECEIPT_TEXT
    assert lr.receipt_line(env={}) == lr.RECEIPT_TEXT
    assert not (store / lr.STATE_FILE).exists()


@pytest.mark.parametrize(
    "env",
    [
        {"JCODEMUNCH_LICENSE_REMINDER": "0"},
        {"JCODEMUNCH_LICENSE_NOTICE": "false"},
        {"CI": "true"},
        {"CLAUDE_CODE_REMOTE": "1"},
    ],
)
def test_the_receipt_line_obeys_the_reminders_switches(store, env):
    assert lr.receipt_line(env=env) is None


@pytest.mark.parametrize("reason", ["disabled", "ci", "silenced", "licensed", "a-switch-added-later"])
def test_every_switch_that_silences_the_reminder_silences_the_receipt_line(store, monkeypatch, reason):
    """Both read `switch_reason`, so a switch added there reaches both."""
    monkeypatch.setattr(lr, "switch_reason", lambda env=None: reason)
    assert lr.skip_reason(env={}) == reason
    assert lr.receipt_line(env={}) is None


def test_the_receipt_line_is_silenced_by_config(store, monkeypatch):
    monkeypatch.setattr(lr, "_config_silenced", lambda: True)
    assert lr.receipt_line(env={}) is None


def test_a_license_holder_never_sees_the_receipt_line(store, monkeypatch):
    monkeypatch.setattr(lr, "_licence_key_configured", lambda: True)
    assert lr.receipt_line(env={}) is None


def test_a_failing_check_says_nothing(store, monkeypatch):
    def boom():
        raise RuntimeError("config unreadable")

    monkeypatch.setattr(lr, "_config_silenced", boom)
    assert lr.receipt_line(env={}) is None


def test_receipt_ends_with_the_line_and_export_does_not_carry_it(store, tmp_path, monkeypatch, capsys):
    from jcodemunch_mcp.cli import receipt

    monkeypatch.setattr(receipt, "iter_calls_multi", lambda *a, **k: iter(()))
    monkeypatch.setattr(receipt, "lifetime_meter", lambda *a, **k: None)
    assert receipt.main(["--projects-root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert out.rstrip().endswith(lr.RECEIPT_TEXT)

    target = tmp_path / "r.json"
    assert receipt.main(["--projects-root", str(tmp_path), "--export", str(target)]) == 0
    assert lr.RECEIPT_TEXT not in capsys.readouterr().out
    assert lr.RECEIPT_TEXT not in target.read_text(encoding="utf-8")

    monkeypatch.setenv("JCODEMUNCH_LICENSE_REMINDER", "0")
    assert receipt.main(["--projects-root", str(tmp_path)]) == 0
    assert lr.RECEIPT_TEXT not in capsys.readouterr().out
