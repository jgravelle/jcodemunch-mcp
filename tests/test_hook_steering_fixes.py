"""Regression tests for the 2026-08 hook-steering fix batch.

Each class pins one confirmed defect from the "Claude forgets to call
jCodeMunch" investigation: coverage gaps (Bash/Glob unhooked), silently dead
hooks (empty in-process journal, absent owner/name keys, bare-name spawn,
symlink-blind root comparison, stale installed matchers), and mis-steering
(Read nudge outside indexed repos, surface-blind subagent catalog).
"""

import io
import json
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

from jcodemunch_mcp.cli.hooks import run_pretooluse, run_subagentstart, run_taskcomplete
from jcodemunch_mcp.cli.hooks._common import _norm_path, _repo_owner_name
from jcodemunch_mcp.cli.hooks.reindex import _self_invocation


def _run(func, stdin_text: str) -> tuple[int, str, str]:
    fake_in, fake_out, fake_err = (
        io.StringIO(stdin_text), io.StringIO(), io.StringIO(),
    )
    with mock.patch.object(sys, "stdin", fake_in), \
         mock.patch.object(sys, "stdout", fake_out), \
         mock.patch.object(sys, "stderr", fake_err):
        rc = func()
    return rc, fake_out.getvalue(), fake_err.getvalue()


def _norm(p) -> str:
    return _norm_path(str(p))  # the production comparison rule, not a copy


@pytest.fixture
def indexed_tmp(tmp_path, monkeypatch):
    """Pretend tmp_path is an indexed repo root."""
    monkeypatch.setattr(
        "jcodemunch_mcp.cli.hooks.steering._indexed_source_roots",
        lambda: [_norm(tmp_path)],
    )
    return tmp_path


def _pretool(tool_name: str, tool_input: dict, cwd: str = "") -> str:
    return json.dumps({
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input,
        "cwd": cwd,
    })


def _mock_store(monkeypatch, repos, loaded=None, idx=None):
    """Install a store fake with the REAL list_repos() entry shape."""
    if idx is None:
        idx = mock.MagicMock()
        idx.symbols = []
        idx.source_files = ["main.py"]
        idx.imports = {}
    def _load(self, owner, name):
        if loaded is not None:
            loaded.append(name)
        return idx

    MockStore = type("MockStore", (), {
        "__init__": lambda self, **kw: None,
        "list_repos": lambda self: repos,
        "load_index": _load,
    })
    monkeypatch.setattr("jcodemunch_mcp.storage.IndexStore", MockStore)


