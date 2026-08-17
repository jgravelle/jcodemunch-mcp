"""A test must never read or write the developer's real global config (#437).

`load_config()` with no `storage_path` resolves to `CODE_INDEX_PATH` or
`~/.code-index/config.jsonc` (`config.py:107-110`). It READS the developer's real
config, and with the default `create_missing=True` it also WRITES that file when
it is absent.

⚠ The consequence is not only flaky tests. On a storage dir that looks like an
existing install (any `.db` present) with no config file, the config a test run
creates has `tool_surface` ABSENT, which resolves to the `full` surface, and
`_fresh_config_content` is explicit that `upgrade_config` "can never add it to an
existing user's config on a package update". A test run can pin a user to a
surface nothing later migrates them off.

⚠⚠ conftest's `_reset_global_config` already resets `_GLOBAL_CONFIG` to `DEFAULTS`
before AND after every test, and its docstring already names this hazard and #411.
A bare `load_config()` inside a fixture runs AFTER that reset and re-pulls the real
config straight past it. The guard existed; the call sites walked around it. That
is why this file checks the CALL, not the reset.

It surfaced as three failures @lilubot hit on PR #433 and reasonably attributed to
their own machine. Our suite was green because this box has `max_folder_files`
commented out, and CI was green because the runner has no global config at all. A
test that passes on two machines and fails on a third, for a reason none of the
three can see, is the defect.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent

# Modules allowed to call load_config() without pinning CODE_INDEX_PATH, named
# with the reason. Exemptions live HERE, not in an omission from a list.
EXEMPT = {
    "test_config_lazy_load.py":
        "#437: deliberately exercises the REAL config path, including the "
        "auto-create, and does it in subprocesses with their own env",
    "test_config_check_matches_resolver.py":
        "#437: calls live in subprocess source strings; the `env` fixture pins "
        "CODE_INDEX_PATH for the child process",
}


def _bare_load_config_lines(path: Path) -> list[int]:
    """Lines calling load_config() with no storage_path, positional or keyword.

    ⚠ AST-based, so it cannot see calls embedded in subprocess source STRINGS.
    Both exempt modules do exactly that, which is a second reason they are
    exempt rather than merely unflagged: a silent miss and a named exemption
    look identical from a green run, and only one of them is a decision.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []

    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name != "load_config":
            continue
        if node.args or any(kw.arg == "storage_path" for kw in node.keywords):
            continue
        hits.append(node.lineno)
    return hits


def _pins_storage(path: Path) -> bool:
    """Whether the module pins CODE_INDEX_PATH anywhere.

    Deliberately coarse. Proving the pin dominates every call on every path
    would need flow analysis; requiring the module to pin at all is the cheap
    check that would have caught all three tier-1 files in #437.
    """
    return "CODE_INDEX_PATH" in path.read_text(encoding="utf-8")


def _test_modules() -> list[Path]:
    return sorted(p for p in _TESTS_DIR.glob("test_*.py"))


_CALLERS = [p for p in _test_modules() if _bare_load_config_lines(p)]


@pytest.mark.parametrize(
    "module",
    [p for p in _CALLERS if p.name not in EXEMPT],
    ids=lambda p: p.name,
)
def test_bare_load_config_pins_storage(module: Path):
    lines = _bare_load_config_lines(module)
    assert _pins_storage(module), (
        f"{module.name} calls load_config() with no storage_path at line(s) "
        f"{lines} and never pins CODE_INDEX_PATH, so it reads and can WRITE the "
        f"developer's real ~/.code-index/config.jsonc. Pin it to tmp_path before "
        f"the call, or add the module to EXEMPT in this file with a reason."
    )


@pytest.mark.parametrize("name", sorted(EXEMPT), ids=lambda n: n)
def test_exemptions_are_not_stale(name: str):
    """The ratchet. An exemption must expire when its reason does."""
    path = _TESTS_DIR / name
    assert path.exists(), f"{name} is gone; remove it from EXEMPT ({EXEMPT[name]})"
    assert "load_config" in path.read_text(encoding="utf-8"), (
        f"{name} no longer calls load_config at all, so its exemption is spent. "
        f"Remove it from EXEMPT: {EXEMPT[name]}"
    )


