"""Two invariants every entry point that takes a filesystem path must hold.

Written as a ratchet because both have now been reported twice, each time
against a different call site of the same mechanism:

**Containment is not identity.** #492 fixed `resolve_repo`, which matched on
`source_root` containment and returned the enclosing parent index for a path
belonging to a nested independent repository. #509 is the same logic still
present in `index_file` — where the consequence is a *write* into the wrong
repository's index rather than a read.

**`repo=` only works if the overlay is loaded.** #491 threaded `repo=` through
`security.py`'s exclusion resolvers. #508 is that keyword arriving on a path
where nothing ever called `load_project_config`, so it resolves to global config
and the project's opt-out is inert. **The parameter is present and does
nothing**, which is indistinguishable from the defect it was added to fix.

⚠ The lesson these share is the one this project keeps re-learning: fixing the
reported call site leaves the mechanism. A ratchet over the entry points costs
less than a third report.
"""

import json
import subprocess

import pytest

from jcodemunch_mcp.tools.index_file import index_file
from jcodemunch_mcp.tools.index_folder import index_folder
from jcodemunch_mcp.tools.resolve_repo import resolve_repo


def _git(cwd, *args):
    subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True
    )


def _make_repo(path, files):
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "repro@example.invalid")
    _git(path, "config", "user.name", "repro")
    for name, text in files.items():
        (path / name).write_text(text)
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")
    return path


class TestContainmentIsNotIdentity:
    """A path inside a nested INDEPENDENT repository must never be attributed to
    the enclosing indexed parent, by any entry point."""

    @pytest.fixture
    def nested(self, tmp_path):
        store = tmp_path / "store"
        store.mkdir()
        parent = _make_repo(
            tmp_path / "parent", {"marker.py": "def marker():\n    return 1\n"}
        )
        (parent / ".gitignore").write_text("nested-repo/\n")
        _git(parent, "add", "-A")
        _git(parent, "commit", "-q", "-m", "ignore nested")
        nested = _make_repo(
            parent / "nested-repo", {"alpha.py": "def alpha():\n    return 1\n"}
        )
        result = index_folder(
            path=str(parent), identity_mode="local", use_ai_summaries=False,
            storage_path=str(store),
        )
        assert result["success"] is True

        class Fixture:
            parent_repo = result["repo"]
            parent_path = parent
            nested_path = nested
            store_path = str(store)
            target = nested / "alpha.py"

        return Fixture

    def test_resolve_repo_does_not_claim_it(self, nested):
        """#492, fixed in v1.108.285. The control for the entry point below."""
        got = resolve_repo(path=str(nested.target), storage_path=nested.store_path)
        assert got.get("repo") != nested.parent_repo or got.get("indexed") is not True

    def test_index_file_does_not_write_it_into_the_parent(self, nested):
        """#509. A read returning the wrong repo is a wrong answer; a WRITE into
        the wrong repo corrupts an index the caller never named."""
        result = index_file(
            path=str(nested.target), use_ai_summaries=False,
            storage_path=nested.store_path,
        )
        wrote_to = result.get("repo")
        assert wrote_to != nested.parent_repo, (
            f"index_file wrote {nested.target.name!r} into {wrote_to!r}, an index "
            "built from a different repository with a different history"
        )

    def test_the_refusal_names_the_repository(self, nested):
        """A refusal that says 'no index contains this' is wrong here — the
        parent index does contain the path. Saying so sends the caller to
        `index_folder` on the parent, which is the wrong remedy."""
        result = index_file(
            path=str(nested.target), use_ai_summaries=False,
            storage_path=nested.store_path,
        )
        assert result.get("success") is False
        assert result.get("skipped") == "different_repository"
        assert "nested-repo" in (result.get("error") or ""), (
            "the refusal does not name the repository the file actually "
            f"belongs to: {result.get('error')!r}"
        )


class TestProjectConfigReachesEveryPathEntryPoint:
    """`config.get(key, repo=path)` reads an overlay only
    `load_project_config()` populates. An entry point that passes `repo=`
    without ensuring the overlay is loaded has a parameter that does nothing."""

    @pytest.fixture
    def project(self, tmp_path):
        store = tmp_path / "store"
        store.mkdir()
        proj = _make_repo(tmp_path / "proj", {"main.py": "def main():\n    return 1\n"})
        # Matches a built-in secret rule AND has a supported extension, so it is
        # refused unless the project's opt-out applies.
        (proj / "settings.env.py").write_text("TOKEN = 'x'\n")
        (proj / ".jcodemunch.jsonc").write_text(
            json.dumps({"exclude_secret_patterns": ["settings.env.py"]}, indent=2)
        )
        _git(proj, "add", "-A")
        _git(proj, "commit", "-q", "-m", "add settings")

        class Fixture:
            path = proj
            store_path = str(store)
            secret_file = proj / "settings.env.py"

        return Fixture

    def test_index_folder_honours_it(self, project):
        """The control: this entry point calls `load_project_config` itself."""
        result = index_folder(
            path=str(project.path), identity_mode="local", use_ai_summaries=False,
            storage_path=project.store_path,
        )
        skipped = (result.get("discovery_skip_counts") or {}).get("secret", 0)
        assert skipped == 0, (
            "the project's exclude_secret_patterns did not apply to index_folder"
        )

    def test_index_file_honours_it(self, project):
        """#508. `index_file` passes `repo=` to `is_secret_file` and nothing on
        that path ever loads the overlay the keyword reads."""
        index_folder(
            path=str(project.path), identity_mode="local", use_ai_summaries=False,
            storage_path=project.store_path,
        )
        # Fresh overlay state: exactly what a hook-driven single-file refresh
        # sees in a process that never walked the folder.
        from jcodemunch_mcp import config as cfg

        cfg._PROJECT_CONFIGS.clear()

        result = index_file(
            path=str(project.secret_file), use_ai_summaries=False,
            storage_path=project.store_path,
        )

        assert result.get("success") is True, (
            f"index_file refused a file the project's opt-out permits: "
            f"{result.get('error')!r}"
        )