class TestBashSearchInterception:
    """Bash `grep`/`rg`/`find` was the dominant unhooked search route — and the
    escape hatch a strict-denied Grep funneled the model into."""

    def test_leading_grep_in_indexed_repo_nudges(self, indexed_tmp):
        rc, out, _ = _run(run_pretooluse, _pretool(
            "Bash", {"command": "grep -rn TODO src/"}, cwd=str(indexed_tmp)))
        assert rc == 0
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        assert "search_text" in ctx

    def test_leading_rg_strict_denies(self, indexed_tmp, monkeypatch):
        monkeypatch.setenv("JCODEMUNCH_ENFORCE", "strict")
        rc, out, _ = _run(run_pretooluse, _pretool(
            "Bash", {"command": "rg foo"}, cwd=str(indexed_tmp)))
        assert rc == 0
        hso = json.loads(out)["hookSpecificOutput"]
        assert hso["permissionDecision"] == "deny"

    def test_piped_grep_passes_silently(self, indexed_tmp):
        """A grep after a pipe filters other output — jcm cannot serve it."""
        rc, out, _ = _run(run_pretooluse, _pretool(
            "Bash", {"command": "git log --oneline | grep fix"},
            cwd=str(indexed_tmp)))
        assert (rc, out) == (0, "")

    def test_non_search_bash_passes_silently(self, indexed_tmp, monkeypatch):
        monkeypatch.setenv("JCODEMUNCH_ENFORCE", "strict")
        rc, out, _ = _run(run_pretooluse, _pretool(
            "Bash", {"command": "pytest tests/ -q"}, cwd=str(indexed_tmp)))
        assert (rc, out) == (0, "")

    def test_outside_indexed_repo_passes_silently(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "jcodemunch_mcp.cli.hooks.steering._indexed_source_roots", lambda: []
        )
        rc, out, _ = _run(run_pretooluse, _pretool(
            "Bash", {"command": "grep -rn TODO ."}, cwd=str(tmp_path)))
        assert (rc, out) == (0, "")

    def test_absolute_target_outside_repo_passes_even_strict(
        self, indexed_tmp, monkeypatch
    ):
        """`grep foo /etc/hosts` from an indexed-repo cwd targets a path jcm
        cannot serve — denying it (or claiming it targets the repo) is false."""
        monkeypatch.setenv("JCODEMUNCH_ENFORCE", "strict")
        rc, out, _ = _run(run_pretooluse, _pretool(
            "Bash", {"command": "grep -n root /etc/hosts"}, cwd=str(indexed_tmp)))
        assert (rc, out) == (0, "")

    def test_pipeline_grep_is_nudged_not_denied(self, indexed_tmp, monkeypatch):
        """A deny is only coherent for a PURE search: `rg | xargs sed -i` or
        `grep -q x && make` do work the jcm routes cannot replace."""
        monkeypatch.setenv("JCODEMUNCH_ENFORCE", "strict")
        for cmd in ("rg -l foo | xargs sed -i 's/a/b/'",
                    "grep -q foo src/x.py && make build"):
            rc, out, _ = _run(run_pretooluse, _pretool(
                "Bash", {"command": cmd}, cwd=str(indexed_tmp)))
            assert rc == 0
            hso = json.loads(out)["hookSpecificOutput"]
            assert "permissionDecision" not in hso, cmd
            assert "search_text" in hso["additionalContext"], cmd

    @pytest.mark.parametrize("cmd", [
        "FOO=1 grep -rn TODO src/",
        'LC_ALL="C" rg foo',
        "(grep -rn TODO src/)",
        "( FOO=bar grep -q x )",
    ])
    def test_env_assignment_and_subshell_prefixes_intercepted(
        self, indexed_tmp, cmd
    ):
        """`FOO=1 grep …` and `(grep …)` were the two shapes that slipped the
        leading-command regex — the escape hatches a denied grep funnels into."""
        rc, out, _ = _run(run_pretooluse, _pretool(
            "Bash", {"command": cmd}, cwd=str(indexed_tmp)))
        assert rc == 0
        assert "search_text" in json.loads(out)["hookSpecificOutput"]["additionalContext"], cmd

    def test_home_anchored_target_passes_even_strict(self, indexed_tmp, monkeypatch):
        """`grep foo ~/notes.txt` names a home path outside the repo — the
        `~/` half of the path-token veto."""
        monkeypatch.setenv("JCODEMUNCH_ENFORCE", "strict")
        rc, out, _ = _run(run_pretooluse, _pretool(
            "Bash", {"command": "grep -n foo ~/notes.txt"},
            cwd=str(indexed_tmp)))
        assert (rc, out) == (0, "")

    def test_dotdot_target_passes_even_strict(self, indexed_tmp, monkeypatch):
        """`grep foo ../sibling/` escapes cwd — the one relative shape that
        provably leaves the repo; silence, never a false deny."""
        monkeypatch.setenv("JCODEMUNCH_ENFORCE", "strict")
        rc, out, _ = _run(run_pretooluse, _pretool(
            "Bash", {"command": "grep -rn foo ../sibling/"},
            cwd=str(indexed_tmp)))
        assert (rc, out) == (0, "")

    def test_find_is_never_strict_denied(self, indexed_tmp, monkeypatch):
        """`find ... -delete` opens with the same word as a search — a deny
        steering to search routes cannot do the deletion, so find only nudges."""
        monkeypatch.setenv("JCODEMUNCH_ENFORCE", "strict")
        rc, out, _ = _run(run_pretooluse, _pretool(
            "Bash", {"command": "find . -name '*.pyc' -delete"},
            cwd=str(indexed_tmp)))
        assert rc == 0
        hso = json.loads(out)["hookSpecificOutput"]
        assert "permissionDecision" not in hso
        assert "get_file_tree" in hso["additionalContext"]


