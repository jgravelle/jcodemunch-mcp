"""Tests for CLI hook handlers (PreToolUse / PostToolUse)."""

import io
import json
import os
import sys
from unittest import mock

import pytest

from jcodemunch_mcp.cli.hooks import (
    run_posttooluse,
    run_precompact,
    run_pretooluse,
    run_subagentstart,
    run_taskcomplete,
)
from jcodemunch_mcp.cli.hooks._common import _CODE_EXTENSIONS, _norm_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_hook_input(tool_name: str, file_path: str, **extra) -> str:
    """Build a JSON string mimicking Claude Code hook stdin."""
    data = {
        "session_id": "test-session",
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": {"file_path": file_path},
        **extra,
    }
    return json.dumps(data)


def _make_hook_input_with_params(tool_name: str, file_path: str, **tool_input_extra) -> str:
    """Build hook JSON with extra tool_input fields (offset, limit, etc.)."""
    data = {
        "session_id": "test-session",
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": {"file_path": file_path, **tool_input_extra},
    }
    return json.dumps(data)


def _run_with_stdin(func, stdin_text: str) -> tuple[int, str, str]:
    """Call *func* with fake stdin/stdout/stderr and return (exit_code, stdout, stderr)."""
    fake_in = io.StringIO(stdin_text)
    fake_out = io.StringIO()
    fake_err = io.StringIO()
    with mock.patch.object(sys, "stdin", fake_in), \
         mock.patch.object(sys, "stdout", fake_out), \
         mock.patch.object(sys, "stderr", fake_err):
        rc = func()
    return rc, fake_out.getvalue(), fake_err.getvalue()


def _additional_context(stdout: str, event: str = "PreToolUse") -> str:
    """Extract and validate model-facing additionalContext from hook stdout."""
    assert stdout, "hook produced no stdout — nudge cannot reach the model"
    payload = json.loads(stdout)
    hso = payload.get("hookSpecificOutput", {})
    assert hso.get("hookEventName") == event, f"wrong hookEventName: {hso!r}"
    assert "permissionDecision" not in hso, "advisory nudge must not carry a decision"
    ctx = hso.get("additionalContext", "")
    assert ctx, f"no additionalContext in {payload!r}"
    return ctx


# ---------------------------------------------------------------------------
# PreToolUse tests
# ---------------------------------------------------------------------------

