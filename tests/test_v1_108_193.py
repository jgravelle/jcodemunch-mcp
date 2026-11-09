"""v1.108.193 — the size cap can be raised, and a withheld file blocks absence.

Both reported by @dkiaulakis (jcodemunch-mcp #375 thread, by mail 2026-07-27),
measured on 1.108.188.

The ask was small: give `MAX_FILE_SIZE` the env override its two neighbours in
`security.py` already have. The finding underneath it is not small. A file
refused for size is counted as an ordinary structural exclusion, so the coverage
contract shipped in 1.108.176 calls the index complete — correctly by its own
definition — while the exact file an agent asked about is missing. In his words:

    Size is the one exclusion reason where the file is real, current, and wanted.

That is the same disease the 1.108.171-.176 releases were about, arriving
through a different door: "I never learned that file" rendering as "that file
does not exist."
"""

import pytest

from jcodemunch_mcp import handoff
from jcodemunch_mcp.tools.index_folder import (
    WITHHELD_SKIP_REASONS,
    _coverage_report,
)


# ── the escape hatch ────────────────────────────────────────────────────────


class TestMaxFileSizeIsMovable:
    """Parity with the two limits three lines below it in security.py."""

    def test_resolver_exists_alongside_its_siblings(self):
        from jcodemunch_mcp import security

        for name in ("get_max_file_size", "get_max_index_files", "get_max_folder_files"):
            assert hasattr(security, name), name

    def test_env_var_is_registered_like_its_siblings(self):
        from jcodemunch_mcp.config import ENV_VAR_MAPPING

        assert ENV_VAR_MAPPING["JCODEMUNCH_MAX_FILE_SIZE"] == "max_file_size"

    def test_explicit_argument_wins(self):
        from jcodemunch_mcp.security import get_max_file_size

        assert get_max_file_size(1_000_000) == 1_000_000

    def test_non_positive_argument_is_rejected(self):
        from jcodemunch_mcp.security import get_max_file_size

        with pytest.raises(ValueError):
            get_max_file_size(0)
        with pytest.raises(ValueError):
            get_max_file_size(-1)

    def test_default_is_unchanged(self):
        """The ask was an escape hatch, NOT a higher default, and it stays that
        way: 500KB protects the common case from a parse worth less than it
        costs."""
        from jcodemunch_mcp.security import DEFAULT_MAX_FILE_SIZE, get_max_file_size

        assert DEFAULT_MAX_FILE_SIZE == 500 * 1024
        assert get_max_file_size() == DEFAULT_MAX_FILE_SIZE

    def test_index_folder_resolves_rather_than_hardcoding(self, monkeypatch):
        """The constant used to be passed straight through, which is why no
        route existed at all.

        ⚠ This assertion was rewritten in v1.108.194. It used to grep the module
        source for the literal ``max_size=get_max_file_size()``, which pinned a
        SPELLING rather than the behaviour: hoisting the identical call into a
        local (so the fast path could reuse the value without calling twice)
        broke it while the resolver was still consulted on every walk. A source
        grep cannot tell a refactor from a regression, so it fails on the one
        that is safe. What matters is that a changed cap reaches the walk.
        """
        from jcodemunch_mcp import security
        from jcodemunch_mcp.tools import index_folder as mod

        seen: list[int] = []
        real = mod.get_max_file_size

        def spy(*a, **k):
            v = real(*a, **k)
            seen.append(v)
            return v

        monkeypatch.setattr(mod, "get_max_file_size", spy)

        # `security._config` is the config MODULE, not a dict — the resolver
        # calls `_config.get(...)`. Patch that function, not an item.
        real_get = security._config.get

        def fake_get(key, default=None, *a, **k):
            if key == "max_file_size":
                return 1_234_567
            return real_get(key, default, *a, **k)

        monkeypatch.setattr(security._config, "get", fake_get)

        import tempfile
        from pathlib import Path

        d = Path(tempfile.mkdtemp())
        (d / "a.py").write_text("x = 1\n", encoding="utf-8")
        mod.index_folder(
            str(d), use_ai_summaries=False, storage_path=tempfile.mkdtemp()
        )

        assert seen, "index_folder never consulted the max_file_size resolver"
        assert 1_234_567 in seen, (
            "index_folder resolved a cap that ignored config; the route exists "
            f"but does not reach the walk. resolved={seen}"
        )


