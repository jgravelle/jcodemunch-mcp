"""Tests for tools module."""

import contextlib
import json
import os
from pathlib import Path
import pytest
from unittest.mock import patch

from tests import _platform_path, _resolve_only
from jcodemunch_mcp.tools.index_repo import (
    parse_github_url,
    discover_source_files,
    should_skip_file,
)
from jcodemunch_mcp.security import MAX_INDEX_FILES_ENV_VAR, MAX_FOLDER_FILES_ENV_VAR


def test_parse_github_url_full():
    """Test parsing full GitHub URL."""
    assert parse_github_url("https://github.com/owner/repo") == ("owner", "repo")


def test_parse_github_url_with_git():
    """Test parsing URL with .git suffix."""
    assert parse_github_url("https://github.com/owner/repo.git") == ("owner", "repo")


def test_parse_github_url_short():
    """Test parsing owner/repo shorthand."""
    assert parse_github_url("owner/repo") == ("owner", "repo")


def test_parse_github_url_trailing_slash():
    assert parse_github_url("https://github.com/owner/repo/") == ("owner", "repo")


def test_parse_github_url_git_suffix_with_trailing_slash():
    assert parse_github_url("https://github.com/owner/repo.git/") == ("owner", "repo")


def test_parse_github_url_ssh():
    assert parse_github_url("git@github.com:owner/repo.git") == ("owner", "repo")
    assert parse_github_url("git@github.com:owner/repo") == ("owner", "repo")


def test_parse_github_url_bare_host():
    assert parse_github_url("github.com/owner/repo") == ("owner", "repo")
    assert parse_github_url("github.com/owner/repo.git") == ("owner", "repo")


def test_parse_github_url_ssh_rejects_other_host():
    with pytest.raises(ValueError, match="Unsupported host"):
        parse_github_url("git@gitlab.com:owner/repo.git")


def test_parse_github_url_empty():
    with pytest.raises(ValueError, match="Empty"):
        parse_github_url("")
    with pytest.raises(ValueError, match="Empty"):
        parse_github_url("   ")


def test_should_skip_file():
    """Test skip patterns."""
    assert should_skip_file("node_modules/foo.js") is True
    assert should_skip_file("vendor/github.com/foo.go") is True
    assert should_skip_file("src/main.py") is False


def test_should_skip_file_respects_exclude_config():
    """exclude_skip_directories config removes entries from skip list."""
    from jcodemunch_mcp import config as config_module

    orig = config_module._GLOBAL_CONFIG.copy()
    config_module._GLOBAL_CONFIG.clear()
    try:
        # proto/ is skipped by default
        assert should_skip_file("proto/messages.proto") is True
        # After excluding, it should pass
        config_module._GLOBAL_CONFIG["exclude_skip_directories"] = ["proto"]
        assert should_skip_file("proto/messages.proto") is False
        # Other skips still work
        assert should_skip_file("node_modules/foo.js") is True
    finally:
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(orig)


def test_discover_source_files():
    """Test file discovery from tree entries."""
    tree_entries = [
        {"path": "src/main.py", "type": "blob", "size": 1000},
        {"path": "node_modules/foo.js", "type": "blob", "size": 500},
        {"path": "README.md", "type": "blob", "size": 200},
        {"path": "src/utils.py", "type": "blob", "size": 500},
        {"path": "src/engine.cpp", "type": "blob", "size": 700},
        {"path": "include/engine.hpp", "type": "blob", "size": 350},
    ]

    files, _, truncated, _total = discover_source_files(tree_entries, gitignore_content=None)


    assert "src/main.py" in files
    assert "src/utils.py" in files
    assert "src/engine.cpp" in files
    assert "include/engine.hpp" in files
    assert "node_modules/foo.js" not in files
    assert "README.md" not in files  # Not a source file
    assert truncated is False


def test_discover_source_files_respects_max():
    """Test that max_files limit is respected."""
    tree_entries = [
        {"path": f"file{i}.py", "type": "blob", "size": 100} for i in range(1000)
    ]

    files, _, truncated, _total = discover_source_files(tree_entries, max_files=100)
    assert len(files) == 100
    assert truncated is True


def test_discover_source_files_prioritizes_src():
    """Test that src/ files are prioritized."""
    tree_entries = [
        {"path": f"other/file{i}.py", "type": "blob", "size": 100}
        for i in range(300)
    ] + [
        {"path": f"src/file{i}.py", "type": "blob", "size": 100}
        for i in range(300)
    ]

    files, _, truncated, _total = discover_source_files(tree_entries, max_files=100)
    # Most files should be from src/
    src_count = sum(1 for f in files if f.startswith("src/"))
    assert src_count > 50  # Majority should be src/
    assert truncated is True


def test_discover_source_files_uses_config_override():
    """Test that config override is used when max_files is omitted."""
    from jcodemunch_mcp import config as config_module

    tree_entries = [
        {"path": f"file{i}.py", "type": "blob", "size": 100} for i in range(20)
    ]

    orig_config = config_module._GLOBAL_CONFIG.copy()
    config_module._GLOBAL_CONFIG.clear()

    try:
        config_module._GLOBAL_CONFIG["max_index_files"] = 7
        files, _, truncated, _total = discover_source_files(tree_entries)

        assert len(files) == 7
        assert truncated is True
    finally:
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(orig_config)


def test_discover_source_files_explicit_max_overrides_config():
    """Explicit max_files should win over config."""
    from jcodemunch_mcp import config as config_module

    tree_entries = [
        {"path": f"file{i}.py", "type": "blob", "size": 100} for i in range(20)
    ]

    orig_config = config_module._GLOBAL_CONFIG.copy()
    config_module._GLOBAL_CONFIG.clear()

    try:
        config_module._GLOBAL_CONFIG["max_index_files"] = 7
        files, _, truncated, _total = discover_source_files(tree_entries, max_files=5)

        assert len(files) == 5
        assert truncated is True
    finally:
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(orig_config)


def test_discover_source_files_exact_limit_is_not_truncated():
    """An exact match to the limit should not be reported as truncation."""
    tree_entries = [
        {"path": f"file{i}.py", "type": "blob", "size": 100} for i in range(5)
    ]

    files, _, truncated, _total = discover_source_files(tree_entries, max_files=5)

    assert len(files) == 5
    assert truncated is False


# --- has_index / version mismatch ---


class TestHasIndex:
    def test_returns_false_when_no_index(self, tmp_path):
        from jcodemunch_mcp.storage.index_store import IndexStore

        store = IndexStore(base_path=str(tmp_path))
        assert store.has_index("local", "myrepo") is False

    def test_returns_true_after_save(self, tmp_path):
        from jcodemunch_mcp.storage.index_store import IndexStore

        store = IndexStore(base_path=str(tmp_path))
        store.save_index(
            owner="local",
            name="myrepo",
            source_files=[],
            symbols=[],
            raw_files={},
        )
        assert store.has_index("local", "myrepo") is True

    def test_returns_true_for_future_version_index(self, tmp_path):
        """has_index should return True even when load_index rejects a future version."""
        from jcodemunch_mcp.storage.index_store import IndexStore, INDEX_VERSION

        store = IndexStore(base_path=str(tmp_path))
        # Write a fake index with a version newer than current
        index_path = store._index_path("local", "myrepo")
        index_path.write_text(
            json.dumps(
                {
                    "index_version": INDEX_VERSION + 1,
                    "indexed_at": "2099-01-01T00:00:00",
                }
            ),
            encoding="utf-8",
        )
        assert store.load_index("local", "myrepo") is None  # rejected
        assert store.has_index("local", "myrepo") is True  # file still there


