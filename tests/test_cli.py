"""CLI behavior tests."""

import json

import pytest

from jcodemunch_mcp.server import main


def test_main_help_exits_without_starting_server(capsys):
    """`--help` should print usage and exit cleanly."""
    with pytest.raises(SystemExit) as exc:
        main(["--help"])

    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "jcodemunch-mcp" in out
    assert "Run the jCodeMunch MCP stdio server" in out


def test_main_version_exits_with_version(capsys):
    """`--version` should print package version and exit cleanly."""
    with pytest.raises(SystemExit) as exc:
        main(["--version"])

    assert exc.value.code == 0
    out = capsys.readouterr().out.strip()
    assert out.startswith("jcodemunch-mcp ")


def test_main_token_stats_outputs_pretty_json(capsys, monkeypatch, tmp_path):
    """`--token-stats --output-format json` should print summary JSON and exit."""
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path))
    (tmp_path / "_savings.json").write_text(json.dumps({"total_tokens_saved": 12345}))

    main(["--token-stats", "--output-format", "json"])

    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["total_tokens_saved"] == 12345
    assert parsed["equivalent_context_windows"]["32k"] > 0
    assert "pricing_usd_per_token" not in parsed


def test_main_token_stats_all_json_outputs_full_report(capsys, monkeypatch, tmp_path):
    """`--token-stats-all --output-format json` should include full report fields."""
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path))
    (tmp_path / "_savings.json").write_text(json.dumps({"total_tokens_saved": 42}))

    main(["--token-stats-all", "--output-format", "json"])

    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["total_tokens_saved"] == 42
    assert "pricing_usd_per_token" in parsed
    assert "savings_file" in parsed


def test_main_token_stats_text_outputs_human_readable(capsys, monkeypatch, tmp_path):
    """`--token-stats` text mode should print readable text and exit."""
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path))
    (tmp_path / "_savings.json").write_text(json.dumps({"total_tokens_saved": 98765}))

    main(["--token-stats"])

    out = capsys.readouterr().out
    assert "jCodeMunch Token Savings" in out
    assert "Total tokens saved: 98,765" in out
    assert "Savings file:" not in out


def test_main_help_lists_defaults_and_explainer(capsys):
    """`--help` should show defaults plus readable token-stats field descriptions."""
    with pytest.raises(SystemExit) as exc:
        main(["--help"])

    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "default: text" in out
    assert "token-stats fields:" in out
    assert "token-stats fields:\njCodeMunch Token Savings\n------------------------" in out
    assert "Cost avoided (Claude Opus):" in out