class TestReadSizeBoundary:
    def test_exactly_threshold_nudges_one_below_does_not(self, indexed_tmp):
        from jcodemunch_mcp.cli.hooks.steering import _MIN_SIZE_BYTES
        at = indexed_tmp / "at.py"
        at.write_bytes(b"#" * _MIN_SIZE_BYTES)
        below = indexed_tmp / "below.py"
        below.write_bytes(b"#" * (_MIN_SIZE_BYTES - 1))
        rc, out, _ = _run(run_pretooluse, _pretool(
            "Read", {"file_path": str(at)}))
        assert "get_file_outline" in json.loads(out)["hookSpecificOutput"]["additionalContext"]
        rc, out, _ = _run(run_pretooluse, _pretool(
            "Read", {"file_path": str(below)}))
        assert (rc, out) == (0, "")


class TestGlobInterception:
    def test_glob_in_indexed_repo_nudges(self, indexed_tmp):
        rc, out, _ = _run(run_pretooluse, _pretool(
            "Glob", {"pattern": "**/*.py"}, cwd=str(indexed_tmp)))
        assert rc == 0
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        assert "get_file_tree" in ctx

    def test_glob_with_outside_path_passes_silently(self, indexed_tmp):
        rc, out, _ = _run(run_pretooluse, _pretool(
            "Glob", {"pattern": "**/*.py", "path": "/usr/lib"},
            cwd=str(indexed_tmp)))
        assert (rc, out) == (0, "")

    def test_glob_strict_denies(self, indexed_tmp, monkeypatch):
        monkeypatch.setenv("JCODEMUNCH_ENFORCE", "strict")
        rc, out, _ = _run(run_pretooluse, _pretool(
            "Glob", {"pattern": "**/*.py"}, cwd=str(indexed_tmp)))
        assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"


class TestSymlinkedRootsStillOverlap:
    """index_folder records source_root via Path.resolve(); the hook compared
    abspath only, so any symlink component (macOS /tmp -> /private/tmp) made
    the whole steering layer silently inert."""

    def test_symlink_alias_of_indexed_root_fires_nudge(self, tmp_path, monkeypatch):
        real = tmp_path / "repo"
        real.mkdir()
        alias = tmp_path / "alias"
        os.symlink(real, alias)
        monkeypatch.setattr(
            "jcodemunch_mcp.cli.hooks.steering._indexed_source_roots",
            lambda: [_norm(real)],  # stored resolved, as index_folder does
        )
        rc, out, _ = _run(run_pretooluse, _pretool(
            "Grep", {"pattern": "foo"}, cwd=str(alias)))
        assert rc == 0
        assert "search_text" in json.loads(out)["hookSpecificOutput"]["additionalContext"]


class TestRepoOwnerName:
    """list_repos() entries carry {"repo": "owner/name"} — never owner/name
    keys. Three hook loops read the absent keys and skipped every repo."""

    def test_real_store_shape(self):
        assert _repo_owner_name({"repo": "local/proj-abc"}) == ("local", "proj-abc")

    def test_wrong_mock_shape_is_rejected(self):
        """The owner/name shape only ever came from wrong test mocks; accepting
        it would re-license the mock shape that masked this defect."""
        assert _repo_owner_name({"owner": "o", "name": "n"}) == ("", "")

    def test_garbage(self):
        assert _repo_owner_name({}) == ("", "")
        assert _repo_owner_name({"repo": "noslash"}) == ("", "")


