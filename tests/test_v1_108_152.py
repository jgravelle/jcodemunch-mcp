"""v1.108.152 — munch.runtime.identity/v1 resource (#371)."""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from jcodemunch_mcp import runtime_identity


@pytest.fixture(autouse=True)
def _reset_identity_state():
    saved = (
        runtime_identity._instance_id,
        runtime_identity._process_start,
        runtime_identity._transport,
    )
    yield
    (
        runtime_identity._instance_id,
        runtime_identity._process_start,
        runtime_identity._transport,
    ) = saved


def test_payload_carries_required_fields():
    payload = runtime_identity.identity_payload()
    assert payload["schema"] == "munch.runtime.identity/v1"
    assert payload["product"] == "jcodemunch-mcp"
    assert isinstance(payload["version"], str) and payload["version"]
    assert isinstance(payload["transport"], str) and payload["transport"]
    assert payload["pid"] == os.getpid()
    ps = payload["process_start"]
    assert set(ps.keys()) == {"value", "source"}
    assert ps["source"] in ("os", "self_recorded")
    assert isinstance(payload["instance_id"], str) and len(payload["instance_id"]) == 36


def test_repeated_reads_are_stable(monkeypatch):
    monkeypatch.delenv("JCODEMUNCH_LAUNCH_ID", raising=False)
    monkeypatch.delenv("MUNCH_LAUNCH_ID", raising=False)
    first = runtime_identity.identity_payload()
    second = runtime_identity.identity_payload()
    assert first == second


def test_excluded_context_never_present():
    payload = runtime_identity.identity_payload()
    text = json.dumps(payload).lower()
    for banned in ("cwd", "hostname", "argv", "command"):
        assert banned not in payload
        assert f'"{banned}"' not in text


def test_launch_id_echoed_from_product_env(monkeypatch):
    monkeypatch.setenv("JCODEMUNCH_LAUNCH_ID", "harness-abc")
    monkeypatch.setenv("MUNCH_LAUNCH_ID", "generic-xyz")
    assert runtime_identity.identity_payload()["launch_id"] == "harness-abc"


def test_launch_id_generic_fallback(monkeypatch):
    monkeypatch.delenv("JCODEMUNCH_LAUNCH_ID", raising=False)
    monkeypatch.setenv("MUNCH_LAUNCH_ID", "generic-xyz")
    assert runtime_identity.identity_payload()["launch_id"] == "generic-xyz"


def test_launch_id_omitted_when_unset(monkeypatch):
    monkeypatch.delenv("JCODEMUNCH_LAUNCH_ID", raising=False)
    monkeypatch.delenv("MUNCH_LAUNCH_ID", raising=False)
    assert "launch_id" not in runtime_identity.identity_payload()


def test_process_start_is_os_derived_on_supported_platforms():
    if (
        os.name != "nt"
        and not os.path.exists("/proc/self/stat")
        and sys.platform != "darwin"
    ):
        pytest.skip("no OS probe on this platform")
    runtime_identity._process_start = None
    ps = runtime_identity.get_process_start()
    assert ps["source"] == "os"
    # ISO-8601 UTC
    assert ps["value"].endswith("+00:00")


def test_probe_failure_discloses_self_recorded(monkeypatch):
    def _boom():
        raise OSError("probe unavailable")

    monkeypatch.setattr(runtime_identity, "_windows_process_start", _boom)
    monkeypatch.setattr(runtime_identity, "_linux_process_start", _boom)
    monkeypatch.setattr(runtime_identity, "_darwin_process_start", _boom)
    runtime_identity._process_start = None
    ps = runtime_identity.get_process_start()
    assert ps["source"] == "self_recorded"
    assert ps["value"].endswith("+00:00")


def test_set_transport_reflected():
    runtime_identity.set_transport("streamable-http")
    assert runtime_identity.identity_payload()["transport"] == "streamable-http"


def test_two_processes_get_distinct_instance_ids():
    src = str(Path(__file__).resolve().parents[1] / "src")
    env = dict(os.environ, PYTHONPATH=src)
    code = (
        "from jcodemunch_mcp import runtime_identity as r;"
        "import json;print(json.dumps(r.identity_payload()))"
    )
    outs = [
        json.loads(
            subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                env=env,
                check=True,
                stdin=subprocess.DEVNULL,
            ).stdout
        )
        for _ in range(2)
    ]
    assert outs[0]["instance_id"] != outs[1]["instance_id"]


def test_list_resources_advertises_identity():
    from jcodemunch_mcp.server import list_resources

    resources = asyncio.run(list_resources())
    assert len(resources) == 1
    assert str(resources[0].uri) == runtime_identity.IDENTITY_URI
    assert resources[0].mimeType == "application/json"


def test_read_resource_returns_identity_json():
    from jcodemunch_mcp.server import read_resource

    contents = asyncio.run(read_resource(runtime_identity.IDENTITY_URI))
    assert len(contents) == 1
    assert contents[0].mime_type == "application/json"
    payload = json.loads(contents[0].content)
    assert payload["schema"] == runtime_identity.IDENTITY_SCHEMA
    assert payload["pid"] == os.getpid()


def test_read_resource_unknown_uri_raises():
    from jcodemunch_mcp.server import read_resource

    with pytest.raises(ValueError):
        asyncio.run(read_resource("munch://runtime/other"))


def test_identity_json_is_read_only(tmp_path, monkeypatch):
    monkeypatch.setenv("CODE_INDEX_PATH", str(tmp_path))
    runtime_identity.identity_json()
    assert list(tmp_path.iterdir()) == []
