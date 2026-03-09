"""Tests for security module - Phase 1 hardening."""

import os
import sys
import pytest
from pathlib import Path
from unittest.mock import patch

from jcodemunch_mcp.security import (
    validate_path,
    is_symlink_escape,
    is_secret_file,
    is_binary_extension,
    is_binary_content,
    is_binary_file,
    safe_decode,
    should_exclude_file,
    SECRET_PATTERNS,
    BINARY_EXTENSIONS,
    DEFAULT_MAX_INDEX_FILES,
    MAX_INDEX_FILES_ENV_VAR,
    get_max_index_files,
    DEFAULT_MAX_FOLDER_FILES,
    MAX_FOLDER_FILES_ENV_VAR,
    get_max_folder_files,
    EXTRA_IGNORE_PATTERNS_ENV_VAR,
    get_extra_ignore_patterns,
)


# --- Path Traversal Prevention (S-01) ---

class TestPathValidation:
    def test_valid_path_within_root(self, tmp_path):
        child = tmp_path / "src" / "main.py"
        child.parent.mkdir(parents=True, exist_ok=True)
        child.touch()
        assert validate_path(tmp_path, child) is True

    def test_path_traversal_blocked(self, tmp_path):
        """Paths that resolve outside root are rejected."""
        evil = tmp_path / ".." / ".." / "etc" / "passwd"
        assert validate_path(tmp_path, evil) is False

    def test_root_itself_is_valid(self, tmp_path):
        assert validate_path(tmp_path, tmp_path) is True

    def test_deeply_nested_valid(self, tmp_path):
        deep = tmp_path / "a" / "b" / "c" / "d" / "file.py"
        deep.parent.mkdir(parents=True, exist_ok=True)
        deep.touch()
        assert validate_path(tmp_path, deep) is True

    def test_sibling_directory_blocked(self, tmp_path):
        """Sibling of root is outside root."""
        sibling = tmp_path.parent / "other_project" / "secret.py"
        assert validate_path(tmp_path, sibling) is False


# --- Symlink Escape Protection (S-02) ---

@pytest.mark.skipif(sys.platform == "win32", reason="Symlinks unreliable on Windows")
class TestSymlinkEscape:
    def test_symlink_inside_root_ok(self, tmp_path):
        target = tmp_path / "real.py"
        target.touch()
        link = tmp_path / "link.py"
        link.symlink_to(target)
        assert is_symlink_escape(tmp_path, link) is False

    def test_symlink_outside_root_blocked(self, tmp_path):
        outside = tmp_path.parent / "outside.py"
        outside.touch()
        link = tmp_path / "escape.py"
        link.symlink_to(outside)
        assert is_symlink_escape(tmp_path, link) is True

    def test_non_symlink_not_escape(self, tmp_path):
        regular = tmp_path / "regular.py"
        regular.touch()
        assert is_symlink_escape(tmp_path, regular) is False


# --- Secret Detection (S-04) ---

class TestSecretDetection:
    @pytest.mark.parametrize("filename", [
        ".env",
        "config.env",
        ".env.local",
        ".env.production",
        "server.pem",
        "private.key",
        "cert.p12",
        "id_rsa",
        "id_rsa.pub",
        "id_ed25519",
        "credentials.json",
        ".htpasswd",
        ".netrc",
        ".npmrc",
        ".pypirc",
        "app.secrets",
    ])
    def test_secret_files_detected(self, filename):
        assert is_secret_file(filename) is True

    @pytest.mark.parametrize("filename", [
        "main.py",
        "utils.js",
        "README.md",
        "config.yaml",
        "server.go",
        "Dockerfile",
        "package.json",
    ])
    def test_non_secret_files_pass(self, filename):
        assert is_secret_file(filename) is False

    def test_secret_in_subdirectory(self):
        assert is_secret_file("config/.env") is True
        assert is_secret_file("deploy/certs/server.pem") is True

    def test_case_insensitive(self):
        assert is_secret_file(".ENV") is True
        assert is_secret_file("Server.PEM") is True

    @pytest.mark.parametrize("path", [
        "docs/secrets-handling.md",
        "docs/internal/secrets-management.md",
        "guides/secrets-guide.rst",
        "how-to-manage-secrets.txt",
        "security/secret-rotation.adoc",
        "notebooks/secrets-demo.ipynb",
        "docs/secrets.html",
    ])
    def test_doc_files_about_secrets_not_flagged(self, path):
        """Documentation files containing 'secret' in the name must not be excluded."""
        assert is_secret_file(path) is False

    @pytest.mark.parametrize("path", [
        "config/secrets.yaml",
        "config/secrets.json",
        "src/secrets.py",
        ".secrets",
        "app.secrets",
        "my-app-secrets",
    ])
    def test_non_doc_secret_files_still_flagged(self, path):
        """Non-doc files with 'secret' in the name must still be caught."""
        assert is_secret_file(path) is True