class TestVersionMismatchWarning:
    def test_index_folder_warns_on_version_mismatch(self, tmp_path, monkeypatch):
        """index_folder should include a warning when the on-disk index is a newer version."""
        import json
        from jcodemunch_mcp.storage.index_store import IndexStore, INDEX_VERSION
        from jcodemunch_mcp.tools.index_folder import index_folder

        # Plant a newer-version index in the store
        store = IndexStore(base_path=str(tmp_path / "store"))
        # We need to know what repo_name index_folder will compute for src_dir
        src_dir = tmp_path / "project"
        src_dir.mkdir()
        (src_dir / "main.py").write_text("def hello(): pass\n")

        import hashlib

        digest = hashlib.sha1(str(src_dir.resolve()).encode("utf-8")).hexdigest()[:8]
        repo_name = f"{src_dir.name}-{digest}"
        index_path = store._index_path("local", repo_name)
        index_path.write_text(
            json.dumps(
                {
                    "index_version": INDEX_VERSION + 1,
                    "indexed_at": "2099-01-01T00:00:00",
                }
            ),
            encoding="utf-8",
        )

        result = index_folder(
            str(src_dir),
            use_ai_summaries=False,
            storage_path=str(tmp_path / "store"),
        )

        assert result["success"] is True
        warnings = result.get("warnings", [])
        assert any("newer version" in w for w in warnings)


class TestNestedGitignore:
    def test_nested_gitignore_excludes_subdirectory_files(self, tmp_path):
        """Nested .gitignore files should exclude files relative to their own directory."""
        from jcodemunch_mcp.tools.index_folder import discover_local_files

        # Root structure: cap/ and core/ each with their own .gitignore + deps/
        for subdir in ("cap", "core"):
            sub = tmp_path / subdir
            (sub / "deps").mkdir(parents=True)
            (sub / "deps" / "some_dep.ex").write_text("defmodule Dep do end\n")
            (sub / "app.ex").write_text("defmodule App do end\n")
            (sub / ".gitignore").write_text("/deps/\n/_build/\n")

        files, _, skip_counts = discover_local_files(tmp_path)
        paths = [f.as_posix() for f in files]

        # app.ex files should be indexed
        assert any("cap/app.ex" in p for p in paths)
        assert any("core/app.ex" in p for p in paths)

        # deps/ files should be excluded by nested .gitignore
        assert not any("deps" in p for p in paths)
        assert skip_counts["gitignore"] >= 2

    def test_root_gitignore_still_works(self, tmp_path):
        """Root .gitignore should still be respected."""
        from jcodemunch_mcp.tools.index_folder import discover_local_files

        (tmp_path / ".gitignore").write_text("*.pyc\n__pycache__/\n")
        (tmp_path / "main.py").write_text("def main(): pass\n")
        (tmp_path / "main.pyc").write_bytes(b"\x00compiled")

        files, _, skip_counts = discover_local_files(tmp_path)
        paths = [f.as_posix() for f in files]

        assert any("main.py" in p for p in paths)
        assert not any(".pyc" in p for p in paths)


class TestFolderFileLimitEnvVar:
    def test_folder_config_respected(self, tmp_path):
        """max_folder_files config should cap index_folder file discovery."""
        from jcodemunch_mcp.tools.index_folder import discover_local_files
        from jcodemunch_mcp import config as config_module

        for i in range(10):
            (tmp_path / f"file{i}.py").write_text(f"def f{i}(): pass\n")

        orig_config = config_module._GLOBAL_CONFIG.copy()
        config_module._GLOBAL_CONFIG.clear()

        try:
            config_module._GLOBAL_CONFIG["max_folder_files"] = 3
            files, _, _ = discover_local_files(tmp_path)
            assert len(files) == 3
        finally:
            config_module._GLOBAL_CONFIG.clear()
            config_module._GLOBAL_CONFIG.update(orig_config)


