"""A Python virtualenv is installed dependency code, not corpus, whatever its name (L-206).

`_SKIP_DIRECTORY_NAMES` lists `venv` and `.venv`, so a venv under any other name
(`jcm-venv`, `env`, `py313`) was walked file by file: site-packages entered the
corpus, competed with the user's code in ranking and used up `max_folder_files`.
A workspace holding several such venvs timed out (2026-10-16 friction report).

⚠⚠ Only `site-packages` is pruned, at the path a venv tool writes it, under a
proven root. A stdlib venv before 3.13 and older virtualenv write no
`.gitignore`, people keep their own source in the venv root, and a venv created
in a project root (`python -m venv .`) sits beside hand-written `bin/` and `lib/`.
Review found a wider rule dropping source twice (the whole root, then the layout
names); `test_source_in_a_venv_root_is_kept` and
`test_a_venv_in_the_project_root_keeps_its_bin_and_lib` are the guards.

⚠ The marker is the writer's, not ours: every venv tool writes `pyvenv.cfg` at the
venv root with a `home =` key (PEP 405). The `home` key is checked, not just the
file name, for the same reason `CACHEDIR.TAG` checks its signature: a file merely
NAMED `pyvenv.cfg` is one instance of the property, not the property. The
load-bearing tests are the ones that keep a directory.
"""

from pathlib import Path

import pytest

from jcodemunch_mcp import config as _config
from jcodemunch_mcp.security import (
    get_skip_virtualenvs,
    is_virtualenv_directory,
    is_virtualenv_site_packages_directory,
)
from jcodemunch_mcp.tools.index_folder import (
    _build_index_filters,
    _build_skip_dirs_regex,
    _should_index_file,
    discover_local_files,
)

_PYVENV_CFG = "home = C:\\Python313\ninclude-system-site-packages = false\nversion = 3.13.5\n"


def _venv(root: Path, name: str, cfg: str = _PYVENV_CFG) -> Path:
    """A virtualenv as `python -m venv` / `uv venv` lays it out, with one vendored module."""
    venv = root / name
    site = venv / "Lib" / "site-packages" / "requests"
    site.mkdir(parents=True)
    (site / "api.py").write_text("def get(url):\n    return url\n", encoding="utf-8")
    (venv / "pyvenv.cfg").write_text(cfg, encoding="utf-8")
    return venv


