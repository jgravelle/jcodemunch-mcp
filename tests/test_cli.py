"""CLI behavior tests."""

import pytest

from jcodemunch_mcp.server import main


def test_main_help_exits_without_starting_server(capsys):
    """`--help` should print usage and exit cleanly."""
    with pytest.raises(SystemExit) as exc:
        main(["--help"])

    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "jcodemunch-mcp" in out
    assert "Run the jCodeMunch MCP server" in out


def test_main_version_exits_with_version(capsys):
    """`--version` should print package version and exit cleanly."""
    with pytest.raises(SystemExit) as exc:
        main(["--version"])

    assert exc.value.code == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("jcodemunch-mcp ")
