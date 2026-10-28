"""The Claude Code plugin meets the plugin directory's submission rules.

Anthropic's plugin directory validates a plugin folder before review, and
our plugin used to be the whole repository root: 1,400+ files against a limit
of 512, and a `uvx jcodemunch-mcp` launcher with no version, which the
directory refuses. The plugin now lives in `plugin/jcodemunch/`. These tests
hold the rules we can check from the repository:

- the launcher is pinned to the exact released version;
- the folder carries the LICENSE, byte-identical to the root one;
- the README is long enough to be the listing's description and names
  everything the plugin runs, sends and fetches;
- the plugin turns the savings counter off, so it makes no network call of
  its own (owner ruling, 2026-10-27);
- every file stays small, readable and inside the folder;
- the marketplace this repository hosts installs that same folder.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PLUGIN_DIR = REPO_ROOT / "plugin" / "jcodemunch"
MANIFEST = PLUGIN_DIR / ".claude-plugin" / "plugin.json"
README = PLUGIN_DIR / "README.md"


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _server() -> dict:
    servers = _manifest()["mcpServers"]
    assert len(servers) == 1
    return next(iter(servers.values()))


def test_the_name_is_lowercase_letters_digits_and_hyphens():
    assert re.fullmatch(r"[a-z0-9-]+", _manifest()["name"])


def test_the_uvx_launcher_is_pinned_to_an_exact_version():
    entry = _server()
    assert entry["command"] == "uvx"
    pinned = [
        a for a in entry["args"] if re.fullmatch(r"jcodemunch-mcp==\d+(\.\d+)+", a)
    ]
    assert pinned, (
        f"no exact pin in {entry['args']!r}; the directory refuses a range or latest"
    )
    assert pinned[0].split("==", 1)[1] == _manifest()["version"]


def test_the_plugin_turns_the_savings_counter_off():
    assert _server().get("env", {}).get("JCODEMUNCH_SHARE_SAVINGS") == "0"


def test_the_license_travels_with_the_plugin_unchanged():
    def norm(p: Path) -> str:
        return p.read_text(encoding="utf-8").replace("\r\n", "\n")

    assert norm(PLUGIN_DIR / "LICENSE") == norm(REPO_ROOT / "LICENSE"), (
        "plugin/jcodemunch/LICENSE differs from LICENSE; copy it again"
    )


def test_the_readme_is_long_enough_to_be_the_listing():
    text = re.sub(r"```.*?```", "", README.read_text(encoding="utf-8"), flags=re.S)
    assert len(text.split()) >= 40


def test_the_readme_names_everything_the_plugin_runs_sends_and_fetches():
    """The directory's security scan rejects an undisclosed destination."""
    text = README.read_text(encoding="utf-8")
    for must in (
        "pypi.org",  # uvx's one download
        "JCODEMUNCH_SHARE_SAVINGS=0",
        "~/.code-index/",
        "j.gravelle.us",  # license check, user-run
        "jcodemunch.com",  # index packs, user-run
        "huggingface.co",  # embedding model, user-run
        "git",
        "SECURITY.md",
    ):
        assert must in text, f"plugin README does not mention {must!r}"


def test_the_plugin_ships_no_hooks():
    """A published plugin was refused review for a hook that ran on permission
    checks (R&D note, 2026-10-28). Ours adds none; the steering hooks stay
    opt-in through `init --guidance`, and the README says so."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert "hooks" not in manifest
    assert not (PLUGIN_DIR / "hooks").exists()
    assert not any(p.name == "hooks.json" for p in PLUGIN_DIR.rglob("*"))
    text = README.read_text(encoding="utf-8")
    assert "No hooks" in text and "init --guidance" in text


def test_every_file_is_small_readable_and_inside_the_folder():
    files = [p for p in PLUGIN_DIR.rglob("*") if p.is_file()]
    assert 0 < len(files) <= 512
    for p in files:
        assert p.stat().st_size < 256 * 1024, f"{p} is over 256 KiB"
        p.read_text(encoding="utf-8")  # readable text, no binaries
        assert not p.is_symlink()
    assert not (PLUGIN_DIR / "uv.toml").exists()
    assert not (PLUGIN_DIR / ".npmrc").exists()


def test_the_hosted_marketplace_installs_this_folder():
    data = json.loads(
        (REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
    )
    entry = next(e for e in data["plugins"] if e["name"] == _manifest()["name"])
    assert (REPO_ROOT / entry["source"]).resolve() == PLUGIN_DIR.resolve()


def test_no_plugin_manifest_is_left_at_the_repository_root():
    """One plugin, one manifest: a second copy would drift from the first."""
    assert not (REPO_ROOT / ".claude-plugin" / "plugin.json").exists()