class TestProjectConfigReachesTheWalk:
    """v1.108.197 (@amarakramali, #390).

    v1.108.193 gave the size cap a config key. v1.108.194 made every walk
    resolve it on entry. Both left the same hole: the resolver read GLOBAL
    config only, so a cap set in the project's own `.jcodemunch.jsonc` — the
    file the docs point at, parsed and cached one call earlier by
    `load_project_config` — was never consulted. The failure is silent. No
    unknown-key warning, no error; the file is simply reported absent.
    """

    @pytest.mark.parametrize(
        "paths",
        [None, ["large.py"]],
        ids=["full-walk", "explicit-path"],
    )
    def test_project_config_moves_the_cap_on_every_discovery_route(
        self, tmp_path, paths
    ):
        """Both entry points, because a cap honoured by one and not the other is
        the shape of #375 all over again: the file appears, then vanishes."""
        from jcodemunch_mcp import config as config_module
        from jcodemunch_mcp.security import DEFAULT_MAX_FILE_SIZE
        from jcodemunch_mcp.tools.index_folder import index_folder

        project = tmp_path / "project"
        project.mkdir()
        source = project / "large.py"
        source.write_text(
            "def marker():\n    return 1\n#" + ("x" * (DEFAULT_MAX_FILE_SIZE + 1)),
            encoding="utf-8",
        )
        (project / ".jcodemunch.jsonc").write_text(
            f'{{"max_file_size": {source.stat().st_size}}}',
            encoding="utf-8",
        )

        try:
            result = index_folder(
                str(project),
                use_ai_summaries=False,
                storage_path=str(tmp_path / "store"),
                incremental=False,
                context_providers=False,
                paths=paths,
            )
        finally:
            config_module.invalidate_project_config_cache(str(project))

        assert result["success"] is True, result
        assert result["file_count"] == 1, result

    @pytest.mark.parametrize(
        "resolver,key,value",
        [
            ("get_max_file_size", "max_file_size", 1_234_567),
            ("get_max_index_files", "max_index_files", 4_321),
            ("get_max_folder_files", "max_folder_files", 1_234),
        ],
    )
    def test_all_three_limit_resolvers_take_a_repo(
        self, monkeypatch, resolver, key, value
    ):
        """Parity is the point. All three limits are documented per-project
        keys; fixing one leaves the other two lying the same way."""
        from jcodemunch_mcp import security

        real_get = security._config.get
        seen: list[str | None] = []

        def fake_get(k, default=None, repo=None, *a, **kw):
            if k == key:
                seen.append(repo)
                return value if repo == "R" else default
            return real_get(k, default, repo=repo, *a, **kw)

        monkeypatch.setattr(security._config, "get", fake_get)

        assert getattr(security, resolver)(repo="R") == value
        assert seen == ["R"], f"{resolver} dropped repo= on the way to config"

    def test_a_repo_without_a_project_file_mirrors_global(self, tmp_path):
        """The trap the fix above walked into.

        `load_project_config` seeded `_PROJECT_CONFIGS[repo]` from global on
        first sight and then never rewrote it, so a repo with NO
        `.jcodemunch.jsonc` served a snapshot of whatever global was when it was
        first indexed. Invisible while repo-scoped reads were rare; a wrong
        answer once the limit resolvers started taking `repo=`.
        """
        from jcodemunch_mcp import config as config_module

        project = tmp_path / "plain"
        project.mkdir()
        repo_key = str(project.resolve())
        original = config_module._GLOBAL_CONFIG.get("max_file_size")
        try:
            config_module._GLOBAL_CONFIG["max_file_size"] = 111
            config_module.load_project_config(repo_key)
            assert config_module.get("max_file_size", repo=repo_key) == 111

            config_module._GLOBAL_CONFIG["max_file_size"] = 222
            config_module.load_project_config(repo_key)
            assert config_module.get("max_file_size", repo=repo_key) == 222, (
                "a repo with no project config froze its copy of global"
            )
        finally:
            if original is None:
                config_module._GLOBAL_CONFIG.pop("max_file_size", None)
            else:
                config_module._GLOBAL_CONFIG["max_file_size"] = original
            config_module.invalidate_project_config_cache(repo_key)

    def test_refresh_does_not_clobber_a_config_it_did_not_write(self, tmp_path):
        """The bound on the refresh above, and the failure it actually caused.

        The first cut refreshed UNCONDITIONALLY, which silently destroyed any
        entry installed by someone else for a repo with no `.jcodemunch.jsonc`
        — caught by `test_tools.py::...gitignore_warn_threshold...` (#301), one
        real failure inside twelve known-env noise. "Refresh" means refresh the
        mirrors we wrote; an entry we did not write is not ours to overwrite.
        """
        from jcodemunch_mcp import config as config_module

        project = tmp_path / "in_memory_only"
        project.mkdir()
        repo_key = str(project.resolve())
        try:
            config_module._PROJECT_CONFIGS[repo_key] = {
                "gitignore_warn_threshold": 10_000,
            }
            config_module.load_project_config(repo_key)
            assert (
                config_module.get("gitignore_warn_threshold", repo=repo_key) == 10_000
            ), "load_project_config overwrote a caller-installed project config"
        finally:
            config_module.invalidate_project_config_cache(repo_key)