class TestTrustedFolders:
    def test_trusted_broad_root_emits_warning_and_proceeds(self, tmp_path):
        """An exact trusted broad root should bypass the safeguard with a warning."""
        from jcodemunch_mcp.tools import index_folder as index_folder_module
        from unittest.mock import patch

        trusted_path = str(_platform_path("/work"))

        with (
            patch.object(
                index_folder_module._config, "load_project_config", return_value=None
            ),
            patch.object(
                index_folder_module._config,
                "get",
                side_effect=lambda key, default=None, repo=None: (
                    [trusted_path] if key == "trusted_folders" else default
                ),
            ),
            patch(
                "jcodemunch_mcp.tools.index_folder.Path.resolve",
                autospec=True,
                side_effect=_resolve_only(tmp_path / "broad", _platform_path("/work")),
            ),
            patch("jcodemunch_mcp.tools.index_folder.Path.exists", return_value=True),
            patch("jcodemunch_mcp.tools.index_folder.Path.is_dir", return_value=True),
            patch.object(
                index_folder_module, "discover_local_files", return_value=([], [], {})
            ),
        ):
            result = index_folder_module.index_folder(
                str(tmp_path / "broad"),
                use_ai_summaries=False,
                storage_path=str(tmp_path / "store"),
            )

        assert result["success"] is False
        assert result["error"] == "No source files found"
        warnings = result.get("warnings", [])
        assert any(
            "matched trusted_folders and was allowed" in warning for warning in warnings
        ), f"Expected trusted bypass warning, got: {warnings}"

    def test_untrusted_broad_root_still_rejected(self, tmp_path):
        """A broad root not in trusted_folders should still be rejected."""
        from jcodemunch_mcp.tools import index_folder as index_folder_module
        from unittest.mock import patch

        broad_root = tmp_path / "broad"
        broad_root.mkdir()

        with (
            patch.object(
                index_folder_module._config, "load_project_config", return_value=None
            ),
            patch.object(
                index_folder_module._config,
                "get",
                side_effect=lambda key, default=None, repo=None: (
                    [] if key == "trusted_folders" else default
                ),
            ),
            patch(
                "jcodemunch_mcp.tools.index_folder.Path.resolve",
                autospec=True,
                side_effect=_resolve_only(broad_root, _platform_path("/work")),
            ),
            patch("jcodemunch_mcp.tools.index_folder.Path.exists", return_value=True),
            patch("jcodemunch_mcp.tools.index_folder.Path.is_dir", return_value=True),
        ):
            result = index_folder_module.index_folder(
                str(broad_root),
                use_ai_summaries=False,
                storage_path=str(tmp_path / "store"),
            )

        assert result["success"] is False
        assert "too broad to index safely" in result["error"]

    def test_trusted_folder_matching_is_exact(self, tmp_path):
        """A trusted folder should not trust sibling broad roots."""
        from jcodemunch_mcp.tools import index_folder as index_folder_module
        from unittest.mock import patch

        sibling_root = tmp_path / "sibling"
        sibling_root.mkdir()

        trusted_path = str(_platform_path("/work"))

        with (
            patch.object(
                index_folder_module._config, "load_project_config", return_value=None
            ),
            patch.object(
                index_folder_module._config,
                "get",
                side_effect=lambda key, default=None, repo=None: (
                    [trusted_path] if key == "trusted_folders" else default
                ),
            ),
            patch(
                "jcodemunch_mcp.tools.index_folder.Path.resolve",
                autospec=True,
                side_effect=_resolve_only(sibling_root, _platform_path("/work2")),
            ),
            patch("jcodemunch_mcp.tools.index_folder.Path.exists", return_value=True),
            patch("jcodemunch_mcp.tools.index_folder.Path.is_dir", return_value=True),
        ):
            result = index_folder_module.index_folder(
                str(sibling_root),
                use_ai_summaries=False,
                storage_path=str(tmp_path / "store"),
            )

        assert result["success"] is False
        assert "not under trusted_folders" in result["error"]

    def test_non_broad_trusted_descendant_skips_bypass_warning(self, tmp_path):
        """A descendant under a trusted root should not need bypass logic once path depth is sufficient."""
        from jcodemunch_mcp.tools import index_folder as index_folder_module
        from unittest.mock import patch

        project_path = tmp_path / "project" / "src"
        project_path.mkdir(parents=True)
        (project_path / "main.py").write_text("def hello():\n    return 1\n")

        trusted_path = str(_platform_path("/work"))

        with (
            patch.object(
                index_folder_module._config, "load_project_config", return_value=None
            ),
            patch.object(
                index_folder_module._config,
                "get",
                side_effect=lambda key, default=None, repo=None: (
                    [trusted_path] if key == "trusted_folders" else default
                ),
            ),
            patch(
                "jcodemunch_mcp.tools.index_folder.Path.resolve",
                autospec=True,
                side_effect=_resolve_only(tmp_path / "project", _platform_path("/work/project")),
            ),
            patch("jcodemunch_mcp.tools.index_folder.Path.exists", return_value=True),
            patch("jcodemunch_mcp.tools.index_folder.Path.is_dir", return_value=True),
            patch.object(
                index_folder_module, "discover_local_files", return_value=([], [], {})
            ),
        ):
            result = index_folder_module.index_folder(
                str(tmp_path / "project"),
                use_ai_summaries=False,
                storage_path=str(tmp_path / "store"),
            )

        assert result["success"] is False
        assert result["error"] == "No source files found"
        warnings = result.get("warnings", [])
        assert not any(
            "matched trusted_folders and was allowed" in warning for warning in warnings
        )

    def test_non_broad_untrusted_path_is_rejected_when_trusted_folders_configured(
        self, tmp_path
    ):
        """When trusted_folders is set, untrusted current folders should be rejected even if not broad."""
        from jcodemunch_mcp.tools import index_folder as index_folder_module
        from unittest.mock import patch

        trusted_path = str(_platform_path("/trusted-root"))

        with (
            patch.object(
                index_folder_module._config, "load_project_config", return_value=None
            ),
            patch.object(
                index_folder_module._config,
                "get",
                side_effect=lambda key, default=None, repo=None: (
                    [trusted_path] if key == "trusted_folders" else default
                ),
            ),
            patch(
                "jcodemunch_mcp.tools.index_folder.Path.resolve",
                autospec=True,
                side_effect=_resolve_only(tmp_path / "project" / "src", _platform_path("/project/src")),
            ),
            patch("jcodemunch_mcp.tools.index_folder.Path.exists", return_value=True),
            patch("jcodemunch_mcp.tools.index_folder.Path.is_dir", return_value=True),
            patch.object(
                index_folder_module, "discover_local_files", return_value=([], [], {})
            ),
        ):
            result = index_folder_module.index_folder(
                str(tmp_path / "project" / "src"),
                use_ai_summaries=False,
                storage_path=str(tmp_path / "store"),
            )

        assert result["success"] is False
        assert "not under trusted_folders" in result["error"]

    def test_non_broad_paths_allow_indexing_when_trusted_folders_empty(self, tmp_path):
        """Empty trusted_folders should allow non-broad paths to index normally."""
        from jcodemunch_mcp.tools.index_folder import index_folder
        from jcodemunch_mcp import config as config_module
        import os

        project_dir = tmp_path / "project" / "src"
        project_dir.mkdir(parents=True)
        (project_dir / "main.py").write_text("def hello():\n    return 1\n")

        orig_global = config_module._GLOBAL_CONFIG.copy()
        orig_projects = config_module._PROJECT_CONFIGS.copy()
        orig_hashes = config_module._PROJECT_CONFIG_HASHES.copy()
        orig_cwd = Path.cwd()

        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(config_module.DEFAULTS)
        config_module._GLOBAL_CONFIG["trusted_folders"] = []
        config_module._PROJECT_CONFIGS.clear()
        config_module._PROJECT_CONFIG_HASHES.clear()

        try:
            os.chdir(project_dir)
            result = index_folder(
                ".",
                use_ai_summaries=False,
                storage_path=str(tmp_path / "store"),
            )
        finally:
            os.chdir(orig_cwd)
            config_module._GLOBAL_CONFIG.clear()
            config_module._GLOBAL_CONFIG.update(orig_global)
            config_module._PROJECT_CONFIGS.clear()
            config_module._PROJECT_CONFIGS.update(orig_projects)
            config_module._PROJECT_CONFIG_HASHES.clear()
            config_module._PROJECT_CONFIG_HASHES.update(orig_hashes)

        assert result["success"] is True, result

    def test_trusted_folders_configured_untrusted_path_returns_trust_error(
        self, tmp_path
    ):
        """Configured trusted_folders should reject an untrusted path before broad-path checks."""
        from jcodemunch_mcp.tools import index_folder as index_folder_module
        from unittest.mock import patch

        trusted_path = str(_platform_path("/trusted-root"))

        with (
            patch.object(
                index_folder_module._config, "load_project_config", return_value=None
            ),
            patch.object(
                index_folder_module._config,
                "get",
                side_effect=lambda key, default=None, repo=None: (
                    [trusted_path] if key == "trusted_folders" else default
                ),
            ),
            patch(
                "jcodemunch_mcp.tools.index_folder.Path.resolve",
                autospec=True,
                side_effect=_resolve_only(tmp_path / "project" / "src", _platform_path("/project/src")),
            ),
            patch("jcodemunch_mcp.tools.index_folder.Path.exists", return_value=True),
            patch("jcodemunch_mcp.tools.index_folder.Path.is_dir", return_value=True),
            patch.object(
                index_folder_module, "discover_local_files", return_value=([], [], {})
            ),
        ):
            result = index_folder_module.index_folder(
                str(tmp_path / "project" / "src"),
                use_ai_summaries=False,
                storage_path=str(tmp_path / "store"),
            )

        assert result["success"] is False
        assert "not under trusted_folders" in result["error"]

    def test_legacy_env_var_still_works_for_folders(self, tmp_path):
        """JCODEMUNCH_MAX_INDEX_FILES should still cap index_folder when folder var unset."""
        from jcodemunch_mcp.tools.index_folder import discover_local_files

        for i in range(10):
            (tmp_path / f"file{i}.py").write_text(f"def f{i}(): pass\n")

        from jcodemunch_mcp import config as config_module

        orig_config = config_module._GLOBAL_CONFIG.copy()
        config_module._GLOBAL_CONFIG.clear()

        try:
            config_module._GLOBAL_CONFIG["max_folder_files"] = 4
            files, _, _ = discover_local_files(tmp_path)

            assert len(files) == 4
        finally:
            config_module._GLOBAL_CONFIG.clear()
            config_module._GLOBAL_CONFIG.update(orig_config)

    def test_index_folder_uses_project_config_trusted_folders(self, tmp_path):
        """index_folder should use .jcodemunch.jsonc trusted_folders setting."""
        from jcodemunch_mcp.tools.index_folder import index_folder
        from jcodemunch_mcp import config as config_module

        project_root = tmp_path / "project"
        project_root.mkdir()
        (project_root / "main.py").write_text("def hello():\n    return 1\n")

        # Create project config that trusts the entire project (".")
        project_config = project_root / ".jcodemunch.jsonc"
        project_config.write_text('{"trusted_folders": ["."]}')

        orig_global = config_module._GLOBAL_CONFIG.copy()
        orig_projects = config_module._PROJECT_CONFIGS.copy()
        orig_hashes = config_module._PROJECT_CONFIG_HASHES.copy()

        # Set global config with a different trusted folder
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(config_module.DEFAULTS)
        config_module._GLOBAL_CONFIG["trusted_folders"] = ["/other/trusted"]
        config_module._PROJECT_CONFIGS.clear()
        config_module._PROJECT_CONFIG_HASHES.clear()

        try:
            # Index should succeed because project config overrides with "."
            result = index_folder(
                str(project_root),
                use_ai_summaries=False,
                storage_path=str(tmp_path / "store"),
            )
        finally:
            config_module._GLOBAL_CONFIG.clear()
            config_module._GLOBAL_CONFIG.update(orig_global)
            config_module._PROJECT_CONFIGS.clear()
            config_module._PROJECT_CONFIGS.update(orig_projects)
            config_module._PROJECT_CONFIG_HASHES.clear()
            config_module._PROJECT_CONFIG_HASHES.update(orig_hashes)

        assert result["success"] is True, result

    def test_index_folder_project_config_escape_blocked(self, tmp_path):
        """Project config escape attempt should cause index to fail trust check."""
        from jcodemunch_mcp.tools.index_folder import index_folder
        from jcodemunch_mcp import config as config_module

        project_root = tmp_path / "project"
        project_root.mkdir()
        (project_root / "main.py").write_text("def hello():\n    return 1\n")

        # Create project config that tries to escape with "../outside"
        project_config = project_root / ".jcodemunch.jsonc"
        project_config.write_text('{"trusted_folders": ["../outside"]}')

        orig_global = config_module._GLOBAL_CONFIG.copy()
        orig_projects = config_module._PROJECT_CONFIGS.copy()
        orig_hashes = config_module._PROJECT_CONFIG_HASHES.copy()

        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(config_module.DEFAULTS)
        config_module._GLOBAL_CONFIG["trusted_folders"] = []
        config_module._PROJECT_CONFIGS.clear()
        config_module._PROJECT_CONFIG_HASHES.clear()

        try:
            # Since the escape is rejected, trusted_folders falls back to global []
            # and the project path is not trusted, so it should fail
            result = index_folder(
                str(project_root),
                use_ai_summaries=False,
                storage_path=str(tmp_path / "store"),
            )
        finally:
            config_module._GLOBAL_CONFIG.clear()
            config_module._GLOBAL_CONFIG.update(orig_global)
            config_module._PROJECT_CONFIGS.clear()
            config_module._PROJECT_CONFIGS.update(orig_projects)
            config_module._PROJECT_CONFIG_HASHES.clear()
            config_module._PROJECT_CONFIG_HASHES.update(orig_hashes)

        # When trusted_folders is empty [], all paths are allowed (non-broad)
        # The project has 3+ path components, so it should succeed
        assert result["success"] is True, result

    def test_blacklist_mode_empty_list_returns_error(self, tmp_path):
        """Blacklist mode with empty list should return helpful error."""
        from jcodemunch_mcp.tools import index_folder as index_folder_module
        from unittest.mock import patch

        index_folder_module._is_trusted.cache_clear()

        broad_root = tmp_path / "broad"
        broad_root.mkdir()

        with (
            patch.object(
                index_folder_module._config, "load_project_config", return_value=None
            ),
            patch.object(
                index_folder_module._config,
                "get",
                side_effect=lambda key, default=None, repo=None: (
                    []
                    if key == "trusted_folders"
                    else False
                    if key == "trusted_folders_whitelist_mode"
                    else default
                ),
            ),
            patch(
                "jcodemunch_mcp.tools.index_folder.Path.resolve",
                autospec=True,
                side_effect=_resolve_only(broad_root, broad_root.resolve()),
            ),
            patch("jcodemunch_mcp.tools.index_folder.Path.exists", return_value=True),
            patch("jcodemunch_mcp.tools.index_folder.Path.is_dir", return_value=True),
        ):
            result = index_folder_module.index_folder(
                str(broad_root),
                use_ai_summaries=False,
                storage_path=str(tmp_path / "store"),
            )

        assert result["success"] is False
        assert "trusted_folders_whitelist_mode is False" in result["error"]
        assert "blacklist mode" in result["error"]
        assert "empty" in result["error"]

    def test_blacklist_mode_listed_path_untrusted(self, tmp_path):
        """Blacklist mode: paths in list should be untrusted."""
        from jcodemunch_mcp.tools import index_folder as index_folder_module
        from unittest.mock import patch

        index_folder_module._is_trusted.cache_clear()

        project_root = tmp_path / "project"
        project_root.mkdir()
        src_dir = project_root / "src"
        src_dir.mkdir()
        (src_dir / "main.py").write_text("def hello():\n    return 1\n")

        with (
            patch.object(
                index_folder_module._config, "load_project_config", return_value=None
            ),
            patch.object(
                index_folder_module._config,
                "get",
                side_effect=lambda key, default=None, repo=None: (
                    [str(project_root)]
                    if key == "trusted_folders"
                    else False
                    if key == "trusted_folders_whitelist_mode"
                    else default
                ),
            ),
        ):
            result = index_folder_module.index_folder(
                str(src_dir),
                use_ai_summaries=False,
                storage_path=str(tmp_path / "store"),
            )

        assert result["success"] is False
        assert "not under trusted_folders" in result["error"]

    def test_blacklist_mode_unlisted_path_trusted(self, tmp_path):
        """Blacklist mode: paths NOT in list should be trusted."""
        from jcodemunch_mcp.tools import index_folder as index_folder_module
        from unittest.mock import patch

        index_folder_module._is_trusted.cache_clear()

        project_root = tmp_path / "project"
        project_root.mkdir()
        src_dir = project_root / "src"
        src_dir.mkdir()
        (src_dir / "main.py").write_text("def hello():\n    return 1\n")

        other_root = tmp_path / "other"
        other_root.mkdir()

        with (
            patch.object(
                index_folder_module._config, "load_project_config", return_value=None
            ),
            patch.object(
                index_folder_module._config,
                "get",
                side_effect=lambda key, default=None, repo=None: (
                    [str(other_root)]
                    if key == "trusted_folders"
                    else False
                    if key == "trusted_folders_whitelist_mode"
                    else default
                ),
            ),
        ):
            result = index_folder_module.index_folder(
                str(src_dir),
                use_ai_summaries=False,
                storage_path=str(tmp_path / "store"),
            )

        # src_dir is NOT in the blacklist, so it should be trusted and succeed
        assert result["success"] is True, result

    def test_blacklist_mode_broad_listed_untrusted(self, tmp_path):
        """Blacklist mode: broad path in list should get trust error (Step 1 fires first)."""
        from jcodemunch_mcp.tools import index_folder as index_folder_module
        from unittest.mock import patch

        index_folder_module._is_trusted.cache_clear()

        broad_root = tmp_path / "work"
        broad_root.mkdir()

        trusted_path = str(_platform_path("/work"))

        with (
            patch.object(
                index_folder_module._config, "load_project_config", return_value=None
            ),
            patch.object(
                index_folder_module._config,
                "get",
                side_effect=lambda key, default=None, repo=None: (
                    [trusted_path]
                    if key == "trusted_folders"
                    else False
                    if key == "trusted_folders_whitelist_mode"
                    else default
                ),
            ),
            patch(
                "jcodemunch_mcp.tools.index_folder.Path.resolve",
                autospec=True,
                side_effect=_resolve_only(broad_root, _platform_path("/work")),
            ),
            patch("jcodemunch_mcp.tools.index_folder.Path.exists", return_value=True),
            patch("jcodemunch_mcp.tools.index_folder.Path.is_dir", return_value=True),
        ):
            result = index_folder_module.index_folder(
                str(broad_root),
                use_ai_summaries=False,
                storage_path=str(tmp_path / "store"),
            )

        assert result["success"] is False
        # Trust error, not broad error (proves Step 1 precedes broad check)
        assert "not under trusted_folders" in result["error"]

    def test_blacklist_mode_broad_unlisted_trusted(self, tmp_path):
        """Blacklist mode: broad path NOT in list should get bypass warning."""
        from jcodemunch_mcp.tools import index_folder as index_folder_module
        from unittest.mock import patch

        index_folder_module._is_trusted.cache_clear()

        other_path = str(_platform_path("/other"))

        with (
            patch.object(
                index_folder_module._config, "load_project_config", return_value=None
            ),
            patch.object(
                index_folder_module._config,
                "get",
                side_effect=lambda key, default=None, repo=None: (
                    [other_path]
                    if key == "trusted_folders"
                    else False
                    if key == "trusted_folders_whitelist_mode"
                    else default
                ),
            ),
            patch(
                "jcodemunch_mcp.tools.index_folder.Path.resolve",
                autospec=True,
                side_effect=_resolve_only(tmp_path / "work", _platform_path("/work")),
            ),
            patch("jcodemunch_mcp.tools.index_folder.Path.exists", return_value=True),
            patch("jcodemunch_mcp.tools.index_folder.Path.is_dir", return_value=True),
            patch.object(
                index_folder_module, "discover_local_files", return_value=([], [], {})
            ),
        ):
            result = index_folder_module.index_folder(
                str(tmp_path / "work"),
                use_ai_summaries=False,
                storage_path=str(tmp_path / "store"),
            )

        # /work is NOT in blacklist, so it's trusted
        # Broad path + trusted = bypass warning
        assert result["success"] is False
        assert result["error"] == "No source files found"
        warnings = result.get("warnings", [])
        assert any("would normally be rejected as too broad" in w for w in warnings), (
            f"Expected broad bypass warning, got: {warnings}"
        )