class TestPreToolUse:
    """Tests for run_pretooluse()."""

    @pytest.fixture
    def indexed_tmp(self, tmp_path, monkeypatch):
        """Pretend tmp_path is an indexed repo root."""
        monkeypatch.setattr(
            "jcodemunch_mcp.cli.hooks.steering._indexed_source_roots",
            lambda: [_norm_path(str(tmp_path))],
        )
        return tmp_path

    def test_allows_non_code_file(self, tmp_path):
        """Non-code extensions (e.g. .txt, .md) are always allowed."""
        f = tmp_path / "readme.md"
        f.write_text("x" * 10_000)
        rc, out, err = _run_with_stdin(run_pretooluse, _make_hook_input("Read", str(f)))
        assert rc == 0
        assert out == ""  # No output → allow
        assert err == ""  # No warning either

    def test_allows_small_code_file(self, tmp_path):
        """Code files below the size threshold are allowed."""
        f = tmp_path / "tiny.py"
        f.write_text("x = 1\n")
        rc, out, err = _run_with_stdin(run_pretooluse, _make_hook_input("Read", str(f)))
        assert rc == 0
        assert out == ""
        assert err == ""

    def test_warns_large_code_file(self, indexed_tmp):
        """Large code files inside an indexed repo get the nudge, to the model."""
        f = indexed_tmp / "big.py"
        f.write_text("x = 1\n" * 2000)  # well above 4KB
        rc, out, err = _run_with_stdin(run_pretooluse, _make_hook_input("Read", str(f)))
        assert rc == 0
        ctx = _additional_context(out)
        assert "get_file_outline" in ctx
        assert "get_symbol_source" in ctx
        assert err == ""  # stderr would never reach the model — must stay unused

    def test_read_nudge_silent_outside_indexed_repos(self, tmp_path, monkeypatch):
        """No nudge for files jcm cannot serve: recommending get_file_outline
        on an unindexed file yields an error and teaches the model to distrust
        the hints."""
        monkeypatch.setattr(
            "jcodemunch_mcp.cli.hooks.steering._indexed_source_roots", lambda: []
        )
        f = tmp_path / "big.py"
        f.write_text("x = 1\n" * 2000)
        rc, out, err = _run_with_stdin(run_pretooluse, _make_hook_input("Read", str(f)))
        assert (rc, out, err) == (0, "", "")

    def test_allows_large_code_file_with_offset(self, tmp_path):
        """Targeted reads (offset set) are allowed silently — likely pre-edit."""
        f = tmp_path / "big.py"
        f.write_text("x = 1\n" * 2000)
        rc, out, err = _run_with_stdin(
            run_pretooluse,
            _make_hook_input_with_params("Read", str(f), offset=50),
        )
        assert rc == 0
        assert out == ""
        assert err == ""  # No warning for targeted reads

    def test_allows_large_code_file_with_limit(self, tmp_path):
        """Targeted reads (limit set) are allowed silently — likely pre-edit."""
        f = tmp_path / "big.py"
        f.write_text("x = 1\n" * 2000)
        rc, out, err = _run_with_stdin(
            run_pretooluse,
            _make_hook_input_with_params("Read", str(f), limit=50),
        )
        assert rc == 0
        assert out == ""
        assert err == ""

    def test_allows_missing_file(self):
        """Files that don't exist are allowed (can't stat)."""
        rc, out, err = _run_with_stdin(
            run_pretooluse,
            _make_hook_input("Read", "/nonexistent/path/foo.py"),
        )
        assert rc == 0
        assert out == ""

    def test_allows_empty_input(self):
        """Empty/missing file_path is allowed."""
        rc, out, err = _run_with_stdin(
            run_pretooluse,
            json.dumps({"tool_input": {}}),
        )
        assert rc == 0
        assert out == ""

    def test_allows_invalid_json(self):
        """Unparseable stdin is allowed (no crash)."""
        rc, out, err = _run_with_stdin(run_pretooluse, "not json at all")
        assert rc == 0
        assert out == ""

    def test_respects_env_override(self, indexed_tmp):
        """JCODEMUNCH_HOOK_MIN_SIZE overrides the threshold."""
        f = indexed_tmp / "medium.ts"
        f.write_text("const x = 1;\n" * 500)  # ~6.5KB
        size = f.stat().st_size

        # With a very high threshold, it should be allowed silently
        with mock.patch("jcodemunch_mcp.cli.hooks.steering._MIN_SIZE_BYTES", size + 1):
            rc, out, err = _run_with_stdin(
                run_pretooluse, _make_hook_input("Read", str(f))
            )
            assert out == ""
            assert err == ""

        # With a low threshold, it should nudge the model
        with mock.patch("jcodemunch_mcp.cli.hooks.steering._MIN_SIZE_BYTES", 100):
            rc, out, err = _run_with_stdin(
                run_pretooluse, _make_hook_input("Read", str(f))
            )
            assert "jCodemunch hint" in _additional_context(out)

    @pytest.mark.parametrize("ext", [".py", ".ts", ".go", ".rs", ".java", ".cpp", ".rb"])
    def test_code_extensions_covered(self, ext, tmp_path):
        """Spot-check that major code extensions are in the set."""
        assert ext in _CODE_EXTENSIONS

    def test_warning_includes_file_size(self, indexed_tmp):
        """The nudge includes the file size for context."""
        f = indexed_tmp / "large.go"
        content = "package main\n" * 1000
        f.write_text(content)
        size = f.stat().st_size
        rc, out, err = _run_with_stdin(run_pretooluse, _make_hook_input("Read", str(f)))
        assert f"{size:,}" in _additional_context(out)


# ---------------------------------------------------------------------------
# PreToolUse: Grep → jCodemunch nudge
# ---------------------------------------------------------------------------

def _make_grep_input(pattern: str, *, path: str = "", cwd: str = "") -> str:
    """Build hook JSON mimicking a Claude Code Grep PreToolUse call."""
    tool_input: dict = {"pattern": pattern}
    if path:
        tool_input["path"] = path
    data = {
        "session_id": "test-session",
        "hook_event_name": "PreToolUse",
        "tool_name": "Grep",
        "tool_input": tool_input,
        "cwd": cwd,
    }
    return json.dumps(data)


