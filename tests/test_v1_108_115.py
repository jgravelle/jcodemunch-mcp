"""v1.108.115 — new installs default to the token-lean `counter` tool surface,
while existing installs are never silently collapsed on a package update.

The safety contract (the whole point of the migration):
  * genuinely first-ever install            -> tool_surface="counter"
  * existing config lacking the key          -> stays "full"
  * existing install with no config file     -> stays "full"
  * explicit user choice                     -> always honored
  * upgrade_config back-injection            -> only the commented (inactive)
                                                line, so effective surface = full
"""
import os

import pytest

from jcodemunch_mcp import config as C
from jcodemunch_mcp import server as S


def _effective(storage_dir):
    """Load config from storage_dir and return (server surface, config value)."""
    C._GLOBAL_CONFIG = None
    os.environ.pop("JCODEMUNCH_TOOL_SURFACE", None)
    C.load_config(str(storage_dir))
    return S._effective_surface(), C.get("tool_surface")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("JCODEMUNCH_TOOL_SURFACE", raising=False)
    yield
    # restore a valid global so atexit callbacks (which call config.get) don't
    # trip on a None global left behind by _effective()
    C._GLOBAL_CONFIG = dict(C.DEFAULTS)


class TestSurfaceDefaultMigration:
    def test_registered_in_defaults_and_types(self):
        assert C.DEFAULTS["tool_surface"] == "full"
        assert C.CONFIG_TYPES["tool_surface"] is str

    def test_fresh_install_defaults_to_counter(self, tmp_path):
        surface, cfg = _effective(tmp_path)
        assert surface == "counter"
        assert cfg == "counter"
        # written durably + actively (survives the next run)
        text = (tmp_path / "config.jsonc").read_text(encoding="utf-8")
        assert '"tool_surface": "counter",' in text
        # and it persists across a second load (durability)
        surface2, _ = _effective(tmp_path)
        assert surface2 == "counter"

    def test_existing_config_without_key_stays_full(self, tmp_path):
        (tmp_path / "config.jsonc").write_text('{\n  "version": "0.0.0"\n}\n', encoding="utf-8")
        surface, cfg = _effective(tmp_path)
        assert surface == "full"
        assert cfg == "full"

    def test_existing_install_no_config_stays_full(self, tmp_path):
        # prior index/telemetry state present but no config.jsonc -> existing user
        (tmp_path / "someindex.db").write_bytes(b"x")
        surface, cfg = _effective(tmp_path)
        assert surface == "full"
        # a config gets created, but with the key COMMENTED (inactive)
        text = (tmp_path / "config.jsonc").read_text(encoding="utf-8")
        assert '// "tool_surface"' in text
        assert '"tool_surface": "counter",' not in text

    def test_explicit_choice_is_honored_both_ways(self, tmp_path):
        (tmp_path / "config.jsonc").write_text(
            '{\n  "version": "0.0.0",\n  "tool_surface": "counter"\n}\n', encoding="utf-8"
        )
        assert _effective(tmp_path)[0] == "counter"
        (tmp_path / "config.jsonc").write_text(
            '{\n  "version": "0.0.0",\n  "tool_surface": "full"\n}\n', encoding="utf-8"
        )
        assert _effective(tmp_path)[0] == "full"

    def test_env_var_wins_over_config(self, tmp_path, monkeypatch):
        # fresh install writes counter, but an explicit env var overrides
        _effective(tmp_path)  # creates a counter config
        monkeypatch.setenv("JCODEMUNCH_TOOL_SURFACE", "full")
        assert S._effective_surface() == "full"

    def test_upgrade_never_activates_counter_on_existing_user(self, tmp_path):
        cp = tmp_path / "config.jsonc"
        cp.write_text('{\n  "version": "0.0.0"\n}\n', encoding="utf-8")
        (tmp_path / "old.db").write_bytes(b"x")  # existing install
        added, _warnings = C.upgrade_config(cp)
        text = cp.read_text(encoding="utf-8")
        # the key may be injected, but ONLY as the commented/inactive form
        assert '\n  "tool_surface": "counter",' not in text
        assert _effective(tmp_path)[0] == "full"


class TestFirstEverInstallDetection:
    def test_missing_dir_is_first_ever(self, tmp_path):
        assert C._is_first_ever_install(tmp_path / "nope") is True

    def test_empty_dir_is_first_ever(self, tmp_path):
        assert C._is_first_ever_install(tmp_path) is True

    @pytest.mark.parametrize("fname", ["idx.db", "telemetry.sqlite", "x.sqlite3"])
    def test_prior_db_state_is_not_first_ever(self, tmp_path, fname):
        (tmp_path / fname).write_bytes(b"x")
        assert C._is_first_ever_install(tmp_path) is False

    def test_non_db_files_do_not_count(self, tmp_path):
        (tmp_path / "notes.txt").write_text("hi", encoding="utf-8")
        assert C._is_first_ever_install(tmp_path) is True


class TestSetStringKey:
    def test_activates_commented_line_in_place(self):
        out = C.set_string_key('{\n  // "k": "a",\n}\n', "k", "b")
        assert '"k": "b",' in out
        assert '// "k"' not in out

    def test_overrides_existing_active_value(self):
        out = C.set_string_key('{\n  "k": "a",\n}\n', "k", "b")
        assert '"k": "b",' in out
        assert '"k": "a"' not in out

    def test_injects_absent_key_with_comment(self):
        out = C.set_string_key('{\n  "other": 1\n}\n', "k", "b", comment="why")
        assert '"k": "b",  // why' in out
