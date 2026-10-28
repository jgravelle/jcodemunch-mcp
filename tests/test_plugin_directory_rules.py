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
  its own;
- every file stays small, readable and inside the folder;
- the marketplace this repository hosts installs that same folder.
"""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

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


LISTING_URLS = ("documentationUrl", "supportUrl", "privacyPolicyUrl", "termsOfServiceUrl")


def test_the_directory_listing_links_are_set():
    """Anthropic's directory reads these four links from plugin.json for the listing.

    There is no terms-of-service page, so the terms link is the LICENSE the
    plugin ships, which is the terms of use.
    """
    manifest = _manifest()
    for key in LISTING_URLS:
        assert str(manifest.get(key, "")).startswith("https://"), f"{key} is not an https URL"
    assert manifest["privacyPolicyUrl"] == "https://jcodemunch.com/privacy.php"
    assert manifest["supportUrl"] == "https://github.com/jgravelle/jcodemunch-mcp/issues"
    assert manifest["termsOfServiceUrl"].endswith("/LICENSE")


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
    checks. Ours adds none; the steering hooks stay opt-in through
    `init --guidance`, and the README says so."""
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


SKILL = PLUGIN_DIR / "skills" / "jcodemunch" / "SKILL.md"


_PINNED_PROBE = """
import json, os, sys
sys.path.insert(0, sys.argv[1])
from jcodemunch_mcp.config import DEFAULTS
from jcodemunch_mcp.cli.skills import _build_skill_content
print(json.dumps({"defaults": sorted(DEFAULTS), "skill": _build_skill_content()}))
"""


class NoPinnedSource(Exception):
    """Neither the pinned tag nor the tree under test is the pinned version."""


def _pyproject_version(repo: Path) -> str:
    text = (repo / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert m, "pyproject.toml has no version line"
    return m.group(1)


def _extract_files(archive: bytes, dest: Path) -> None:
    """Write the archive's regular files under `dest`, refusing any path outside it."""
    root = dest.resolve()
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            target = (root / member.name).resolve()
            if not target.is_relative_to(root):
                raise ValueError(f"archive member outside {root}: {member.name}")
            source = tar.extractfile(member)
            assert source is not None
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read())


def _pinned_source(repo: Path, pin: str, dest: Path) -> Path:
    """The `src/` directory of the version the plugin pins.

    Between releases `src/` on main moves ahead of the pin, so the tagged
    source `v<pin>` is read when it exists. On a release PR the pin has just
    moved to a version whose tag does not exist yet (`release.yml` creates it
    after the merge, and the pre-flight requires it absent); there the pin
    equals pyproject's version and the tree under test IS that version.
    Anything else has no source to compare against.
    """
    tag = f"v{pin}"
    if subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}"],
        cwd=repo, capture_output=True,
    ).returncode == 0:
        archive = subprocess.run(
            ["git", "archive", "--format=tar", tag, "src"],
            cwd=repo, capture_output=True, check=True,
        ).stdout
        _extract_files(archive, dest)
        return dest / "src"
    if pin == _pyproject_version(repo):
        return repo / "src"
    raise NoPinnedSource(
        f"tag {tag} is not in this checkout and the pin is not pyproject's version; "
        "fetch tags to check the pinned version"
    )


@pytest.fixture(scope="module")
def pinned(tmp_path_factory):
    """The pinned version's own settings and skill (see `_pinned_source`)."""
    root = tmp_path_factory.mktemp("pinned")
    try:
        src = _pinned_source(REPO_ROOT, _manifest()["version"], root)
    except NoPinnedSource as exc:
        pytest.fail(str(exc))
    home = root / "index-home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("JCODEMUNCH_")}
    env.pop("PYTHONPATH", None)
    env["CODE_INDEX_PATH"] = str(home)
    out = subprocess.run(
        [sys.executable, "-I", "-c", _PINNED_PROBE, str(src)],
        capture_output=True, text=True, encoding="utf-8", env=env, check=True,
    ).stdout
    return json.loads(out)


def _scratch_repo(tmp_path: Path, version: str, tag: str | None) -> Path:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "marker.txt").write_text("tagged", encoding="utf-8")
    (repo / "pyproject.toml").write_text(f'[project]\nversion = "{version}"\n', encoding="utf-8")
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]
    subprocess.run(git + ["init", "-q"], cwd=repo, check=True)
    subprocess.run(git + ["add", "-A"], cwd=repo, check=True)
    subprocess.run(git + ["commit", "-q", "-m", "x"], cwd=repo, check=True)
    if tag:
        subprocess.run(git + ["tag", tag], cwd=repo, check=True)
    (repo / "src" / "marker.txt").write_text("working tree", encoding="utf-8")
    return repo


def test_a_tagged_pin_reads_the_tagged_source(tmp_path):
    repo = _scratch_repo(tmp_path, "2.0.1", tag="v2.0.0")
    src = _pinned_source(repo, "2.0.0", tmp_path / "out")
    assert (src / "marker.txt").read_text(encoding="utf-8") == "tagged"


def test_a_release_pr_reads_the_tree_it_is(tmp_path):
    """A release PR moves the pin to a version with no tag yet; the tree under
    test is that version, so it is what the plugin's text is checked against."""
    repo = _scratch_repo(tmp_path, "2.0.1", tag="v2.0.0")
    src = _pinned_source(repo, "2.0.1", tmp_path / "out")
    assert src == repo / "src"


def test_an_untagged_pin_behind_pyproject_has_no_source(tmp_path):
    repo = _scratch_repo(tmp_path, "2.0.1", tag=None)
    with pytest.raises(NoPinnedSource):
        _pinned_source(repo, "2.0.0", tmp_path / "out")


def test_the_plugin_ships_the_skill_init_writes(pinned):
    """The plugin carries the skill `init` installs in the pinned version, so a
    plugin user gets the routing guidance that matches the server they run.
    Generated, never hand-edited: if this fails, regenerate it from that
    version's `cli.skills._build_skill_content()`."""
    assert SKILL.exists(), "plugin/jcodemunch/skills/jcodemunch/SKILL.md is missing"
    shipped = SKILL.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert shipped == pinned["skill"].replace("\r\n", "\n")


def test_the_plugin_ships_the_skill_and_no_agent():
    """The plugin ships the server and the skill only, with no agent."""
    assert not (PLUGIN_DIR / "agents").exists()
    assert "agents" not in _manifest()
    assert "skill" in README.read_text(encoding="utf-8").lower()


def test_the_readme_names_only_settings_the_pinned_version_has(pinned):
    """A setting named in the listing must exist in the version the plugin
    pins, or the listing promises behavior the user does not get."""
    text = README.read_text(encoding="utf-8")
    named = set(re.findall(r"`([a-z][a-z0-9_]+)\s*:", text))
    assert named, "the README's Settings section names no setting"
    missing = sorted(n for n in named if n not in set(pinned["defaults"]))
    assert not missing, f"README names settings the pinned version lacks: {missing}"