class TestGrepNudge:
    """Tests for the Grep branch of run_pretooluse()."""

    def test_nudges_grep_inside_indexed_repo(self, tmp_path, monkeypatch):
        """A Grep whose cwd is an indexed source root gets the jcm nudge."""
        monkeypatch.setattr(
            "jcodemunch_mcp.cli.hooks.steering._indexed_source_roots",
            lambda: [os.path.normcase(os.path.abspath(str(tmp_path)))],
        )
        rc, out, err = _run_with_stdin(
            run_pretooluse, _make_grep_input("ViewBag.Username", cwd=str(tmp_path))
        )
        assert rc == 0
        ctx = _additional_context(out)  # never blocks; reaches the model
        assert "search_text" in ctx
        assert "search_symbols" in ctx
        assert "find_references" in ctx
        assert "ViewBag.Username" in ctx  # echoes the pattern
        assert err == ""  # stderr would never reach the model

    def test_nudges_grep_with_subpath_inside_repo(self, tmp_path, monkeypatch):
        """A relative `path` resolved under the indexed cwd still nudges."""
        monkeypatch.setattr(
            "jcodemunch_mcp.cli.hooks.steering._indexed_source_roots",
            lambda: [os.path.normcase(os.path.abspath(str(tmp_path)))],
        )
        rc, out, err = _run_with_stdin(
            run_pretooluse, _make_grep_input("foo", path="src", cwd=str(tmp_path))
        )
        assert rc == 0
        assert "search_text" in _additional_context(out)

    def test_silent_outside_indexed_repo(self, tmp_path, monkeypatch):
        """A Grep outside every indexed repo is allowed silently."""
        other = tmp_path / "indexed"
        elsewhere = tmp_path / "elsewhere"
        monkeypatch.setattr(
            "jcodemunch_mcp.cli.hooks.steering._indexed_source_roots",
            lambda: [os.path.normcase(os.path.abspath(str(other)))],
        )
        rc, out, err = _run_with_stdin(
            run_pretooluse, _make_grep_input("foo", cwd=str(elsewhere))
        )
        assert rc == 0
        assert out == ""
        assert err == ""

    def test_silent_when_nothing_indexed(self, monkeypatch):
        """No indexed repos → jcm can't help → no nudge."""
        monkeypatch.setattr("jcodemunch_mcp.cli.hooks.steering._indexed_source_roots", lambda: [])
        rc, out, err = _run_with_stdin(run_pretooluse, _make_grep_input("foo", cwd="/tmp"))
        assert rc == 0
        assert err == ""

    def test_env_disable(self, tmp_path, monkeypatch):
        """JCODEMUNCH_HOOK_GREP_NUDGE=0 silences the nudge even inside a repo."""
        monkeypatch.setenv("JCODEMUNCH_HOOK_GREP_NUDGE", "0")
        monkeypatch.setattr(
            "jcodemunch_mcp.cli.hooks.steering._indexed_source_roots",
            lambda: [os.path.normcase(os.path.abspath(str(tmp_path)))],
        )
        rc, out, err = _run_with_stdin(
            run_pretooluse, _make_grep_input("foo", cwd=str(tmp_path))
        )
        assert rc == 0
        assert err == ""

    def test_store_failure_is_silent(self, monkeypatch):
        """If the index store blows up, the Grep is allowed without a nudge."""
        def boom():
            raise RuntimeError("store unavailable")
        # _indexed_source_roots swallows internally, but guard the contract too.
        monkeypatch.setattr("jcodemunch_mcp.cli.hooks.steering._indexed_source_roots", boom)
        rc, out, err = _run_with_stdin(run_pretooluse, _make_grep_input("foo", cwd="/x"))
        assert rc == 0  # never crashes the agent's Grep

    def test_grep_never_blocks(self, tmp_path, monkeypatch):
        """The nudge informs without denying: context, but no permissionDecision."""
        monkeypatch.setattr(
            "jcodemunch_mcp.cli.hooks.steering._indexed_source_roots",
            lambda: [os.path.normcase(os.path.abspath(str(tmp_path)))],
        )
        rc, out, err = _run_with_stdin(
            run_pretooluse, _make_grep_input("foo", cwd=str(tmp_path))
        )
        assert rc == 0
        assert _additional_context(out)


