"""Build the thin MCPB bundle for Smithery's local-stdio publish path.

Smithery retired `smithery.yaml`; local stdio servers are distributed as a
prebuilt `.mcpb` bundle instead. This builds a THIN bundle: the manifest's
mcp_config launches `uvx <package>`, so nothing is vendored and one artifact
covers darwin/win32/linux.

The tradeoff, recorded deliberately: a vendored bundle would need a
per-platform matrix (native dependencies ship platform-specific wheels) and a
rebuild on every release. These packages ship constantly, so a vendored bundle would go stale
the same way the MCP registry entry did. The thin bundle tracks whatever is
latest on PyPI and needs no rebuild.

Cost of the tradeoff: uv must be present on the user's machine. The MCPB spec
says dependencies "must be bundled", so Smithery may reject this on scan. If
it does, that is the signal to reconsider the vendored path.

Usage:  python mcpb/build.py
Output: dist/<name>-<version>.mcpb
"""

import json
import re
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
MCPB_DIR = REPO_ROOT / "mcpb"
MANIFEST_VERSION = "0.3"  # schemas/mcpb-manifest-latest.schema.json pins 0.3


def pyproject_field(field):
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(rf'^{field}\s*=\s*"([^"]+)"', text, re.M)
    assert match, f"Could not read {field} from pyproject.toml"
    return match.group(1)


def build_manifest():
    name = pyproject_field("name")
    version = pyproject_field("version")
    server_json = json.loads((REPO_ROOT / "server.json").read_text(encoding="utf-8"))

    return {
        "manifest_version": MANIFEST_VERSION,
        "name": name,
        "display_name": server_json["title"],
        "version": version,
        "description": pyproject_field("description"),
        "author": {
            "name": "J. Gravelle",
            "url": "https://github.com/jgravelle",
        },
        "repository": {
            "type": "git",
            "url": f"https://github.com/jgravelle/{name}",
        },
        "homepage": f"https://github.com/jgravelle/{name}",
        "documentation": f"https://github.com/jgravelle/{name}#readme",
        "support": f"https://github.com/jgravelle/{name}/issues",
        "license": "LicenseRef-Dual-Use",
        "server": {
            "type": "python",
            "entry_point": "server/main.py",
            "mcp_config": {
                "command": "uvx",
                "args": [name],
            },
        },
        "compatibility": {
            "platforms": ["darwin", "win32", "linux"],
            "runtimes": {"python": ">=3.10"},
        },
    }


def main():
    manifest = build_manifest()
    (MCPB_DIR / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )

    dist = REPO_ROOT / "dist"
    dist.mkdir(exist_ok=True)
    out = dist / f"{manifest['name']}-{manifest['version']}.mcpb"

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as bundle:
        bundle.write(MCPB_DIR / "manifest.json", "manifest.json")
        bundle.write(MCPB_DIR / "server" / "main.py", "server/main.py")

    print(f"{out}  ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()

# Fix for issue #476: safe input handling