def test_the_guard_can_fail(tmp_path):
    """A guard nobody can see fail is one nobody believes.

    Feeds the detector a module shaped like the #437 defect and one shaped like
    the fix, so a green run above means the check discriminates rather than
    returning empty for everything.
    """
    bad = tmp_path / "test_bad.py"
    bad.write_text(
        "from jcodemunch_mcp import config as c\n"
        "def test_x(monkeypatch):\n"
        "    c.load_config()\n",
        encoding="utf-8",
    )
    good = tmp_path / "test_good.py"
    good.write_text(
        "from jcodemunch_mcp import config as c\n"
        "def test_x(monkeypatch, tmp_path):\n"
        '    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path))\n'
        "    c.load_config()\n",
        encoding="utf-8",
    )
    explicit = tmp_path / "test_explicit.py"
    explicit.write_text(
        "from jcodemunch_mcp import config as c\n"
        "def test_x(tmp_path):\n"
        "    c.load_config(storage_path=str(tmp_path))\n",
        encoding="utf-8",
    )

    assert _bare_load_config_lines(bad) == [3]
    assert not _pins_storage(bad)

    assert _bare_load_config_lines(good) == [4]
    assert _pins_storage(good)

    # An explicit storage_path is not a bare call at all.
    assert _bare_load_config_lines(explicit) == []


def test_the_sweep_found_the_callers():
    """Guards against the parametrization silently collapsing to zero cases.

    If a refactor moves every call behind a helper, this file would pass by
    having nothing to check, which is the same shape of false green #437 was.
    """
    assert _CALLERS, (
        "no test module calls load_config() any more. If that is real, delete "
        "this guard deliberately rather than leaving it passing on an empty set."
    )


# ---------------------------------------------------------------------------
# The same hazard reached through a second module object (#482)
# ---------------------------------------------------------------------------
# Importing the package as `src.jcodemunch_mcp` instead of `jcodemunch_mcp`
# produces a SEPARATE module object: `src.jcodemunch_mcp.config is
# jcodemunch_mcp.config` is False. That twin has its own `_GLOBAL_CONFIG`, and
# conftest's `_reset_global_config` resets only the canonical one -- so the twin
# lazily loads the developer's real `~/.code-index/config.jsonc` and every
# protection above is bypassed by a spelling.
#
# ⚠⚠ It is the SAME defect this file already guards, which is why it lives here
# rather than in a file of its own. The guard existed and a different import
# path walked around it, exactly as the call sites walked around the reset.
#
# Found via test_css.py / test_json.py: `parse_file` consulted the twin's
# config, `is_language_enabled` gated `scss` and `json` out of the real
# `languages` allowlist, and both returned [] against a direct extractor's 10
# symbols. ⚠ They passed in a full serial run because test_config.py -- also
# twin-importing -- overwrote its config earlier in alphabetical order, so the
# failure only appeared under xdist and in isolation.
#
# ⚠ The `patch("src.jcodemunch_mcp...")` form is covered too, and it fails the
# other way: it patches an attribute on the twin while the test exercises the
# canonical module, so the patch quietly does nothing and the test passes
# without testing what it names.

# ⚠ Assembled from parts on purpose. Written as one literal it is a string
# constant starting with the twin root, so this file would flag ITSELF -- and
# the alternatives are worse: exempting the guard from its own rule, or
# special-casing its filename, both of which stop it policing itself.
_TWIN_ROOT = "src." + "jcodemunch_mcp"

# Modules allowed to import the twin, named with the reason. Empty is the
# intended end state; an entry here is a decision, never an omission.
TWIN_EXEMPT: dict[str, str] = {}


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """ids of Constant nodes that are docstrings, so prose is not flagged."""
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None) or []
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            if isinstance(body[0].value.value, str):
                out.add(id(body[0].value))
    return out