# --- Binary Detection (S-05) ---

class TestBinaryDetection:
    @pytest.mark.parametrize("ext", [
        ".exe", ".dll", ".so", ".png", ".jpg", ".zip", ".wasm",
        ".pyc", ".class", ".pdf", ".db", ".sqlite",
    ])
    def test_binary_extensions_detected(self, ext):
        assert is_binary_extension(f"file{ext}") is True

    @pytest.mark.parametrize("ext", [
        ".py", ".js", ".ts", ".go", ".rs", ".java", ".md", ".txt",
    ])
    def test_source_extensions_not_binary(self, ext):
        assert is_binary_extension(f"file{ext}") is False

    def test_binary_content_with_null_bytes(self):
        data = b"Hello\x00World"
        assert is_binary_content(data) is True

    def test_text_content_no_null_bytes(self):
        data = b"def hello():\n    print('world')\n"
        assert is_binary_content(data) is False

    def test_empty_content_not_binary(self):
        assert is_binary_content(b"") is False

    def test_binary_file_detection(self, tmp_path):
        # Binary by extension
        bin_file = tmp_path / "image.png"
        bin_file.write_bytes(b"\x89PNG\r\n\x1a\n")
        assert is_binary_file(bin_file) is True

        # Binary by content (null bytes)
        sneaky = tmp_path / "data.py"
        sneaky.write_bytes(b"import os\x00\nprint('hi')")
        assert is_binary_file(sneaky) is True

        # Normal text file
        normal = tmp_path / "main.py"
        normal.write_text("def foo(): pass\n")
        assert is_binary_file(normal) is False

    def test_large_file_size_check(self, tmp_path):
        """Files over the size limit should be excluded."""
        big_file = tmp_path / "big.py"
        big_file.write_bytes(b"x" * (600 * 1024))  # 600KB
        reason = should_exclude_file(big_file, tmp_path, max_file_size=500 * 1024)
        assert reason == "file_too_large"


# --- Encoding Safety (S-06) ---

class TestEncodingSafety:
    def test_safe_decode_valid_utf8(self):
        data = "Hello, World!".encode("utf-8")
        assert safe_decode(data) == "Hello, World!"

    def test_safe_decode_invalid_bytes(self):
        data = b"Hello \xff\xfe World"
        result = safe_decode(data)
        assert "Hello" in result
        assert "World" in result
        # Invalid bytes replaced, not crashing
        assert "\ufffd" in result

    def test_safe_decode_latin1_bytes(self):
        """Latin-1 encoded content doesn't crash."""
        data = "café".encode("latin-1")
        result = safe_decode(data)
        # Won't decode perfectly but won't crash
        assert isinstance(result, str)


# --- Composite Filter (should_exclude_file) ---

