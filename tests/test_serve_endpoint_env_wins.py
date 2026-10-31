"""`serve` picks transport, host and port with the launching client's env var
ahead of the config file (L-258).

That's deliberate: an MCP client that launches the server with
`JCODEMUNCH_PORT` set must get that port, whatever a shared config.jsonc says.
It's the opposite of the fallback rule every other key follows (#1252 moved
`rate_limit` onto that rule), so these tests pin the order and check that
`jcodemunch-mcp config` shows the endpoint the server will actually use.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

from jcodemunch_mcp import config as config_module
from jcodemunch_mcp import server


@pytest.fixture
def store(tmp_path: Path, monkeypatch) -> Path:
    (tmp_path / "config.jsonc").write_text(
        '{"transport": "sse", "host": "10.0.0.5", "port": 9200}', encoding="utf-8"
    )
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path))
    for var in ("JCODEMUNCH_TRANSPORT", "JCODEMUNCH_HOST", "JCODEMUNCH_PORT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    config_module.load_config(str(tmp_path))
    yield tmp_path
    config_module.load_config(str(tmp_path))


def _row(out: str, key: str) -> str:
    plain = re.sub(r"\x1b\[[0-9;]*m", "", out)
    line = next(ln for ln in plain.splitlines() if ln.strip().startswith(key + " "))
    return " ".join(line.split())


def test_serve_takes_the_env_var_over_the_config_file(store, monkeypatch):
    monkeypatch.setenv("JCODEMUNCH_PORT", "9100")
    monkeypatch.setenv("JCODEMUNCH_HOST", "127.0.0.9")
    monkeypatch.setenv("JCODEMUNCH_TRANSPORT", "streamable-http")
    assert server._resolve_serve_endpoint(argparse.Namespace()) == (
        "streamable-http",
        "127.0.0.9",
        9100,
    )


def test_serve_uses_the_config_file_when_no_env_var_is_set(store):
    assert server._resolve_serve_endpoint(argparse.Namespace()) == ("sse", "10.0.0.5", 9200)


def test_config_shows_the_endpoint_serve_will_use(store, monkeypatch, capsys):
    monkeypatch.setenv("JCODEMUNCH_PORT", "9100")
    monkeypatch.setenv("JCODEMUNCH_HOST", "127.0.0.9")
    server._run_config()
    out = capsys.readouterr().out
    port, host = _row(out, "port"), _row(out, "host")
    assert "9100" in port and "env" in port, port
    assert "127.0.0.9" in host and "env" in host, host
    assert "sse" in _row(out, "transport") and "config" in _row(out, "transport")


def test_config_shows_the_file_value_when_no_env_var_is_set(store, capsys):
    server._run_config()
    out = capsys.readouterr().out
    assert "9200" in _row(out, "port") and "config" in _row(out, "port")


def test_an_unparsable_port_env_var_does_not_claim_the_row(store, monkeypatch, capsys):
    monkeypatch.setenv("JCODEMUNCH_PORT", "not-a-port")
    assert server._resolve_serve_endpoint(argparse.Namespace())[2] == 9200
    server._run_config()
    port = _row(capsys.readouterr().out, "port")
    assert "9200" in port and "env" not in port, port


def test_configuration_md_names_the_exception():
    doc = (Path(__file__).resolve().parents[1] / "CONFIGURATION.md").read_text(encoding="utf-8")
    assert "the launching client's env var wins" in doc
