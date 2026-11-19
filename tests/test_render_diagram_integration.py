"""Integration test: render_diagram with open_in_viewer using real mmd-viewer binary."""

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest


VIEWER_PATH = os.environ.get("MMD_VIEWER_PATH", "")


def _skip_if_no_viewer():
    if not VIEWER_PATH:
        pytest.skip("set MMD_VIEWER_PATH to enable render_diagram integration test")
    if not Path(VIEWER_PATH).exists():
        pytest.skip(
            f"mmd-viewer.exe not found at {VIEWER_PATH} "
            f"(update MMD_VIEWER_PATH to point at your local build)"
        )


def _call_hierarchy_source():
    """Minimal call hierarchy output for render_diagram."""
    return {
        "repo": "test/repo",
        "symbol": {"id": "app.py::main", "name": "main", "kind": "function", "file": "app.py", "line": 1},
        "direction": "both",
        "depth": 3,
        "depth_reached": 1,
        "caller_count": 1,
        "callee_count": 1,
        "callers": [{"id": "cli.py::run", "name": "run", "kind": "function", "file": "cli.py", "line": 5, "depth": 1, "resolution": "ast_resolved"}],
        "callees": [{"id": "db.py::connect", "name": "connect", "kind": "function", "file": "db.py", "line": 10, "depth": 1, "resolution": "ast_resolved"}],
        "dispatches": [],
    }


@pytest.mark.asyncio
async def test_render_diagram_opens_real_viewer(tmp_path):
    """End-to-end: call render_diagram with open_in_viewer=True, verify viewer spawns and file is created."""
    _skip_if_no_viewer()

    from jcodemunch_mcp import config as config_module
    from jcodemunch_mcp.server import call_tool, list_tools

    orig_config = config_module._GLOBAL_CONFIG.copy()
    config_module._GLOBAL_CONFIG.clear()
    config_module._GLOBAL_CONFIG.update(config_module.DEFAULTS.copy())

    storage = tmp_path / "storage"
    storage.mkdir()

    old_path = os.environ.get("CODE_INDEX_PATH")
    os.environ["CODE_INDEX_PATH"] = str(storage)

    try:
        config_module._GLOBAL_CONFIG["render_diagram_viewer_enabled"] = True
        config_module._GLOBAL_CONFIG["mermaid_viewer_path"] = VIEWER_PATH
        config_module._GLOBAL_CONFIG["disabled_tools"] = []

        from jcodemunch_mcp import server as server_mod
        import importlib
        importlib.reload(server_mod)

        tools = await server_mod.list_tools()
        render_tool = next(t for t in tools if t.name == "render_diagram")
        assert "open_in_viewer" in render_tool.input_schema["properties"]

        source = _call_hierarchy_source()
        result = await server_mod.call_tool("render_diagram", {
            "source": source,
            "theme": "flow",
            "max_nodes": 80,
            "open_in_viewer": True,
        })

        payload = json.loads(result[0].text)

        assert "mermaid" in payload
        assert payload["mermaid"].startswith("flowchart TD")

        assert "viewer_path" in payload, f"Expected viewer_path in response, got: {payload}"
        assert payload["viewer_path"].endswith(".mmd")
        assert "jcm-diagram-" in payload["viewer_path"]

        mmd_file = Path(payload["viewer_path"])
        assert mmd_file.exists(), f"Expected {mmd_file} to exist"
        assert mmd_file.read_text(encoding="utf-8").startswith("flowchart TD")

        # Verify selective cleanup: jcm- files removed, others preserved
        temp_dir = storage / "temp" / "mermaid"
        assert temp_dir.exists()

        # Count jcm files before cleanup
        jcm_files_before = [f for f in temp_dir.iterdir() if f.is_file() and f.name.startswith("jcm-")]
        assert len(jcm_files_before) >= 1, f"Expected at least 1 jcm- file, found: {[f.name for f in temp_dir.iterdir()]}"

        # Drop a non-jcm file to verify it survives cleanup
        (temp_dir / "other-tool.txt").write_text("leave me")

        # Import fresh module for cleanup test
        import jcodemunch_mcp.tools.mermaid_viewer as mv
        importlib.reload(mv)

        # Small delay to let viewer process release file lock (Windows)
        time.sleep(1)

        removed = mv.cleanup_temp_dir(storage_path=storage)
        assert removed >= 1

        # Non-jcm file still there
        assert (temp_dir / "other-tool.txt").exists()

        # Our jcm file is gone
        assert not mmd_file.exists()

    finally:
        if old_path is not None:
            os.environ["CODE_INDEX_PATH"] = old_path
        else:
            os.environ.pop("CODE_INDEX_PATH", None)
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(orig_config)


@pytest.mark.asyncio
async def test_render_diagram_viewer_false_still_returns_mermaid(tmp_path):
    """open_in_viewer=False returns mermaid without touching the viewer."""
    from jcodemunch_mcp import config as config_module
    from jcodemunch_mcp.server import call_tool

    orig_config = config_module._GLOBAL_CONFIG.copy()
    config_module._GLOBAL_CONFIG.clear()
    config_module._GLOBAL_CONFIG.update(config_module.DEFAULTS.copy())

    try:
        config_module._GLOBAL_CONFIG["render_diagram_viewer_enabled"] = True
        config_module._GLOBAL_CONFIG["mermaid_viewer_path"] = VIEWER_PATH
        config_module._GLOBAL_CONFIG["disabled_tools"] = []

        from jcodemunch_mcp import server as server_mod
        import importlib
        importlib.reload(server_mod)

        result = await server_mod.call_tool("render_diagram", {
            "source": _call_hierarchy_source(),
            "open_in_viewer": False,
        })

        payload = json.loads(result[0].text)
        assert "mermaid" in payload
        assert "viewer_path" not in payload
        assert "viewer_error" not in payload

    finally:
        config_module._GLOBAL_CONFIG.clear()
        config_module._GLOBAL_CONFIG.update(orig_config)
