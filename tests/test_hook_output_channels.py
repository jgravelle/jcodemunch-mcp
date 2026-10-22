"""Regression tests ensuring model-facing hook output uses additionalContext.

These assert the delivery channel, not message content: a hook can compute a
perfect nudge and still be inert if it writes to stderr or systemMessage.
"""

import io
import json
import sys
from unittest import mock

import pytest

from jcodemunch_mcp.cli.hooks import run_pretooluse, run_sessionstart, run_subagentstart
from jcodemunch_mcp.cli.hooks._common import _emit_additional_context, _norm_path


def _run(func, stdin_text: str) -> tuple[int, str, str]:
    fake_in, fake_out, fake_err = (
        io.StringIO(stdin_text), io.StringIO(), io.StringIO(),
    )
    with mock.patch.object(sys, "stdin", fake_in), \
         mock.patch.object(sys, "stdout", fake_out), \
         mock.patch.object(sys, "stderr", fake_err):
        rc = func()
    return rc, fake_out.getvalue(), fake_err.getvalue()


def _read_input(path: str) -> str:
    return json.dumps({
        "session_id": "s", "hook_event_name": "PreToolUse",
        "tool_name": "Read", "tool_input": {"file_path": path},
    })


def _grep_input(pattern: str, cwd: str) -> str:
    return json.dumps({
        "session_id": "s", "hook_event_name": "PreToolUse",
        "tool_name": "Grep", "tool_input": {"pattern": pattern}, "cwd": cwd,
    })


@pytest.fixture
def big_py(tmp_path):
    f = tmp_path / "big.py"
    f.write_text("x = 1\n" * 2000)  # comfortably over the 4KB threshold
    return f


class TestEmitAdditionalContext:
    """The shared helper's wire format."""

    def test_emits_additional_context_on_stdout(self):
        rc, out, err = _run(
            lambda: _emit_additional_context("PreToolUse", "hello model"), ""
        )
        assert rc == 0
        assert err == ""
        assert json.loads(out) == {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": "hello model",
            }
        }

    def test_carries_no_permission_decision(self):
        """Advisory context must not double as a deny — the call still proceeds."""
        _, out, _ = _run(lambda: _emit_additional_context("PreToolUse", "x"), "")
        assert "permissionDecision" not in json.loads(out)["hookSpecificOutput"]

    def test_stdout_is_pure_json(self):
        """Claude Code parses stdout as a single JSON object; stray text breaks it."""
        _, out, _ = _run(lambda: _emit_additional_context("PreToolUse", "x"), "")
        json.loads(out)  # would raise on leading/trailing noise


class TestReadNudgeReachesModel:
    @pytest.fixture(autouse=True)
    def _indexed(self, tmp_path, monkeypatch):
        # The nudge only fires for files inside an indexed repo — outside,
        # the recommended tools would error and erode trust in the hints.
        from jcodemunch_mcp.cli.hooks._common import _norm_path
        monkeypatch.setattr(
            "jcodemunch_mcp.cli.hooks.steering._indexed_source_roots",
            lambda: [_norm_path(str(tmp_path))],
        )

    def test_read_nudge_uses_additional_context(self, big_py):
        rc, out, err = _run(run_pretooluse, _read_input(str(big_py)))
        assert rc == 0
        hso = json.loads(out)["hookSpecificOutput"]
        assert hso["hookEventName"] == "PreToolUse"
        assert "get_file_outline" in hso["additionalContext"]

    def test_read_nudge_leaves_stderr_empty(self, big_py):
        """The regression itself: a nudge on stderr is invisible to the model."""
        _, _, err = _run(run_pretooluse, _read_input(str(big_py)))
        assert err == ""

    def test_read_nudge_does_not_block(self, big_py):
        """Advisory must stay advisory — Read-before-Edit depends on it (#241)."""
        rc, out, _ = _run(run_pretooluse, _read_input(str(big_py)))
        assert rc == 0  # exit 2 would block AND is the only stderr route to model
        assert "permissionDecision" not in json.loads(out)["hookSpecificOutput"]


class TestGrepNudgeReachesModel:
    @pytest.fixture(autouse=True)
    def _indexed(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "jcodemunch_mcp.cli.hooks.steering._indexed_source_roots",
            lambda: [_norm_path(str(tmp_path))],
        )

    def test_grep_nudge_uses_additional_context(self, tmp_path):
        rc, out, err = _run(run_pretooluse, _grep_input("foo", str(tmp_path)))
        assert rc == 0
        assert err == ""
        hso = json.loads(out)["hookSpecificOutput"]
        assert "search_text" in hso["additionalContext"]
        assert "permissionDecision" not in hso

    def test_silent_paths_stay_fully_silent(self, tmp_path, monkeypatch):
        """Gating is unchanged: outside an indexed repo, no channel is used."""
        monkeypatch.setattr(
            "jcodemunch_mcp.cli.hooks.steering._indexed_source_roots", lambda: []
        )
        rc, out, err = _run(run_pretooluse, _grep_input("foo", str(tmp_path)))
        assert (rc, out, err) == (0, "", "")


