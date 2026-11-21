"""v1.108.269 — a withheld oversize file says so, and the cap is reachable.

jcodemunch-mcp #429, filed against 1.108.264 after `src/jcodemunch_mcp/
server.py` crossed `DEFAULT_MAX_FILE_SIZE` by 532 bytes and the MCP entrypoint
stopped entering its own index.

The size cap itself is working as designed — v1.108.193 made `too_large` a
WITHHELD reason precisely so a corpus missing a real, current, wanted file
refuses to certify absence. Two things around it were not.

**1. The exclusion was silent on the path that matters.** `resolve_explicit_
paths` warned per entry; the `os.walk` path bumped a counter and said nothing.
Same limit, same repo, and whether you were told depended on whether you had
passed `paths=`. The counter surfaced only inside a verdict block, and only
when some later query happened to ask about absence and got refused — so a user
with a 600KB generated client got quietly incomplete answers with no signal
that anything had been withheld. `index_repo` did not even count it.

**2. The per-call override was unreachable over MCP.** `get_max_file_size` has
accepted `max_size` since .193, but no caller passed one and it reached no tool
schema, so over the transport every actual user is on, editing a config file
was the only route.

⚠ And `index_repo.discover_source_files` carried a hardcoded `500 * 1024`
default that consulted neither config nor env — a FOURTH copy of the limit, so
.193's escape hatch and .197's per-project key both applied to `index_folder`
and did nothing at all for a GitHub repo. A cap fixed on one discovery path is
not fixed.
"""

import inspect

import pytest


# ⚠ Imported inside each test, not at module scope. `size_cap_warning` does not
# exist before this release, and a module-level import turns every assertion in
# this file into one collection error — which reports as a single red line and
# proves nothing about which guards actually bite.
def _warn(dropped, max_size):
    from jcodemunch_mcp.tools._utils import size_cap_warning

    return size_cap_warning(dropped, max_size)


# ── the warning itself ──────────────────────────────────────────────────────


class TestSizeCapWarning:
    def test_clean_index_is_unchanged(self):
        """No withheld file, no warning — the healthy path must not gain noise."""
        assert _warn([], 512_000) is None

    def test_a_withheld_file_is_named(self):
        msg = _warn(["src/server.py"], 512_000)
        assert msg is not None
        assert "src/server.py" in msg

    def test_the_effective_cap_is_quoted(self):
        """A number the caller cannot see is a number they cannot act on."""
        assert "1048576" in _warn(["a.py"], 1_048_576)

    def test_both_routes_out_are_named(self):
        """The per-call param AND the persistent key — one is a one-off, the
        other makes it stick, and #429 is a repo that needs the second."""
        msg = _warn(["a.py"], 512_000)
        assert "max_size" in msg
        assert "max_file_size" in msg
        assert "JCODEMUNCH_MAX_FILE_SIZE" in msg

    def test_it_says_the_file_is_missing_not_merely_capped(self):
        """The consequence is the point: symbols are absent and absence cannot
        be proven. 'Skipped' alone reads like a file nobody wanted."""
        msg = _warn(["a.py"], 512_000).lower()
        assert "missing" in msg
        assert "absence" in msg

    def test_the_sample_is_bounded(self):
        """A repo of generated clients must not turn one warning into a wall of
        them — that is its own kind of silence."""
        from jcodemunch_mcp.tools._utils import SIZE_CAP_WARNING_SAMPLE

        many = [f"gen/client_{i:03d}.py" for i in range(50)]
        msg = _warn(many, 512_000)
        for i in range(SIZE_CAP_WARNING_SAMPLE):
            assert f"client_{i:03d}.py" in msg
        assert "client_049.py" not in msg
        assert f"and {50 - SIZE_CAP_WARNING_SAMPLE} more" in msg
        assert "50 file(s)" in msg

    def test_the_full_count_survives_truncation(self):
        """Naming five must never imply five were dropped."""
        many = [f"f{i}.py" for i in range(12)]
        assert "12 file(s)" in _warn(many, 512_000)

    def test_order_is_stable(self):
        """Two runs over the same tree must produce the same sentence; os.walk
        order is not guaranteed and a warning that reshuffles reads as churn."""
        paths = ["z.py", "a.py", "m.py"]
        assert _warn(paths, 1) == _warn(
            list(reversed(paths)), 1
        )