def _project(root: Path) -> Path:
    (root / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    return root


# ---------------------------------------------------------------------------
# The predicate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["jcm-venv", "env", "py313", ".tox-env"])
def test_a_venv_is_a_venv_by_any_name(tmp_path, name):
    assert is_virtualenv_directory(_venv(tmp_path, name)) is True


@pytest.mark.parametrize(
    "cfg",
    [
        "home=/usr/bin\n",
        "  HOME = /usr/local/bin\nversion_info = 3.12.1\n",
        "\ufeffhome = C:\\Python313\n",
    ],
)
def test_the_home_key_is_read_in_every_spelling_venv_tools_write(tmp_path, cfg):
    assert is_virtualenv_directory(_venv(tmp_path, "v", cfg)) is True


@pytest.mark.parametrize(
    "cfg",
    ["", "version = 3.13.5\n", "# home = commented out\n", "homepage = https://example.org\n"],
)
def test_a_pyvenv_cfg_without_a_home_key_is_not_a_venv(tmp_path, cfg):
    """The name alone is not the property."""
    assert is_virtualenv_directory(_venv(tmp_path, "v", cfg)) is False


def test_a_directory_without_pyvenv_cfg_is_not_a_venv(tmp_path):
    (tmp_path / "src").mkdir()
    assert is_virtualenv_directory(tmp_path / "src") is False


def test_a_missing_directory_is_not_a_venv(tmp_path):
    assert is_virtualenv_directory(tmp_path / "gone") is False


def test_a_pyvenv_cfg_directory_is_not_a_venv(tmp_path):
    """`pyvenv.cfg` must be a FILE; a directory by that name raises on open."""
    (tmp_path / "v" / "pyvenv.cfg").mkdir(parents=True)
    assert is_virtualenv_directory(tmp_path / "v") is False


@pytest.mark.parametrize("lib", ["Lib", "lib", "lib64", "LIB"])
def test_windows_site_packages_under_a_venv_root_is_installed_packages(tmp_path, lib):
    venv = _venv(tmp_path, "jcm-venv")
    assert is_virtualenv_site_packages_directory("site-packages", venv / lib) is True


@pytest.mark.parametrize("pydir", ["python3.12", "python3.13", "pypy3.10", "Python3.11"])
def test_posix_site_packages_under_a_venv_root_is_installed_packages(tmp_path, pydir):
    venv = _venv(tmp_path, "env")
    assert is_virtualenv_site_packages_directory("site-packages", venv / "lib" / pydir) is True


@pytest.mark.parametrize("name", ["Lib", "lib", "bin", "Scripts", "Include", "src", "dist-packages"])
def test_nothing_but_site_packages_is_installed_packages(tmp_path, name):
    venv = _venv(tmp_path, "jcm-venv")
    assert is_virtualenv_site_packages_directory(name, venv) is False
    assert is_virtualenv_site_packages_directory(name, venv / "Lib") is False


def test_site_packages_without_a_venv_root_is_not_installed_packages(tmp_path):
    """A vendored `lib/site-packages` in an ordinary project is source someone chose to keep."""
    (tmp_path / "lib" / "site-packages").mkdir(parents=True)
    assert is_virtualenv_site_packages_directory("site-packages", tmp_path / "lib") is False


@pytest.mark.parametrize("parent", ["site-packages", "src/site-packages", "lib/notpython/site-packages"])
def test_site_packages_at_another_path_in_a_venv_is_not_installed_packages(tmp_path, parent):
    venv = _venv(tmp_path, "jcm-venv")
    p = venv / parent
    assert is_virtualenv_site_packages_directory(p.name, p.parent) is False


def test_a_pyvenv_cfg_deeper_down_does_not_reach_up(tmp_path):
    """Only the venv root's own `pyvenv.cfg` counts."""
    _venv(tmp_path / "nested", "inner")
    assert is_virtualenv_site_packages_directory("site-packages", tmp_path / "Lib") is False


# ---------------------------------------------------------------------------
# The config flag
# ---------------------------------------------------------------------------


def test_flag_defaults_to_on():
    assert get_skip_virtualenvs() is True


@pytest.mark.parametrize("value", [None, "false", 0, "", "garbage", []])
def test_only_an_explicit_false_disables_it(monkeypatch, value):
    """A typo must not silently re-admit a dependency tree, the sibling flags' rule."""
    monkeypatch.setattr(_config, "get", lambda key, default=None, repo=None: value)
    assert get_skip_virtualenvs() is True


def test_explicit_false_disables_it(monkeypatch):
    monkeypatch.setattr(_config, "get", lambda key, default=None, repo=None: False)
    assert get_skip_virtualenvs() is False


# ---------------------------------------------------------------------------
# The walk
# ---------------------------------------------------------------------------


def test_an_unnamed_venv_is_pruned_and_counted(tmp_path):
    root = _project(tmp_path)
    _venv(root, "jcm-venv")
    _venv(root / "bench", "py313")

    files, warnings, skip_counts = discover_local_files(root)

    names = {Path(f).name for f in files}
    assert "app.py" in names
    assert "api.py" not in names, "a venv's site-packages leaked into the corpus"
    assert skip_counts["virtualenv"] == 2
    assert warnings == []


def test_source_in_a_venv_root_is_kept(tmp_path):
    """The load-bearing guard: `python -m venv proj` then `proj/src/mine.py`,
    with no `.gitignore`. Installed packages go; the user's source stays."""
    root = _project(tmp_path)
    venv = _venv(root, "proj")
    (venv / "src").mkdir()
    (venv / "src" / "mine.py").write_text("def mine():\n    return 3\n", encoding="utf-8")
    (venv / "manage.py").write_text("def run():\n    return 4\n", encoding="utf-8")

    files, _, skip_counts = discover_local_files(root)

    names = {Path(f).name for f in files}
    assert {"app.py", "mine.py", "manage.py"} <= names
    assert "api.py" not in names
    assert skip_counts["virtualenv"] == 1


def test_a_venv_in_the_project_root_keeps_its_bin_and_lib(tmp_path):
    """`python -m venv .` beside a hand-written `bin/` and `lib/` (review round 2).

    The venv tool writes into the same root and a POSIX venv merges into an
    existing `lib/`; only the installed packages are the writer's.
    """
    root = _project(tmp_path)
    (root / "pyvenv.cfg").write_text(_PYVENV_CFG, encoding="utf-8")
    site = root / "lib" / "python3.12" / "site-packages" / "requests"
    site.mkdir(parents=True)
    (site / "api.py").write_text("def get(url):\n    return url\n", encoding="utf-8")
    (root / "lib" / "mylib.py").write_text("def mine():\n    return 5\n", encoding="utf-8")
    (root / "bin").mkdir()
    (root / "bin" / "deploy.py").write_text("def deploy():\n    return 6\n", encoding="utf-8")
    (root / "Scripts").mkdir()
    (root / "Scripts" / "tool.py").write_text("def tool():\n    return 7\n", encoding="utf-8")

    files, _, skip_counts = discover_local_files(root)

    names = {Path(f).name for f in files}
    assert {"app.py", "mylib.py", "deploy.py", "tool.py"} <= names
    assert "api.py" not in names
    assert skip_counts["virtualenv"] == 1

    ok, _reason, _rel, _warning = _should_index_file(root / "bin" / "deploy.py", _fast_cfg(root))
    assert ok is True
    ok, reason, _rel, _warning = _should_index_file(site / "api.py", _fast_cfg(root))
    assert (ok, reason) == (False, "virtualenv")


def test_a_project_with_a_lookalike_cfg_keeps_its_source(tmp_path):
    """The walk half of the home-key guard: a `lib/` beside a pyvenv.cfg with no
    `home` key is indexed."""
    root = _project(tmp_path)
    pkg = root / "tools"
    (pkg / "lib").mkdir(parents=True)
    (pkg / "pyvenv.cfg").write_text("version = 3.13.5\n", encoding="utf-8")
    (pkg / "lib" / "build.py").write_text("def build():\n    return 2\n", encoding="utf-8")

    files, _, skip_counts = discover_local_files(root)

    assert "build.py" in {Path(f).name for f in files}
    assert skip_counts["virtualenv"] == 0


def test_flag_off_re_admits_the_venv(tmp_path, monkeypatch):
    """Patches the config read, not the env var: the env fallback is resolved once
    at `load_config()`, so setting it afterwards would pass for the wrong reason."""
    from jcodemunch_mcp import security

    original = security._config.get
    monkeypatch.setattr(
        security._config,
        "get",
        lambda k, d=None, repo=None: False if k == "skip_virtualenvs" else original(k, d, repo=repo),
    )
    root = _project(tmp_path)
    _venv(root, "jcm-venv")

    files, _, skip_counts = discover_local_files(root)

    assert "api.py" in {Path(f).name for f in files}
    assert skip_counts["virtualenv"] == 0


def test_virtualenv_is_not_a_withheld_reason():
    """Installed packages are re-installable derived data by the writer's own
    marker, so this is the corpus being DEFINED; absence over the rest stays citable."""
    from jcodemunch_mcp.tools.index_folder import WITHHELD_SKIP_REASONS

    assert "virtualenv" not in WITHHELD_SKIP_REASONS


# ---------------------------------------------------------------------------
# The watcher fast path
# ---------------------------------------------------------------------------


def _fast_cfg(root: Path, **flags):
    return _build_index_filters(root=root.resolve(), skip_dirs_regex=_build_skip_dirs_regex(), **flags)


def test_fast_path_refuses_a_file_inside_a_venv(tmp_path):
    root = _project(tmp_path)
    venv = _venv(root, "jcm-venv")

    ok, reason, _rel, _warning = _should_index_file(
        venv / "Lib" / "site-packages" / "requests" / "api.py", _fast_cfg(root)
    )

    assert ok is False
    assert reason == "virtualenv"


def test_fast_path_keeps_a_file_beside_a_lookalike_cfg(tmp_path):
    root = _project(tmp_path)
    pkg = root / "tools"
    (pkg / "lib").mkdir(parents=True)
    (pkg / "pyvenv.cfg").write_text("version = 3.13.5\n", encoding="utf-8")
    (pkg / "lib" / "build.py").write_text("def build():\n    return 2\n", encoding="utf-8")

    ok, _reason, _rel, _warning = _should_index_file(pkg / "lib" / "build.py", _fast_cfg(root))

    assert ok is True


def test_fast_path_keeps_source_in_a_venv_root(tmp_path):
    root = _project(tmp_path)
    venv = _venv(root, "proj")
    (venv / "src").mkdir()
    (venv / "src" / "mine.py").write_text("def mine():\n    return 3\n", encoding="utf-8")

    ok, _reason, _rel, _warning = _should_index_file(venv / "src" / "mine.py", _fast_cfg(root))

    assert ok is True


def test_the_watcher_reads_the_flag_from_the_shared_switches(tmp_path, monkeypatch):
    """The watcher builds its filters from `_config_skip_flags`, so a switch
    missing there would be missing from the fast path in production."""
    from jcodemunch_mcp import security
    from jcodemunch_mcp.tools.index_folder import _config_skip_flags

    assert _config_skip_flags(str(tmp_path))["skip_virtualenvs"] is True
    original = security._config.get
    monkeypatch.setattr(
        security._config,
        "get",
        lambda k, d=None, repo=None: False if k == "skip_virtualenvs" else original(k, d, repo=repo),
    )
    assert _config_skip_flags(str(tmp_path))["skip_virtualenvs"] is False


def test_fast_path_honours_the_flag(tmp_path):
    root = _project(tmp_path)
    venv = _venv(root, "jcm-venv")

    ok, _reason, _rel, _warning = _should_index_file(
        venv / "Lib" / "site-packages" / "requests" / "api.py",
        _fast_cfg(root, skip_virtualenvs=False),
    )

    assert ok is True
