"""A `migrations` directory is skipped only when it holds generated migrations.

`migrations` sat in the built-in skip list, so every directory with that name was
pruned. That is right for an application's generated migration files and wrong
for a library whose own code lives in a package called `migrations`: django's
migration framework (`django/db/migrations/`: the autodetector, the executor, the
loader, the operations) was invisible, and nothing in the index result said which
rule dropped it.

The rule now asks what the directory holds. A migration tool names what it writes
by sequence or timestamp (`0001_initial.py`, `2014_10_12_000000_create_users.php`,
`20230101120000_init/`, `V1__init.sql`, `1612345678901-Init.ts`), so a directory
holding one such entry is generated and is skipped, counted as
`generated_migrations`. A directory holding none is source and is indexed.

⚠ Every entry point applies the one predicate: the full walk, the watcher fast
path and the GitHub tree listing. ⚠ `exclude_skip_directories: ["migrations"]`
still indexes every migrations directory, generated or not, as it did before.
"""

from pathlib import Path

import pytest

from jcodemunch_mcp import config as config_module
from jcodemunch_mcp.security import (
    generated_migrations_prefixes,
    get_skip_generated_migrations,
    is_generated_migration_name,
    is_generated_migrations_directory,
)
from jcodemunch_mcp.tools.index_folder import (
    WITHHELD_SKIP_REASONS,
    _build_index_filters,
    _config_skip_flags,
    _build_skip_dirs_regex,
    _should_index_file,
    discover_local_files,
)
from jcodemunch_mcp.tools.index_repo import discover_source_files


def _write(path: Path, text: str = "x = 1\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# Names migration tools write, one per tool family.
GENERATED = [
    "0001_initial.py",                                # django
    "2014_10_12_000000_create_users_table.php",       # laravel
    "20200101120000_create_users.js",                 # knex, sequelize
    "1612345678901-Init.ts",                          # typeorm
    "000001_init.up.sql",                             # golang-migrate
    "V1__init.sql",                                   # flyway
    "V1_2__add_column.sql",                           # flyway, underscored version
    "V1.2__add_column.sql",                           # flyway, dotted version
    "001_create_users.sql",                           # three-digit sequence (tern, sql-migrate)
    "Version20230101120000.php",                      # doctrine
    "m230101_120000_create_users.php",                # yii2
    "Migration20230101120000.ts",                     # mikro-orm
    "script.py.mako",                                 # alembic / flask-migrate template
    "alembic.ini",                                    # alembic config
]

# Hand-written names that must not read as generated.
HAND_WRITTEN_NAMES = ["__init__.py", "autodetector.py", "v2_compat.py", "Versioning.php", "migration.py", "12_factor.py"]

# What a framework's own migrations package holds.
SOURCE = ["__init__.py", "autodetector.py", "executor.py", "loader.py", "state.py", "utils.py"]


# ---------------------------------------------------------------------------
# The predicate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", GENERATED)
def test_a_directory_holding_a_generated_migration_is_generated(tmp_path, name):
    d = tmp_path / "migrations"
    _write(d / "__init__.py")
    _write(d / name)
    assert is_generated_migrations_directory("migrations", d) is True


def test_timestamped_subdirectories_are_generated(tmp_path):
    """prisma and diesel write one directory per migration."""
    for sub in ("20230101120000_init", "2020-01-01-000000_create_users"):
        d = tmp_path / sub / "migrations"
        _write(d / sub / "migration.sql", "CREATE TABLE t (id int);")
        assert is_generated_migrations_directory("migrations", d) is True, sub


def test_a_framework_migrations_package_is_source(tmp_path):
    d = tmp_path / "migrations"
    for name in SOURCE:
        _write(d / name)
    _write(d / "operations" / "base.py")
    assert is_generated_migrations_directory("migrations", d) is False


@pytest.mark.parametrize("name", HAND_WRITTEN_NAMES)
def test_a_hand_written_name_is_not_generated(name):
    assert is_generated_migration_name(name) is False


def test_only_the_name_migrations_is_considered(tmp_path):
    d = tmp_path / "versions"
    _write(d / "0001_initial.py")
    assert is_generated_migrations_directory("versions", d) is False


def test_an_unreadable_directory_is_not_claimed(tmp_path):
    """The rule does not claim to know what a directory it could not list holds;
    the walk reports such a directory as `unreadable`, as it does for NuGet's."""
    assert is_generated_migrations_directory("migrations", tmp_path / "gone") is False


# ---------------------------------------------------------------------------
# The full walk
# ---------------------------------------------------------------------------


def _mixed_repo(root: Path) -> Path:
    for name in SOURCE:
        _write(root / "framework" / "db" / "migrations" / name)
    _write(root / "framework" / "db" / "migrations" / "operations" / "base.py")
    _write(root / "app" / "migrations" / "__init__.py")
    _write(root / "app" / "migrations" / "0001_initial.py")
    _write(root / "app" / "models.py")
    return root


def test_the_walk_indexes_the_framework_and_skips_generated_migrations(tmp_path):
    files, warnings, skip_counts = discover_local_files(_mixed_repo(tmp_path))
    rel = {Path(f).relative_to(tmp_path).as_posix() for f in files}

    assert "framework/db/migrations/autodetector.py" in rel
    assert "framework/db/migrations/operations/base.py" in rel
    assert "app/models.py" in rel
    assert not any(r.startswith("app/migrations/") for r in rel), rel
    assert skip_counts["generated_migrations"] == 1
    assert warnings == []


def test_un_skipping_migrations_still_indexes_every_one(tmp_path, monkeypatch):
    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "exclude_skip_directories", ["migrations"])
    assert get_skip_generated_migrations() is False

    files, _, skip_counts = discover_local_files(_mixed_repo(tmp_path))
    rel = {Path(f).relative_to(tmp_path).as_posix() for f in files}

    assert "app/migrations/0001_initial.py" in rel
    assert "framework/db/migrations/autodetector.py" in rel
    assert skip_counts["generated_migrations"] == 0