def _twin_reference_lines(path: Path) -> list[int]:
    """Lines importing the twin, or naming it as a patch target.

    ⚠ Matches a string only when it STARTS with the twin root, which is the
    shape of a `patch()` target. Prose that mentions the spelling mid-sentence
    -- including this file's own commentary -- is deliberately not a hit, and
    docstrings are skipped outright.
    """
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError):
        return []

    skip = _docstring_nodes(tree)
    hits: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == _TWIN_ROOT or mod.startswith(_TWIN_ROOT + "."):
                hits.add(node.lineno)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == _TWIN_ROOT or alias.name.startswith(_TWIN_ROOT + "."):
                    hits.add(node.lineno)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in skip:
                continue
            if node.value.startswith(_TWIN_ROOT):
                hits.add(node.lineno)
    return sorted(hits)


_TWIN_IMPORTERS = {
    p.name: _twin_reference_lines(p)
    for p in _test_modules()
    if _twin_reference_lines(p)
}


def test_no_test_module_imports_the_package_twin():
    offenders = {n: ls for n, ls in _TWIN_IMPORTERS.items() if n not in TWIN_EXEMPT}
    assert not offenders, (
        "these modules reach the package as `src.jcodemunch_mcp`, which is a "
        "DIFFERENT module object from `jcodemunch_mcp` with its own "
        "_GLOBAL_CONFIG that conftest never resets -- so they read the "
        f"developer's real ~/.code-index/config.jsonc: {offenders}. Import "
        "`jcodemunch_mcp...` instead (and use it in patch() targets), or add "
        "the module to TWIN_EXEMPT in this file with a reason."
    )


@pytest.mark.parametrize("name", sorted(TWIN_EXEMPT), ids=lambda n: n)
def test_twin_exemptions_are_not_stale(name: str):
    """The ratchet. Parametrizing over an empty TWIN_EXEMPT SKIPS, which is this
    ratchet at rest -- not a lost test. It re-arms the moment anyone adds one."""
    path = _TESTS_DIR / name
    assert path.exists(), f"{name} is gone; remove it from TWIN_EXEMPT ({TWIN_EXEMPT[name]})"
    assert _twin_reference_lines(path), (
        f"{name} no longer references {_TWIN_ROOT}, so its exemption is spent. "
        f"Remove it from TWIN_EXEMPT: {TWIN_EXEMPT[name]}"
    )


def test_the_twin_guard_can_fail(tmp_path):
    """Prove the detector fires, and that it does not fire on prose.

    A guard that cannot fail reads as coverage while providing none -- the #453
    lesson, where a tripwire raised an AssertionError that production's bare
    `except Exception` swallowed.
    """
    from_import = tmp_path / "test_from.py"
    from_import.write_text(
        "from src.jcodemunch_mcp.parser.extractor import parse_file\n",
        encoding="utf-8",
    )
    plain_import = tmp_path / "test_plain.py"
    plain_import.write_text("import src.jcodemunch_mcp.config as c\n", encoding="utf-8")
    patch_target = tmp_path / "test_patch.py"
    patch_target.write_text(
        "from unittest.mock import patch\n"
        "def test_x():\n"
        '    with patch("src.jcodemunch_mcp.config.get"):\n'
        "        pass\n",
        encoding="utf-8",
    )
    canonical = tmp_path / "test_ok.py"
    canonical.write_text(
        '"""Explains why src.jcodemunch_mcp is wrong, in a docstring."""\n'
        "# and in a comment: never import src.jcodemunch_mcp\n"
        "from jcodemunch_mcp.parser.extractor import parse_file\n",
        encoding="utf-8",
    )

    assert _twin_reference_lines(from_import) == [1]
    assert _twin_reference_lines(plain_import) == [1]
    assert _twin_reference_lines(patch_target) == [3]
    # Prose naming the hazard is not the hazard.
    assert _twin_reference_lines(canonical) == []
