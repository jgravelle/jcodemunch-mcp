"""Shared pytest fixtures for jcodemunch-mcp tests."""

import os
import sys
from pathlib import Path

import pytest

# `harness/` (thresholds, tiers) is a root-level dev package, deliberately
# outside src/ and the wheel. Tests reach it through the repo root, which
# pytest's prepend import mode does not put on sys.path by itself.
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


@pytest.fixture(autouse=True, scope="session")
def _no_network():
    """No test reaches the network (STANDARD N5).

    Before 2026-09-03 this was established by inspection only. Any outbound
    `socket.connect` to a non-loopback address raises; the in-process ASGI
    transports the HTTP tests use never touch a socket. A test that genuinely
    needs the network opts out with `@pytest.mark.network` (zero users today)
    and is excluded from every harness tier by that marker.
    """
    import socket

    real_connect = socket.socket.connect

    def guarded_connect(self, address, *a, **kw):
        host = address[0] if isinstance(address, tuple) else address
        if isinstance(host, str) and host not in ("127.0.0.1", "::1", "localhost", ""):
            raise RuntimeError(
                f"test attempted a network connection to {host!r}; tests are offline "
                "(STANDARD N5). Mark it @pytest.mark.network if it must reach out. "
                "If this is tiktoken fetching its BPE asset on a cold box, run "
                "`uv run python -m harness warm` once before pytest (FINDINGS F-14)."
            )
        return real_connect(self, address, *a, **kw)

    socket.socket.connect = guarded_connect
    try:
        yield
    finally:
        socket.socket.connect = real_connect


@pytest.fixture(autouse=True, scope="session")
def _pin_code_index_path(tmp_path_factory):
    """Point `CODE_INDEX_PATH` at a per-worker temp store for the whole run.

    ⚠⚠ Without this, any test calling `index_folder()` or constructing an
    `IndexStore` WITHOUT an explicit `storage_path` / `base_path` writes the
    DEVELOPER'S REAL `~/.code-index`. That was tolerable serially and is not
    under `pytest-xdist`: four workers index into one store, contend on the
    same `indexwrite` / `watcher` process-lock scopes, and produce failures
    that do not reproduce in isolation. `test_v1_108_2.py::
    test_probe_runs_when_identity_true` failed exactly once on a Linux CI leg
    on 2026-08-17, passed on a re-run of the identical tree, and survived all
    14 bisect pairings -- the signature of contention rather than a defect.

    ⚠ **SESSION-scoped, deliberately, not `tmp_path`.** Some fixtures are
    module-scoped and index once for several tests -- `served_repo` in
    `test_blast_radius_package_granular_verdict.py` indexes "where the SERVER
    looks", because `server.call_tool` hands `IndexStore` no `base_path`. A
    per-test pin would move the store out from under that write and turn a
    real assertion into a not-found path. One store per session keeps
    write-then-read fixtures working while still isolating from the real one.

    ⚠ Under xdist each worker is a separate process with its own
    `tmp_path_factory` base, so workers cannot collide with each other either.

    ⚠ A test that sets `CODE_INDEX_PATH` itself still wins: `monkeypatch`
    applies over this and restores to it, so per-test isolation composes on
    top rather than fighting it.
    """
    store = tmp_path_factory.mktemp("code-index")
    previous = os.environ.get("CODE_INDEX_PATH")
    os.environ["CODE_INDEX_PATH"] = str(store)
    try:
        yield store
    finally:
        if previous is None:
            os.environ.pop("CODE_INDEX_PATH", None)
        else:
            os.environ["CODE_INDEX_PATH"] = previous


@pytest.fixture(autouse=True)
def _clear_index_cache():
    """Clear the in-memory SQLite index cache before and after each test.

    Tests that modify the SQLite DB directly (e.g. changing index_version)
    can leave stale entries in the module-level cache.  SQLite WAL mode does
    not always update the main DB file mtime on write, so the cache key
    (owner, name, mtime_ns) may still match after a direct DB modification.
    Clearing before each test ensures no cross-test contamination; clearing
    after ensures no stale entries persist for the next test.
    """
    try:
        from jcodemunch_mcp.storage.sqlite_store import _cache_clear
        _cache_clear()
    except ImportError:
        pass
    yield
    try:
        from jcodemunch_mcp.storage.sqlite_store import _cache_clear
        _cache_clear()
    except ImportError:
        pass