# ── the local walk now speaks (#429 defect 3) ───────────────────────────────


class TestLocalWalkWarnsOnOversize:
    def _tree(self, tmp_path, size):
        (tmp_path / "small.py").write_text("def small():\n    pass\n")
        (tmp_path / "huge.py").write_text("x = 1\n" + ("# pad\n" * size))
        return tmp_path

    def test_walk_names_the_withheld_file(self, tmp_path):
        from jcodemunch_mcp.tools.index_folder import discover_local_files

        self._tree(tmp_path, 400)
        files, warnings, skip_counts = discover_local_files(
            tmp_path.resolve(), max_size=100
        )

        assert skip_counts["too_large"] == 1
        assert any("huge.py" in w for w in warnings), warnings
        assert [f.name for f in files] == ["small.py"]

    def test_the_counter_alone_was_the_defect(self, tmp_path):
        """Pinned deliberately: before #429 `too_large` incremented and no
        warning was emitted, so this exact assertion failed while the index
        reported success. Deleting it re-opens the silence."""
        from jcodemunch_mcp.tools.index_folder import discover_local_files

        self._tree(tmp_path, 400)
        _, warnings, skip_counts = discover_local_files(
            tmp_path.resolve(), max_size=100
        )
        assert skip_counts["too_large"] > 0
        assert warnings != []

    def test_a_clean_walk_gains_no_warning(self, tmp_path):
        from jcodemunch_mcp.tools.index_folder import discover_local_files

        self._tree(tmp_path, 5)
        _, warnings, skip_counts = discover_local_files(
            tmp_path.resolve(), max_size=10_000_000
        )
        assert skip_counts["too_large"] == 0
        assert not any("max_file_size" in w for w in warnings), warnings

    def test_the_explicit_paths_route_still_warns(self, tmp_path):
        """The route that was already correct stays correct — this fix is about
        making the two agree, not about moving the noise from one to the other."""
        from jcodemunch_mcp.tools.index_folder import resolve_explicit_paths

        self._tree(tmp_path, 400)
        _, warnings, skip_counts, _ = resolve_explicit_paths(
            tmp_path.resolve(), ["huge.py"], max_files=100, max_size=100
        )
        assert skip_counts["too_large"] == 1
        assert any("huge.py" in w for w in warnings), warnings


# ── the cap is reachable per call (#429 defect 2) ───────────────────────────


class TestMaxSizeIsReachable:
    def test_index_folder_accepts_it(self):
        from jcodemunch_mcp.tools.index_folder import index_folder

        assert "max_size" in inspect.signature(index_folder).parameters

    def test_index_repo_accepts_it(self):
        from jcodemunch_mcp.tools.index_repo import index_repo

        assert "max_size" in inspect.signature(index_repo).parameters

    def test_it_defaults_to_none_on_both(self):
        """None, not the constant: a default that is a number would freeze the
        resolution order (project config, then global, then default) at import
        time and silently outrank a project's own .jcodemunch.jsonc."""
        from jcodemunch_mcp.tools.index_folder import index_folder
        from jcodemunch_mcp.tools.index_repo import index_repo

        for fn in (index_folder, index_repo):
            assert inspect.signature(fn).parameters["max_size"].default is None

    def test_a_per_call_cap_beats_config(self, tmp_path, monkeypatch):
        """The whole point of the param: raise it for one run without touching
        a file on disk."""
        from jcodemunch_mcp import config as config_module
        from jcodemunch_mcp.tools.index_folder import discover_local_files

        monkeypatch.setitem(config_module._GLOBAL_CONFIG, "max_file_size", 50)
        (tmp_path / "mid.py").write_text("def mid():\n    return 1\n" + "# p\n" * 40)

        capped, _, counts_capped = discover_local_files(tmp_path.resolve())
        raised, _, counts_raised = discover_local_files(
            tmp_path.resolve(), max_size=10_000_000
        )

        assert counts_capped["too_large"] == 1 and capped == []
        assert counts_raised["too_large"] == 0
        assert [f.name for f in raised] == ["mid.py"]

    def test_a_non_positive_cap_is_refused_not_uncapped(self):
        """A typo must never disable the limit — same contract the resolver has
        had since .193, asserted here because a new caller now feeds it."""
        from jcodemunch_mcp.security import get_max_file_size

        with pytest.raises(ValueError):
            get_max_file_size(0)