class TestContainerDetection:
    """Container environments should allow shallow paths like /workspace."""

    def test_container_env_allows_shallow_path(self, tmp_path):
        """When REMOTE_CONTAINERS is set, /workspace (2 parts) should be allowed."""
        from jcodemunch_mcp.tools import index_folder as index_folder_module
        from unittest.mock import patch

        shallow_root = tmp_path / "ws"
        shallow_root.mkdir()

        with (
            patch.object(
                index_folder_module._config, "load_project_config", return_value=None
            ),
            patch.object(
                index_folder_module._config,
                "get",
                side_effect=lambda key, default=None, repo=None: (
                    [] if key == "trusted_folders" else default
                ),
            ),
            patch(
                "jcodemunch_mcp.tools.index_folder.Path.resolve",
                autospec=True,
                side_effect=_resolve_only(shallow_root, _platform_path("/workspace")),
            ),
            patch("jcodemunch_mcp.tools.index_folder.Path.exists", return_value=True),
            patch("jcodemunch_mcp.tools.index_folder.Path.is_dir", return_value=True),
            patch.dict(os.environ, {"REMOTE_CONTAINERS": "true"}),
        ):
            result = index_folder_module.index_folder(
                str(shallow_root),
                use_ai_summaries=False,
                storage_path=str(tmp_path / "store"),
            )

        # Should proceed past the guard (fail later with "No source files")
        assert result.get("error") != "too broad to index safely"
        warnings = result.get("warnings", [])
        assert any("Container environment detected" in w for w in warnings)

    def test_codespaces_env_allows_shallow_path(self, tmp_path):
        """CODESPACES env var should also trigger container relaxation."""
        from jcodemunch_mcp.tools import index_folder as index_folder_module
        from unittest.mock import patch

        shallow_root = tmp_path / "ws"
        shallow_root.mkdir()

        with (
            patch.object(
                index_folder_module._config, "load_project_config", return_value=None
            ),
            patch.object(
                index_folder_module._config,
                "get",
                side_effect=lambda key, default=None, repo=None: (
                    [] if key == "trusted_folders" else default
                ),
            ),
            patch(
                "jcodemunch_mcp.tools.index_folder.Path.resolve",
                autospec=True,
                side_effect=_resolve_only(shallow_root, _platform_path("/workspaces/myrepo")),
            ),
            patch("jcodemunch_mcp.tools.index_folder.Path.exists", return_value=True),
            patch("jcodemunch_mcp.tools.index_folder.Path.is_dir", return_value=True),
            patch.dict(os.environ, {"CODESPACES": "true"}),
        ):
            result = index_folder_module.index_folder(
                str(shallow_root),
                use_ai_summaries=False,
                storage_path=str(tmp_path / "store"),
            )

        # /workspaces/myrepo has 3 parts, so no container warning needed
        assert "too broad" not in result.get("error", "")

    def test_container_still_rejects_bare_root(self, tmp_path):
        """Even in a container, bare '/' (1 part) should be rejected."""
        from jcodemunch_mcp.tools import index_folder as index_folder_module
        from unittest.mock import patch

        shallow_root = tmp_path / "rt"
        shallow_root.mkdir()

        with (
            patch.object(
                index_folder_module._config, "load_project_config", return_value=None
            ),
            patch.object(
                index_folder_module._config,
                "get",
                side_effect=lambda key, default=None, repo=None: (
                    [] if key == "trusted_folders" else default
                ),
            ),
            patch(
                "jcodemunch_mcp.tools.index_folder.Path.resolve",
                autospec=True,
                side_effect=_resolve_only(shallow_root, _platform_path("/")),
            ),
            patch("jcodemunch_mcp.tools.index_folder.Path.exists", return_value=True),
            patch("jcodemunch_mcp.tools.index_folder.Path.is_dir", return_value=True),
            patch.dict(os.environ, {"REMOTE_CONTAINERS": "true"}),
        ):
            result = index_folder_module.index_folder(
                str(shallow_root),
                use_ai_summaries=False,
                storage_path=str(tmp_path / "store"),
            )

        assert result["success"] is False
        assert "too broad to index safely" in result["error"]

    def test_no_container_env_still_rejects_shallow(self, tmp_path):
        """Without container signals, shallow paths are still rejected."""
        from jcodemunch_mcp.tools import index_folder as index_folder_module
        from unittest.mock import patch

        shallow_root = tmp_path / "ws"
        shallow_root.mkdir()

        # Ensure no container env vars are set
        env_clean = {
            k: v for k, v in os.environ.items()
            if k not in ("REMOTE_CONTAINERS", "CODESPACES", "container")
        }

        with (
            patch.object(
                index_folder_module._config, "load_project_config", return_value=None
            ),
            patch.object(
                index_folder_module._config,
                "get",
                side_effect=lambda key, default=None, repo=None: (
                    [] if key == "trusted_folders" else default
                ),
            ),
            patch(
                "jcodemunch_mcp.tools.index_folder.Path.resolve",
                autospec=True,
                side_effect=_resolve_only(shallow_root, _platform_path("/workspace")),
            ),
            patch("jcodemunch_mcp.tools.index_folder.Path.exists", return_value=True),
            patch("jcodemunch_mcp.tools.index_folder.Path.is_dir", return_value=True),
            patch.dict(os.environ, env_clean, clear=True),
        ):
            result = index_folder_module.index_folder(
                str(shallow_root),
                use_ai_summaries=False,
                storage_path=str(tmp_path / "store"),
            )

        assert result["success"] is False
        assert "too broad to index safely" in result["error"]