class TestTaskCompleteLiveJournal:
    """run_taskcomplete read the empty in-process journal (#334 class) — the
    diagnostics never fired in any real deployment. It must read the persisted
    live journal, like PreCompact/SessionStart already do."""

    def test_live_journal_drives_diagnostics(self, monkeypatch):
        monkeypatch.setattr(
            "jcodemunch_mcp.tools.session_state.load_live_journal",
            lambda **kw: {"files_edited": [{"file": "src/a.py", "edits": 1}]},
        )
        idx = mock.MagicMock()
        idx.source_files = ["src/a.py", "src/b.py"]
        idx.symbols = [{"name": "f", "file": "src/a.py", "line": 1}]
        _mock_store(monkeypatch, [{"repo": "local/proj"}], idx=idx)
        monkeypatch.setattr(
            "jcodemunch_mcp.tools.find_dead_code.find_dead_code",
            lambda repo_id, granularity: {
                "dead_symbols": [{"name": "f", "file": "src/a.py", "line": 1}],
            },
        )
        monkeypatch.setattr(
            "jcodemunch_mcp.tools.get_untested_symbols.get_untested_symbols",
            lambda *a, **kw: {"symbols": [], "untested_count": 0},
        )
        monkeypatch.setattr(
            "jcodemunch_mcp.tools.check_references.check_references",
            lambda *a, **kw: {
                "results": [{"identifier": "f", "is_referenced": True}],
            },
        )
        rc, out, _ = _run(run_taskcomplete, '{"hook_event_name": "TaskCompleted"}')
        assert rc == 0
        assert out, "live journal with edits must produce diagnostics"
        msg = json.loads(out)["systemMessage"]
        assert "local/proj" in msg
        assert "Possibly orphaned" in msg


class TestTaskCompleteDiagnosticQuality:
    """The diagnostics must be LOSSLESS for the session's files and render
    the negative reference verdicts they were built to surface."""

    @pytest.fixture
    def _diag_env(self, monkeypatch):
        monkeypatch.setattr(
            "jcodemunch_mcp.tools.session_state.load_live_journal",
            lambda **kw: {"files_edited": [{"file": "src/a.py", "edits": 1}]},
        )
        idx = mock.MagicMock()
        idx.source_files = ["src/a.py", "src/b.py"]
        idx.symbols = [{"name": "f", "file": "src/a.py", "line": 1}]
        _mock_store(monkeypatch, [{"repo": "local/proj"}], idx=idx)
        monkeypatch.setattr(
            "jcodemunch_mcp.tools.find_dead_code.find_dead_code",
            lambda repo_id, granularity: {"dead_symbols": []},
        )

    def test_session_untested_symbols_survive_a_large_corpus(
        self, _diag_env, monkeypatch
    ):
        """The regression the branch review caught: a corpus-wide call CAPPED
        its result before the session filter, so the session file's symbols
        vanished in exactly the worst-tested repos. This mock is an honest
        oracle: the session symbol ranks LAST among 150, so any capped
        corpus-wide read loses it and any lossless read keeps it.

        ⚠⚠ **The mock returned `untested_symbols` until #559, and the real tool
        has never emitted that key -- it emits `symbols`.** So this test was
        green against a hook that produced NOTHING in production: the consumer
        asked for the invented key, `.get(..., [])` answered empty, and the
        diagnostic this test exists to protect was dark for its whole life.
        **The mock was not merely broad enough to bypass the assertion; it
        supplied a contract the producer does not have**, which is the only
        way an absent-key defect can hide from a test written about it.
        `TestTaskCompleteReadsTheRealContract` below closes that by using no
        producer mock at all."""
        corpus = [
            {"name": f"other_{i}", "file": "other.py", "line": i}
            for i in range(149)
        ] + [{"name": "session_sym", "file": "src/a.py", "line": 1}]

        def fake_untested(repo_id, file_pattern=None, max_results=50, **kw):
            pool = ([u for u in corpus if u["file"] == file_pattern]
                    if file_pattern else corpus)
            return {"symbols": pool[:max_results], "untested_count": len(pool)}

        monkeypatch.setattr(
            "jcodemunch_mcp.tools.get_untested_symbols.get_untested_symbols",
            fake_untested,
        )
        monkeypatch.setattr(
            "jcodemunch_mcp.tools.check_references.check_references",
            lambda *a, **kw: {"results": []},
        )
        _, out, _ = _run(run_taskcomplete, '{"hook_event_name": "TaskCompleted"}')
        assert "session_sym" in json.loads(out)["systemMessage"]

    def test_unreferenced_symbol_is_rendered(self, _diag_env, monkeypatch):
        """The negative verdict path — the one #406 showed was mis-consumed —
        must reach the agent-facing message."""
        monkeypatch.setattr(
            "jcodemunch_mcp.tools.get_untested_symbols.get_untested_symbols",
            lambda *a, **kw: {"symbols": [], "untested_count": 0},
        )
        monkeypatch.setattr(
            "jcodemunch_mcp.tools.check_references.check_references",
            lambda *a, **kw: {
                "results": [{"identifier": "f", "is_referenced": False}],
            },
        )
        _, out, _ = _run(run_taskcomplete, '{"hook_event_name": "TaskCompleted"}')
        msg = json.loads(out)["systemMessage"]
        assert "Unreferenced" in msg and "`f`" in msg


