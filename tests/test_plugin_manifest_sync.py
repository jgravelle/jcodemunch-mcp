"""The Claude Code plugin manifest must stay in lockstep with pyproject.toml.

`plugin/jcodemunch/.claude-plugin/plugin.json` pins the plugin version. Claude Code caches the
plugin against that string, so users receive updates ONLY when it changes.
A stale version here means every user stays on an old copy while PyPI moves,
which is the same failure server.json had for five months.

The MCP config is checked too: it names the PyPI package that `uvx` resolves,
so a rename in pyproject.toml without a matching edit here ships a plugin that
launches a package that no longer exists.

The config is declared INLINE in plugin.json rather than in a root `.mcp.json`.
A `.mcp.json` at a repository root is also read as project-scoped config, so
that file would offer a duplicate server to anyone opening this repo in Claude
Code. `test_no_root_mcp_json` holds that line.
"""

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PLUGIN_JSON = REPO_ROOT / "plugin" / "jcodemunch" / ".claude-plugin" / "plugin.json"
PYPROJECT = REPO_ROOT / "pyproject.toml"


def _pyproject_field(field):
    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(rf'^{field}\s*=\s*"([^"]+)"', text, re.M)
    assert match, f"Could not read {field} from {PYPROJECT}"
    return match.group(1)


def test_plugin_manifest_exists():
    assert PLUGIN_JSON.exists(), (
        "plugin/jcodemunch/.claude-plugin/plugin.json is the Claude Code plugin manifest"
    )


def test_plugin_version_matches_pyproject():
    manifest = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
    expected = _pyproject_field("version")
    assert manifest["version"] == expected, (
        f"plugin.json version {manifest['version']!r} != pyproject.toml "
        f"{expected!r}. Claude Code caches against this string, so users get "
        f"no updates until it changes."
    )


def test_plugin_description_is_set():
    """The plugin directory shows this text as the listing's summary.

    It used to have to equal pyproject's description. The listing is written
    for a person choosing a plugin and the package summary for PyPI, so they
    may differ; an empty one may not.
    """
    manifest = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
    assert manifest["description"].strip()


def test_no_root_mcp_json():
    """A root .mcp.json would also register as project-scoped config.

    That offers a duplicate server to anyone opening this repo in Claude Code,
    on top of whatever they already have configured. The plugin's MCP config
    belongs inline in plugin.json, where only plugin installs see it.
    """
    assert not (REPO_ROOT / ".mcp.json").exists(), (
        "Declare mcpServers inline in the plugin's plugin.json instead; a "
        "root .mcp.json leaks into project scope for anyone opening this repo"
    )


def test_marketplace_uses_no_github_sources():
    """A `github` plugin source clones over SSH and fails without a key.

    Measured: `claude plugin install jdocmunch@jmunch` against a github source
    died on `git@github.com: Permission denied (publickey)`. The marketplace
    fetch itself falls back to HTTPS ("SSH not configured, cloning via HTTPS")
    but the plugin-source fetch does not, so a public marketplace built on
    github sources is installable only by users who happen to have an SSH key.
    Use an explicit `url` source with an https:// URL instead.
    """
    marketplace = REPO_ROOT / ".claude-plugin" / "marketplace.json"
    if not marketplace.exists():
        return  # only the marketplace-hosting repo carries this file

    data = json.loads(marketplace.read_text(encoding="utf-8"))
    for entry in data["plugins"]:
        source = entry["source"]
        if isinstance(source, dict):
            assert source["source"] != "github", (
                f"plugin {entry['name']!r} uses a github source, which clones "
                f"over SSH; use {{'source': 'url', 'url': 'https://...'}}"
            )
            if source["source"] == "url":
                assert source["url"].startswith("https://"), (
                    f"plugin {entry['name']!r} source url must be https"
                )


def test_mcp_config_launches_this_package():
    manifest = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
    package = _pyproject_field("name")

    servers = manifest["mcpServers"]
    assert len(servers) == 1, f"expected exactly one server entry, got {list(servers)}"

    entry = next(iter(servers.values()))
    assert f"{package}=={_pyproject_field('version')}" in entry["args"], (
        f".mcp.json args {entry['args']!r} do not name the package {package!r} "
        f"from pyproject.toml"
    )
