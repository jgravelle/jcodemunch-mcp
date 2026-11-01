"""A fake ``IndexStore`` must not outlive the test that installed it (L-259).

Tests patch ``jcodemunch_mcp.storage.IndexStore``. A tool module imported for
the first time while that patch is live binds the fake through
``from ..storage import IndexStore``, and ``monkeypatch`` undoes only the
attribute it set, so the module keeps the fake for the rest of the process.
`test_hook_steering_fixes.py` did exactly this by patching
``tools.check_references.check_references`` by dotted path after installing
its store fake, and a later ``check_references`` call failed with
``'MockStore' object has no attribute '_content_dir'``.

The repair is in ``tests/conftest.py`` (``_restore_real_index_store``), one
layer below any single test, so a test written next cannot re-arm it.
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

import jcodemunch_mcp.storage as storage
import jcodemunch_mcp.tools as tools_pkg
from jcodemunch_mcp.storage.index_store import IndexStore as REAL

_PROBE = "jcodemunch_mcp.tools._l259_probe"
_TESTS = Path(__file__).resolve().parent


def _load_probe_copy():
    """Import a fresh copy of a real tool module under a name we own."""
    src = Path(tools_pkg.__file__).parent / "check_references.py"
    spec = importlib.util.spec_from_file_location(_PROBE, src)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_PROBE] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def restore(request):
    """conftest's repair, found the way pytest loaded it."""
    for plugin in request.config.pluginmanager.get_plugins():
        fn = getattr(plugin, "_restore_real_index_store", None)
        if fn is not None:
            return fn
    pytest.fail("tests/conftest.py no longer defines _restore_real_index_store")


def test_a_module_first_imported_under_the_fake_gets_the_real_class_back(
    monkeypatch, restore,
):
    fake = type("MockStore", (), {})
    try:
        with monkeypatch.context() as mp:
            mp.setattr(storage, "IndexStore", fake)
            probe = _load_probe_copy()
            assert probe.IndexStore is fake, "the probe must reproduce the binding"
        # The patch is undone; the module still holds the fake. This is the leak.
        assert storage.IndexStore is REAL
        assert probe.IndexStore is fake
        restore()
        assert probe.IndexStore is REAL
    finally:
        sys.modules.pop(_PROBE, None)


def test_a_patched_home_module_is_never_spread(monkeypatch, restore):
    """With the defining module itself patched there is no real class to
    hand out, so the repair must do nothing rather than copy the fake."""
    import jcodemunch_mcp.storage.index_store as home

    fake = type("MockStore", (), {})
    monkeypatch.setattr(home, "IndexStore", fake)
    try:
        restore()
        assert storage.IndexStore is REAL
    finally:
        # A broken repair writes with plain setattr, which monkeypatch never
        # undoes; put it back so a failure here cannot fail the next arm.
        storage.IndexStore = REAL


def test_a_repair_that_meets_a_live_patch_hands_out_the_real_class(
    monkeypatch, restore,
):
    """The repair runs BEFORE monkeypatch's undo (fixture order), so the
    package attribute is still the fake when it runs. It must take the class
    from its defining module, or a module first imported under the patch
    keeps the fake after the undo."""
    import jcodemunch_mcp.storage.index_store as home

    fake = type("MockStore", (), {})
    try:
        with monkeypatch.context() as mp:
            mp.setattr(storage, "IndexStore", fake)
            probe = _load_probe_copy()
            restore()
        assert probe.IndexStore is REAL
        assert home.IndexStore is REAL
    finally:
        sys.modules.pop(_PROBE, None)


def test_the_reported_sequence_passes_in_one_process():
    """L-259 as reported: the hook test that leaks, then the victim, in ONE
    process. The full tier's worker split hid it, so run it unsplit."""
    leaker = (
        "tests/test_hook_steering_fixes.py::TestTaskCompleteLiveJournal::"
        "test_live_journal_drives_diagnostics"
    )
    victim = (
        "tests/test_search_text_enclosing_symbol.py::"
        "test_check_references_content_matches_carry_the_enclosing_symbol"
    )
    env = dict(os.environ)
    env.pop("PYTEST_XDIST_WORKER", None)
    env.pop("PYTEST_XDIST_WORKER_COUNT", None)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", leaker, victim, "-q",
         "-p", "no:cacheprovider", "-p", "no:xdist", "-p", "no:randomly"],
        cwd=str(_TESTS.parent), env=env, capture_output=True, text=True,
        timeout=300,
    )
    tail = (proc.stdout + proc.stderr)[-3000:]
    assert "2 passed" in proc.stdout, tail
    assert proc.returncode == 0, tail