class TestSelfInvocation:
    """The reindex child must reuse THIS install's invocation — a bare-name
    Popen died silently under the hook shell's minimal PATH, on exactly the
    installs init's absolute-path resolution exists for."""

    def test_never_bare_name(self):
        inv = _self_invocation()
        assert inv != ["jcodemunch-mcp"]
        assert os.path.isabs(inv[0])

    def test_fallback_is_python_dash_m(self, monkeypatch):
        from jcodemunch_mcp import install_layout

        monkeypatch.setattr(sys, "argv", ["/nonexistent/whatever"])
        # No console script in this interpreter's scripts directory either.
        monkeypatch.setattr(install_layout, "_scripts_dir", lambda: None)
        assert _self_invocation() == [sys.executable, "-m", "jcodemunch_mcp"]

    def test_prefers_own_executable_when_it_exists(self, tmp_path, monkeypatch):
        exe = tmp_path / "jcodemunch-mcp"
        exe.write_text("#!/bin/sh\n")
        monkeypatch.setattr(sys, "argv", [str(exe)])
        assert _self_invocation() == [str(exe)]


class TestSubagentCatalogMatchesSurface:
    """Under tool_surface=counter the raw catalog names are not callable; the
    briefing must describe the order/menu/route front door instead."""

    @pytest.fixture
    def _store(self, monkeypatch):
        _mock_store(monkeypatch, [{"repo": "test/repo"}])

    def test_counter_surface_briefs_front_door(self, _store, monkeypatch):
        monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", "counter")
        _, out, _ = _run(run_subagentstart, "{}")
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        assert "`menu`" in ctx and "`order`" in ctx and "`route`" in ctx
        assert "search_symbols," not in ctx  # the 41-name list is uncallable here

    def test_cwd_scopes_briefing_to_containing_repo(self, monkeypatch, tmp_path):
        """A subagent spawned into repo A must not pay hydration + PageRank
        for every other indexed repo on the box (nor read briefings about
        them). No cwd overlap keeps the brief-everything fallback."""
        a = tmp_path / "a"
        a.mkdir()
        loaded: list = []
        # Real list_repos() entries carry symbol_count; #1139 hydrates only a
        # KNOWN-small index, so a count-less mock would hydrate nothing.
        _mock_store(monkeypatch, [
            {"repo": "test/a", "source_root": str(a), "symbol_count": 1},
            {"repo": "test/b", "source_root": str(tmp_path / "b"), "symbol_count": 1},
        ], loaded=loaded)
        monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", "full")
        _, out, _ = _run(run_subagentstart, json.dumps({"cwd": str(a)}))
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        assert "test/a" in ctx
        assert "test/b" not in ctx
        assert loaded == ["a"]  # the unrelated repo was never hydrated

    def test_no_cwd_overlap_briefs_no_repo(self, monkeypatch, tmp_path):
        """A cwd outside every indexed root briefs NO repo (#1139). Inverted
        under Practice 9: this test pinned the brief-everything fallback,
        which hydrated and PageRanked every index on the agent-startup path
        (533 s at worst in the report)."""
        a = tmp_path / "a"
        a.mkdir()
        loaded: list = []
        _mock_store(monkeypatch, [
            {"repo": "test/a", "source_root": str(a), "symbol_count": 1},
            {"repo": "test/b", "source_root": str(tmp_path / "b"), "symbol_count": 1},
        ], loaded=loaded)
        monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", "full")
        _, out, _ = _run(run_subagentstart, json.dumps(
            {"cwd": str(tmp_path / "elsewhere")}))
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        assert "test/a" not in ctx and "test/b" not in ctx
        assert loaded == []
        assert "search_symbols" in ctx  # the catalog still briefs

    def test_full_surface_briefs_catalog(self, _store, monkeypatch):
        monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", "full")
        _, out, _ = _run(run_subagentstart, "{}")
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        assert "search_symbols" in ctx

    def _fresh_config_state(self, monkeypatch, store):
        """Force config's lazy load to run against *store* (conftest pre-seeds
        DEFAULTS with _CONFIG_LOADED=True, which would bypass the file read)."""
        import jcodemunch_mcp.config as cfg
        monkeypatch.delenv("JCODEMUNCH_TOOL_SURFACE", raising=False)
        monkeypatch.setenv("CODE_INDEX_PATH", str(store))
        monkeypatch.setattr(cfg, "_GLOBAL_CONFIG", {})
        monkeypatch.setattr(cfg, "_CONFIG_LOADED", False)

    def test_briefed_catalog_names_exist_in_the_server_catalog(
        self, monkeypatch
    ):
        """The full-surface briefing hardcodes a tool list; without this
        ratchet it rots exactly like the surface-blind briefing this batch
        fixed (a briefed name the server does not serve trains the model to
        distrust jcm). Binds the prose to the live catalog, same as
        test_mcp_instructions does for the MCP instructions string."""
        # Import server BEFORE mocking the store: the import chain binds
        # whatever IndexStore is visible into lazily-imported modules for
        # the rest of the process.
        from jcodemunch_mcp import server
        live = set(server._catalog_names())
        _mock_store(monkeypatch, [{"repo": "test/repo"}])
        monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", "full")
        _, out, _ = _run(run_subagentstart, "{}")
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        catalog_text = ctx.split("### Available jCodemunch Tools", 1)[1]
        briefed = {
            n.strip().strip("`.")
            for n in catalog_text.replace("\n", " ").split(",")
            if n.strip()
        }
        briefed = {n.split()[-1] for n in briefed if n} - {"task"}
        missing = sorted(n for n in briefed if n and n not in live)
        assert not missing, f"briefing names tools the server does not serve: {missing}"

    def test_counter_from_config_file_not_env(self, _store, monkeypatch, tmp_path):
        """The defect's primary scenario: fresh installs get counter via
        config.jsonc, not the env var. _tool_surface must read it through
        config.get — load_config() returns None, so the old
        load_config().get(...) raised into the except and silently resolved
        to 'full' forever, keeping the surface-blind briefing alive."""
        store = tmp_path / "store"
        store.mkdir()
        (store / "config.jsonc").write_text(
            '{"tool_surface": "counter"}', encoding="utf-8"
        )
        self._fresh_config_state(monkeypatch, store)
        from jcodemunch_mcp.cli.hooks.briefing import _tool_surface
        assert _tool_surface() == "counter"

    def test_tool_surface_read_never_writes_config(self, monkeypatch, tmp_path):
        """A config READ from a hook process must never create config.jsonc
        (Maintenance Practice 8; load_config's default create_missing=True
        did exactly that before the fix)."""
        store = tmp_path / "store2"
        store.mkdir()
        (store / "index.db").write_text("", encoding="utf-8")  # looks installed
        self._fresh_config_state(monkeypatch, store)
        from jcodemunch_mcp.cli.hooks.briefing import _tool_surface
        _tool_surface()
        assert not (store / "config.jsonc").exists()