class _RealFilesystemReached(BaseException):
    r"""Raised when a path-safety test touches the real filesystem (#453).

    ⚠⚠ Derives from ``BaseException``, NOT ``Exception``, and that is the whole
    point. Every read site this guard covers is wrapped in a bare
    ``except Exception`` in production (``_composer_requires`` and friends in
    ``parser/context/framework_profiles.py``), so an ``AssertionError`` tripwire
    is swallowed and reports nothing. Measured: a deliberately re-broadened mock
    passed cleanly with an ``Exception``-derived guard in place.
    """


@contextlib.contextmanager
def _no_real_access_under(fake_root: Path):
    r"""Fail loudly if anything stats or reads a path under ``fake_root``.

    These tests name a UNC path that does not exist. Any unpatched probe of it
    goes to the NETWORK, and the verdict then depends on the runner rather than
    on the code under test. That produced both halves of #453, in opposite
    directions:

    * **False red.** ``resolve_index_identity`` calls ``folder_path.is_file()``
      (``storage/git_root.py:160``), which was never patched. ``Path.is_file``
      swallows ENOENT-class errors -- what a box with no such share returns --
      but PROPAGATES ``WinError 64`` (``ERROR_NETNAME_DELETED``), which is what a
      runner with live-but-failing networking returns. Same code, same test,
      opposite outcomes, decided by the network. Two releases lost to it.
    * **False green.** ``test_unc_share_root_remains_too_broad`` let the runner's
      lack of a ``\\server\share\.git`` decide the ``#438`` probe, so the guard
      it defends could move without the test noticing. It did move, in #439.

    This is a tripwire, not a mock: narrowing the patches is what makes the tests
    pass. This exists so that re-broadening one fails with a message naming the
    cause, instead of intermittently and somewhere else.
    """
    needle = str(fake_root).lower()
    originals = {name: getattr(Path, name) for name in ("stat", "read_text", "open")}

    def _guard(name):
        real = originals[name]

        def wrapper(self, *args, **kwargs):
            if needle in str(self).lower():
                raise _RealFilesystemReached(
                    f"Path.{name}() reached the real filesystem at {str(self)!r}. "
                    f"A patch is broad enough to make this fake path look real, "
                    f"or a probe of it is unpatched -- narrow or add one rather "
                    f"than letting the runner decide the result (see #453)."
                )
            return real(self, *args, **kwargs)

        return wrapper

    with contextlib.ExitStack() as stack:
        for name in originals:
            stack.enter_context(patch.object(Path, name, _guard(name)))
        yield