@pytest.fixture(autouse=True)
def _reset_global_config():
    """Reset the global config state between tests.

    Tests that call main() or load_config() modify _GLOBAL_CONFIG and
    _PROJECT_CONFIGS at module level.  Without cleanup, subsequent tests
    see config values left by earlier tests (e.g. 'watch': true from the
    user's disk config bleeds into tests that expect the default).

    ⚠ The reset runs BEFORE the test as well as after (v1.108.258, #426).
    Teardown alone leaves exactly one hole: `_GLOBAL_CONFIG` is `{}` until the
    first test's teardown fires, and `config.get()` now lazily loads out of that
    state -- which would pull the DEVELOPER's real `~/.code-index/config.jsonc`
    into whichever test happened to read config first. Same family as #411,
    where a test broke on any box that had the key it was testing actually set.
    """
    _reset_config_state()
    yield
    _reset_config_state()
    _close_perf_dbs()


def _close_perf_dbs():
    """Drop any perf telemetry connection a test left open (v1.108.276, #442).

    ⚠ Those connections are now held for the PROCESS rather than closed after
    each write. Without this, a handle opened against one test's `tmp_path`
    survives into the next test, and on Windows an open handle also blocks
    removal of the directory holding it. Same isolation principle as the config
    reset above: process-lifetime state must not cross a test boundary.
    """
    try:
        from jcodemunch_mcp.storage import token_tracker
        token_tracker.close_perf_dbs()
    except ImportError:
        pass


def _reset_config_state():
    try:
        from jcodemunch_mcp import config as cfg
        from copy import deepcopy
        cfg._GLOBAL_CONFIG = deepcopy(cfg.DEFAULTS)
        cfg._CONFIG_LOADED = True  # DEFAULTS is a deliberate load, not an absence
        cfg._PROJECT_CONFIGS.clear()
        cfg._PROJECT_CONFIG_HASHES.clear()
        cfg._REPO_PATH_CACHE.clear()
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# T12 — Correctness fixture library
# ---------------------------------------------------------------------------
# Small, medium, and graph-rich synthetic repos used by multiple test modules.
# Each fixture builds a deterministic synthetic codebase with ground-truth
# expected outputs documented inline.
# ---------------------------------------------------------------------------

@pytest.fixture
def small_index(tmp_path):
    """Small synthetic Python repo: 1 file, 3 symbols.

    Ground truth:
        symbols: MAX_RETRIES (constant), add (function), subtract (function)
        files:   ["utils.py"]
        kinds:   {"constant": 1, "function": 2}
    """
    from jcodemunch_mcp.tools.index_folder import index_folder

    src = tmp_path / "src"
    store = tmp_path / "store"
    src.mkdir()
    store.mkdir()
    (src / "utils.py").write_text(
        "MAX_RETRIES = 3\n\n"
        "def add(a, b):\n"
        "    return a + b\n\n"
        "def subtract(a, b):\n"
        "    return a - b\n"
    )
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert r["success"] is True
    return {"repo": r["repo"], "store": str(store), "src": str(src)}


@pytest.fixture
def medium_index(tmp_path):
    """Medium synthetic Python repo: 3 files with cross-imports.

    Ground truth:
        files:   models.py, service.py, api.py
        classes: User, Product (models.py)
        functions: get_user, create_user (service.py), handle_request (api.py)
        imports: service.py imports from models; api.py imports from models + service
        most_imported: models.py (imported by 2 files)
    """
    from jcodemunch_mcp.tools.index_folder import index_folder

    src = tmp_path / "src"
    store = tmp_path / "store"
    src.mkdir()
    store.mkdir()
    (src / "models.py").write_text(
        "class User:\n"
        "    \"\"\"Represents a user.\"\"\"\n"
        "    pass\n\n"
        "class Product:\n"
        "    \"\"\"Represents a product.\"\"\"\n"
        "    pass\n"
    )
    (src / "service.py").write_text(
        "from models import User\n\n"
        "def get_user(user_id):\n"
        "    return User()\n\n"
        "def create_user(name):\n"
        "    return User()\n"
    )
    (src / "api.py").write_text(
        "from models import User, Product\n"
        "from service import get_user\n\n"
        "def handle_request(req):\n"
        "    return get_user(req)\n"
    )
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert r["success"] is True
    return {"repo": r["repo"], "store": str(store), "src": str(src)}


