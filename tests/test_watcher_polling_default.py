"""The watcher's polling default survives a watchfiles release renaming a private name.

#641 review item 4: `_safe_awatch` imported `watchfiles.main._default_force_polling`,
a private symbol of a pinned dependency; a rename would have failed the watcher at
its first `_safe_awatch`. `_force_polling_default` asks watchfiles when the name
exists and otherwise applies the same documented rule itself. Both halves are
pinned here against the INSTALLED watchfiles, so a divergence between the fallback
and the authority is a red test, not a silently different watcher.
"""

from __future__ import annotations

import builtins
import importlib

import pytest

from jcodemunch_mcp import watcher


@pytest.fixture
def watchfiles_main():
    return pytest.importorskip("watchfiles.main")


@pytest.mark.parametrize("env", [None, "true", "1", "0", "false", "disable", "disabled", "TRUE", "yes"])
def test_with_watchfiles_present_the_helper_agrees_with_the_authority(monkeypatch, env, watchfiles_main):
    if env is None:
        monkeypatch.delenv("WATCHFILES_FORCE_POLLING", raising=False)
    else:
        monkeypatch.setenv("WATCHFILES_FORCE_POLLING", env)
    assert watcher._force_polling_default() is bool(watchfiles_main._default_force_polling(None))


def _without_the_private_name(monkeypatch):
    """Make `from watchfiles.main import _default_force_polling` raise ImportError
    the way a renamed symbol does, leaving the rest of watchfiles intact."""
    real_import = builtins.__import__

    def guarded(name, globals=None, locals=None, fromlist=(), level=0):
        module = real_import(name, globals, locals, fromlist, level)
        if name == "watchfiles.main" and fromlist and "_default_force_polling" in fromlist:
            raise ImportError("cannot import name '_default_force_polling' (renamed)")
        return module

    monkeypatch.setattr(builtins, "__import__", guarded)


@pytest.mark.parametrize("env", ["true", "1", "0", "false", "disable", "disabled", "TRUE", "yes"])
def test_without_the_private_name_the_fallback_matches_the_authority(monkeypatch, env, watchfiles_main):
    monkeypatch.setenv("WATCHFILES_FORCE_POLLING", env)
    expected = bool(watchfiles_main._default_force_polling(None))
    _without_the_private_name(monkeypatch)
    assert watcher._force_polling_default() is expected


def test_without_the_private_name_and_no_env_the_fallback_matches_wsl_detection(monkeypatch, watchfiles_main):
    monkeypatch.delenv("WATCHFILES_FORCE_POLLING", raising=False)
    expected = bool(watchfiles_main._default_force_polling(None))
    _without_the_private_name(monkeypatch)
    assert watcher._force_polling_default() is expected


def test_the_guard_is_exercised_not_bypassed(monkeypatch, watchfiles_main):
    """Non-vacuity: the import really raises under the patch, so the fallback
    branch is the one that answered above."""
    _without_the_private_name(monkeypatch)
    importlib.import_module("watchfiles.main")  # the module still imports ...
    with pytest.raises(ImportError):
        from watchfiles.main import _default_force_polling  # noqa: F401  ... the NAME is gone


def test_safe_awatch_no_longer_names_the_private_symbol_directly():
    import inspect
    src = inspect.getsource(watcher._safe_awatch)
    assert "_default_force_polling" not in src
    assert "_force_polling_default()" in src