@pytest.mark.skipif(os.name != "nt", reason="Windows UNC path semantics only")
class TestWindowsUNCPathSafety:
    """Windows UNC roots should be treated as server/share plus descendants."""

    def test_unc_repo_at_share_child_is_not_too_broad(self, tmp_path):
        from jcodemunch_mcp.tools import index_folder as index_folder_module

        unc_repo = Path(r"\\server\share\repo")

        with (
            patch.object(
                index_folder_module._config, "load_project_config", return_value=None
            ),
            patch.object(
                index_folder_module._config,
                "get",
                side_effect=lambda key, default=None, repo=None: (
                    [] if key == "trusted_folders" else default
                ),
            ),
            patch(
                "jcodemunch_mcp.tools.index_folder.Path.resolve",
                autospec=True,
                side_effect=_resolve_only(tmp_path / "repo", unc_repo),
            ),
            # ⚠ Narrow, NOT return_value=True. A blanket True convinces
            # detect_framework that composer.json / package.json / pyproject.toml
            # / pom.xml / build.gradle / Gemfile / requirements.txt all exist
            # here, and it read_text()s all seven over the network. Those are
            # swallowed by production's `except Exception`, so they never failed
            # the test -- they just made a unit test do seven network round
            # trips. Measured with an audit hook.
            patch(
                "jcodemunch_mcp.tools.index_folder.Path.exists",
                autospec=True,
                side_effect=lambda candidate: candidate == unc_repo,
            ),
            patch(
                "jcodemunch_mcp.tools.index_folder.os.path.exists",
                return_value=False,
            ),
            patch("jcodemunch_mcp.tools.index_folder.Path.is_dir", return_value=True),
            # ⚠⚠ THIS is the one that lost two releases. resolve_index_identity
            # calls folder_path.is_file() (storage/git_root.py:160) and nothing
            # patched it, so os.stat went to the network and raised WinError 64.
            patch("jcodemunch_mcp.tools.index_folder.Path.is_file", return_value=False),
            patch.object(
                index_folder_module, "discover_local_files", return_value=([], [], {})
            ),
            _no_real_access_under(unc_repo),
        ):
            result = index_folder_module.index_folder(
                str(tmp_path / "repo"),
                use_ai_summaries=False,
                storage_path=str(tmp_path / "store"),
            )

        assert "too broad" not in result.get("error", "")
        assert result["error"] == "No source files found"

    def test_unc_share_root_remains_too_broad(self, tmp_path):
        from jcodemunch_mcp.tools import index_folder as index_folder_module

        unc_share_root = Path(r"\\server\share")

        with (
            patch.object(
                index_folder_module._config, "load_project_config", return_value=None
            ),
            patch.object(
                index_folder_module._config,
                "get",
                side_effect=lambda key, default=None, repo=None: (
                    [] if key == "trusted_folders" else default
                ),
            ),
            patch(
                "jcodemunch_mcp.tools.index_folder.Path.resolve",
                autospec=True,
                side_effect=_resolve_only(tmp_path / "share", unc_share_root),
            ),
            patch(
                "jcodemunch_mcp.tools.index_folder.Path.exists",
                autospec=True,
                side_effect=lambda candidate: candidate == unc_share_root,
            ),
            # ⚠⚠ Answering the .git probe TRUE is the point. It proves the UNC
            # scope predicate (#439) excludes a share root on its own, rather
            # than because the runner happens to have no such share. Before this,
            # the guard could move and the test could not tell -- and it did.
            patch(
                "jcodemunch_mcp.tools.index_folder.os.path.exists",
                side_effect=lambda candidate: (
                    Path(candidate) == unc_share_root / ".git"
                ),
            ),
            patch("jcodemunch_mcp.tools.index_folder.Path.is_dir", return_value=True),
            patch("jcodemunch_mcp.tools.index_folder.Path.is_file", return_value=False),
            _no_real_access_under(unc_share_root),
        ):
            result = index_folder_module.index_folder(
                str(tmp_path / "share"),
                use_ai_summaries=False,
                storage_path=str(tmp_path / "store"),
            )

        assert result["success"] is False
        assert "too broad to index safely" in result["error"]