def test_generated_migrations_is_not_a_withheld_reason():
    """Generated migrations are corpus definition, like a build tree: absence
    claims over what remains stay citable, as they did under the name rule."""
    assert "generated_migrations" not in WITHHELD_SKIP_REASONS


# ---------------------------------------------------------------------------
# The watcher fast path
# ---------------------------------------------------------------------------


def _fast_cfg(root: Path):
    return _build_index_filters(root=root.resolve(), skip_dirs_regex=_build_skip_dirs_regex())


def test_the_fast_path_refuses_a_generated_migration(tmp_path):
    root = _mixed_repo(tmp_path)
    ok, reason, _rel, _warning = _should_index_file(
        root / "app" / "migrations" / "0001_initial.py", _fast_cfg(root)
    )
    assert (ok, reason) == (False, "generated_migrations")


def test_the_fast_path_keeps_a_framework_module(tmp_path):
    root = _mixed_repo(tmp_path)
    ok, _reason, _rel, _warning = _should_index_file(
        root / "framework" / "db" / "migrations" / "autodetector.py", _fast_cfg(root)
    )
    assert ok is True


# ---------------------------------------------------------------------------
# The GitHub tree listing
# ---------------------------------------------------------------------------


def _tree(paths: list[str]) -> list[dict]:
    return [{"type": "blob", "path": p, "size": 10, "sha": "0" * 40} for p in paths]


def test_the_remote_listing_uses_the_same_rule():
    paths = [
        "django/db/migrations/__init__.py",
        "django/db/migrations/autodetector.py",
        "django/db/migrations/operations/base.py",
        "django/contrib/auth/migrations/__init__.py",
        "django/contrib/auth/migrations/0001_initial.py",
        "prisma/migrations/20230101120000_init/migration.sql",
        "migrations/0002_more.py",
        # django's own test package holds test apps whose migrations are generated;
        # only those leaves are skipped, never the package around them.
        "tests/migrations/test_autodetector.py",
        "tests/migrations/migrations_test_apps/app/migrations/0001_initial.py",
    ]
    assert generated_migrations_prefixes(paths) == {
        "django/contrib/auth/migrations/",
        "prisma/migrations/",
        "migrations/",
        "tests/migrations/migrations_test_apps/app/migrations/",
    }

    result = discover_source_files(_tree(paths))
    files = result[0] if isinstance(result, tuple) else result
    assert "django/db/migrations/autodetector.py" in files
    assert "django/db/migrations/operations/base.py" in files
    assert not any("contrib/auth/migrations" in f for f in files), files
    assert "migrations/0002_more.py" not in files
    assert "tests/migrations/test_autodetector.py" in files


def test_the_remote_listing_honours_the_opt_out(monkeypatch):
    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "exclude_skip_directories", ["migrations"])
    files = discover_source_files(_tree(["app/migrations/0001_initial.py"]))[0]
    assert files == ["app/migrations/0001_initial.py"]


def test_get_filtered_files_prunes_the_same_way(tmp_path):
    from jcodemunch_mcp.tools.index_folder import get_filtered_files

    root = _mixed_repo(tmp_path)
    rel = {Path(p).relative_to(root).as_posix() for p in get_filtered_files(str(root))}
    assert "framework/db/migrations/autodetector.py" in rel
    assert "app/migrations/0001_initial.py" not in rel


def test_get_filtered_files_honours_the_opt_out(tmp_path, monkeypatch):
    from jcodemunch_mcp.tools.index_folder import get_filtered_files

    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "exclude_skip_directories", ["migrations"])
    root = _mixed_repo(tmp_path)
    rel = {Path(p).relative_to(root).as_posix() for p in get_filtered_files(str(root))}
    assert "app/migrations/0001_initial.py" in rel