class TestStrictModeUnaffected:
    """Strict mode already used a model-facing channel; it must keep working."""

    def test_strict_read_still_denies(self, big_py, monkeypatch, tmp_path):
        monkeypatch.setenv("JCODEMUNCH_ENFORCE", "strict")
        monkeypatch.setattr(
            "jcodemunch_mcp.cli.hooks.steering._indexed_source_roots",
            lambda: [_norm_path(str(tmp_path))],
        )
        rc, out, _ = _run(run_pretooluse, _read_input(str(big_py)))
        assert rc == 0
        hso = json.loads(out)["hookSpecificOutput"]
        assert hso["permissionDecision"] == "deny"
        assert "additionalContext" not in hso  # deny carries its own reason


class TestSubagentBriefingReachesSubagent:
    def test_briefing_uses_additional_context(self, monkeypatch):
        from jcodemunch_mcp.storage import CodeIndex

        idx = mock.MagicMock(spec=CodeIndex)
        idx.symbols = [{
            "id": "a", "name": "main", "kind": "function",
            "file": "main.py", "line": 1, "language": "python",
        }]
        idx.source_files = ["main.py"]
        idx.imports = {}
        idx.alias_map = None
        monkeypatch.setattr("jcodemunch_mcp.storage.IndexStore", type(
            "MockStore", (), {
                "__init__": lambda self, **kw: None,
                # Real list_repos() shape: entries keyed "repo", no owner/name.
                "list_repos": lambda self: [
                    {"repo": "test/repo", "source_root": "/tmp/x"}
                ],
                "load_index": lambda self, o, n: idx,
            },
        ))

        rc, out, _ = _run(run_subagentstart, '{"hook_event_name": "SubagentStart"}')
        assert rc == 0
        # Unconditional: the mock store guarantees a repo, so silence here means
        # the patch stopped taking effect, not an unindexed environment. This also
        # pins the coupling — run_subagentstart does `from ..storage import
        # IndexStore` at call time, so patching the module attribute is what
        # works; rebinding it to a from-import at module scope would break this.
        assert out, "mocked store should have produced a briefing"
        payload = json.loads(out)
        assert "systemMessage" not in payload, (
            "a briefing the subagent cannot read is pointless"
        )
        hso = payload["hookSpecificOutput"]
        assert hso["hookEventName"] == "SubagentStart"
        assert "search_symbols" in hso["additionalContext"]


class TestSessionStartRestoresSnapshot:
    """SessionStart restores snapshots for continuing sessions."""

    @pytest.fixture
    def _snapshot(self, monkeypatch):
        monkeypatch.setattr(
            "jcodemunch_mcp.cli.hooks.snapshot._build_session_snapshot",
            lambda: "## Session Snapshot (jCodemunch)\n- src/auth.py (9 reads)",
        )

    def _start(self, source: str) -> tuple[int, str, str]:
        return _run(run_sessionstart, json.dumps({
            "hook_event_name": "SessionStart", "source": source,
        }))

    @pytest.mark.parametrize("source,label", [
        ("compact", "restored after compaction"),
        ("resume", "restored on resume"),
        ("fork", "carried into this fork"),
    ])
    def test_injects_on_continuing_sources(self, source, label, _snapshot):
        """A journal from a continuing session still describes this session."""
        rc, out, err = self._start(source)
        assert rc == 0
        assert err == ""
        hso = json.loads(out)["hookSpecificOutput"]
        assert hso["hookEventName"] == "SessionStart"
        assert label in hso["additionalContext"]
        assert "src/auth.py" in hso["additionalContext"]

    @pytest.mark.parametrize("source", ["startup", "clear", "", "unknown"])
    def test_silent_on_fresh_sessions(self, source, _snapshot):
        """Restoring an unrelated session's state would misdirect the model."""
        assert self._start(source) == (0, "", "")

    def test_silent_when_no_journal(self, monkeypatch):
        """No journal renders as an empty snapshot — nothing to inject."""
        monkeypatch.setattr(
            "jcodemunch_mcp.cli.hooks.snapshot._build_session_snapshot", lambda: ""
        )
        assert self._start("compact") == (0, "", "")

    def test_silent_on_blank_snapshot(self, monkeypatch):
        monkeypatch.setattr(
            "jcodemunch_mcp.cli.hooks.snapshot._build_session_snapshot",
            lambda: "   \n  ",
        )
        assert self._start("compact") == (0, "", "")

    def test_never_blocks_startup_on_error(self, monkeypatch):
        """A snapshot failure must not take the session down with it."""
        def boom():
            raise RuntimeError("journal unreadable")
        monkeypatch.setattr(
            "jcodemunch_mcp.cli.hooks.snapshot._build_session_snapshot", boom
        )
        assert self._start("compact") == (0, "", "")

    def test_invalid_json_is_tolerated(self):
        assert _run(run_sessionstart, "not json") == (0, "", "")

    def test_precompact_is_silent_and_sessionstart_delivers(self, monkeypatch):
        """PreCompact has no exit-0 output channel — Claude Code discards its
        ``systemMessage`` (it used to emit the snapshot into that discarded
        field). SessionStart(compact) is the delivery route for the snapshot."""
        from jcodemunch_mcp.cli.hooks import run_precompact

        body = "## Session Snapshot (jCodemunch)\n- a.py"
        monkeypatch.setattr(
            "jcodemunch_mcp.cli.hooks.snapshot._build_session_snapshot", lambda: body
        )
        assert _run(run_precompact, '{"hook_event_name": "PreCompact"}') == (0, "", "")
        _, ss_out, _ = _run(run_sessionstart, json.dumps(
            {"hook_event_name": "SessionStart", "source": "compact"}
        ))
        assert body in json.loads(ss_out)["hookSpecificOutput"]["additionalContext"]