class TestStoreBackedGates:
    """Integration against a REAL index: the light source-roots listing and
    the membership probe that spares unrelated repos a full hydration."""

    @pytest.fixture
    def real_store(self, tmp_path, monkeypatch):
        proj = tmp_path / "proj"
        proj.mkdir()
        (proj / "mod.py").write_text("def alpha():\n    return 1\n")
        store_dir = tmp_path / "store"
        monkeypatch.setenv("CODE_INDEX_PATH", str(store_dir))
        monkeypatch.setenv("JCODEMUNCH_TRUSTED_FOLDERS", str(tmp_path))
        from jcodemunch_mcp.tools.index_folder import index_folder
        result = index_folder(str(proj))
        assert result.get("success"), result
        from jcodemunch_mcp.storage import IndexStore
        return IndexStore()

    def test_list_source_roots_matches_list_repos(self, real_store):
        light = sorted(real_store.list_source_roots())
        heavy = sorted(
            e["source_root"] for e in real_store.list_repos()
            if e.get("source_root")
        )
        assert light == heavy and light

    def test_probe_spares_unrelated_repo_from_hydration(self, real_store):
        from jcodemunch_mcp.cli.hooks._common import _iter_loaded_repos
        repos = real_store.list_repos()
        loaded = list(_iter_loaded_repos(
            real_store, repos, wanted_files=["not/in/this/repo.py"]
        ))
        assert loaded == []  # probe said no — never hydrated
        loaded = list(_iter_loaded_repos(
            real_store, repos, wanted_files=["mod.py"]
        ))
        assert len(loaded) == 1  # the indexed file is repo-relative

    def test_steering_end_to_end_through_symlink_alias(
        self, real_store, tmp_path
    ):
        """The full chain, nothing mocked: index_folder stores the resolved
        root, the gate reads it via list_source_roots, and a Grep addressed
        through a symlink alias of the repo still gets the nudge."""
        alias = tmp_path / "alias"
        os.symlink(tmp_path / "proj", alias)
        rc, out, _ = _run(run_pretooluse, _pretool(
            "Grep", {"pattern": "alpha"}, cwd=str(alias)))
        assert rc == 0
        assert "search_text" in json.loads(out)["hookSpecificOutput"]["additionalContext"]

    def test_probe_fails_open_without_sqlite_backend(self, monkeypatch):
        """A store fake without _sqlite must hydrate, never silently skip."""
        idx = mock.MagicMock()
        idx.source_files = ["src/a.py"]
        idx.symbols = []
        loaded: list = []
        _mock_store(monkeypatch, [{"repo": "t/r"}], loaded=loaded, idx=idx)
        from jcodemunch_mcp.storage import IndexStore
        from jcodemunch_mcp.cli.hooks._common import _iter_loaded_repos
        out = list(_iter_loaded_repos(IndexStore(), [{"repo": "t/r"}],
                                      wanted_files=["src/a.py"]))
        assert len(out) == 1 and loaded == ["r"]


