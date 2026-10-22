"""(L-203) A backup is the file's own name plus ``.bak``.

`install_windsurf_rules` and `uninstall_windsurf_rules` built the backup with
``path.with_suffix(".windsurfrules.bak")``. ``.windsurfrules`` is a dotfile and
has no suffix, so ``with_suffix`` appended instead of replacing and the backup
was ``.windsurfrules.windsurfrules.bak``. Every other backup site guessed the
file's suffix the same way and was right only because the guess matched. The
rule now lives in one helper, `cli.backup.backup_path` (re-exported as
`cli.init._backup_path`), and the scan below
fails if a ``with_suffix`` backup comes back anywhere under ``cli/``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from jcodemunch_mcp.cli import init

CLI_DIR = Path(init.__file__).parent


def _patch(monkeypatch, target: Path) -> None:
    monkeypatch.setattr("jcodemunch_mcp.cli.init._windsurf_rules_path", lambda: target)


def test_install_backs_up_windsurfrules_under_its_own_name(tmp_path, monkeypatch):
    target = tmp_path / ".windsurfrules"
    target.write_text("# my rules\n", encoding="utf-8")
    _patch(monkeypatch, target)

    init.install_windsurf_rules(backup=True)

    assert (tmp_path / ".windsurfrules.bak").read_text(encoding="utf-8") == "# my rules\n"
    assert not (tmp_path / ".windsurfrules.windsurfrules.bak").exists()


def test_uninstall_backs_up_windsurfrules_under_its_own_name(tmp_path, monkeypatch):
    target = tmp_path / ".windsurfrules"
    target.write_text("# my rules\n", encoding="utf-8")
    _patch(monkeypatch, target)
    init.install_windsurf_rules(backup=False)
    before = target.read_text(encoding="utf-8")

    init.uninstall_windsurf_rules(backup=True)

    assert (tmp_path / ".windsurfrules.bak").read_text(encoding="utf-8") == before
    assert not (tmp_path / ".windsurfrules.windsurfrules.bak").exists()


@pytest.mark.parametrize(
    "name",
    [".windsurfrules", "CLAUDE.md", "settings.local.json", "jcodemunch.mdc", "config.toml", "Makefile"],
)
def test_backup_path_appends_bak_to_the_whole_name(tmp_path, name):
    assert init._backup_path(tmp_path / name) == tmp_path / f"{name}.bak"


def _with_suffix_backups(source: str) -> list[int]:
    """Lines that CALL ``.with_suffix(...)`` with anything but a plain literal free of ``.bak``.

    A literal that mentions ``.bak`` is the old spelling. An argument that is
    not a literal at all (a name, an expression) is flagged too, because the
    scan cannot see what it holds: ``BAK = ".md.bak"; p.with_suffix(BAK)`` is
    the same defect one assignment away. Read from the syntax tree, not the
    text: the helper's docstring quotes the old spelling, and a text scan fails
    on the explanation instead of the defect.
    """
    hits = []
    for node in ast.walk(ast.parse(source)):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "with_suffix"
        ):
            continue
        plain = (
            len(node.args) == 1
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and ".bak" not in node.args[0].value
        )
        if not plain:
            hits.append(node.lineno)
    return hits


def test_no_cli_module_builds_a_backup_with_with_suffix():
    """The class guard: a backup named by guessing the file's suffix is the defect."""
    offenders = [
        f"{p.name}:{line}"
        for p in sorted(CLI_DIR.rglob("*.py"))
        for line in _with_suffix_backups(p.read_text(encoding="utf-8"))
    ]
    assert offenders == [], (
        f"{offenders}: under cli/, with_suffix may take only a plain string literal "
        "with no '.bak' in it. A backup is named with cli/backup.backup_path, which "
        "appends .bak to the whole name; a suffix held in a name or built at run "
        "time is refused because this scan cannot see what it holds."
    )


@pytest.mark.parametrize(
    "line",
    [
        'p.with_suffix(".windsurfrules.bak")',
        'p.with_suffix(p.suffix + ".bak")',
        'shutil.copy2(p, p.with_suffix(".md.bak"))',
        'p.with_suffix(BAK)',
        'p.with_suffix(f"{ext}.bak")',
    ],
)
def test_the_guard_sees_each_old_spelling(line):
    """Non-vacuity: every spelling this fix removed is caught by the scan,
    and so is a suffix held in a name or built at run time."""
    assert _with_suffix_backups(line) == [1]


def test_the_guard_allows_a_plain_non_backup_suffix():
    """`policy_refresh` writes a temp file with `.with_suffix(".json.tmp")`; that is not a backup."""
    assert _with_suffix_backups('marker.with_suffix(".json.tmp")') == []