class TestCompositeFilter:
    def test_normal_file_passes(self, tmp_path):
        f = tmp_path / "main.py"
        f.write_text("def foo(): pass\n")
        assert should_exclude_file(f, tmp_path) is None

    def test_secret_file_excluded(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("SECRET=foo\n")
        assert should_exclude_file(f, tmp_path) == "secret_file"

    def test_large_file_excluded(self, tmp_path):
        f = tmp_path / "huge.py"
        f.write_bytes(b"x" * (600 * 1024))
        assert should_exclude_file(f, tmp_path) == "file_too_large"

    def test_binary_extension_excluded(self, tmp_path):
        f = tmp_path / "image.png"
        f.write_bytes(b"\x89PNG")
        assert should_exclude_file(f, tmp_path) == "binary_extension"

    def test_checks_can_be_disabled(self, tmp_path):
        f = tmp_path / ".env"
        f.write_text("SECRET=foo\n")
        # With secret check disabled, passes (it's not binary, not too large)
        assert should_exclude_file(f, tmp_path, check_secrets=False) is None


class TestMaxIndexFilesConfig:
    def test_defaults_when_env_is_unset(self):
        with patch.dict(os.environ, {}, clear=True):
            assert get_max_index_files() == DEFAULT_MAX_INDEX_FILES

    def test_reads_env_override(self):
        with patch.dict(os.environ, {MAX_INDEX_FILES_ENV_VAR: "1234"}, clear=True):
            assert get_max_index_files() == 1234

    def test_invalid_env_falls_back_to_default(self):
        with patch.dict(os.environ, {MAX_INDEX_FILES_ENV_VAR: "invalid"}, clear=True):
            assert get_max_index_files() == DEFAULT_MAX_INDEX_FILES

    def test_non_positive_explicit_value_is_rejected(self):
        with pytest.raises(ValueError, match="positive integer"):
            get_max_index_files(0)


class TestMaxFolderFilesConfig:
    def test_default_is_lower_than_repo_default(self):
        assert DEFAULT_MAX_FOLDER_FILES < DEFAULT_MAX_INDEX_FILES

    def test_defaults_when_env_is_unset(self):
        with patch.dict(os.environ, {}, clear=True):
            assert get_max_folder_files() == DEFAULT_MAX_FOLDER_FILES

    def test_folder_specific_env_var_takes_priority(self):
        env = {MAX_FOLDER_FILES_ENV_VAR: "500", MAX_INDEX_FILES_ENV_VAR: "9999"}
        with patch.dict(os.environ, env, clear=True):
            assert get_max_folder_files() == 500

    def test_falls_back_to_legacy_env_var(self):
        env = {MAX_INDEX_FILES_ENV_VAR: "1234"}
        with patch.dict(os.environ, env, clear=True):
            assert get_max_folder_files() == 1234

    def test_invalid_folder_env_falls_back_to_legacy(self):
        env = {MAX_FOLDER_FILES_ENV_VAR: "bad", MAX_INDEX_FILES_ENV_VAR: "999"}
        with patch.dict(os.environ, env, clear=True):
            assert get_max_folder_files() == 999

    def test_both_invalid_returns_default(self):
        env = {MAX_FOLDER_FILES_ENV_VAR: "bad", MAX_INDEX_FILES_ENV_VAR: "also_bad"}
        with patch.dict(os.environ, env, clear=True):
            assert get_max_folder_files() == DEFAULT_MAX_FOLDER_FILES

    def test_explicit_override_respected(self):
        assert get_max_folder_files(42) == 42

    def test_non_positive_explicit_value_is_rejected(self):
        with pytest.raises(ValueError, match="positive integer"):
            get_max_folder_files(0)


# --- Extra Ignore Patterns (JCODEMUNCH_EXTRA_IGNORE_PATTERNS) ---

class TestGetExtraIgnorePatterns:
    def test_no_env_no_call_returns_empty(self):
        with patch.dict(os.environ, {}, clear=True):
            assert get_extra_ignore_patterns() == []

    def test_call_patterns_only(self):
        with patch.dict(os.environ, {}, clear=True):
            result = get_extra_ignore_patterns(["*.log", "tmp/"])
            assert result == ["*.log", "tmp/"]

    def test_env_comma_separated(self):
        env = {EXTRA_IGNORE_PATTERNS_ENV_VAR: "**/scrapes/**, **/images/**"}
        with patch.dict(os.environ, env, clear=True):
            result = get_extra_ignore_patterns()
            assert "**/scrapes/**" in result
            assert "**/images/**" in result

    def test_env_json_array(self):
        import json
        patterns = ["**/scrapes/**", "*.png"]
        env = {EXTRA_IGNORE_PATTERNS_ENV_VAR: json.dumps(patterns)}
        with patch.dict(os.environ, env, clear=True):
            result = get_extra_ignore_patterns()
            assert result == patterns

    def test_env_and_call_are_merged(self):
        env = {EXTRA_IGNORE_PATTERNS_ENV_VAR: "global/"}
        with patch.dict(os.environ, env, clear=True):
            result = get_extra_ignore_patterns(["local/"])
            assert "global/" in result
            assert "local/" in result

    def test_env_patterns_come_first(self):
        env = {EXTRA_IGNORE_PATTERNS_ENV_VAR: "first/"}
        with patch.dict(os.environ, env, clear=True):
            result = get_extra_ignore_patterns(["second/"])
            assert result.index("first/") < result.index("second/")

    def test_empty_env_string_returns_call_only(self):
        env = {EXTRA_IGNORE_PATTERNS_ENV_VAR: ""}
        with patch.dict(os.environ, env, clear=True):
            result = get_extra_ignore_patterns(["only/"])
            assert result == ["only/"]

    def test_invalid_json_falls_back_to_comma_split(self):
        env = {EXTRA_IGNORE_PATTERNS_ENV_VAR: "a/, b/"}
        with patch.dict(os.environ, env, clear=True):
            result = get_extra_ignore_patterns()
            assert "a/" in result
            assert "b/" in result


# --- Integration: discover_local_files with security ---

class TestDiscoverLocalFilesSecure:
    def test_excludes_secret_files(self, tmp_path):
        """Secret files are excluded from discovery."""
        from jcodemunch_mcp.tools.index_folder import discover_local_files

        (tmp_path / "main.py").write_text("def main(): pass\n")
        (tmp_path / ".env").write_text("SECRET=foo\n")
        (tmp_path / "config.pem").write_text("-----BEGIN CERTIFICATE-----\n")

        files, warnings, _ = discover_local_files(tmp_path)
        rel_paths = [f.name for f in files]
        assert "main.py" in rel_paths
        assert ".env" not in rel_paths
        assert "config.pem" not in rel_paths
        assert any("secret" in w.lower() for w in warnings)

    def test_excludes_binary_files(self, tmp_path):
        """Binary files (by content) are excluded."""
        from jcodemunch_mcp.tools.index_folder import discover_local_files

        (tmp_path / "good.py").write_text("x = 1\n")
        binary = tmp_path / "bad.py"
        binary.write_bytes(b"import os\x00\nprint('hi')")

        files, warnings, _ = discover_local_files(tmp_path)
        names = [f.name for f in files]
        assert "good.py" in names
        assert "bad.py" not in names

    def test_respects_gitignore(self, tmp_path):
        """Local .gitignore is respected."""
        from jcodemunch_mcp.tools.index_folder import discover_local_files

        (tmp_path / ".gitignore").write_text("ignored.py\n")
        (tmp_path / "kept.py").write_text("x = 1\n")
        (tmp_path / "ignored.py").write_text("y = 2\n")

        files, *_ = discover_local_files(tmp_path)
        names = [f.name for f in files]
        assert "kept.py" in names
        assert "ignored.py" not in names

    def test_extra_ignore_patterns(self, tmp_path):
        """Extra ignore patterns are applied."""
        from jcodemunch_mcp.tools.index_folder import discover_local_files

        (tmp_path / "main.py").write_text("x = 1\n")
        (tmp_path / "temp.py").write_text("y = 2\n")

        files, *_ = discover_local_files(tmp_path, extra_ignore_patterns=["temp.py"])
        names = [f.name for f in files]
        assert "main.py" in names
        assert "temp.py" not in names

    def test_respects_env_file_limit(self, tmp_path):
        """Environment override controls local folder file discovery limit."""
        from jcodemunch_mcp.tools.index_folder import discover_local_files

        for i in range(10):
            (tmp_path / f"file{i}.py").write_text(f"x = {i}\n")

        with patch.dict(os.environ, {MAX_FOLDER_FILES_ENV_VAR: "3", MAX_INDEX_FILES_ENV_VAR: "3"}, clear=False):
            files, *_ = discover_local_files(tmp_path)

        assert len(files) == 3

    def test_exact_env_file_limit_does_not_report_truncation(self, tmp_path):
        """Exact file-count matches should not be treated as truncation."""
        from jcodemunch_mcp.tools.index_folder import discover_local_files

        for i in range(3):
            (tmp_path / f"file{i}.py").write_text(f"x = {i}\n")

        with patch.dict(os.environ, {MAX_INDEX_FILES_ENV_VAR: "3"}, clear=False):
            files, _, skip_counts = discover_local_files(tmp_path)

        assert len(files) == 3
        assert skip_counts["file_limit"] == 0

    @pytest.mark.skipif(sys.platform == "win32", reason="Symlinks unreliable on Windows")
    def test_symlinks_skipped_by_default(self, tmp_path):
        """Symlinks are skipped when follow_symlinks=False."""
        from jcodemunch_mcp.tools.index_folder import discover_local_files

        real = tmp_path / "real.py"
        real.write_text("x = 1\n")
        link = tmp_path / "link.py"
        link.symlink_to(real)

        files, *_ = discover_local_files(tmp_path, follow_symlinks=False)
        names = [f.name for f in files]
        assert "real.py" in names
        assert "link.py" not in names


# --- Index repo secret filtering ---

class TestIndexRepoSecretFilter:
    def test_secret_files_filtered_in_discovery(self):
        """Secret files are excluded from remote repo file discovery."""
        from jcodemunch_mcp.tools.index_repo import discover_source_files

        tree_entries = [
            {"path": "src/main.py", "type": "blob", "size": 1000},
            {"path": ".env", "type": "blob", "size": 100},
            {"path": "config/secrets.py", "type": "blob", "size": 500},
            {"path": "certs/server.pem", "type": "blob", "size": 2000},
            {"path": "src/utils.py", "type": "blob", "size": 500},
        ]

        files, truncated = discover_source_files(tree_entries)
        assert "src/main.py" in files
        assert "src/utils.py" in files
        assert ".env" not in files
        assert "certs/server.pem" not in files
        assert truncated is False


# --- Encoding safety in index_store ---

class TestIndexStoreEncodingSafety:
    def test_get_symbol_content_handles_invalid_utf8(self, tmp_path):
        """get_symbol_content doesn't crash on invalid UTF-8."""
        from jcodemunch_mcp.storage import IndexStore
        from jcodemunch_mcp.parser import Symbol

        store = IndexStore(base_path=str(tmp_path))

        # Write content with invalid UTF-8 bytes
        content_dir = tmp_path / "test-repo"
        content_dir.mkdir()
        test_file = content_dir / "test.py"
        test_file.write_bytes(b"def foo():\n    return '\xff\xfe'\n")

        symbols = [
            Symbol(
                id="test-py::foo",
                file="test.py",
                name="foo",
                qualified_name="foo",
                kind="function",
                language="python",
                signature="def foo():",
                byte_offset=0,
                byte_length=30,
            )
        ]

        store.save_index(
            owner="test",
            name="repo",
            source_files=["test.py"],
            symbols=symbols,
            raw_files={},  # We wrote the file manually
            languages={"python": 1}
        )

        # Manually ensure the raw file has invalid bytes
        raw_file = tmp_path / "test-repo" / "test.py"
        raw_file.write_bytes(b"def foo():\n    return '\xff\xfe'\n")

        # Should not crash
        result = store.get_symbol_content("test", "repo", "test-py::foo")
        assert result is not None
        assert "def foo():" in result