class TestHostileStdin:
    """A hook must never crash on unexpected payload shapes."""

    @pytest.mark.parametrize("payload", [
        "null", "[]", '"a string"', "42",
        '{"tool_name": "Read", "tool_input": null}',
        '{"tool_name": "Read", "tool_input": "nope"}',
        '{"tool_name": "Bash", "tool_input": {"command": null}}',
        '{"tool_name": "Read", "tool_input": {"file_path": 7}}',
    ])
    def test_pretooluse_survives(self, payload):
        rc, _, _ = _run(run_pretooluse, payload)
        assert rc == 0

    def test_posttooluse_swallows_nul_byte_path_with_real_spawn(self):
        """No Popen mock: the NUL byte must reach the real spawn and die in
        _spawn_index_file's except, not crash the hook."""
        from jcodemunch_mcp.cli.hooks import run_posttooluse
        payload = json.dumps({
            "tool_name": "Edit",
            "tool_input": {"file_path": "src/a\u0000.py"},
        })
        rc, out, _ = _run(run_posttooluse, payload)
        assert (rc, out) == (0, "")

    @pytest.mark.parametrize("payload", [
        "null", "[]", '{"tool_input": null}', '{"tool_input": {"file_path": 7}}',
    ])
    def test_posttooluse_survives(self, payload):
        from jcodemunch_mcp.cli.hooks import run_posttooluse
        rc, _, _ = _run(run_posttooluse, payload)
        assert rc == 0