# ── the finding underneath it ───────────────────────────────────────────────


def _cov(skips, indexed=10, accepted=10):
    return _coverage_report(skips, indexed, 0, files_accepted=accepted)


class TestWithheldFilesBreakCompleteness:
    def test_an_oversize_file_makes_the_corpus_incomplete(self):
        """The defect, stated as the assertion that used to fail.

        `too_large` is refused during DISCOVERY, so the file never reaches
        `files_accepted` and the reconciliation balances perfectly. Coverage
        called that complete.
        """
        assert _cov({"too_large": 1})["complete"] is False

    def test_structural_exclusions_still_complete(self):
        """Non-vacuity, and the boundary that keeps this signal useful.

        A `.png` is `binary`, a vendored tree is `gitignore`, a lockfile is
        `wrong_extension`. Those are the corpus being DEFINED. If they blocked
        absence too, no real repo could ever prove absence and the signal would
        be worthless.
        """
        cov = _cov({"binary": 40, "gitignore": 900, "wrong_extension": 120, "secret": 2})
        assert cov["complete"] is True
        assert "withheld" not in cov

    def test_withheld_is_reported_separately_from_excluded(self):
        cov = _cov({"too_large": 2, "binary": 5})
        assert cov["withheld"] == {"too_large": 2}
        assert cov["skip_counts"] == {"too_large": 2, "binary": 5}

    def test_the_withheld_reasons(self):
        # `parse_failed` joined in L-316: a file whose parse ran past the
        # budget or raised is real, current and wanted, and its symbols are
        # missing because of our parser. Exact equality stays, so a reason
        # added or dropped by accident still fails here.
        assert WITHHELD_SKIP_REASONS == {"too_large", "file_limit", "unreadable", "parse_failed"}

    def test_file_limit_counts_too(self):
        assert _cov({"file_limit": 300})["complete"] is False

    def test_unknown_coverage_is_still_unknown(self):
        """An index with no files_accepted reports None, never True or False."""
        assert _coverage_report({"too_large": 1}, 10, 0)["complete"] is None


class TestAbsenceIsRefusedOverAWithheldCorpus:
    def test_refusal_names_the_cause_and_the_remedy(self):
        record = {
            "state": "absent",
            "channels": {"lexical": "ok", "index": "fresh"},
            "coverage": {"complete": False, "withheld": {"too_large": 1}},
        }
        reason = handoff.absence_refusal(record)
        assert reason is not None
        assert "too_large" in reason
        assert "re-index" in reason
        # It must NOT be the generic partial message: a reader told "the index
        # did not cover the whole tree" goes looking at their repo, when the
        # actual remedy is one setting.
        assert "did not cover the whole tree" not in reason

    def test_a_clean_corpus_still_proves_absence(self):
        record = {
            "state": "absent",
            "channels": {"lexical": "ok", "index": "fresh"},
            "coverage": {"complete": True, "skip_counts": {"binary": 3}},
        }
        assert handoff.absence_refusal(record) is None

    def test_structural_exclusions_alone_do_not_refuse(self):
        record = {
            "state": "absent",
            "channels": {"lexical": "ok", "index": "fresh"},
            "coverage": {"complete": True, "skip_counts": {"gitignore": 4000}},
        }
        assert handoff.absence_refusal(record) is None