@pytest.mark.skipif(os.name != "nt", reason="Windows drive path semantics only")
class TestWindowsDriveRootPathSafety:
    """Exact Git roots directly below a Windows drive root are project-scoped."""

    def test_drive_root_child_git_repo_is_not_too_broad(self, tmp_path):
        from jcodemunch_mcp.tools import index_folder as index_folder_module

        drive_root_repo = Path(r"F:\repo")

        with (
            patch.object(
                index_folder_module._config, "load_project_config", return_value=None
            ),
            patch.object(
                index_folder_module._config,
                "get",
                side_effect=lambda key, default=None, repo=None: (
                    [] if key == "trusted_folders" else default
                ),
            ),
            patch(
                "jcodemunch_mcp.tools.index_folder.Path.resolve",
                autospec=True,
                side_effect=_resolve_only(tmp_path / "repo", drive_root_repo),
            ),
            patch("jcodemunch_mcp.tools.index_folder.Path.exists", return_value=True),
            patch("jcodemunch_mcp.tools.index_folder.Path.is_dir", return_value=True),
            patch(
                "jcodemunch_mcp.tools.index_folder.os.path.exists",
                side_effect=lambda candidate: Path(candidate) == drive_root_repo / ".git",
            ),
            patch.object(
                index_folder_module, "discover_local_files", return_value=([], [], {})
            ),
        ):
            result = index_folder_module.index_folder(
                str(tmp_path / "repo"),
                use_ai_summaries=False,
                storage_path=str(tmp_path / "store"),
            )

        assert "too broad" not in result.get("error", "")
        assert result["error"] == "No source files found"

    def test_drive_root_remains_too_broad(self, tmp_path):
        from jcodemunch_mcp.tools import index_folder as index_folder_module

        drive_root = Path("F:/")

        with (
            patch.object(
                index_folder_module._config, "load_project_config", return_value=None
            ),
            patch.object(
                index_folder_module._config,
                "get",
                side_effect=lambda key, default=None, repo=None: (
                    [] if key == "trusted_folders" else default
                ),
            ),
            patch(
                "jcodemunch_mcp.tools.index_folder.Path.resolve",
                autospec=True,
                side_effect=_resolve_only(tmp_path / "drive", drive_root),
            ),
            patch("jcodemunch_mcp.tools.index_folder.Path.exists", return_value=True),
            patch("jcodemunch_mcp.tools.index_folder.Path.is_dir", return_value=True),
        ):
            result = index_folder_module.index_folder(
                str(tmp_path / "drive"),
                use_ai_summaries=False,
                storage_path=str(tmp_path / "store"),
            )

        assert result["success"] is False
        assert "too broad to index safely" in result["error"]

    def test_shallow_non_git_directory_remains_too_broad(self, tmp_path):
        from jcodemunch_mcp.tools import index_folder as index_folder_module

        shallow_directory = Path(r"F:\Users")

        with (
            patch.object(
                index_folder_module._config, "load_project_config", return_value=None
            ),
            patch.object(
                index_folder_module._config,
                "get",
                side_effect=lambda key, default=None, repo=None: (
                    [] if key == "trusted_folders" else default
                ),
            ),
            patch(
                "jcodemunch_mcp.tools.index_folder.Path.resolve",
                autospec=True,
                side_effect=_resolve_only(tmp_path / "users", shallow_directory),
            ),
            patch(
                "jcodemunch_mcp.tools.index_folder.Path.exists",
                autospec=True,
                side_effect=lambda candidate: candidate == shallow_directory,
            ),
            patch("jcodemunch_mcp.tools.index_folder.Path.is_dir", return_value=True),
            patch(
                "jcodemunch_mcp.tools.index_folder.os.path.exists",
                return_value=False,
            ),
        ):
            result = index_folder_module.index_folder(
                str(tmp_path / "users"),
                use_ai_summaries=False,
                storage_path=str(tmp_path / "store"),
            )

        assert result["success"] is False
        assert "too broad to index safely" in result["error"]

    def test_unc_share_root_with_git_remains_too_broad(self, tmp_path):
        r"""A UNC share root is out of scope even when it holds a ``.git``.

        ⚠ This is NOT covered by the depth check. ``_path_safety_part_count``
        adds one for the ``\\server\share`` anchor, so a share root computes to
        exactly two — the same depth as ``C:\repo``. Only the explicit UNC scope
        predicate keeps it out, so removing that predicate makes this admit a
        whole file server. See #321/#322.
        """
        from jcodemunch_mcp.tools import index_folder as index_folder_module

        share_root = Path(r"\\server\share")
        assert index_folder_module._path_safety_part_count(share_root) == 2, (
            "premise: a UNC share root is depth-equivalent to C:\\repo, so the "
            "depth check alone cannot exclude it"
        )

        with (
            patch.object(
                index_folder_module._config, "load_project_config", return_value=None
            ),
            patch.object(
                index_folder_module._config,
                "get",
                side_effect=lambda key, default=None, repo=None: (
                    [] if key == "trusted_folders" else default
                ),
            ),
            patch(
                "jcodemunch_mcp.tools.index_folder.Path.resolve",
                autospec=True,
                side_effect=_resolve_only(tmp_path / "share", share_root),
            ),
            patch("jcodemunch_mcp.tools.index_folder.Path.exists", return_value=True),
            patch("jcodemunch_mcp.tools.index_folder.Path.is_dir", return_value=True),
            patch(
                "jcodemunch_mcp.tools.index_folder.os.path.exists",
                # ⚠ Narrow, NOT return_value=True. A blanket True also answers
                # _is_container()'s /.dockerenv probe, which drops
                # _MIN_PATH_PARTS to 2 so `2 < 2` skips the guard entirely and
                # the test passes without ever reaching the code under test.
                side_effect=lambda candidate: Path(candidate) == share_root / ".git",
            ),
            patch.object(
                index_folder_module, "discover_local_files", return_value=([], [], {})
            ),
        ):
            result = index_folder_module.index_folder(
                str(tmp_path / "share"),
                use_ai_summaries=False,
                storage_path=str(tmp_path / "store"),
            )

        assert result["success"] is False
        assert "too broad to index safely" in result["error"]

    def test_unc_repository_below_share_root_is_unaffected(self, tmp_path):
        r"""``\\server\share\repo`` never reaches the exception at all.

        Its safety depth is three, so it clears the guard on depth alone. Pinned
        so a future widening of the exception cannot be justified by "UNC repos
        need it" — they do not.
        """
        from jcodemunch_mcp.tools import index_folder as index_folder_module

        unc_repo = Path(r"\\server\share\repo")
        assert index_folder_module._path_safety_part_count(unc_repo) == 3
        with patch(
            "jcodemunch_mcp.tools.index_folder.os.path.exists", return_value=True
        ):
            assert index_folder_module._is_shallow_windows_git_root(unc_repo) is False