# ── the GitHub path shares the limit and the signal ─────────────────────────


class TestRepoDiscoveryHonoursTheConfiguredCap:
    def _tree(self, *sizes):
        return [
            {"type": "blob", "path": f"f{i}.py", "size": s, "sha": f"sha{i}"}
            for i, s in enumerate(sizes)
        ]

    def _cap(self, monkeypatch, value):
        from jcodemunch_mcp import config as config_module

        monkeypatch.setitem(config_module._GLOBAL_CONFIG, "max_file_size", value)

    def test_no_hardcoded_default_survives(self):
        """The literal that made .193 and .197 no-ops here. Its absence is the
        fix; a returning constant is the regression.

        ⚠ Docstring stripped before matching — the fix's own comment NAMES the
        literal it removed, and a naive substring check fails on the very
        documentation that explains it."""
        import ast

        from jcodemunch_mcp.tools import index_repo as mod

        tree = ast.parse(inspect.getsource(mod.discover_source_files))
        ast.get_docstring(tree.body[0])  # presence check; body[0] is the def
        tree.body[0].body = [
            n
            for n in tree.body[0].body
            if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))
        ]
        code = ast.unparse(tree)

        assert "500 * 1024" not in code
        assert "get_max_file_size" in code

    def test_the_default_is_none_so_the_resolver_decides(self):
        """A numeric default here is precisely how the config route was voided:
        an int always looks like an explicit override to `get_max_file_size`."""
        from jcodemunch_mcp.tools.index_repo import discover_source_files

        params = inspect.signature(discover_source_files).parameters
        assert params["max_size"].default is None

    def test_config_now_moves_the_github_cap(self, monkeypatch):
        """The headline: .193's escape hatch and .197's project key applied to
        `index_folder` and did nothing at all for a GitHub repo."""
        from jcodemunch_mcp.tools.index_repo import discover_source_files

        entries = self._tree(100, 900_000)

        self._cap(monkeypatch, 1_000)
        files, _, _, _ = discover_source_files(entries)
        assert files == ["f0.py"]

        self._cap(monkeypatch, 1_000_000)
        files, _, _, _ = discover_source_files(entries)
        assert sorted(files) == ["f0.py", "f1.py"]

    def test_the_documented_env_var_reaches_this_path(self, monkeypatch, tmp_path):
        """⚠ Read at `load_config()` time, so a bare `os.environ` poke without a
        reload proves nothing — that is what made the env route look dead in
        #375 when it was not."""
        from jcodemunch_mcp import config as config_module
        from jcodemunch_mcp.tools.index_repo import discover_source_files

        # ⚠ Pin storage before load_config() (#437). Unpinned, it reads the
        # developer's real ~/.code-index/config.jsonc past conftest's reset, and
        # creates that file when it is absent.
        monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path / "storage"))
        monkeypatch.setenv("JCODEMUNCH_MAX_FILE_SIZE", "1000000")
        config_module.load_config()
        try:
            files, _, _, _ = discover_source_files(self._tree(100, 900_000))
            assert sorted(files) == ["f0.py", "f1.py"]
        finally:
            monkeypatch.delenv("JCODEMUNCH_MAX_FILE_SIZE", raising=False)
            config_module.load_config()

    def test_per_call_cap_wins_over_config(self, monkeypatch):
        from jcodemunch_mcp.tools.index_repo import discover_source_files

        self._cap(monkeypatch, 1_000)
        files, _, _, _ = discover_source_files(
            self._tree(100, 900_000), max_size=1_000_000
        )
        assert sorted(files) == ["f0.py", "f1.py"]

    def test_the_drop_is_observable(self, monkeypatch):
        """It used to be a bare `continue` with no counter anywhere, so the
        withheld file was invisible from every surface."""
        from jcodemunch_mcp.tools.index_repo import discover_source_files

        self._cap(monkeypatch, 1_000)
        dropped: list[str] = []
        discover_source_files(self._tree(100, 900_000), oversize_out=dropped)
        assert dropped == ["f1.py"]

    def test_the_sink_is_optional(self, monkeypatch):
        """Existing callers pass nothing and must keep working."""
        from jcodemunch_mcp.tools.index_repo import discover_source_files

        self._cap(monkeypatch, 1_000)
        files, _, _, _ = discover_source_files(self._tree(100, 900_000))
        assert files == ["f0.py"]