@pytest.fixture
def hierarchy_index(tmp_path):
    """Python class hierarchy: Animal -> Mammal -> Dog, Cat.

    Ground truth:
        Animal:  0 ancestors, 1 descendant (Mammal) [via Mammal, transitively Dog+Cat]
        Mammal:  1 ancestor (Animal), 2 direct descendants (Dog, Cat)
        Dog:     2 ancestors (Mammal, Animal), 0 descendants
        Cat:     2 ancestors (Mammal, Animal), 0 descendants
    """
    from jcodemunch_mcp.tools.index_folder import index_folder

    src = tmp_path / "src"
    store = tmp_path / "store"
    src.mkdir()
    store.mkdir()
    (src / "animals.py").write_text(
        "class Animal:\n"
        "    \"\"\"Base animal class.\"\"\"\n"
        "    pass\n\n"
        "class Mammal(Animal):\n"
        "    \"\"\"A warm-blooded animal.\"\"\"\n"
        "    pass\n\n"
        "class Dog(Mammal):\n"
        "    \"\"\"A domestic dog.\"\"\"\n"
        "    pass\n\n"
        "class Cat(Mammal):\n"
        "    \"\"\"A domestic cat.\"\"\"\n"
        "    pass\n"
    )
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(store))
    assert r["success"] is True
    return {"repo": r["repo"], "store": str(store), "src": str(src)}


@pytest.fixture(autouse=True)
def _isolate_claude_home(request, monkeypatch, tmp_path_factory):
    """Redirect every home-derived path `cli.init` writes to a per-test dir,
    and fail the test that changes the developer's real Claude Code files.

    W-34 (docs/workflows/FINDINGS.md): five `run_init(yes=True, no_backup=True)`
    tests in `tests/test_init.py` left `_settings_json_path` unredirected, so
    the full tier ran `install_enforcement_hooks` against the REAL
    `~/.claude/settings.json` and `_converge_rule` rewrote every jcm hook
    command to whatever `shutil.which` found -- in a git worktree, that
    worktree's `.venv`, deleted minutes later; the next Claude Code session
    start failed on every product hook. Practice 8's `load_config()` guard
    (#437) is written against that name and never saw this path.

    Two halves, deliberately: the redirect fixes the helpers that exist today;
    the tripwire catches the writer that reaches the real file some other way
    (a new helper, a `Path.home()` inlined at a call site). A test that patches
    one of these helpers itself still wins -- monkeypatch is last-set. The
    per-test directory is created LAZILY, inside the redirected helpers, so
    the ~9,300 tests that never call `run_init` pay nothing.
    `install_agents_md` writes `Path.cwd()/AGENTS.md` and is NOT redirected:
    the repo root's tracked AGENTS.md is watched instead.
    """
    from jcodemunch_mcp.cli import init as _init

    base = tmp_path_factory.getbasetemp() / "claude_home" / request.node.name[:60].replace("/", "_")

    def _under(*parts: str):
        base.mkdir(parents=True, exist_ok=True)
        return base.joinpath(*parts)

    monkeypatch.setattr(_init, "_settings_json_path", lambda: _under(".claude", "settings.json"))
    monkeypatch.setattr(_init, "_claude_md_path", lambda scope: _under(".claude", "CLAUDE.md"))
    monkeypatch.setattr(_init, "_cursor_rules_path", lambda: _under(".cursor", "rules", "jcodemunch.mdc"))
    monkeypatch.setattr(_init, "_windsurf_rules_path", lambda: _under(".windsurfrules"))

    # Same resolution as `_settings_json_path`: USERPROFILE on Windows only.
    if sys.platform == "win32":
        real_home = Path(os.environ.get("USERPROFILE", str(Path.home())))
    else:
        real_home = Path.home()
    watched = [
        real_home / ".claude" / "settings.json",
        real_home / ".claude" / "CLAUDE.md",
        Path(__file__).resolve().parents[1] / "AGENTS.md",
    ]

    def _stamp(p: Path):
        try:
            st = p.stat()
            return (st.st_mtime_ns, st.st_size)
        except OSError:
            return None

    before = [_stamp(p) for p in watched]
    yield
    after = [_stamp(p) for p in watched]
    changed = [str(p) for p, a, b in zip(watched, before, after) if a != b]
    assert not changed, (
        f"{request.node.nodeid} changed the developer's real Claude Code file(s) "
        f"{changed} (W-34; Practice 8). Redirect the path helper in cli.init or "
        f"point HOME/USERPROFILE at a directory the test owns."
    )