class TestMinSizeGarbageEnv:
    def test_garbage_env_parses_to_default(self):
        """Garbage JCODEMUNCH_HOOK_MIN_SIZE must fall back to the default, not
        crash every hook at import time."""
        import subprocess
        env = {**os.environ,
               "JCODEMUNCH_HOOK_MIN_SIZE": "not-a-number",
               # Anchored to this file: a cwd-relative "src" would import
               # whatever package the runner's cwd happens to expose.
               "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
        r = subprocess.run(
            [sys.executable, "-c",
             "from jcodemunch_mcp.cli.hooks.steering import _MIN_SIZE_BYTES; "
             "print(_MIN_SIZE_BYTES)"],
            env=env, capture_output=True, text=True, timeout=60,
        )
        assert r.returncode == 0, r.stderr
        # The property: garbage parses to the same default an env-free import
        # gets (this process's own value), not to any pinned literal.
        from jcodemunch_mcp.cli.hooks.steering import _MIN_SIZE_BYTES
        assert int(r.stdout.strip()) == _MIN_SIZE_BYTES > 0


class TestMatcherUpgrade:
    """_merge_hooks left a pre-1.108.47 install's matcher "Read" in place
    forever: re-running init reported success while Grep steering never fired."""

    def test_stale_matcher_upgraded_in_place(self):
        from jcodemunch_mcp.cli.init import _enforcement_hooks, _merge_hooks

        data = {"hooks": {"PreToolUse": [{
            "matcher": "Read",
            "hooks": [{"type": "command",
                       "command": "jcodemunch-mcp hook-pretooluse"}],
        }]}}
        added, updated = _merge_hooks(
            data, _enforcement_hooks(), "jcodemunch-mcp hook-p"
        )
        shipped = _enforcement_hooks()["PreToolUse"][0]["matcher"]
        assert data["hooks"]["PreToolUse"][0]["matcher"] == shipped
        assert "PreToolUse" in updated
        assert "PreToolUse" not in added
        # Still no duplicate rule for the same subcommand.
        assert len(data["hooks"]["PreToolUse"]) == 1

    def test_mixed_rule_matcher_is_not_converged(self):
        """A user who hand-merged their own hook into our rule keeps their
        trigger: the rule-level matcher is only converged when every hook in
        the rule is ours (their command string was already never touched)."""
        from jcodemunch_mcp.cli.init import _enforcement_hooks, _merge_hooks

        data = {"hooks": {"PreToolUse": [{
            "matcher": "Read",
            "hooks": [
                {"type": "command", "command": "jcodemunch-mcp hook-pretooluse"},
                {"type": "command", "command": "/home/u/my-guard.sh"},
            ],
        }]}}
        _merge_hooks(data, _enforcement_hooks(), "jcodemunch-mcp hook-p")
        rule = data["hooks"]["PreToolUse"][0]
        assert rule["matcher"] == "Read"  # user's trigger untouched
        # Our command inside the mixed rule still converges to absolute form.
        from jcodemunch_mcp.cli.init import _extract_jcm_subcommand
        jcm_cmds = [h["command"] for h in rule["hooks"]
                    if _extract_jcm_subcommand(h["command"])]
        assert jcm_cmds and all("hook-pretooluse" in c for c in jcm_cmds)
        assert rule["hooks"][1]["command"] == "/home/u/my-guard.sh"

    def test_current_matcher_untouched_and_idempotent(self):
        from jcodemunch_mcp.cli.init import _enforcement_hooks, _merge_hooks

        data: dict = {}
        _merge_hooks(data, _enforcement_hooks(), "jcodemunch-mcp hook-p")
        again = _merge_hooks(data, _enforcement_hooks(), "jcodemunch-mcp hook-p")
        assert again == ([], [])  # nothing added, nothing updated → no rewrite