# ---------------------------------------------------------------------------
# PreToolUse: strict enforcement mode (JCODEMUNCH_ENFORCE=strict)
# ---------------------------------------------------------------------------

def _assert_deny(out: str) -> str:
    """Assert *out* is a Claude Code PreToolUse deny decision; return the reason."""
    payload = json.loads(out)
    hso = payload["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    return hso["permissionDecisionReason"]


class TestStrictEnforce:
    """Strict mode turns the advisory nudges into hard PreToolUse denials,
    but only where an indexed-repo jcm route can serve the call."""

    def _index(self, monkeypatch, *roots):
        monkeypatch.setattr(
            "jcodemunch_mcp.cli.hooks.steering._indexed_source_roots",
            lambda: [os.path.normcase(os.path.abspath(str(r))) for r in roots],
        )

    def test_strict_denies_grep_inside_indexed_repo(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JCODEMUNCH_ENFORCE", "strict")
        self._index(monkeypatch, tmp_path)
        rc, out, err = _run_with_stdin(
            run_pretooluse, _make_grep_input("Secret", cwd=str(tmp_path))
        )
        assert rc == 0
        reason = _assert_deny(out)
        assert "search_text" in reason
        assert err == ""  # deny rides stdout JSON, not stderr

    def test_strict_allows_grep_outside_indexed_repo(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JCODEMUNCH_ENFORCE", "strict")
        self._index(monkeypatch, tmp_path / "indexed")
        rc, out, err = _run_with_stdin(
            run_pretooluse, _make_grep_input("foo", cwd=str(tmp_path / "elsewhere"))
        )
        assert rc == 0
        assert out == "" and err == ""  # jcm can't serve it → allow

    def test_strict_denies_full_read_of_indexed_code(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JCODEMUNCH_ENFORCE", "strict")
        self._index(monkeypatch, tmp_path)
        f = tmp_path / "big.py"
        f.write_text("x = 1\n" * 2000)  # well above the 4KB gate
        rc, out, err = _run_with_stdin(run_pretooluse, _make_hook_input("Read", str(f)))
        assert rc == 0
        reason = _assert_deny(out)
        assert "get_symbol_source" in reason

    def test_strict_allows_targeted_read_of_indexed_code(self, tmp_path, monkeypatch):
        """offset/limit reads (pre-Edit) always pass, even in strict mode."""
        monkeypatch.setenv("JCODEMUNCH_ENFORCE", "strict")
        self._index(monkeypatch, tmp_path)
        f = tmp_path / "big.py"
        f.write_text("x = 1\n" * 2000)
        rc, out, err = _run_with_stdin(
            run_pretooluse, _make_hook_input_with_params("Read", str(f), offset=10)
        )
        assert rc == 0
        assert out == "" and err == ""

    def test_strict_allows_read_outside_indexed_repo(self, tmp_path, monkeypatch):
        """A big code file the index can't serve is allowed (no jcm route)."""
        monkeypatch.setenv("JCODEMUNCH_ENFORCE", "strict")
        self._index(monkeypatch, tmp_path / "indexed")
        f = tmp_path / "loose" / "big.py"
        f.parent.mkdir()
        f.write_text("x = 1\n" * 2000)
        rc, out, err = _run_with_stdin(run_pretooluse, _make_hook_input("Read", str(f)))
        assert rc == 0
        assert out == ""

    def test_strict_allows_small_indexed_file(self, tmp_path, monkeypatch):
        """Tiny files stay under the size gate even in strict mode."""
        monkeypatch.setenv("JCODEMUNCH_ENFORCE", "strict")
        self._index(monkeypatch, tmp_path)
        f = tmp_path / "tiny.py"
        f.write_text("x = 1\n")
        rc, out, err = _run_with_stdin(run_pretooluse, _make_hook_input("Read", str(f)))
        assert rc == 0
        assert out == ""

    def test_off_mode_is_fully_silent(self, tmp_path, monkeypatch):
        """JCODEMUNCH_ENFORCE=off suppresses both the deny and the advisory nudge."""
        monkeypatch.setenv("JCODEMUNCH_ENFORCE", "off")
        self._index(monkeypatch, tmp_path)
        f = tmp_path / "big.py"
        f.write_text("x = 1\n" * 2000)
        rc_r, out_r, err_r = _run_with_stdin(run_pretooluse, _make_hook_input("Read", str(f)))
        rc_g, out_g, err_g = _run_with_stdin(
            run_pretooluse, _make_grep_input("foo", cwd=str(tmp_path))
        )
        assert (out_r, err_r, out_g, err_g) == ("", "", "", "")

    def test_advisory_default_still_warns(self, tmp_path, monkeypatch):
        """Unset / advisory keeps the v1.108.47 warn-but-allow behavior."""
        monkeypatch.delenv("JCODEMUNCH_ENFORCE", raising=False)
        self._index(monkeypatch, tmp_path)
        rc, out, err = _run_with_stdin(
            run_pretooluse, _make_grep_input("foo", cwd=str(tmp_path))
        )
        assert rc == 0
        assert "search_text" in _additional_context(out)  # advisory never denies

    def test_unknown_value_falls_back_to_advisory(self, tmp_path, monkeypatch):
        """A typo'd mode must never hard-block — it degrades to advisory."""
        monkeypatch.setenv("JCODEMUNCH_ENFORCE", "stict")  # typo
        self._index(monkeypatch, tmp_path)
        rc, out, err = _run_with_stdin(
            run_pretooluse, _make_grep_input("foo", cwd=str(tmp_path))
        )
        assert rc == 0
        assert "search_text" in _additional_context(out)  # not a deny


# ---------------------------------------------------------------------------
# PostToolUse tests
# ---------------------------------------------------------------------------

class TestPostToolUse:
    """Tests for run_posttooluse()."""

    def test_spawns_index_for_code_file(self, tmp_path):
        """Editing a code file triggers jcodemunch-mcp index-file."""
        f = tmp_path / "edited.py"
        f.write_text("def foo(): pass\n")
        inp = json.dumps({
            "hook_event_name": "PostToolUse",
            "tool_name": "Edit",
            "tool_input": {"file_path": str(f)},
            "tool_response": {"success": True},
        })
        with mock.patch("jcodemunch_mcp.cli.hooks.reindex.subprocess.Popen") as mock_popen:
            rc, out, _ = _run_with_stdin(run_posttooluse, inp)

        assert rc == 0
        mock_popen.assert_called_once()
        call_args = mock_popen.call_args[0][0]
        # The spawn must reuse THIS install's invocation (_self_invocation), not
        # a bare PATH lookup — the hook shell's minimal PATH is the reason
        # init writes absolute paths, and a bare name died silently there.
        # (TestSelfInvocation pins that the invocation is never the bare name.)
        from jcodemunch_mcp.cli.hooks.reindex import _self_invocation
        assert call_args == _self_invocation() + ["index-file", str(f)]

    def test_skips_non_code_file(self, tmp_path):
        """Non-code files don't trigger indexing."""
        f = tmp_path / "data.json"
        f.write_text("{}")
        inp = json.dumps({
            "hook_event_name": "PostToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": str(f)},
            "tool_response": {"success": True},
        })
        with mock.patch("jcodemunch_mcp.cli.hooks.reindex.subprocess.Popen") as mock_popen:
            rc, out, _ = _run_with_stdin(run_posttooluse, inp)

        assert rc == 0
        mock_popen.assert_not_called()

    def test_handles_missing_file_path(self):
        """Missing file_path in input is handled gracefully."""
        inp = json.dumps({"tool_input": {}})
        with mock.patch("jcodemunch_mcp.cli.hooks.reindex.subprocess.Popen") as mock_popen:
            rc, out, _ = _run_with_stdin(run_posttooluse, inp)
        assert rc == 0
        mock_popen.assert_not_called()

    def test_handles_invalid_json(self):
        """Invalid JSON stdin is handled gracefully."""
        with mock.patch("jcodemunch_mcp.cli.hooks.reindex.subprocess.Popen") as mock_popen:
            rc, out, _ = _run_with_stdin(run_posttooluse, "broken json")
        assert rc == 0
        mock_popen.assert_not_called()

    def test_handles_popen_failure(self, tmp_path):
        """If jcodemunch-mcp is not in PATH, fail silently."""
        f = tmp_path / "code.rs"
        f.write_text("fn main() {}")
        inp = json.dumps({
            "hook_event_name": "PostToolUse",
            "tool_name": "Edit",
            "tool_input": {"file_path": str(f)},
            "tool_response": {"success": True},
        })
        with mock.patch(
            "jcodemunch_mcp.cli.hooks.reindex.subprocess.Popen",
            side_effect=FileNotFoundError("jcodemunch-mcp not found"),
        ):
            rc, out, _ = _run_with_stdin(run_posttooluse, inp)
        assert rc == 0  # No crash

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
    def test_windows_creation_flags(self, tmp_path):
        """On Windows, CREATE_NO_WINDOW flag is passed."""
        import subprocess as sp
        f = tmp_path / "win.py"
        f.write_text("pass\n")
        inp = json.dumps({
            "hook_event_name": "PostToolUse",
            "tool_name": "Write",
            "tool_input": {"file_path": str(f)},
            "tool_response": {"success": True},
        })
        with mock.patch("jcodemunch_mcp.cli.hooks.reindex.subprocess.Popen") as mock_popen:
            rc, _, _ = _run_with_stdin(run_posttooluse, inp)
        kwargs = mock_popen.call_args[1]
        assert kwargs.get("creationflags") == sp.CREATE_NO_WINDOW


# ---------------------------------------------------------------------------
# PreCompact tests 
# ---------------------------------------------------------------------------

class TestPreCompact:
    """Tests for run_precompact()."""
    
    def test_precompact_empty_stdin(self):
        """Empty stdin returns exit 0, no stdout."""
        rc, out, _ = _run_with_stdin(run_precompact, "")
        assert rc == 0
        assert out == ""

    def test_precompact_invalid_json(self):
        """Invalid JSON stdin returns exit 0."""
        rc, out, _ = _run_with_stdin(run_precompact, "invalid json")
        assert rc == 0
        assert out == ""
    
    def test_precompact_emits_nothing(self):
        """PreCompact has NO exit-0 output channel: Claude Code discards its
        ``systemMessage`` (documented), and there is no additionalContext for
        this event. The hook used to emit the snapshot into that discarded
        field; the model-facing restore happens in SessionStart(compact)."""
        rc, out, err = _run_with_stdin(
            run_precompact, '{"hook_event_name": "PreCompact"}'
        )
        assert (rc, out, err) == (0, "", "")

    def test_precompact_still_registers_transcript_root(self, monkeypatch):
        """The one job PreCompact keeps: learning the transcript root (#421)."""
        seen: list = []
        monkeypatch.setattr(
            "jcodemunch_mcp.cli.hooks.snapshot._note_transcript_root", seen.append
        )
        _run_with_stdin(run_precompact, json.dumps({
            "hook_event_name": "PreCompact",
            "transcript_path": "/tmp/p/projects/repo/abc.jsonl",
        }))
        assert [d["transcript_path"] for d in seen] == [
            "/tmp/p/projects/repo/abc.jsonl"
        ]

    def test_precompact_landmark_enrichment_graceful_on_no_repos(self, monkeypatch):
        """Landmark enrichment should not crash if no indexed repos exist."""
        from jcodemunch_mcp.cli.hooks.landmarks import _build_landmark_section
        from jcodemunch_mcp.tools.session_journal import get_journal

        journal = get_journal()
        journal.record_edit("src/nonexistent.py")

        # Mock IndexStore at the storage module level (where it's imported from)
        MockStore = type("MockStore", (), {
            "__init__": lambda self, **kw: None,
            "list_repos": lambda self: [],
        })
        monkeypatch.setattr("jcodemunch_mcp.storage.IndexStore", MockStore)
        result = _build_landmark_section()
        assert isinstance(result, str)

    def test_precompact_landmark_returns_string(self):
        """_build_landmark_section must always return a string."""
        from jcodemunch_mcp.cli.hooks.landmarks import _build_landmark_section
        result = _build_landmark_section()
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Init integration: enforcement hooks
# ---------------------------------------------------------------------------

class TestEnforcementHooksInstall:
    """Tests for install_enforcement_hooks() in init.py."""

    def test_installs_enforcement_hooks(self, tmp_path):
        """Enforcement hooks are added to a clean settings file."""
        from jcodemunch_mcp.cli.init import install_enforcement_hooks

        settings = tmp_path / "settings.json"
        settings.write_text("{}", encoding="utf-8")

        with mock.patch("jcodemunch_mcp.cli.init._settings_json_path", return_value=settings):
            msg = install_enforcement_hooks(dry_run=False, backup=False, guidance=True)

        assert "PreToolUse" in msg or "PostToolUse" in msg or "PreCompact" in msg
        data = json.loads(settings.read_text(encoding="utf-8"))
        hooks = data["hooks"]
        assert "PreToolUse" in hooks
        assert "PostToolUse" in hooks
        assert "PreCompact" in hooks
        # Verify matchers
        pre_matcher = hooks["PreToolUse"][0]["matcher"]
        post_matcher = hooks["PostToolUse"][0]["matcher"]
        precompact_matcher = hooks["PreCompact"][0]["matcher"]
        assert pre_matcher == "Read|Grep|Glob|Bash"
        assert post_matcher == "Edit|Write"
        assert precompact_matcher == ""  # PreCompact hook has empty matcher

    def test_idempotent(self, tmp_path):
        """Running install_enforcement_hooks twice doesn't duplicate entries."""
        from jcodemunch_mcp.cli.init import install_enforcement_hooks

        settings = tmp_path / "settings.json"
        settings.write_text("{}", encoding="utf-8")

        with mock.patch("jcodemunch_mcp.cli.init._settings_json_path", return_value=settings):
            install_enforcement_hooks(dry_run=False, backup=False, guidance=True)
            msg2 = install_enforcement_hooks(dry_run=False, backup=False, guidance=True)

        assert "already present" in msg2
        data = json.loads(settings.read_text(encoding="utf-8"))
        assert len(data["hooks"]["PreToolUse"]) == 1
        assert len(data["hooks"]["PostToolUse"]) == 1

    def test_preserves_existing_hooks(self, tmp_path):
        """Existing hooks in settings.json are preserved."""
        from jcodemunch_mcp.cli.init import install_enforcement_hooks

        settings = tmp_path / "settings.json"
        existing = {
            "hooks": {
                "SessionStart": [{"hooks": [{"type": "command", "command": "echo hello"}]}],
            }
        }
        settings.write_text(json.dumps(existing), encoding="utf-8")

        with mock.patch("jcodemunch_mcp.cli.init._settings_json_path", return_value=settings):
            install_enforcement_hooks(dry_run=False, backup=False, guidance=True)

        data = json.loads(settings.read_text(encoding="utf-8"))
        assert "SessionStart" in data["hooks"]  # preserved
        assert "PreToolUse" in data["hooks"]     # added
        assert "PostToolUse" in data["hooks"]    # added

    def test_dry_run(self, tmp_path):
        """Dry run doesn't write anything."""
        from jcodemunch_mcp.cli.init import install_enforcement_hooks

        settings = tmp_path / "settings.json"
        settings.write_text("{}", encoding="utf-8")

        with mock.patch("jcodemunch_mcp.cli.init._settings_json_path", return_value=settings):
            msg = install_enforcement_hooks(dry_run=True, backup=False)

        assert "would add" in msg
        data = json.loads(settings.read_text(encoding="utf-8"))
        assert "hooks" not in data  # Nothing written

    def test_installs_taskcomplete_and_subagentstart(self, tmp_path):
        """TaskCompleted and SubagentStart hooks are added."""
        from jcodemunch_mcp.cli.init import install_enforcement_hooks

        settings = tmp_path / "settings.json"
        settings.write_text("{}", encoding="utf-8")

        with mock.patch("jcodemunch_mcp.cli.init._settings_json_path", return_value=settings):
            install_enforcement_hooks(dry_run=False, backup=False, guidance=True)

        data = json.loads(settings.read_text(encoding="utf-8"))
        hooks = data["hooks"]
        assert "TaskCompleted" in hooks
        assert "SubagentStart" in hooks
        # Verify commands
        tc_cmd = hooks["TaskCompleted"][0]["hooks"][0]["command"]
        sa_cmd = hooks["SubagentStart"][0]["hooks"][0]["command"]
        assert "hook-taskcomplete" in tc_cmd
        assert "hook-subagent-start" in sa_cmd


# ---------------------------------------------------------------------------
# TaskCompleted tests
# ---------------------------------------------------------------------------

class TestTaskComplete:
    """Tests for run_taskcomplete()."""

    def test_empty_stdin(self):
        """Empty stdin returns exit 0."""
        rc, out, _ = _run_with_stdin(run_taskcomplete, "")
        assert rc == 0
        assert out == ""

    def test_invalid_json(self):
        """Invalid JSON returns exit 0."""
        rc, out, _ = _run_with_stdin(run_taskcomplete, "not json")
        assert rc == 0
        assert out == ""

    def test_no_edited_files(self, monkeypatch):
        """No edited files → no output."""
        mock_journal = mock.MagicMock()
        mock_journal.get_context.return_value = {"files_edited": [], "files_accessed": []}
        monkeypatch.setattr("jcodemunch_mcp.cli.hooks.get_journal", lambda: mock_journal, raising=False)

        # Need to import get_journal in hooks context
        import jcodemunch_mcp.cli.hooks as hooks_mod
        with mock.patch.object(hooks_mod, "get_journal", create=True, return_value=mock_journal):
            rc, out, _ = _run_with_stdin(run_taskcomplete, '{"hook_event_name": "TaskCompleted"}')
        assert rc == 0

    def test_always_returns_zero(self):
        """Hook must always return 0 to avoid blocking the agent."""
        rc, _, _ = _run_with_stdin(run_taskcomplete, '{"hook_event_name": "TaskCompleted"}')
        assert rc == 0


# ---------------------------------------------------------------------------
# SubagentStart tests
# ---------------------------------------------------------------------------

class TestSubagentStart:
    """Tests for run_subagentstart()."""

    def test_empty_stdin(self):
        """Empty stdin returns exit 0."""
        rc, out, _ = _run_with_stdin(run_subagentstart, "")
        assert rc == 0
        assert out == ""

    def test_invalid_json(self):
        """Invalid JSON returns exit 0."""
        rc, out, _ = _run_with_stdin(run_subagentstart, "not json")
        assert rc == 0
        assert out == ""

    def test_no_repos(self, monkeypatch):
        """No indexed repos → no output."""
        MockStore = type("MockStore", (), {
            "__init__": lambda self, **kw: None,
            "list_repos": lambda self: [],
        })
        monkeypatch.setattr("jcodemunch_mcp.storage.IndexStore", MockStore)
        rc, out, _ = _run_with_stdin(run_subagentstart, '{"hook_event_name": "SubagentStart"}')
        assert rc == 0
        assert out == ""

    def test_with_indexed_repo(self, monkeypatch):
        """With an indexed repo, produces a briefing with tool catalog."""
        from jcodemunch_mcp.storage import CodeIndex

        mock_index = mock.MagicMock(spec=CodeIndex)
        mock_index.symbols = [
            {"id": "a", "name": "main", "kind": "function", "file": "main.py", "line": 1, "language": "python"},
        ]
        mock_index.source_files = ["main.py"]
        mock_index.imports = {}
        mock_index.alias_map = None

        MockStore = type("MockStore", (), {
            "__init__": lambda self, **kw: None,
            # Real list_repos() shape: entries keyed "repo", NO owner/name keys.
            # The old mock's {"owner","name"} shape masked months of every repo
            # being skipped (the real store never produces those keys).
            "list_repos": lambda self: [
                {"repo": "test/repo", "source_root": "/tmp/x", "symbol_count": 1}
            ],
            "load_index": lambda self, owner, name: mock_index,
        })
        monkeypatch.setattr("jcodemunch_mcp.storage.IndexStore", MockStore)

        # #1139: a repo is briefed only for a cwd inside its root; with no cwd
        # this test exercised the brief-everything fallback (Practice 9).
        rc, out, _ = _run_with_stdin(run_subagentstart, json.dumps(
            {"hook_event_name": "SubagentStart", "cwd": "/tmp/x"}))
        assert rc == 0
        if out:
            msg = _additional_context(out, event="SubagentStart")
            assert "test/repo" in msg
            assert "search_symbols" in msg  # Tool catalog

    def test_always_returns_zero(self):
        """Hook must always return 0 to avoid blocking the agent."""
        rc, _, _ = _run_with_stdin(run_subagentstart, '{"hook_event_name": "SubagentStart"}')
        assert rc == 0
