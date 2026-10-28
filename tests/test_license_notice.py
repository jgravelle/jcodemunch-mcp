"""The license notice for installs that skip `init` (jjg, DECISIONS 2026-10-28, option B).

A `uvx` or plugin install never runs `init`, so it never prints `init`'s
license line, and the person using it is not told that work use needs a
license. The first successful tool answer on a machine now carries a plain
`license_notice` field: the statement and the buying page, never phrased as an
instruction to the model. It is said once per machine (a marker under
`CODE_INDEX_PATH`), and never with a license key configured, after `init` has
run, with `JCODEMUNCH_LICENSE_NOTICE=0`, or on a CI or remote runner
(`CI=true`, `CLAUDE_CODE_REMOTE=true`). The same line goes to stderr once.

⚠ `CLAUDECODE=1` is set by every local Claude Code session, so it must NOT
count as a CI marker: a test below pins that.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from jcodemunch_mcp import license_notice, server

MARKER = license_notice.MARKER_FILE
_ENV_TO_CLEAR = (
    "CI",
    "CLAUDE_CODE_REMOTE",
    "JCODEMUNCH_LICENSE_NOTICE",
    "JCODEMUNCH_LICENSE_KEY",
)


@pytest.fixture()
def install(tmp_path, monkeypatch):
    """An unlicensed install outside CI, with its own store and no notice said yet."""
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setenv("CODE_INDEX_PATH", str(store))
    for var in _ENV_TO_CLEAR:
        monkeypatch.delenv(var, raising=False)
    from jcodemunch_mcp import config

    real_get = config.get
    monkeypatch.setattr(
        config,
        "get",
        lambda key, *a, **k: "" if key == "license_key" else real_get(key, *a, **k),
    )
    monkeypatch.setitem(license_notice.NOTICE_STATE, "checked", False)
    work = tmp_path / "work"
    work.mkdir()
    return store, work


def _call(tool: str, args: dict) -> dict:
    out = asyncio.run(server.call_tool(tool, args))
    content = out.content if hasattr(out, "content") else out
    return json.loads(content[0].text)


def _new_process(monkeypatch):
    monkeypatch.setitem(license_notice.NOTICE_STATE, "checked", False)


def test_the_first_answer_on_a_machine_carries_the_notice(install):
    store, work = install
    answer = _call("resolve_repo", {"path": str(work)})
    assert answer.get("license_notice") == license_notice.NOTICE_TEXT
    assert (store / MARKER).is_file()


def test_the_notice_is_in_the_body_not_in_meta(install):
    """`meta_fields: []` is the default and strips `_meta`, so a notice there reaches nobody."""
    store, work = install
    answer = _call("resolve_repo", {"path": str(work)})
    assert "license_notice" in answer
    assert "license_notice" not in (answer.get("_meta") or {})


def test_the_answer_is_json_even_when_compact_output_is_asked_for(install):
    """A compact encoding drops a key it does not declare; the marker would be spent on nothing."""
    store, work = install
    out = asyncio.run(
        server.call_tool("resolve_repo", {"path": str(work), "format": "compact"})
    )
    assert json.loads(out[0].text).get("license_notice") == license_notice.NOTICE_TEXT


def test_it_is_said_once_per_machine(install, monkeypatch):
    store, work = install
    assert "license_notice" in _call("resolve_repo", {"path": str(work)})
    assert "license_notice" not in _call("resolve_repo", {"path": str(work)})
    _new_process(monkeypatch)
    assert "license_notice" not in _call("resolve_repo", {"path": str(work)})


def test_the_marker_lives_under_code_index_path(install):
    store, _work = install
    assert license_notice.marker_path() == Path(store) / MARKER


def test_an_error_answer_does_not_use_it_up(install):
    store, work = install
    assert license_notice.notice_for("resolve_repo", {"error": "nope"}) is None
    assert not (store / MARKER).exists()
    assert "license_notice" in _call("resolve_repo", {"path": str(work)})


@pytest.mark.parametrize("result", [["a", "b"], "text", None])
def test_an_answer_that_cannot_carry_it_does_not_use_it_up(install, result):
    store, _work = install
    assert license_notice.notice_for("resolve_repo", result) is None
    assert not (store / MARKER).exists()
    assert (
        license_notice.notice_for("resolve_repo", {"ok": True})
        == license_notice.NOTICE_TEXT
    )


def test_any_tool_answer_can_carry_it(install):
    """The ruling says the first tool result, not the first `resolve_repo`."""
    assert (
        license_notice.notice_for("search_symbols", {"results": []})
        == license_notice.NOTICE_TEXT
    )


@pytest.mark.parametrize(
    "env",
    [
        {"JCODEMUNCH_LICENSE_KEY": "jcm-test-key-not-real"},
        {"JCODEMUNCH_LICENSE_NOTICE": "0"},
        {"JCODEMUNCH_LICENSE_NOTICE": "false"},
        {"JCODEMUNCH_LICENSE_NOTICE": "off"},
        {"CI": "true"},
        {"CI": "1"},
        {"CLAUDE_CODE_REMOTE": "true"},
    ],
    ids=lambda e: ",".join(f"{k}={v}" for k, v in e.items()),
)
def test_nothing_is_said_and_no_marker_written_when_skipped(install, monkeypatch, env):
    store, work = install
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert "license_notice" not in _call("resolve_repo", {"path": str(work)})
    assert not (store / MARKER).exists()


def test_a_key_in_the_config_file_suppresses_it(install, monkeypatch):
    from jcodemunch_mcp import config

    real_get = config.get
    monkeypatch.setattr(
        config,
        "get",
        lambda key, *a, **k: "jcm-test-key-not-real"
        if key == "license_key"
        else real_get(key, *a, **k),
    )
    assert license_notice.notice_for("resolve_repo", {"ok": True}) is None


def test_an_init_from_before_the_marker_is_not_taken_as_proof(install):
    """An `init` from before this marker left only `last_init_version.txt`.

    That stamp cannot say the line was printed: `init` printed it only from
    1.108.376 (#1131), and `upgrade` re-runs `init` silently and writes a
    current-version stamp too. So such an install is told once.
    """
    store, work = install
    from jcodemunch_mcp.cli.init import _install_version_path

    assert _install_version_path().parent == store
    _install_version_path().write_text("1.108.401", encoding="utf-8")
    assert license_notice.skip_reason() is None
    assert "license_notice" in _call("resolve_repo", {"path": str(work)})


def test_a_local_claude_code_session_is_not_a_ci_runner(install, monkeypatch):
    """Every local Claude Code session sets CLAUDECODE=1; reading it as CI would hide the notice from everyone."""
    monkeypatch.setenv("CLAUDECODE", "1")
    assert (
        license_notice.notice_for("resolve_repo", {"ok": True})
        == license_notice.NOTICE_TEXT
    )


@pytest.mark.parametrize("value", ["false", "0", ""])
def test_a_ci_variable_that_says_no_is_not_ci(install, monkeypatch, value):
    monkeypatch.setenv("CI", value)
    assert (
        license_notice.notice_for("resolve_repo", {"ok": True})
        == license_notice.NOTICE_TEXT
    )


def test_the_same_line_goes_to_stderr_once(install, capsys):
    license_notice.notice_for("resolve_repo", {"ok": True})
    license_notice.notice_for("resolve_repo", {"ok": True})
    err = capsys.readouterr().err
    assert err.count(license_notice.NOTICE_TEXT) == 1


def test_nothing_reaches_stdout(install, capsys):
    """stdout is the stdio transport's JSON-RPC stream: one stray line breaks the client."""
    license_notice.notice_for("resolve_repo", {"ok": True})
    assert capsys.readouterr().out == ""


def test_an_unwritable_marker_still_says_it_once_in_the_process(install, monkeypatch):
    store, _work = install
    monkeypatch.setattr(
        license_notice, "marker_path", lambda: store / "missing-dir" / "nested" / MARKER
    )
    monkeypatch.setattr(
        Path, "mkdir", lambda *a, **k: (_ for _ in ()).throw(OSError("read-only"))
    )
    assert (
        license_notice.notice_for("resolve_repo", {"ok": True})
        == license_notice.NOTICE_TEXT
    )
    assert license_notice.notice_for("resolve_repo", {"ok": True}) is None


def test_the_text_is_a_statement_not_an_instruction():
    """jjg: never phrased as an instruction to the model."""
    text = license_notice.NOTICE_TEXT
    assert "https://jcodemunch.com/buying.php" in text
    lowered = text.lower()
    for word in (
        "tell the user",
        "you must",
        "relay",
        "ask the user",
        "agent",
        "assistant",
    ):
        assert word not in lowered, word


def test_init_counts_as_having_been_told(install, monkeypatch, capsys):
    """`init` prints its own license line, so a machine that ran it is not told again."""
    from unittest.mock import MagicMock

    from jcodemunch_mcp.cli import init as init_module

    store, _work = install
    for name in (
        "install_claude_md",
        "install_cursor_rules",
        "install_windsurf_rules",
        "install_agents_md",
        "install_hooks",
        "install_enforcement_hooks",
        "install_copilot_hooks",
        "run_index",
    ):
        monkeypatch.setattr(init_module, name, MagicMock(return_value="  (spy)"))
    monkeypatch.setattr(init_module, "run_audit", MagicMock(return_value=iter([])))
    client = MagicMock()
    client.name = "Claude Code"
    client.config_path = None
    monkeypatch.setattr(init_module, "_detect_clients", lambda: [client])
    monkeypatch.setattr(
        init_module, "configure_client", MagicMock(return_value="  (spy)")
    )
    assert (
        init_module.run_init(
            clients=None, hooks=False, index=False, audit=False, yes=True, minimal=True
        )
        == 0
    )
    assert init_module._LICENCE_LINES[0] in capsys.readouterr().out
    assert (store / MARKER).is_file()
    assert license_notice.notice_for("resolve_repo", {"ok": True}) is None


def test_a_dry_run_init_does_not_count(install, monkeypatch):
    from unittest.mock import MagicMock

    from jcodemunch_mcp.cli import init as init_module

    store, _work = install
    monkeypatch.setattr(init_module, "run_audit", MagicMock(return_value=iter([])))
    monkeypatch.setattr(init_module, "_detect_clients", lambda: [])
    init_module.run_init(
        clients=None,
        hooks=False,
        index=False,
        audit=False,
        yes=True,
        minimal=True,
        dry_run=True,
    )
    assert not (store / MARKER).exists()


def _spy_init(monkeypatch):
    from unittest.mock import MagicMock

    from jcodemunch_mcp.cli import init as init_module

    for name in (
        "install_claude_md",
        "install_cursor_rules",
        "install_windsurf_rules",
        "install_agents_md",
        "install_hooks",
        "install_enforcement_hooks",
        "install_copilot_hooks",
        "run_index",
    ):
        monkeypatch.setattr(init_module, name, MagicMock(return_value="  (spy)"))
    monkeypatch.setattr(init_module, "run_audit", MagicMock(return_value=iter([])))
    monkeypatch.setattr(init_module, "_detect_clients", lambda: [])
    return init_module


def test_an_upgrade_refresh_prints_no_line_and_writes_no_marker(install, monkeypatch, capsys):
    """`upgrade` re-runs `init --hooks` silently; nothing was shown, so nothing is marked.

    It also writes a current-version `last_init_version.txt`, which is why that
    stamp is never read as proof the line was printed.
    """
    store, _work = install
    init_module = _spy_init(monkeypatch)
    monkeypatch.setenv(init_module.UPGRADE_REFRESH_ENV, "1")
    init_module.run_init(
        clients=None, hooks=True, hooks_explicit=True, index=False, audit=False,
        yes=True, minimal=True,
    )
    init_module.install_enforcement_hooks.assert_called_once()
    assert init_module._LICENCE_LINES[0] not in capsys.readouterr().out
    assert not (store / MARKER).exists()
    assert license_notice.skip_reason() is None


def test_the_notice_and_its_marker_are_disclosed():
    """SECURITY.md holds the background-behaviour disclosures; the README points to it."""
    root = Path(__file__).resolve().parent.parent
    security = (root / "SECURITY.md").read_text(encoding="utf-8")
    para = next(p for p in security.split("\n- ") if MARKER in p)
    for word in (
        "license_notice",
        "once per machine",
        "JCODEMUNCH_LICENSE_NOTICE",
        "CI=true",
        "CLAUDE_CODE_REMOTE=true",
        "license key",
        "init",
        "Nothing is sent",
    ):
        assert word in para, word
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "license_notice" in readme