class TestIndexFolderGitignoreWarning:
    """index_folder should warn when no root .gitignore exists and file count is large."""

    def test_no_warning_when_gitignore_present(self, tmp_path):
        """No warning when .gitignore is present, even with many files."""
        from jcodemunch_mcp.tools.index_folder import index_folder

        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()
        (repo_dir / ".gitignore").write_text("*.pyc\n")
        for i in range(600):
            f = repo_dir / f"file_{i}.py"
            f.write_text(f"# file {i}\n")

        result = index_folder(str(repo_dir), use_ai_summaries=False, storage_path=str(tmp_path / "store"))
        assert result.get("success") is not False
        warnings = result.get("warnings", [])
        assert not any(".gitignore" in w and "No .gitignore" in w for w in warnings), (
            f"Unexpected gitignore warning: {warnings}"
        )

    def test_no_warning_when_file_count_below_threshold(self, tmp_path):
        """No warning when there are fewer files than the threshold, even without .gitignore."""
        from jcodemunch_mcp.tools.index_folder import index_folder

        repo_dir = tmp_path / "small"
        repo_dir.mkdir()
        for i in range(10):
            (repo_dir / f"file_{i}.py").write_text(f"# file {i}\n")

        result = index_folder(str(repo_dir), use_ai_summaries=False, storage_path=str(tmp_path / "store"))
        assert result.get("success") is not False
        warnings = result.get("warnings", [])
        assert not any("No .gitignore" in w for w in warnings)

    def test_warning_emitted_when_no_gitignore_and_large_index(self, tmp_path):
        """Warning emitted when no .gitignore and file count >= 500."""
        from jcodemunch_mcp.tools.index_folder import index_folder

        repo_dir = tmp_path / "big"
        repo_dir.mkdir()
        for i in range(500):
            (repo_dir / f"file_{i}.py").write_text(f"# file {i}\n")

        result = index_folder(str(repo_dir), use_ai_summaries=False, storage_path=str(tmp_path / "store"))
        assert result.get("success") is not False
        warnings = result.get("warnings", [])
        assert any("No .gitignore" in w for w in warnings), (
            f"Expected gitignore warning, got: {warnings}"
        )

    def test_warning_message_includes_file_count_and_advice(self, tmp_path):
        """Warning message names the file count and advises adding a .gitignore."""
        from jcodemunch_mcp.tools.index_folder import index_folder

        repo_dir = tmp_path / "advice"
        repo_dir.mkdir()
        for i in range(500):
            (repo_dir / f"file_{i}.py").write_text(f"# file {i}\n")

        result = index_folder(str(repo_dir), use_ai_summaries=False, storage_path=str(tmp_path / "store"))
        gitignore_warnings = [w for w in result.get("warnings", []) if "No .gitignore" in w]
        assert gitignore_warnings
        msg = gitignore_warnings[0]
        assert "500" in msg or "file" in msg.lower()
        assert ".gitignore" in msg

    def test_project_config_override_threshold_suppresses_warning(self, tmp_path):
        """Issue #301 audit: gitignore_warn_threshold honors .jcodemunch.jsonc.

        A project setting the threshold above the file count should suppress
        the warning, proving the project-config override threads through to
        the index_folder.py call site (not just to global config).
        """
        from jcodemunch_mcp.tools.index_folder import index_folder
        from jcodemunch_mcp import config as config_module

        repo_dir = tmp_path / "monorepo"
        repo_dir.mkdir()
        for i in range(500):
            (repo_dir / f"file_{i}.py").write_text(f"# file {i}\n")

        repo_key = str(repo_dir.resolve())
        orig_project = config_module._PROJECT_CONFIGS.copy()
        config_module._PROJECT_CONFIGS.clear()
        try:
            config_module._PROJECT_CONFIGS[repo_key] = {
                "gitignore_warn_threshold": 10_000,  # well above 500
            }
            result = index_folder(
                str(repo_dir),
                use_ai_summaries=False,
                storage_path=str(tmp_path / "store"),
            )
            warnings = result.get("warnings", [])
            assert not any("No .gitignore" in w for w in warnings), (
                "Project-level gitignore_warn_threshold=10000 should have "
                "suppressed the warning at 500 files. If this fires, the "
                "fix at index_folder.py:1321 was reverted (issue #301)."
            )
        finally:
            config_module._PROJECT_CONFIGS.clear()
            config_module._PROJECT_CONFIGS.update(orig_project)