# ── the MCP surface (#429 defect 2, at the user's entry point) ──────────────


class TestMaxSizeReachesTheToolSchema:
    """⚠ Asserted against the built tool list, not the handler. The #429
    finding was exactly that a param the Python API accepted reached no schema,
    so a signature check alone would have passed while the defect stood."""

    def _schema(self, name):
        from jcodemunch_mcp import config as config_module
        from jcodemunch_mcp.server import _build_tools_list

        # The full schema: compact schemas hide max_size on purpose (it stays
        # honoured), and compact is the default now.
        cfg = config_module._GLOBAL_CONFIG
        before = cfg.get("compact_schemas")
        cfg["compact_schemas"] = False
        try:
            tools = _build_tools_list()
        finally:
            if before is None:
                cfg.pop("compact_schemas", None)
            else:
                cfg["compact_schemas"] = before
        for tool in tools:
            if tool.name == name:
                return tool.inputSchema
        raise AssertionError(f"{name} not in the tool list")

    @pytest.mark.parametrize("tool_name", ["index_folder", "index_repo"])
    def test_max_size_is_a_declared_property(self, tool_name):
        props = self._schema(tool_name)["properties"]
        assert "max_size" in props, sorted(props)

    @pytest.mark.parametrize("tool_name", ["index_folder", "index_repo"])
    def test_it_is_a_positive_integer(self, tool_name):
        prop = self._schema(tool_name)["properties"]["max_size"]
        assert prop["type"] == "integer"
        assert prop.get("minimum") == 1

    @pytest.mark.parametrize("tool_name", ["index_folder", "index_repo"])
    def test_it_is_not_required(self, tool_name):
        assert "max_size" not in self._schema(tool_name).get("required", [])

    @pytest.mark.parametrize("tool_name", ["index_folder", "index_repo"])
    def test_the_dispatcher_forwards_it(self, tool_name):
        """A schema property the handler drops is worse than no property."""
        import re

        from jcodemunch_mcp import server

        src = inspect.getsource(server)
        block = re.search(
            rf'name == "{tool_name}":(.*?)_result_cache_invalidate\(\)',
            src,
            re.S,
        )
        assert block, f"dispatch branch for {tool_name} not found"
        assert 'max_size=arguments.get("max_size")' in block.group(1)

    @pytest.mark.parametrize("tool_name", ["index_folder", "index_repo"])
    def test_it_is_hidden_under_compact_but_still_honoured(self, tool_name):
        """Same rule as its neighbours: an escape hatch is not worth core-tier
        schema tokens. `_DECLARED_ARG_KEYS` is snapshotted before the strip, so
        hidden must not mean reported-as-ignored."""
        from jcodemunch_mcp.server import _COMPACT_STRIP_PARAMS, _DECLARED_ARG_KEYS

        assert "max_size" in _COMPACT_STRIP_PARAMS[tool_name]
        assert "max_size" in _DECLARED_ARG_KEYS[tool_name]
