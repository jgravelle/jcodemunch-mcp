"""The plugin and the MCP registry entry show the same icon, from one file.

The plugin directory reads `icon` from `plugin.json` as a path inside the
plugin folder; the MCP registry reads `icons[].src` from `server.json` as an
HTTPS URL. Both name the same tracked SVG, so the two listings cannot drift
apart, and the registry URL resolves once the file is on `main`.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO_ROOT / "plugin" / "jcodemunch"
MANIFEST = PLUGIN_DIR / ".claude-plugin" / "plugin.json"
SERVER_JSON = REPO_ROOT / "server.json"
RAW_PREFIX = "https://raw.githubusercontent.com/jgravelle/jcodemunch-mcp/main/"
SVG_NS = "{http://www.w3.org/2000/svg}"

# The registry schema (2025-12-11) allows these MIME types for an icon.
REGISTRY_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _server() -> dict:
    return json.loads(SERVER_JSON.read_text(encoding="utf-8"))


def _icon_file() -> Path:
    rel = _manifest()["icon"]
    return (PLUGIN_DIR / rel).resolve()


def test_the_plugin_icon_is_a_relative_path_to_a_file_inside_the_folder():
    rel = _manifest().get("icon")
    assert isinstance(rel, str) and rel.startswith("./"), rel
    path = _icon_file()
    assert path.is_file(), path
    assert PLUGIN_DIR.resolve() in path.parents, "icon must sit inside the plugin folder"


def test_the_plugin_icon_is_an_svg_named_for_the_product():
    path = _icon_file()
    assert path.suffix == ".svg"
    root = ET.fromstring(path.read_text(encoding="utf-8"))
    assert root.tag == f"{SVG_NS}svg"
    title = root.find(f"{SVG_NS}title")
    assert title is not None and title.text == "jCodeMunch"
    assert "draft" not in path.read_text(encoding="utf-8").lower()


def test_the_plugin_icon_carries_no_script_or_external_reference():
    # Both listings render this file; an SVG can run script and fetch URLs.
    allowed = {"svg", "title", "rect", "g", "path", "circle", "ellipse", "line", "polyline", "polygon"}
    root = ET.fromstring(_icon_file().read_text(encoding="utf-8"))
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        assert tag in allowed, f"unexpected element <{tag}>"
        for name, value in el.attrib.items():
            local = name.split("}")[-1].lower()
            assert not local.startswith("on"), f"event handler {name} on <{tag}>"
            assert local != "href", f"link {name} on <{tag}>"
            assert "url(" not in value.lower(), f"url() in {name} on <{tag}>"


def test_the_registry_icon_points_at_the_same_file_on_main():
    icons = _server().get("icons")
    assert isinstance(icons, list) and len(icons) == 1, icons
    icon = icons[0]
    src = icon["src"]
    assert src.startswith("https://") and len(src) <= 255
    rel = _icon_file().relative_to(REPO_ROOT)
    assert src == RAW_PREFIX + PurePosixPath(*rel.parts).as_posix()
    assert icon["mimeType"] == REGISTRY_MIME[_icon_file().suffix]
    assert icon["sizes"] == ["any"]