class TestSessionStartRegistersTranscriptRoot:
    """SessionStart is the earliest hook on a resumed session, so it is the
    earliest point a custom-profile transcript root can be learned (#421)."""

    @pytest.fixture
    def _seen(self, monkeypatch):
        seen: list = []
        monkeypatch.setattr(
            "jcodemunch_mcp.cli.hooks.snapshot._note_transcript_root", seen.append
        )
        return seen

    def _start(self, source: str) -> None:
        _run(run_sessionstart, json.dumps({
            "hook_event_name": "SessionStart",
            "source": source,
            "transcript_path": "/tmp/p/projects/repo/abc.jsonl",
        }))

    @pytest.mark.parametrize("source", ["compact", "resume", "fork"])
    def test_registers_on_continuing_sources(self, source, _seen):
        self._start(source)
        assert [d["transcript_path"] for d in _seen] == [
            "/tmp/p/projects/repo/abc.jsonl"
        ]

    @pytest.mark.parametrize("source", ["startup", "clear"])
    def test_registers_even_when_no_snapshot_is_injected(self, source, _seen):
        """The root is a property of the session, not of whether we inject.

        A fresh session must stay silent on every output channel, but its
        transcripts still belong to a profile `receipt` needs to know about —
        so registration happens BEFORE the source gate.
        """
        self._start(source)
        assert len(_seen) == 1

    def test_invalid_json_registers_nothing(self, _seen):
        _run(run_sessionstart, "not json")
        assert _seen == []


class TestSessionStartIsRegistered:
    """A correct handler nobody invokes is still a no-op — assert `init` wires it."""

    def _install(self, tmp_path, initial="{}"):
        from jcodemunch_mcp.cli.init import install_enforcement_hooks

        settings = tmp_path / "settings.json"
        settings.write_text(initial, encoding="utf-8")
        with mock.patch(
            "jcodemunch_mcp.cli.init._settings_json_path", return_value=settings
        ):
            install_enforcement_hooks(dry_run=False, backup=False, guidance=True)
        return json.loads(settings.read_text(encoding="utf-8"))["hooks"]

    def test_sessionstart_hook_installed(self, tmp_path):
        hooks = self._install(tmp_path)
        assert "SessionStart" in hooks
        cmd = hooks["SessionStart"][0]["hooks"][0]["command"]
        assert cmd.endswith("hook-sessionstart")

    def test_matcher_excludes_fresh_sessions(self, tmp_path):
        """startup/clear must not match — restoring unrelated state misleads."""
        hooks = self._install(tmp_path)
        matcher = hooks["SessionStart"][0]["matcher"]
        assert matcher == "compact|resume|fork"
        assert "startup" not in matcher
        assert "clear" not in matcher

    def test_added_to_existing_install_on_rerun(self, tmp_path):
        """An install with other hooks still gains SessionStart when guidance is asked for.

        Duplicate detection is per-subcommand, so an existing PreToolUse entry
        must not cause the whole merge to skip the new event. (Since the light
        default install, a re-run without `--guidance` adds no guidance hook;
        tests/test_light_default_install.py pins that half.)
        """
        existing = json.dumps({"hooks": {
            "PreToolUse": [{
                "matcher": "Read|Grep",
                "hooks": [{"type": "command",
                           "command": "jcodemunch-mcp hook-pretooluse"}],
            }],
        }})
        hooks = self._install(tmp_path, existing)
        assert "SessionStart" in hooks
        assert len(hooks["PreToolUse"]) == 1  # not duplicated

    def test_idempotent(self, tmp_path):
        from jcodemunch_mcp.cli.init import install_enforcement_hooks

        settings = tmp_path / "settings.json"
        settings.write_text("{}", encoding="utf-8")
        with mock.patch(
            "jcodemunch_mcp.cli.init._settings_json_path", return_value=settings
        ):
            install_enforcement_hooks(dry_run=False, backup=False, guidance=True)
            install_enforcement_hooks(dry_run=False, backup=False, guidance=True)
        hooks = json.loads(settings.read_text(encoding="utf-8"))["hooks"]
        assert len(hooks["SessionStart"]) == 1