def test_both_routes_read_the_switch_from_one_place(tmp_path, monkeypatch):
    """The full walk and the watcher's fast path build their filters from
    `_config_skip_flags`, so the opt-out reaches both."""
    assert _config_skip_flags(str(tmp_path))["skip_generated_migrations"] is True
    monkeypatch.setitem(config_module._GLOBAL_CONFIG, "exclude_skip_directories", ["migrations"])
    assert _config_skip_flags(str(tmp_path))["skip_generated_migrations"] is False


def test_the_fast_path_honours_the_opt_out(tmp_path):
    root = _mixed_repo(tmp_path)
    cfg = _build_index_filters(
        root=root.resolve(), skip_dirs_regex=_build_skip_dirs_regex(), skip_generated_migrations=False
    )
    ok, _reason, _rel, _warning = _should_index_file(root / "app" / "migrations" / "0001_initial.py", cfg)
    assert ok is True


def test_the_tsconfig_walk_still_skips_migrations():
    """Removing a skip is the one direction the tsconfig walk may not go; the name
    left the index skip list, so the walk's own set keeps it."""
    from jcodemunch_mcp.parser.imports import _tsconfig_skip_dirs

    assert "migrations" in _tsconfig_skip_dirs()


def test_a_django_project_indexes_a_migrations_package(tmp_path):
    """The Django framework profile ignored `migrations/` by name, a second copy of
    the rule; a Django project indexes a hand-written migrations package."""
    from jcodemunch_mcp.parser.context.framework_profiles import _DJANGO

    assert not any("migrations" in p for p in _DJANGO.ignore_patterns)


def test_project_intel_counts_only_generated_migration_files():
    from types import SimpleNamespace

    from jcodemunch_mcp.tools.get_project_intel import _collect_data_intel

    index = SimpleNamespace(context_metadata={}, source_files=[
        "django/db/migrations/autodetector.py",
        "django/db/migrations/operations/base.py",
        "app/migrations/0001_initial.py",
        "db/migrate/20200101120000_create_users.rb",
    ])
    assert _collect_data_intel(index)["migration_files"] == 2


def test_a_django_project_indexes_its_migrations_package_end_to_end(tmp_path):
    """With the Django profile detected, a hand-written migrations package is
    indexed and an app's generated migrations are not."""
    from jcodemunch_mcp.tools.index_folder import index_folder

    src = tmp_path / "proj"
    _write(src / "manage.py", "import django\n")
    _write(src / "requirements.txt", "django\n")
    _write(src / "lib" / "migrations" / "executor.py", "def run():\n    return 1\n")
    _write(src / "app" / "migrations" / "0001_initial.py", "def migrate():\n    return 1\n")
    r = index_folder(str(src), use_ai_summaries=False, storage_path=str(tmp_path / "store"))

    assert r.get("success"), r
    assert r.get("discovery_skip_counts", {}).get("generated_migrations") == 1, r.get("discovery_skip_counts")
    assert r.get("framework_profile") == "django", r.get("framework_profile")
    files = {f.replace("\\", "/") for f in r.get("files", [])}
    assert "lib/migrations/executor.py" in files, files
    assert "app/migrations/0001_initial.py" not in files
    assert r["discovery_skip_counts"]["extra_ignore"] == 0


@pytest.mark.parametrize("opt_out", [False, True])
def test_index_folders_watcher_route_reads_the_switch(tmp_path, monkeypatch, opt_out):
    """Through `index_folder`'s own fast path, not a hand-built filter: a watcher
    event for a generated migration is refused by default and admitted under the
    `exclude_skip_directories` opt-out."""
    from jcodemunch_mcp.reindex_state import WatcherChange
    from jcodemunch_mcp.tools.index_folder import index_folder

    src = tmp_path / "proj"
    _write(src / "a.py", "def a():\n    return 1\n")
    _write(src / "app" / "migrations" / "__init__.py")
    store = tmp_path / ".code-index"
    first = index_folder(path=str(src), use_ai_summaries=False, storage_path=str(store),
                         incremental=False, context_providers=False)
    assert first["success"], first

    if opt_out:
        monkeypatch.setitem(config_module._GLOBAL_CONFIG, "exclude_skip_directories", ["migrations"])
    target = _write(src / "app" / "migrations" / "0001_initial.py", "def migrate():\n    return 1\n")
    r = index_folder(
        path=str(src), use_ai_summaries=False, storage_path=str(store),
        incremental=True, context_providers=False,
        changed_paths=[WatcherChange("added", str(target.resolve()), "__cache_miss__")],
    )
    assert r["success"], r
    # Refused, the event leaves nothing to index; admitted, the fast path indexes it.
    if opt_out:
        assert r.get("fast_path") is True, "the fast path did not engage; the test proves nothing"
    else:
        assert r.get("message") == "No changes detected", r

    from jcodemunch_mcp.storage.index_store import IndexStore

    repo = r["repo"]
    owner, name = repo.split("/", 1)
    index = IndexStore(base_path=str(store)).load_index(owner, name)
    files = {f.replace("\\", "/") for f in index.source_files}
    assert ("app/migrations/0001_initial.py" in files) is opt_out, files
